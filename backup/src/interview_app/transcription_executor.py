from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .transcription_queue import TranscriptionQueueState
from .types import TranscriptionQueuePayload, TranscriptionQueueSnapshot


@dataclass(frozen=True, slots=True)
class TranscriptionJobStatusEvent:
    flow_idx: int
    status: str
    snapshot: TranscriptionQueueSnapshot


def recommended_max_workers(cpu_count: int | None = None) -> int:
    detected = cpu_count if cpu_count is not None else os.cpu_count()
    if detected is None:
        return 2
    half_cores = max(1, detected // 2)
    return max(1, min(4, half_cores))


def resolve_transcription_max_workers(settings: dict[str, Any]) -> int:
    raw = settings.get("transcription_max_workers", 0)
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return min(configured, 8)
    return recommended_max_workers()


def resolve_transcription_job_timeout_seconds(settings: dict[str, Any]) -> float:
    raw = settings.get("transcription_job_timeout_seconds", 180)
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        configured = 180.0
    if configured < 5:
        return 5.0
    return min(configured, 3600.0)


class BoundedTranscriptionExecutor:
    def __init__(
        self,
        *,
        queue_state: TranscriptionQueueState,
        worker_fn: Callable[..., None],
        max_workers: int,
        on_status_change: Callable[[TranscriptionJobStatusEvent], None] | None = None,
    ) -> None:
        self._queue_state = queue_state
        self._worker_fn = worker_fn
        self._max_workers = max(1, int(max_workers))
        self._on_status_change = on_status_change
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[None]] = set()

    def submit(self, flow_idx: int, payload: TranscriptionQueuePayload) -> None:
        snapshot = self._queue_state.enqueue(flow_idx, payload)
        self._emit(flow_idx, "queued", snapshot)
        future = self._executor_instance().submit(self._run_job, flow_idx, payload)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def shutdown(self, *, wait: bool) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            self._futures.clear()
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=not wait)

    def _executor_instance(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="transcription")
            return self._executor

    def _run_job(self, flow_idx: int, payload: TranscriptionQueuePayload) -> None:
        if self._queue_state.is_canceled(flow_idx):
            return
        started = self._queue_state.mark_started(flow_idx)
        self._emit(flow_idx, "running", started)
        try:
            self._worker_fn(**self._runtime_payload(payload))
        except TimeoutError:
            timed_out = self._queue_state.mark_timed_out(flow_idx)
            self._emit(flow_idx, "failed", timed_out)
        except Exception as exc:
            failed = self._queue_state.mark_failed(flow_idx, str(exc))
            self._emit(flow_idx, "failed", failed)
        else:
            completed = self._queue_state.mark_completed(flow_idx)
            self._emit(flow_idx, "completed", completed)


    def _runtime_payload(self, payload: TranscriptionQueuePayload) -> dict[str, Any]:
        return {
            "flow_idx": int(payload["flow_idx"]),
            "session": payload["session"],
            "base_dir": payload["base_dir"],
            "base_name": str(payload["base_name"]),
            "candidate_label": str(payload["candidate_label"]),
            "job_timeout_seconds": float(payload.get("job_timeout_seconds", 180.0)),
        }
    def _emit(self, flow_idx: int, status: str, snapshot: TranscriptionQueueSnapshot) -> None:
        callback = self._on_status_change
        if callback is None:
            return
        callback(TranscriptionJobStatusEvent(flow_idx=flow_idx, status=status, snapshot=snapshot))

    def _discard_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)
