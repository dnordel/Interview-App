from __future__ import annotations

import queue
import threading
from pathlib import Path
from types import SimpleNamespace

from interview_app.audio_runtime import AudioRuntimeController
from interview_app.finalize_pipeline import FinalizePipelineController
from interview_app.history_controller import HistoryController
from interview_app.state import AppSharedState
from interview_app.transcription_queue import TranscriptionQueueState
import interview_runtime


class _RootWindow:
    def __init__(self) -> None:
        self.lifted = False
        self.focused = False

    def lift(self) -> None:
        self.lifted = True

    def focus_force(self) -> None:
        self.focused = True


class _FakeSession:
    def __init__(self) -> None:
        self.result = SimpleNamespace(
            mic_wav="/tmp/m.wav",
            sys_wav="/tmp/s.wav",
            transcript_txt="/tmp/t.txt",
            transcript_jsonl="/tmp/t.jsonl",
        )

    def stop_and_transcribe(self, **_: object) -> SimpleNamespace:
        return self.result


class _FakeGrid:
    def __init__(self, _parent, **_kwargs) -> None:
        self.rows: list[dict[str, object]] = []
        self.filter_text = ""

    def pack(self, **_kwargs) -> None:
        return None

    def set_rows(self, rows):
        self.rows = list(rows)

    def set_filter_text(self, value: str) -> None:
        self.filter_text = value

    def visible_rows(self):
        text = self.filter_text.strip().lower()
        if not text:
            return self.rows
        return [row for row in self.rows if text in str(row.get("candidate_name", "")).lower()]

    def selected_row(self):
        if not self.rows:
            return None
        return self.rows[0]


def test_audio_runtime_transcription_state_transitions() -> None:
    app = SimpleNamespace()
    app._audio_state_lock = threading.Lock()
    app._transcription_queue_state = TranscriptionQueueState()
    app._transcription_queue_state.enqueue(2, {
        "flow_idx": 2,
        "session": object(),
        "base_dir": Path("/tmp"),
        "base_name": "base",
        "candidate_label": "CANDIDATE",
    })
    app.state = SimpleNamespace(flow_candidate_transcripts={})
    app._append_recording_attempt = lambda _idx, payload: payload
    app._persist_interview_session_snapshot = lambda _idx: None
    app._extract_candidate_transcript_from_jsonl = lambda _path, _label: "candidate text"
    app._delete_file_if_exists = lambda _path: None
    shared = AppSharedState()
    controller = AudioRuntimeController(app, shared)

    controller.background_transcribe_question(
        flow_idx=2,
        session=_FakeSession(),
        base_dir=Path("/tmp"),
        base_name="base",
        candidate_label="CANDIDATE",
    )

    assert app._transcription_queue_state.pending_count() == 0
    assert app.state.flow_candidate_transcripts[2] == "candidate text"


def test_history_controller_refresh_renders_rows() -> None:
    row = {
        "history_id": "abc",
        "candidate_name": "Test",
        "interview_date": "2026-01-01",
        "interview_score": "99",
        "determination": "Hire",
    }
    app = SimpleNamespace()
    app.history_store = SimpleNamespace(load=lambda: [row])
    app.history_search_var = SimpleNamespace(get=lambda: "")
    app.history_sort_column = "interview_date"
    app.history_sort_desc = True
    shared = AppSharedState()
    controller = HistoryController(app, shared, grid_factory=_FakeGrid)
    controller.history_grid = _FakeGrid(None)
    controller.refresh_history_tree()

    assert shared.history_rows[0]["candidate_name"] == "Test"


def test_history_controller_regenerates_missing_notes_with_selected_mode(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    history_path = tmp_path / "interview_history.json"
    history_path.write_text("[]", encoding="utf-8")
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text("{}", encoding="utf-8")
    progress_path = job_path.with_suffix(".progress.json")
    app = SimpleNamespace(
        settings={"base_dir": str(tmp_path)},
        history_store=SimpleNamespace(path=history_path),
        _watch_deepseek_finalize_progress=lambda path: calls.append(Path(path)),
        _show_finalize_progress=lambda: calls.append("show-progress"),
    )
    monkeypatch.setattr(
        interview_runtime,
        "regenerate_interview_notes_job",
        lambda path, *, mode: calls.extend([Path(path), mode]) or progress_path,
    )
    controller = HistoryController(app, AppSharedState())
    controller._choose_notes_regeneration_mode = lambda _row: "document_only"

    controller._on_open_notes_link(
        {
            "history_id": "hist-1",
            "interview_notes_path": str(tmp_path / "missing.docx"),
            "deepseek_processing_status": "failed",
        }
    )

    assert calls == [job_path, "document_only", "show-progress", progress_path]


def test_history_controller_can_regenerate_existing_notes(tmp_path, monkeypatch) -> None:
    calls: list[object] = []
    notes_path = tmp_path / "notes.docx"
    notes_path.write_text("docx", encoding="utf-8")
    history_path = tmp_path / "interview_history.json"
    history_path.write_text("[]", encoding="utf-8")
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text("{}", encoding="utf-8")
    progress_path = job_path.with_suffix(".progress.json")
    app = SimpleNamespace(
        settings={"base_dir": str(tmp_path)},
        history_store=SimpleNamespace(path=history_path),
        _open_path_in_default_app=lambda path: calls.append(f"open:{path}"),
        _watch_deepseek_finalize_progress=lambda path: calls.append(Path(path)),
        _show_finalize_progress=lambda: calls.append("show-progress"),
    )
    monkeypatch.setattr(
        interview_runtime,
        "regenerate_interview_notes_job",
        lambda path, *, mode: calls.extend([Path(path), mode]) or progress_path,
    )
    controller = HistoryController(app, AppSharedState())
    controller._choose_existing_notes_action = lambda _row: "regenerate"
    controller._choose_notes_regeneration_mode = lambda _row: "full"

    controller._on_open_notes_link(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_notes_path": str(notes_path),
            "deepseek_processing_status": "complete",
        }
    )

    assert calls == [job_path, "full", "show-progress", progress_path]


def test_finalize_controller_poll_retries_first_failure_keeps_progress_open() -> None:
    calls: list[str] = []
    app = SimpleNamespace(
        _refresh_finalize_processing_state=lambda: None,
        after=lambda _ms, _fn: None,
        _close_finalize_progress=lambda: calls.append("close"),
        _show_finalize_progress=lambda: None,
        _start_finalize_worker=lambda attempt: calls.append(f"start:{attempt}"),
        recording_session=None,
    )
    shared = AppSharedState()
    controller = FinalizePipelineController(app, shared)
    q: queue.Queue[dict[str, object]] = queue.Queue()
    q.put({"ok": False, "error": RuntimeError("x"), "attempt": 1, "tb": "tb"})

    controller.poll_finalize_worker(q)

    assert calls == ["start:2"]


def test_finalize_interview_dispatches_worker_returns_to_start_and_keeps_progress_open() -> None:
    root = _RootWindow()
    calls: list[str] = []
    app = SimpleNamespace(
        validate_before_finalize=lambda: calls.append("validate"),
        _show_finalize_progress=lambda: calls.append("show"),
        _start_finalize_worker=lambda attempt: calls.append(f"start:{attempt}"),
        _close_finalize_progress=lambda: calls.append("close"),
        show_start_screen=lambda: calls.append("start-screen"),
        winfo_toplevel=lambda: root,
        current_finalize_correlation_id="",
    )
    controller = FinalizePipelineController(app, AppSharedState())

    controller.finalize_interview()

    assert calls == ["validate", "show", "start:1", "start-screen"]


def test_finalize_interview_ignores_second_click_while_worker_running() -> None:
    calls: list[str] = []
    app = SimpleNamespace(
        _finalize_worker_running=True,
        validate_before_finalize=lambda: calls.append("validate"),
        _show_finalize_progress=lambda: calls.append("show"),
        _start_finalize_worker=lambda attempt: calls.append(f"start:{attempt}"),
        show_start_screen=lambda: calls.append("start-screen"),
        winfo_toplevel=lambda: SimpleNamespace(lift=lambda: None, focus_force=lambda: None),
        current_finalize_correlation_id="in-flight",
    )
    controller = FinalizePipelineController(app, AppSharedState())

    controller.finalize_interview()

    assert calls == []


def test_finalize_interview_restores_main_window_focus_after_dispatch() -> None:
    root = _RootWindow()
    app = SimpleNamespace(
        validate_before_finalize=lambda: None,
        _show_finalize_progress=lambda: None,
        _start_finalize_worker=lambda attempt: attempt,
        _close_finalize_progress=lambda: None,
        show_start_screen=lambda: None,
        winfo_toplevel=lambda: root,
        current_finalize_correlation_id="",
    )
    controller = FinalizePipelineController(app, AppSharedState())

    controller.finalize_interview()

    assert root.lifted is True
    assert root.focused is True


def test_finalize_interview_warns_when_pending_transcriptions_exist() -> None:
    calls: list[str] = []
    warning_messages: list[str] = []
    banner_calls: list[str] = []
    app = SimpleNamespace(
        validate_before_finalize=lambda: calls.append("validate"),
        _show_finalize_partial_transcript_warning=lambda msg: (warning_messages.append(msg), banner_calls.append("banner")),
        _transcription_queue_state=SimpleNamespace(_pending_flow_transcriptions={1}),
        _show_finalize_progress=lambda: calls.append("show"),
        _start_finalize_worker=lambda attempt: calls.append(f"start:{attempt}"),
        _close_finalize_progress=lambda: calls.append("close"),
        show_start_screen=lambda: calls.append("start-screen"),
        winfo_toplevel=lambda: SimpleNamespace(lift=lambda: None, focus_force=lambda: None),
        current_finalize_correlation_id="",
    )
    controller = FinalizePipelineController(app, AppSharedState())

    controller.finalize_interview()

    assert calls == ["validate", "show", "start:1", "start-screen"]
    assert banner_calls == ["banner"]
    assert warning_messages == ["Transcription still processing in background; report may be partial."]


def test_finalize_success_surfaces_partial_transcript_warning(monkeypatch) -> None:
    monkeypatch.setattr("interview_app.finalize_pipeline.messagebox.showinfo", lambda *_args, **_kwargs: None)
    app = SimpleNamespace(
        last_finalize_result={},
        metrics_logger=SimpleNamespace(log_ux_completion=lambda **_kwargs: None, log_event=lambda *_args, **_kwargs: None),
        state=SimpleNamespace(track="lead"),
        _prompt_resume_if_outcome_requires_it=lambda _scoring: None,
        _open_path_in_default_app=lambda _path: None,
        _delete_interview_recording_artifacts=lambda: None,
        show_start_screen=lambda: None,
        _show_finalize_partial_transcript_warning=lambda _msg: setattr(app, "warning_seen", True),
        warning_seen=False,
    )
    controller = FinalizePipelineController(app, AppSharedState())

    controller._handle_finalize_success(
        {
            "result": {
                "scoring": {"outcome": "Hire", "weighted_total": 1, "max_weighted_total": 1, "percent_of_max": 100},
                "out_path": "/tmp/out.docx",
                "integration_path": "/tmp/out.json",
                "warnings": [],
                "transcript_completeness_status": "partial",
            }
        }
    )

    assert app.warning_seen is True
