import importlib.machinery
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
InterviewApp = interview_app.InterviewApp


class _HistoryStoreStub:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class _QStoreStub:
    data = {}


class _DocxExporterStub:
    def __init__(self, _base_dir: Path):
        pass

    def export(self, _rubric, _payload, _scoring):
        return Path("/tmp/generated-final-notes.docx")


class TestFinalizeHistoryPersistence(unittest.TestCase):
    def test_finalize_history_interview_notes_path_uses_generated_docx(self):
        with tempfile.TemporaryDirectory() as td:
            app = InterviewApp.__new__(InterviewApp)
            app.rubric = {}
            app.qstore = _QStoreStub()
            app.settings = {
                "base_dir": td,
                "send_director_referral_on_finalize": False,
            }
            app.active_flow = []
            app.custom_questions = []
            app.history_store = _HistoryStoreStub()
            app.state = SimpleNamespace(
                track="lead",
                trait_inputs={},
                custom_inputs={},
                flow_recordings={},
                referral_packet={"interview_notes_path": "stale-path.docx", "transcript_path": ""},
                communication_log=[],
                flow_candidate_transcripts={},
                candidate_name="Ada Lovelace",
                to_dict=lambda: {
                    "candidate": {
                        "name": "Ada Lovelace",
                        "interview_date": "2026-02-20",
                        "school": "PS 10",
                        "track": "lead",
                    }
                },
            )
            app._stop_interview_recording = lambda show_warning=False: None
            app._transcription_cv = threading.Condition()
            app._transcription_queue_state = interview_app.TranscriptionQueueState()
            app.audio_runtime_controller = SimpleNamespace(
                wait_for_pending_transcriptions=lambda: app._transcription_queue_state.wait_for_pending(),
            )
            app._question_transcription_errors = {}
            app.ui_router = SimpleNamespace(show_flow_screen=lambda _idx: None)
            app._build_flow_transcript = lambda: []
            app._apply_candidate_transcripts_to_flow = lambda flow_tx: None
            app._rewrite_live_transcript_docx_from_flow = lambda flow_tx: None

            old_evaluate = interview_app.ScoringEngine.evaluate
            old_exporter = interview_app.DocxExporter
            old_build_integration_payload = interview_app.build_integration_payload
            old_serialize_integration_payload = interview_app.serialize_integration_payload
            old_build_director_packet = interview_app.build_director_packet
            try:
                interview_app.ScoringEngine.evaluate = staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 95, "outcome": "Hire"})
                interview_app.DocxExporter = _DocxExporterStub
                interview_app.build_integration_payload = lambda *_args, **_kwargs: {"ok": True}
                interview_app.serialize_integration_payload = lambda *_args, **_kwargs: Path("/tmp/integration.json")
                interview_app.build_director_packet = lambda **_kwargs: {"documents": {}}

                app.__dict__.pop("live_transcript_docx", None)
                app.__dict__.pop("recording_flow_idx", None)
                app._run_finalize_pipeline()
            finally:
                interview_app.ScoringEngine.evaluate = old_evaluate
                interview_app.DocxExporter = old_exporter
                interview_app.build_integration_payload = old_build_integration_payload
                interview_app.serialize_integration_payload = old_serialize_integration_payload
                interview_app.build_director_packet = old_build_director_packet

            self.assertEqual(len(app.history_store.rows), 1)
            self.assertEqual(
                app.history_store.rows[0]["interview_notes_path"],
                "/tmp/generated-final-notes.docx",
            )
            self.assertEqual(
                app.state.referral_packet["interview_notes_path"],
                "/tmp/generated-final-notes.docx",
            )
            self.assertEqual(
                app.history_store.rows[0]["offer_status"],
                "not_generated",
            )
            self.assertIn("history_id", app.history_store.rows[0])


if __name__ == "__main__":
    unittest.main()
