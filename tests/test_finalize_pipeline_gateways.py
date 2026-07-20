from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from candidate_report import CandidateReportRepository
from data_store import InterviewHistoryStore
from interview_runtime import FinalizeGateways, FinalizePipelineController, PENDING_TRANSCRIPTION_WARNING


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
    app._safe_attr = lambda _name: None
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


def _scoring_engine(result: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(evaluate=lambda *_args, **_kwargs: result)


def test_finalize_pipeline_accumulates_warnings() -> None:
    app = _build_app(flow_recordings={})
    controller = FinalizePipelineController(
        app,
        shared_state=SimpleNamespace(),
        gateways=_GatewayStub(),
        scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 92}),
    )

    result = controller.run_finalize_pipeline()

    assert result["warnings"] == [
        "Transcription queue was delayed.",
        "Recording/transcription did not complete. Interview was finalized without transcript text.",
    ]


def test_finalize_pipeline_retry_safe_does_not_resend_after_first_failure() -> None:
    send_calls = {"count": 0}
    append_calls = {"count": 0}

    def _send_packet(_packet, _endpoint):
        send_calls["count"] += 1
        return {"status": "ok"}

    def _append_log(_base_dir, _event, *, candidate_name):
        append_calls["count"] += 1
        if append_calls["count"] == 1:
            raise RuntimeError("disk full")
        return Path("/tmp/comm-log.json")

    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    gateways = FinalizeGateways(
        exporter_factory=lambda *_args, **_kwargs: SimpleNamespace(export=lambda *_a, **_k: "/tmp/final-notes.docx"),
        integration_payload_builder=lambda *_args, **_kwargs: {"candidate": "Ada"},
        integration_payload_serializer=lambda *_args, **_kwargs: Path("/tmp/integration.json"),
        director_packet_builder=lambda **_kwargs: {"documents": {"final_report_path": "/tmp/final-notes.docx"}},
        director_packet_sender=_send_packet,
        communication_log_appender=_append_log,
    )
    controller = FinalizePipelineController(
        app,
        shared_state=SimpleNamespace(),
        gateways=gateways,
        scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 99}),
    )

    with pytest.raises(RuntimeError, match="disk full"):
        controller.run_finalize_pipeline()

    result = controller.run_finalize_pipeline()

    assert send_calls["count"] == 1
    assert result["director_packet"]["documents"]["final_report_path"] == "/tmp/final-notes.docx"


def test_finalize_pipeline_result_payload_invariants() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    gateway = _GatewayStub()
    controller = FinalizePipelineController(
        app,
        shared_state=SimpleNamespace(),
        gateways=gateway,
        scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 88}),
    )

    result = controller.run_finalize_pipeline()
    context = gateway.contexts[0]

    assert result["out_path"] == "/tmp/final-notes.docx"
    assert result["integration_path"] == "/tmp/integration.json"
    assert result["transcript_path"] == ""
    assert context.payload["flow_transcript"][0]["candidate_transcript"] == "hello"
    assert context.payload["audio_recording"] == context.recording_metadata
    assert context.payload["transcript_metadata"] == {
        "transcript_complete": True,
        "transcript_completeness_status": "complete",
        "remaining_question_indices": [],
    }
    assert app.state.referral_packet["transcript_path"] == ""




def test_finalize_pipeline_partial_result_has_remaining_indices() -> None:

    class _PendingQueueState:
        def __init__(self, pending: set[int]) -> None:
            self._pending_flow_transcriptions = pending

    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = _PendingQueueState({2})
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub(), scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 90}))

    result = controller.run_finalize_pipeline()

    assert result["transcript_complete"] is False
    assert result["remaining_question_indices"]
    assert result["remaining_question_indices"] == [3]


def test_finalize_result_includes_partial_transcript_fields() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={3})
    shared_state = SimpleNamespace(transcription=SimpleNamespace(pending_flow_transcriptions={1}))
    controller = FinalizePipelineController(app, shared_state=shared_state, gateways=_GatewayStub(), scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 91}))

    result = controller.run_finalize_pipeline()

    assert result["transcript_complete"] is False
    assert result["remaining_question_indices"] == [2, 4]
    assert result["remaining_question_indices"]


def test_finalize_pipeline_marks_partial_transcript_metadata() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={0, 2})
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub(), scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 88}))

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


def test_handle_finalize_success_warns_on_partial_metadata() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app.metrics_logger = SimpleNamespace(log_ux_completion=lambda **_kwargs: None, log_event=lambda *_args, **_kwargs: None)
    app._prompt_resume_if_outcome_requires_it = lambda _scoring: None
    app._open_path_in_default_app = lambda _path: None
    app._delete_interview_recording_artifacts = lambda: None
    app.show_start_screen = lambda: None
    app.warning_message = ""
    app._show_finalize_partial_transcript_warning = lambda message: setattr(app, "warning_message", message)
    controller = FinalizePipelineController(
        app,
        shared_state=SimpleNamespace(),
        gateways=_GatewayStub(),
        dialogs=SimpleNamespace(showinfo=lambda *_args, **_kwargs: None),
    )

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


def test_finalize_pipeline_includes_timeout_failure_warning() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._collect_transcription_health_warnings = lambda: ["Audio transcription failed for Q2. Details:\nQ2: transcription_timeout"]
    controller = FinalizePipelineController(app, shared_state=SimpleNamespace(), gateways=_GatewayStub(), scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 81}))

    result = controller.run_finalize_pipeline()
    context = controller.gateways.contexts[0]

    assert "transcription_timeout" in result["warnings"][0]
    assert context.payload["transcript_metadata"]["transcript_complete"] is True


def test_finalize_pipeline_uses_shared_state_pending_indices() -> None:
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app._transcription_queue_state = SimpleNamespace(_pending_flow_transcriptions={"1", "bad"})
    shared_state = SimpleNamespace(transcription=SimpleNamespace(pending_flow_transcriptions={0, "2"}))
    controller = FinalizePipelineController(app, shared_state=shared_state, gateways=_GatewayStub(), scoring_engine=_scoring_engine({"outcome": "Hire", "percent_of_max": 87}))

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
        transcript_path="",
        recording_metadata=[{"flow_index": 1}],
        interview_notes_document_path="/tmp/final-notes.docx",
    )

    FinalizeGateways().persist_finalize_history(app, context, "/tmp/final-notes.docx")

    assert len(app.history_store.rows) == 1
    history_entry = app.history_store.rows[0]
    assert history_entry["interview_notes_path"] == "/tmp/final-notes.docx"
    assert history_entry["transcript_path"] == ""
    assert history_entry["offer_status"] == "not_generated"


def test_finalize_gateway_atomically_creates_structured_candidate_report(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "interview_history.sqlite3"
    app = _build_app(flow_recordings={1: {"base_name": "session1"}})
    app.history_store = InterviewHistoryStore(history_path)
    context = SimpleNamespace(
        payload={
            "candidate": {
                "interview_date": "2026-07-13",
                "name": "Ada Lovelace",
                "school": "Hawthorne",
                "track": "lead",
                "qualification": {"ece_units": 24},
            },
            "flow_transcript": [
                {
                    "flow_index": 0,
                    "id": "Q1",
                    "type": "trait",
                    "title": "Reliability",
                    "prompt": "Tell me about a commitment.",
                    "candidate_transcript": "I kept my commitment.",
                    "evaluator_notes": "Specific example.",
                    "rating": 4,
                    "weight": 2,
                }
            ],
        },
        scoring={"earned": 8, "max": 10, "percent_of_max": 80, "outcome": "Hire"},
        transcript_path="",
        recording_metadata=[{"flow_index": 0}],
        interview_notes_document_path=str(tmp_path / "Ada.docx"),
    )

    history_id = FinalizeGateways().persist_finalize_history(app, context, str(tmp_path / "Ada.docx"))

    history_rows = app.history_store.load()
    report = CandidateReportRepository(history_path).load_visible_version(history_id, role="admin")
    assert len(history_rows) == 1
    assert history_rows[0]["history_id"] == history_id
    assert report.snapshot["candidate"]["candidate_name"] == "Ada Lovelace"
    assert report.snapshot["scoring"]["outcome"] == "Hire"
