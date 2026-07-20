from __future__ import annotations

import threading
import time
from pathlib import Path

from interview_runtime import (
    BoundedTranscriptionExecutor,
    TranscriptionJobStatusEvent,
    recommended_max_workers,
    resolve_transcription_max_workers,
)
from interview_runtime import TranscriptionQueueState


def _payload(flow_idx: int) -> dict:
    return {
        "flow_idx": flow_idx,
        "session": object(),
        "base_dir": Path("/tmp"),
        "base_name": f"q-{flow_idx}",
        "candidate_label": "CANDIDATE",
    }


def test_recommended_max_workers_bounds() -> None:
    assert recommended_max_workers(1) == 1
    assert recommended_max_workers(2) == 1
    assert recommended_max_workers(8) == 4
    assert resolve_transcription_max_workers({"transcription_max_workers": "5"}) == 5


def test_executor_runs_faster_than_serial_with_simulated_delay() -> None:
    queue_state = TranscriptionQueueState()
    completed: list[int] = []
    lock = threading.Lock()

    def worker_fn(**payload) -> None:
        time.sleep(0.2)
        with lock:
            completed.append(int(payload["flow_idx"]))

    executor = BoundedTranscriptionExecutor(queue_state=queue_state, worker_fn=worker_fn, max_workers=2)

    started = time.perf_counter()
    for flow_idx in range(4):
        executor.submit(flow_idx, _payload(flow_idx))
    queue_state.wait_for_pending()
    elapsed = time.perf_counter() - started
    executor.shutdown(wait=True)

    assert len(completed) == 4
    assert elapsed < 0.65


def test_ordered_merge_is_stable_regardless_of_completion_order() -> None:
    queue_state = TranscriptionQueueState()
    completion_order: list[int] = []
    lock = threading.Lock()
    done = threading.Event()

    delay_by_flow = {0: 0.25, 1: 0.1, 2: 0.2}
    result_by_flow: dict[int, str] = {}

    def worker_fn(**payload) -> None:
        flow_idx = int(payload["flow_idx"])
        time.sleep(delay_by_flow[flow_idx])
        with lock:
            completion_order.append(flow_idx)
            result_by_flow[flow_idx] = f"segment-{flow_idx}"
            if len(result_by_flow) == 3:
                done.set()

    executor = BoundedTranscriptionExecutor(queue_state=queue_state, worker_fn=worker_fn, max_workers=3)

    for flow_idx in (0, 1, 2):
        executor.submit(flow_idx, _payload(flow_idx))
    assert done.wait(timeout=2)
    queue_state.wait_for_pending()
    executor.shutdown(wait=True)

    merged = [result_by_flow[idx] for idx in sorted(result_by_flow.keys())]
    assert completion_order != [0, 1, 2]
    assert merged == ["segment-0", "segment-1", "segment-2"]


def test_queue_state_accounting_is_thread_safe() -> None:
    queue_state = TranscriptionQueueState()
    events: list[TranscriptionJobStatusEvent] = []
    lock = threading.Lock()

    def on_status_change(event: TranscriptionJobStatusEvent) -> None:
        with lock:
            events.append(event)

    def worker_fn(**_payload) -> None:
        time.sleep(0.05)

    executor = BoundedTranscriptionExecutor(
        queue_state=queue_state,
        worker_fn=worker_fn,
        max_workers=3,
        on_status_change=on_status_change,
    )

    for flow_idx in range(6):
        executor.submit(flow_idx, _payload(flow_idx))
    queue_state.wait_for_pending()
    executor.shutdown(wait=True)

    assert queue_state.pending_count() == 0
    statuses = [event.status for event in events]
    assert statuses.count("queued") == 6
    assert statuses.count("running") == 6
    assert statuses.count("completed") == 6
