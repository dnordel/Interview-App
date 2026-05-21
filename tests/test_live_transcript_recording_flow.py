import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from interview_session_store import InterviewSessionStore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
InterviewApp = interview_app.InterviewApp


class _QStoreStub:
    @staticmethod
    def get_trait_question_override(_trait_id: str) -> str:
        return ""


class _RecordingSessionStub:
    def __init__(self, jsonl_path: Path):
        self._jsonl_path = jsonl_path
        self.mic_wav = Path("/tmp/mic.wav")
        self.sys_wav = Path("/tmp/sys.wav")
        self.stop_called = False

    def stop(self):
        self.stop_called = True

    def stop_and_transcribe(self, **_kwargs):
        return SimpleNamespace(
            mic_wav=Path("/tmp/mic.wav"),
            sys_wav=Path("/tmp/sys.wav"),
            transcript_txt=Path("/tmp/transcript.txt"),
            transcript_jsonl=self._jsonl_path,
        )


class TestLiveTranscriptRecordingFlow(unittest.TestCase):
    def _build_app(self, base_dir: Path):
        app = InterviewApp.__new__(InterviewApp)
        app.settings = {"base_dir": str(base_dir)}
        app.state = SimpleNamespace(
            candidate_name="Jane Doe",
            interview_date="2026-02-06",
            school="Maple Preschool",
            trait_inputs={"trait-1": {"verbatim_notes": "", "question_notes": ""}},
            custom_inputs={},
            flow_recordings={},
            flow_candidate_transcripts={0: "I supported children through transitions by modeling routines."},
            flow_time_marks=[],
            referral_packet={"resume_path": "", "interview_notes_path": "", "transcript_path": ""},
            communication_log=[],
            track="",
        )
        app.state.to_dict = lambda: {
            "candidate": {
                "name": app.state.candidate_name,
                "interview_date": app.state.interview_date,
                "school": app.state.school,
                "track": app.state.track,
            },
            "trait_inputs": app.state.trait_inputs,
            "custom_inputs": app.state.custom_inputs,
            "flow_recordings": app.state.flow_recordings,
            "flow_candidate_transcripts": app.state.flow_candidate_transcripts,
            "referral_packet": app.state.referral_packet,
            "communication_log": app.state.communication_log,
        }
        app.qstore = _QStoreStub()
        app.active_flow = [{"type": "trait", "id": "trait-1"}]
        app.active_traits = [{"id": "trait-1", "name": "Classroom Management", "primary_question": "Tell me about a time you handled a difficult transition."}]
        app.custom_questions = []
        app.recording_session = None
        app.recording_base_name = ""
        app.recording_started_monotonic = None
        app.recording_candidate_label = "CANDIDATE"
        app.live_transcript_docx = None
        app.transcript_available = True
        app.transcript_warning = ""
        app._transcription_cv = threading.Condition()
        app._transcription_in_progress = False
        app._transcription_queue_state = interview_app.TranscriptionQueueState()
        app.audio_runtime_controller = SimpleNamespace(
            wait_for_pending_transcriptions=lambda: app._transcription_queue_state.wait_for_pending(),
            background_transcribe_question=lambda **kwargs: app._background_transcribe_question(**kwargs),
            start_recording_with_runtime_fallback=lambda *_args, **_kwargs: None,
        )
        app._transcription_worker_started = False
        app._question_transcription_errors = {}
        app._current_question_whisper_source = "default"
        app.ui_router = SimpleNamespace(show_flow_screen=lambda _idx: None)
        app.history_store = SimpleNamespace(append=lambda _row: None)
        app.state.track = "Lead Teacher"
        app.rubric = {"tracks": {"Lead Teacher": {}}, "traits": []}
        app._rubric_with_question_overrides = lambda: app.rubric
        return app


    def test_background_transcription_forwards_job_timeout_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            captured: dict[str, float] = {}

            app.audio_runtime_controller = SimpleNamespace(
                background_transcribe_question=lambda **kwargs: captured.update(
                    {"job_timeout_seconds": float(kwargs["job_timeout_seconds"])}
                )
            )
            app._background_transcribe_question(
                flow_idx=0,
                session=object(),
                base_dir=base_dir,
                base_name="q0",
                candidate_label="CANDIDATE",
                job_timeout_seconds=42.0,
            )

            self.assertEqual(captured["job_timeout_seconds"], 42.0)

    def test_background_transcription_is_serialized(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            observed_order: list[int] = []
            order_lock = threading.Lock()

            def fake_background_transcribe(**kwargs):
                flow_idx = int(kwargs["flow_idx"])
                with order_lock:
                    observed_order.append(flow_idx)
                time.sleep(0.02)
                app._transcription_queue_state.mark_completed(flow_idx)

            app._background_transcribe_question = fake_background_transcribe
            app.recording_candidate_label = "CANDIDATE"
            app.recording_session = SimpleNamespace(stop=lambda: None)
            app.recording_flow_idx = 0
            app.recording_base_name = "q0"
            app._start_background_question_transcription(0)

            app.recording_session = SimpleNamespace(stop=lambda: None)
            app.recording_flow_idx = 1
            app.recording_base_name = "q1"
            app._start_background_question_transcription(1)
            app._wait_for_pending_transcriptions()

            self.assertEqual(observed_order, [0, 1])

    def test_transition_persists_interim_session_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            app._start_interview_recording()
            app.show_flow_screen = lambda _idx: None

            app.state.trait_inputs["trait-1"]["question_notes"] = "Candidate described transition routine."
            app.state.flow_candidate_transcripts[0] = "I supported children through transitions by modeling routines."
            app._queue_transcription_and_transition(0, next_index=0, discard_recording=True)

            self.assertIsNotNone(app.live_transcript_docx)
            self.assertFalse(app.live_transcript_docx.exists())

            store = InterviewSessionStore(base_dir)
            payload = store.load(app.interview_session_id, app.state.candidate_name, app.state.interview_date)
            question = payload["questions"].get("0", {})
            self.assertEqual(question.get("item_type"), "trait")
            self.assertIn("transition routine", question.get("notes", {}).get("question_notes", ""))
            self.assertIn("modeling routines", question.get("candidate_transcript", ""))

    def test_finalize_pipeline_creates_docx_transcript_once(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            app._start_interview_recording()
            app.state.flow_candidate_transcripts[0] = "Final transcript segment."
            app._persist_interview_session_snapshot(0)

            with patch.object(
                interview_app.ScoringEngine,
                "evaluate",
                new=staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 90, "outcome": "Hire"}),
            ), patch.object(
                interview_app,
                "DocxExporter",
                new=lambda *_args, **_kwargs: SimpleNamespace(export=lambda *_a, **_k: str(base_dir / "notes.docx")),
            ), patch.object(
                interview_app,
                "build_integration_payload",
                new=lambda *_args, **_kwargs: {"ok": True},
            ), patch.object(
                interview_app,
                "serialize_integration_payload",
                new=lambda *_args, **_kwargs: str(base_dir / "integration.json"),
            ), patch.object(
                interview_app,
                "build_director_packet",
                new=lambda **_kwargs: {"documents": {"transcript_path": str(app.live_transcript_docx)}},
            ), patch.object(app, "_wait_for_pending_transcriptions", new=lambda: None), patch.object(
                app,
                "_collect_transcription_health_warnings",
                new=lambda: [],
            ):
                self.assertFalse(app.live_transcript_docx.exists())
                result = app._run_finalize_pipeline()

            self.assertTrue(Path(result["transcript_path"]).exists())

    def test_flow_transcript_trait_block_does_not_replace_transcript_with_notes(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            block = app._flow_transcript_question_block(
                {
                    "type": "trait",
                    "id": "trait-1",
                    "title": "Classroom Management",
                    "question": "Tell me about a transition you handled.",
                    "candidate_transcript": "",
                    "question_notes": "Candidate described transition routine.",
                    "verbatim_notes": "",
                },
                0,
            )

            self.assertIn(
                "Answer Segment (auto-transcribed): (No candidate transcript captured)",
                block,
            )
            self.assertIn("Evaluator Notes: Candidate described transition routine.", block)

    def test_flow_transcript_custom_block_does_not_replace_transcript_with_answer_notes(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            block = app._flow_transcript_question_block(
                {
                    "type": "custom",
                    "id": "custom-1",
                    "title": "Custom Question",
                    "question": "Tell me about parent communication.",
                    "candidate_transcript": "",
                    "answer": "Interviewer typed a summary note.",
                },
                0,
            )

            self.assertIn(
                "Answer Segment (auto-transcribed): (No candidate transcript captured)",
                block,
            )
            self.assertNotIn("Interviewer typed a summary note.", "\n".join(block))


    def test_stop_extracts_candidate_transcript_from_recording_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            jsonl_path = base_dir / "segments.jsonl"
            entries = [
                {"speaker": "INTERVIEWER", "text": "Question text"},
                {"speaker": "CANDIDATE", "text": "First answer sentence."},
                {"speaker": "CANDIDATE", "text": "Second answer sentence."},
            ]
            with jsonl_path.open("w", encoding="utf-8") as fh:
                for item in entries:
                    fh.write(json.dumps(item) + "\n")

            app = self._build_app(base_dir)
            app.recording_session = _RecordingSessionStub(jsonl_path)
            app.recording_flow_idx = 0
            app.recording_base_name = "Candidate_Jane_Doe_2026-02-06"
            rec = app._stop_interview_recording(show_warning=False)

            self.assertIsNotNone(rec)
            self.assertIn("First answer sentence. Second answer sentence.", rec["candidate_transcript"])
            self.assertEqual(rec["transcript_jsonl"], str(jsonl_path))

    def test_stop_interview_recording_ignores_missing_runtime_attrs(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            app.__dict__.pop("recording_flow_idx", None)
            app.__dict__.pop("recording_session", None)
            app.__dict__.pop("recording_base_name", None)

            self.assertIsNone(app._stop_interview_recording(show_warning=False))

    def test_extract_candidate_transcript_normalizes_speaker_label(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            jsonl_path = base_dir / "segments.jsonl"
            entries = [
                {"speaker": "candidate", "text": "Lower-case answer."},
                {"speaker": " CANDIDATE ", "text": "Trimmed answer."},
                {"speaker": "Candidate", "text": "Title-case answer."},
                {"speaker": "INTERVIEWER", "text": "Question text"},
            ]
            with jsonl_path.open("w", encoding="utf-8") as fh:
                for item in entries:
                    fh.write(json.dumps(item) + "\n")

            app = self._build_app(base_dir)
            transcript = app._extract_candidate_transcript_from_jsonl(jsonl_path, "CANDIDATE")

            self.assertEqual(
                transcript,
                "Lower-case answer. Trimmed answer. Title-case answer.",
            )

    def test_load_candidate_segments_normalizes_speaker_label(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            jsonl_path = base_dir / "segments.jsonl"
            entries = [
                {"speaker": "candidate", "start": 1.0, "text": "Lower-case segment."},
                {"speaker": " CANDIDATE ", "start": 2.0, "text": "Trimmed segment."},
                {"speaker": "Candidate", "start": 3.0, "text": "Title-case segment."},
                {"speaker": "INTERVIEWER", "start": 4.0, "text": "Question text"},
            ]
            with jsonl_path.open("w", encoding="utf-8") as fh:
                for item in entries:
                    fh.write(json.dumps(item) + "\n")

            app = self._build_app(base_dir)
            segments = app._load_candidate_segments_from_jsonl(jsonl_path, "CANDIDATE")

            self.assertEqual(
                segments,
                [
                    {"start": 1.0, "text": "Lower-case segment."},
                    {"start": 2.0, "text": "Trimmed segment."},
                    {"start": 3.0, "text": "Title-case segment."},
                ],
            )

    def test_apply_candidate_transcripts_accepts_rec_keyword(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            app.state.flow_candidate_transcripts = {}
            app.state.flow_recordings = {}

            flow_tx = [{"question": "Q1"}]
            app._apply_candidate_transcripts_to_flow(
                flow_tx,
                rec={"flow_index": 0, "candidate_transcript": "Transcript via rec kw."},
            )

            self.assertEqual(flow_tx[0]["candidate_transcript"], "Transcript via rec kw.")

    def test_discard_question_recording_skips_transcription_and_clears_state(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            mic_path = base_dir / "mic.wav"
            sys_path = base_dir / "sys.wav"
            mic_path.write_text("audio", encoding="utf-8")
            sys_path.write_text("audio", encoding="utf-8")

            app = self._build_app(base_dir)
            app.recording_session = _RecordingSessionStub(base_dir / "segments.jsonl")
            app.recording_session.mic_wav = mic_path
            app.recording_session.sys_wav = sys_path
            app.recording_flow_idx = 0
            app.recording_base_name = "Candidate_Jane_Doe_2026-02-06"
            app.state.flow_recordings[0] = {"candidate_transcript": "old"}
            app.state.flow_candidate_transcripts[0] = "old"

            app.show_flow_screen = lambda _idx: None
            app._queue_transcription_and_transition(0, next_index=0, discard_recording=True)

            self.assertTrue(app.recording_session is None)
            self.assertFalse(mic_path.exists())
            self.assertFalse(sys_path.exists())
            self.assertNotIn(0, app.state.flow_recordings)
            self.assertNotIn(0, app.state.flow_candidate_transcripts)

    def test_show_flow_screen_continues_when_recording_start_fails(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            app = self._build_app(base_dir)
            routed_indexes: list[int] = []
            app.ui_router = SimpleNamespace(show_flow_screen=lambda idx: routed_indexes.append(idx))

            app.show_flow_screen(0)

            self.assertEqual(routed_indexes, [0])

    def test_sanitize_transcription_error_reason_keeps_diagnostic_filename(self):
        with tempfile.TemporaryDirectory() as td:
            app = self._build_app(Path(td))
            raw = "Transcription failed. Diagnostic log: /tmp/some/deep/path/failure-report.json"
            sanitized = app._sanitize_transcription_error_reason(raw)
            self.assertIn("failure-report.json", sanitized)
            self.assertNotIn("/tmp/some/deep/path", sanitized)


if __name__ == "__main__":
    unittest.main()
