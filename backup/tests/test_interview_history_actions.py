import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

loader = importlib.machinery.SourceFileLoader("interview_app", "src/interview_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
interview_app = importlib.util.module_from_spec(spec)
loader.exec_module(interview_app)
HistoryActionsService = interview_app.HistoryActionsService


class _HistoryStoreStub:
    def __init__(self, row_key: str = "row-1") -> None:
        self.row_key = row_key
        self.offer_updates: list[tuple[str, str, str]] = []
        self.row_updates: list[tuple[str, dict[str, object]]] = []
        self.rows: list[dict[str, object]] = []

    def build_row_key(self, _row):
        return self.row_key

    def update_offer_state(self, row_key, status, offer_path):
        self.offer_updates.append((row_key, status, offer_path))
        return True

    def update_row(self, row_key, payload):
        self.row_updates.append((row_key, payload))
        return True

    def load(self):
        return list(self.rows)


class _AppStub:
    def __init__(self, row_key: str = "row-1") -> None:
        self.history_store = _HistoryStoreStub(row_key=row_key)
        self.refresh_count = 0
        self.draft_ok = True
        self.opened_onboarding = 0
        self.offer_window_opened = 0
        self.retranscribe_path = "transcript.txt"
        self.settings = {}
        self.whisper_fallback_warnings = 0

    def _refresh_history_tree(self):
        self.refresh_count += 1

    def _open_offer_generator(self, _row):
        self.offer_window_opened += 1

    def _open_onboarding_tracker(self):
        self.opened_onboarding += 1
        return True

    def _draft_offer_email_for_transition(self, _status, _row):
        return self.draft_ok

    def _latest_recording_attempt(self, recording):
        attempts = recording.get("attempts") if isinstance(recording, dict) else []
        if not attempts:
            return {}
        return attempts[-1]

    def _current_whisper_language(self):
        return "en"

    def _current_whisper_transcription_settings(self):
        return {"vad_filter": True, "beam_size": 5, "temperature": 0.0}

    def _extract_candidate_transcript_from_jsonl(self, _path, _label):
        return ""

    def _load_jsonl_segments_for_merge(self, _path):
        return []

    def _write_merged_history_transcript(self, _row, _segments):
        return Path(self.retranscribe_path)

    def _warn_whisper_fallback_once(self):
        self.whisper_fallback_warnings += 1


class _ImmediateThread:
    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if callable(self._target):
            self._target()


class _TkAppStub(_AppStub):
    def __init__(self):
        super().__init__()
        self._scheduled: list[tuple[int, object]] = []

    def after(self, delay_ms, callback):
        self._scheduled.append((delay_ms, callback))

    def run_scheduled(self):
        callbacks = [callback for _delay, callback in self._scheduled]
        self._scheduled.clear()
        for callback in callbacks:
            callback()



class TestInterviewHistoryActions(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_offer_status_transitions(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        row = {"offer_status": "generated"}

        with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
            service.handle_offer_action_for_row(row)

        self.assertEqual(app.history_store.offer_updates, [("row-1", "approved", "")])
        self.assertEqual(app.refresh_count, 1)
        showinfo.assert_called_once_with("Offer Workflow", "Offer marked as approved.")

    def test_retranscribe_missing_recording_metadata_shows_warning(self):
        app = _AppStub()
        service = HistoryActionsService(app)

        with patch("interview_app.history_actions.messagebox.showwarning") as showwarning:
            service.handle_retranscribe_for_row({"flow_recordings": []})

        self.assertEqual(app.history_store.row_updates, [])
        self.assertEqual(app.refresh_count, 0)
        showwarning.assert_called_once()


    def test_retranscribe_recovers_recordings_from_saved_wav_files(self):
        base_dir = Path(self._tmpdir.name) / "interviews"
        base_dir.mkdir(parents=True, exist_ok=True)
        stem = "Candidate_Yanet_Luna_Romero_2026-03-12_Q3_trait_trait_1"
        (base_dir / f"{stem}_sys.wav").write_bytes(b"audio")

        app = _AppStub()
        app.settings = {"base_dir": str(Path(self._tmpdir.name))}
        service = HistoryActionsService(app)
        row = {"candidate_name": "Yanet Luna Romero", "interview_date": "2026-03-12"}

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertIn("flow_recordings", row)
        self.assertEqual(row["flow_recordings"][0]["flow_index"], 2)
        self.assertEqual(
            Path(row["flow_recordings"][0]["attempts"][0]["sys_wav"]).name,
            f"{stem}_sys.wav",
        )
        self.assertEqual(len(app.history_store.row_updates), 1)
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")


    def test_retranscribe_recovers_latest_date_when_row_date_missing(self):
        base_dir = Path(self._tmpdir.name) / "interviews"
        base_dir.mkdir(parents=True, exist_ok=True)
        old_stem = "Candidate_Yanet_Luna_Romero_2026-03-10_Q1_trait_trait_1"
        new_stem = "Candidate_Yanet_Luna_Romero_2026-03-12_Q1_trait_trait_1"
        (base_dir / f"{old_stem}_sys.wav").write_bytes(b"old")
        (base_dir / f"{new_stem}_sys.wav").write_bytes(b"new")

        app = _AppStub()
        app.settings = {"base_dir": str(Path(self._tmpdir.name))}
        service = HistoryActionsService(app)
        row = {"candidate_name": "Yanet Luna Romero"}

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertIn("flow_recordings", row)
        self.assertEqual(
            Path(row["flow_recordings"][0]["attempts"][0]["sys_wav"]).name,
            f"{new_stem}_sys.wav",
        )
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")

    def test_retranscribe_recovers_wav_files_from_parent_interviews_folder(self):
        workspace_root = Path(self._tmpdir.name)
        app_base_dir = workspace_root / "Initial Teacher Interview Guide"
        app_base_dir.mkdir(parents=True, exist_ok=True)
        interviews_dir = workspace_root / "interviews"
        interviews_dir.mkdir(parents=True, exist_ok=True)
        stem = "Candidate_Yanet_Luna_Romero_2026-03-12_Q1_trait_trait_1"
        (interviews_dir / f"{stem}_sys.wav").write_bytes(b"audio")

        app = _AppStub()
        app.settings = {"base_dir": str(app_base_dir)}
        service = HistoryActionsService(app)
        row = {"candidate_name": "Yanet Luna Romero", "interview_date": "2026-03-12"}

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertIn("flow_recordings", row)
        self.assertEqual(row["flow_recordings"][0]["flow_index"], 0)
        self.assertEqual(
            Path(row["flow_recordings"][0]["attempts"][0]["sys_wav"]).name,
            f"{stem}_sys.wav",
        )
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")


    def test_retranscribe_success_updates_history_and_shows_info(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        row = {"flow_recordings": [{"flow_index": 0, "attempts": []}]}

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertEqual(len(app.history_store.row_updates), 1)
        row_key, payload = app.history_store.row_updates[0]
        self.assertEqual(row_key, "row-1")
        self.assertEqual(payload["transcript_path"], "transcript.txt")
        self.assertEqual(payload["flow_recordings"], row["flow_recordings"])
        self.assertEqual(app.refresh_count, 1)
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")

    def test_retranscribe_accepts_dict_flow_recordings_format(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        row = {
            "flow_recordings": {
                "0": {
                    "base_name": "Candidate_A_2026-03-12_Q1_trait_x",
                    "mic_wav": "mic.wav",
                    "sys_wav": "sys.wav",
                    "candidate_label": "CANDIDATE",
                }
            }
        }

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertIsInstance(row["flow_recordings"], list)
        self.assertEqual(row["flow_recordings"][0]["flow_index"], 0)
        self.assertEqual(row["flow_recordings"][0]["wav_paths"]["system"], "sys.wav")
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")


    def test_retranscribe_uses_persisted_history_row_metadata(self):
        app = _AppStub()
        app.history_store.rows = [{
            "history_id": "row-1",
            "flow_recordings": [{"flow_index": 0, "attempts": [{"sys_wav": "persisted.wav"}]}],
        }]
        service = HistoryActionsService(app)
        row = {"history_id": "row-1"}

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertEqual(row["flow_recordings"][0]["attempts"][0]["sys_wav"], "persisted.wav")
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")

    def test_retranscribe_uses_audio_recording_when_flow_recordings_missing(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        row = {
            "audio_recording": [
                {
                    "flow_index": 1,
                    "base_name": "Candidate_B_2026-03-12_Q2_trait_y",
                    "attempts": [{"sys_wav": "sys.wav"}],
                }
            ]
        }

        with patch.object(service, "retranscribe_history_recordings", return_value="transcript.txt"):
            with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                service.handle_retranscribe_for_row(row)

        self.assertIn("flow_recordings", row)
        self.assertEqual(row["flow_recordings"][0]["flow_index"], 1)
        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")


    def test_retranscribe_async_uses_progress_dialog_and_keeps_ui_responsive(self):
        app = _TkAppStub()
        service = HistoryActionsService(app)
        row = {
            "flow_recordings": [
                {
                    "flow_index": 0,
                    "attempts": [{"sys_wav": "sys.wav", "candidate_label": "CANDIDATE"}],
                }
            ]
        }

        class _DialogStub:
            def __init__(self, _parent, total_steps):
                self.total_steps = total_steps
                self.events: list[tuple[str, int, str]] = []

            def show(self):
                self.events.append(("show", self.total_steps, ""))

            def update(self, *, completed_steps, status_text):
                self.events.append(("update", completed_steps, status_text))

            def close(self):
                self.events.append(("close", self.total_steps, ""))

        with patch("interview_app.history_actions.RetranscriptionProgressDialog", _DialogStub):
            with patch("interview_app.history_actions.threading.Thread", _ImmediateThread):
                with patch.object(service, "_run_retranscribe_flow", return_value={"transcript_path": "transcript.txt"}):
                    with patch("interview_app.history_actions.messagebox.showinfo") as showinfo:
                        service.handle_retranscribe_for_row(row)
                        app.run_scheduled()

        showinfo.assert_called_once_with("Transcription", "Transcript regenerated:\ntranscript.txt")

    def test_retranscribe_falls_back_to_cpu_on_cublas_error(self):
        app = _AppStub()
        app.settings = {
            "whisper_runtime_model": "large-v3",
            "whisper_runtime_device": "cuda",
            "whisper_runtime_compute_type": "float16",
        }
        service = HistoryActionsService(app)

        call_count = {"total": 0}

        def _transcribe(**kwargs):
            call_count["total"] += 1
            if kwargs["whisper_device"] == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return SimpleNamespace(transcript_txt=Path("out.txt"), transcript_jsonl=Path("out.jsonl"))

        result = service._transcribe_recording_with_runtime_fallback(
            transcribe_existing_recordings=_transcribe,
            output_dir=Path(self._tmpdir.name),
            base_name="Candidate_Test_2026-03-12_Q1_trait_trait_1",
            attempt={"mic_wav": "mic.wav", "sys_wav": "sys.wav"},
            candidate_label="CANDIDATE",
            preferred_runtime=SimpleNamespace(model="large-v3", device="cuda", compute_type="float16"),
        )

        self.assertEqual(call_count["total"], 2)
        self.assertEqual(result.transcript_txt, Path("out.txt"))
        self.assertEqual(app.settings.get("whisper_runtime_device"), "cpu")
        self.assertEqual(app.settings.get("whisper_runtime_mode"), "cpu_fallback")
        self.assertEqual(app.whisper_fallback_warnings, 1)

    def test_resolve_transcribe_existing_recordings_supports_legacy_name(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        recorder_module = ModuleType("interview_audio_recorder")

        def _legacy(**_kwargs):
            return None

        setattr(recorder_module, "transcribe_existing_recording", _legacy)

        with patch("interview_app.history_actions.import_module", return_value=recorder_module):
            resolved = service._resolve_transcribe_existing_recordings()

        self.assertIs(resolved, _legacy)

    def test_resolve_transcribe_existing_recordings_raises_with_available_exports(self):
        app = _AppStub()
        service = HistoryActionsService(app)
        recorder_module = ModuleType("interview_audio_recorder")

        def _transcribe_history(**_kwargs):
            return None

        setattr(recorder_module, "transcribe_history", _transcribe_history)

        with patch("interview_app.history_actions.import_module", return_value=recorder_module):
            with self.assertRaises(RuntimeError) as exc_info:
                service._resolve_transcribe_existing_recordings()

        self.assertIn("Expected one of", str(exc_info.exception))
        self.assertIn("transcribe_history", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
