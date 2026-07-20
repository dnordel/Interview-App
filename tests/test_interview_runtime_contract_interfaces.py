from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from interview_runtime import AudioRuntimeController, HistoryActionsService, TranscriptionQueueState, _value_or_default


def test_security_value_or_default_enforces_stripped_defaults() -> None:
    assert _value_or_default({}, runtime_key="r", preferred_key="p", default_value="safe") == "safe"
    assert _value_or_default({"r": "  gpu  "}, runtime_key="r", preferred_key="p", default_value="safe") == "gpu"


def test_security_transcription_cancel_is_side_effect_safe() -> None:
    app = SimpleNamespace()
    import threading

    app._audio_state_lock = threading.Lock()
    queue_state = TranscriptionQueueState()
    app._transcription_queue_state = queue_state
    app._append_recording_attempt = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not append"))
    app._persist_interview_session_snapshot = lambda _idx: None
    app._delete_file_if_exists = lambda _path: None
    app.state = SimpleNamespace(flow_candidate_transcripts={})
    queue_state.enqueue(
        7,
        {
            "flow_idx": 7,
            "session": object(),
            "base_dir": Path("/tmp"),
            "base_name": "b",
            "candidate_label": "candidate",
        },
    )
    queue_state.cancel(7)

    controller = AudioRuntimeController(app, SimpleNamespace())
    controller.background_transcribe_question(
        flow_idx=7,
        session=SimpleNamespace(stop_and_transcribe=lambda **_kwargs: None),
        base_dir=Path("/tmp"),
        base_name="b",
        candidate_label="candidate",
    )

    assert app.state.flow_candidate_transcripts == {}


def test_security_offer_transition_requires_known_status() -> None:
    service = HistoryActionsService(SimpleNamespace())
    assert service.offer_transition("INVALID") is None


def test_history_delete_uses_injected_dialogs() -> None:
    calls: list[str] = []
    store = SimpleNamespace(
        build_row_key=lambda _row: "row-1",
        delete_row=lambda key: calls.append(f"delete:{key}") or True,
    )
    app = SimpleNamespace(history_store=store, _refresh_history_tree=lambda: calls.append("refresh"))
    dialogs = SimpleNamespace(askyesno=lambda *_args: True)

    HistoryActionsService(app, dialogs=dialogs).handle_delete_for_row({"candidate_name": "Ada"})

    assert calls == ["delete:row-1", "refresh"]


def test_history_offer_notification_uses_injected_factory() -> None:
    events: list[tuple[str, str]] = []
    service = SimpleNamespace(emit_event=lambda event, _payload, key: events.append((event, key)))
    store = SimpleNamespace(
        build_row_key=lambda _row: "row-2",
        update_offer_state=lambda *_args: True,
    )
    app = SimpleNamespace(history_store=store, _refresh_history_tree=lambda: None)
    factory = lambda **_kwargs: service

    updated = HistoryActionsService(app, notification_service_factory=factory).update_history_offer_status(
        {"candidate_name": "Ada"},
        "approved",
    )

    assert updated is True
    assert events == [("offer.approved", "row-2:offer.approved")]


def test_security_queue_cancel_is_idempotent() -> None:
    queue_state = TranscriptionQueueState()
    queue_state.enqueue(3, {"flow_idx": 3, "session": object(), "base_dir": Path("/tmp"), "base_name": "x", "candidate_label": "c"})
    first = queue_state.cancel(3)
    second = queue_state.cancel(3)

    assert first["is_canceled"] is True
    assert second["is_canceled"] is True
