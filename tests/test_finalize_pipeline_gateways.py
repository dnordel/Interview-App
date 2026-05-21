from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from interview_app.finalize_gateways import FinalizeGateways
from interview_app.finalize_pipeline import FinalizePipelineController, PENDING_TRANSCRIPTION_WARNING


class _GatewayStub:
    def __init__(self) -> None:
        self.contexts = []

    def export_report(self, _app, context):
        self.contexts.append(context)
        return "/tmp/final-notes.docx"

    def export_integration(self, _app, _context):
        return Path("/tmp/integration.json")

    def send_referral(self, _app, _context, _out_path, _integration_path):
        return {"documents": {}}, None

    def persist_finalize_history(self, _app, _context, _out_path):
        return None


class _State:
    def __init__(self, flow_recordings: dict[int, dict] | None = None) -> None:
        self.track = "lead"
        self.trait_inputs = {"trait": {"raw_score": 4}}
        self.flow_recordings = flow_recordings or {}
        self.referral_packet = {"transcript_path": "", "interview_notes_path": ""}
        self.candidate_name = "Ada Lovelace"

    def to_dict(self):
        return {"candidate": {"name": self.candidate_name, "track": self.track}}


def _build_app(flow_recordings: dict[int, dict] | None = None):
    app = SimpleNamespace()
    app.state = _State(flow_recordings=flow_recordings)
    app.settings = {
        "base_dir": "/tmp",
        "send_director_referral_on_finalize": True,
        "director_referral_endpoint": "https://example.test/referrals",
    }
    app._rubric_with_question_overrides = lambda: {"traits": []}
    app._safe_attr = lambda name: Path("/tmp/transcript.docx") if name == "live_transcript_docx" else None
    app._finalize_current_question_audio_and_doc = lambda _idx: None
    app._collect_transcription_health_warnings = lambda: ["Transcription queue was delayed."]
    app._hydrate_state_from_session_store = lambda: None
    app._serialize_flow_audio_recordings = lambda: [{"flow_index": 1, "candidate_transcript": "hello"}]
    app._ordered_custom_answers = lambda: [{"question": "Q", "answer": "A"}]
    app._build_flow_transcript = lambda: [{"flow_index": 1, "prompt": "Why?"}]
    app._apply_candidate_transcripts_to_flow = lambda flow_tx: flow_tx[0].update({"candidate_transcript": "hello"})
    app._rewrite_live_transcript_docx_from_flow = lambda _flow_tx: None
    app._persist_finalize_history = lambda _scoring, _out_path: None
    app.history_store = SimpleNamespace(rows=[], append=lambda row: app.history_store.rows.append(row))
    return app


def test_finalize_pipeline_accumulates_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 92}),
    )
    app = _build_app(flow_recordings={})
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()

    assert result["warnings"] == [
        "Transcription queue was delayed.",
        "Recording/transcription did not complete. Interview was finalized without transcript text.",
    ]


def test_finalize_pipeline_retry_safe_does_not_resend_after_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 99}),
    )
    send_calls = {"count": 0}
    append_calls = {"count": 0}

    def _send_packet(_packet, _endpoint):
        send_calls["count"] += 1
        return {"status": "ok"}

    def _append_log(_base_dir, _event):
        append_calls["count"] += 1
        if append_calls["count"] == 1:
            raise RuntimeError("disk full")
        return Path("/tmp/comm-log.json")

    monkeypatch.setattr("interview_app.finalize_gateways.send_director_packet", _send_packet)
    monkeypatch.setattr("interview_app.finalize_gateways.append_communication_log", _append_log)
    monkeypatch.setattr(
        "interview_app.finalize_gateways.build_director_packet",
        lambda **_kwargs: {"documents": {"final_report_path": "/tmp/final-notes.docx"}},
    )
    monkeypatch.setattr(
        "interview_app.finalize_gateways.build_integration_payload",
        lambda *_args, **_kwargs: {"candidate": "Ada"},
    )
    monkeypatch.setattr(
        "interview_app.finalize_gateways.serialize_integration_payload",
        lambda *_args, **_kwargs: Path("/tmp/integration.json"),
    )
    monkeypatch.setattr(
        "interview_app.finalize_gateways.DocxExporter",
        lambda *_args, **_kwargs: SimpleNamespace(export=lambda *_a, **_k: "/tmp/final-notes.docx"),
    )

    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    gateways = FinalizeGateways()
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=gateways)

    with pytest.raises(RuntimeError, match="disk full"):
        controller.run_finalize_pipeline()

    result = controller.run_finalize_pipeline()

    assert send_calls["count"] == 1
    assert result["director_packet"]["documents"]["final_report_path"] == "/tmp/final-notes.docx"


def test_background_summary_retry_skips_state_mutation_on_correlation_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app.current_finalize_correlation_id = "new-finalize"
    app.state.referral_packet["interview_notes_path"] = "/tmp/current-session.docx"
    gateways = FinalizeGateways()
    context = SimpleNamespace(payload={"candidate": {}}, scoring={})
    monkeypatch.setattr(
        "interview_app.finalize_gateways.DocxExporter",
        lambda *_args, **_kwargs: SimpleNamespace(export=lambda *_a, **_k: "/tmp/old-session-updated.docx"),
    )

    gateways._schedule_summary_retry(app, context, "old-finalize")

    assert app.state.referral_packet["interview_notes_path"] == "/tmp/current-session.docx"


def test_finalize_pipeline_result_payload_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 88}),
    )
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    gateway = _GatewayStub()
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=gateway)

    result = controller.run_finalize_pipeline()
    context = gateway.contexts[0]

    assert result["out_path"] == "/tmp/final-notes.docx"
    assert result["integration_path"] == "/tmp/integration.json"
    assert result["transcript_path"] == "/tmp/transcript.docx"
    assert context.payload["flow_transcript"][0]["candidate_transcript"] == "hello"
    assert context.payload["audio_recording"] == context.recording_metadata
    assert context.payload["transcript_metadata"] == {
        "transcript_complete": True,
        "transcript_completeness_status": "complete",
        "remaining_question_indices": [],
    }
    assert app.state.referral_packet["transcript_path"] == "/tmp/transcript.docx"




def test_finalize_pipeline_partial_result_has_remaining_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 90}),
    )

    class _PendingQueueState:
        def __init__(self, pending: set[int]) -> None:
            self._pending_flow_transcriptions = pending

    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = _PendingQueueState({2})
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()

    assert result["transcript_complete"] is False
    assert result["remaining_question_indices"]
    assert result["remaining_question_indices"] == [3]


def test_finalize_result_includes_partial_transcript_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 91}),
    )
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={3})
    shared_state = SimpleNamespace(transcription=SimpleNamespace(pending_flow_transcriptions={1}))
    controller = FinalizePipelineController(app, shared_state=shared_state, gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()

    assert result["transcript_complete"] is False
    assert result["remaining_question_indices"] == [2, 4]
    assert result["remaining_question_indices"]


def test_finalize_pipeline_marks_partial_transcript_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 88}),
    )
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={0, 2})
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()
    context = controller.gateways.contexts[0]

    assert result["transcript_complete"] is False
    assert result["transcript_completeness_status"] == "partial"
    assert result["remaining_question_indices"] == [1, 3]
    assert context.payload["transcript_metadata"]["remaining_question_indices"] == [1, 3]
    assert "Transcription queue was delayed." in result["warnings"]
    assert "Transcription still processing in background; report may be partial." in result["warnings"]


def test_warn_if_finalize_starts_with_pending_transcriptions() -> None:
    app = SimpleNamespace()
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={1})
    app._show_finalize_partial_transcript_warning = lambda message: setattr(app, "warning_message", message)
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    controller._warn_if_finalize_starts_with_pending_transcriptions()

    assert app.warning_message == PENDING_TRANSCRIPTION_WARNING


def test_dispatch_finalize_work_does_not_route_to_start_screen_before_snapshot() -> None:
    app = SimpleNamespace()
    app.validate_before_finalize = lambda: None
    app._show_finalize_partial_transcript_warning = lambda _message: None
    app._show_finalize_progress = lambda: None
    app._close_finalize_progress = lambda: None
    app._start_finalize_worker = lambda **_kwargs: None
    app.winfo_toplevel = lambda: app
    app.lift = lambda: None
    app.focus_force = lambda: None
    app.show_start_screen_called = False
    app.show_start_screen = lambda: setattr(app, "show_start_screen_called", True)
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    controller._dispatch_finalize_work()

    assert app.show_start_screen_called is False


def test_poll_finalize_worker_routes_after_snapshot_capture() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._finalize_snapshot_captured = True
    app._finalize_start_screen_routed = False
    app.show_start_screen_called = False
    app.show_start_screen = lambda: setattr(app, "show_start_screen_called", True)
    app._refresh_finalize_processing_state = lambda: None
    app.after = lambda _delay, _fn: None
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    controller.poll_finalize_worker(queue.Queue(maxsize=1))

    assert app.show_start_screen_called is True


def test_handle_finalize_success_warns_on_partial_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("interview_app.finalize_pipeline.messagebox.showinfo", lambda *_args, **_kwargs: None)
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app.metrics_logger = SimpleNamespace(log_ux_completion=lambda **_kwargs: None, log_event=lambda *_args, **_kwargs: None)
    app._prompt_resume_if_outcome_requires_it = lambda _scoring: None
    app._open_path_in_default_app = lambda _path: None
    app._delete_interview_recording_artifacts = lambda: None
    app.show_start_screen = lambda: None
    app.warning_message = ""
    app._show_finalize_partial_transcript_warning = lambda message: setattr(app, "warning_message", message)
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    controller._handle_finalize_success(
        {
            "result": {
                "scoring": {
                    "outcome": "Hire",
                    "weighted_total": 20,
                    "max_weighted_total": 25,
                    "percent_of_max": 80,
                    "percent_of_max_label": "80%",
                },
                "warnings": [],
                "out_path": "/tmp/report.docx",
                "integration_path": "/tmp/integration.json",
                "transcript_path": "",
                "transcript_completeness_status": "partial",
            }
        }
    )

    assert app.warning_message == PENDING_TRANSCRIPTION_WARNING


def test_finalize_pipeline_includes_timeout_failure_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 81}),
    )
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._collect_transcription_health_warnings = lambda: ["Audio transcription failed for Q2. Details:\nQ2: transcription_timeout"]
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()
    context = controller.gateways.contexts[0]

    assert "transcription_timeout" in result["warnings"][0]
    assert context.payload["transcript_metadata"]["transcript_complete"] is True


def test_finalize_pipeline_uses_shared_state_pending_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "interview_app.finalize_pipeline.ScoringEngine.evaluate",
        staticmethod(lambda *_args, **_kwargs: {"outcome": "Hire", "percent_of_max": 87}),
    )
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={"1", "bad"})
    shared_state = SimpleNamespace(transcription=SimpleNamespace(pending_flow_transcriptions={0, "2"}))
    controller = FinalizePipelineController(app, shared_state=shared_state, gateways=_GatewayStub())

    result = controller.run_finalize_pipeline()

    assert result["transcript_complete"] is False
    assert result["remaining_question_indices"] == [1, 2, 3]

def test_finalize_gateway_persists_history_entry() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app.history_store = SimpleNamespace(rows=[], append=lambda row: app.history_store.rows.append(row))
    context = SimpleNamespace(
        payload={
            "candidate": {
                "interview_date": "2026-01-02",
                "name": "Ada Lovelace",
                "school": "PS 10",
                "track": "lead",
            }
        },
        scoring={"percent_of_max": 98, "outcome": "Hire"},
        transcript_path="/tmp/transcript.docx",
        recording_metadata=[{"flow_index": 1}],
    )

    FinalizeGateways().persist_finalize_history(app, context, "/tmp/final-notes.docx")

    assert len(app.history_store.rows) == 1
    history_entry = app.history_store.rows[0]
    assert history_entry["interview_notes_path"] == "/tmp/final-notes.docx"
    assert history_entry["offer_status"] == "not_generated"
