from __future__ import annotations

import threading
from pathlib import Path

from interview_runtime import TranscriptionQueueState


def _payload(flow_idx: int) -> dict[str, object]:
    return {
        "flow_idx": flow_idx,
        "session": object(),
        "base_dir": Path("."),
        "base_name": "take1",
        "candidate_label": "CANDIDATE",
    }


def test_enqueue_then_cancel_removes_pending_and_queue() -> None:
    queue = TranscriptionQueueState()

    enqueue_snapshot = queue.enqueue(7, _payload(7))
    cancel_snapshot = queue.cancel(7)

    assert enqueue_snapshot["is_pending"] is True
    assert cancel_snapshot["is_pending"] is False
    assert cancel_snapshot["queued_count"] == 0
    assert queue.pending_count() == 0
    assert queue.next_payload() is None


def test_cancel_and_completion_race_is_safe() -> None:
    queue = TranscriptionQueueState()
    queue.enqueue(3, _payload(3))

    barrier = threading.Barrier(2)

    def _cancel() -> None:
        barrier.wait()
        queue.cancel(3)

    def _complete() -> None:
        barrier.wait()
        queue.mark_completed(3)

    t1 = threading.Thread(target=_cancel)
    t2 = threading.Thread(target=_complete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final = queue.mark_started(3)
    assert final["is_pending"] is False
    assert final["queued_count"] == 0
    assert final["error_reason"] is None


def test_mark_failed_does_not_overwrite_first_error() -> None:
    queue = TranscriptionQueueState()

    queue.enqueue(11, _payload(11))
    queue.mark_failed(11, "first error")
    queue.mark_failed(11, "second error")

    errors = queue.error_reasons()
    assert errors[11] == "first error"


def test_cancel_prevents_future_error_capture() -> None:
    queue = TranscriptionQueueState()

    queue.enqueue(9, _payload(9))
    queue.cancel(9)
    queue.mark_failed(9, "should not persist")

    assert 9 not in queue.error_reasons()
