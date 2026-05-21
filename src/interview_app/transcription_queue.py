from __future__ import annotations

import logging
import threading
from collections import deque
from time import monotonic
from uuid import uuid4
from typing import Deque

from .types import (
    TranscriptionQueuePayload,
    TranscriptionQueueSnapshot,
)

logger = logging.getLogger(__name__)


class TranscriptionQueueState:
    def __init__(self) -> None:
        self._pending_flow_transcriptions: set[int] = set()
        self._queued_flow_transcriptions: Deque[tuple[int, TranscriptionQueuePayload]] = deque()
        self._canceled_flow_transcriptions: set[int] = set()
        self._question_transcription_errors: dict[int, str] = {}
        self._job_started_at: dict[int, float] = {}
        self._job_payloads: dict[int, TranscriptionQueuePayload] = {}
        self._condition = threading.Condition()

    def enqueue(self, flow_idx: int, payload: TranscriptionQueuePayload) -> TranscriptionQueueSnapshot:
        with self._condition:
            if flow_idx in self._pending_flow_transcriptions:
                return self._snapshot_locked(flow_idx)
            payload = self._prepare_payload(flow_idx, payload)
            self._pending_flow_transcriptions.add(flow_idx)
            self._job_payloads[flow_idx] = payload
            self._canceled_flow_transcriptions.discard(flow_idx)
            self._question_transcription_errors.pop(flow_idx, None)
            self._queued_flow_transcriptions.append((flow_idx, payload))
            self._condition.notify_all()
            self._log_event(flow_idx, "transcription_job_queued", payload=payload, elapsed_ms=0, terminal_status="queued")
            return self._snapshot_locked(flow_idx)

    def mark_started(self, flow_idx: int) -> TranscriptionQueueSnapshot:
        with self._condition:
            self._job_started_at[flow_idx] = monotonic()
            self._log_event(flow_idx, "transcription_job_started", elapsed_ms=0, terminal_status="started")
            return self._snapshot_locked(flow_idx)

    def mark_completed(self, flow_idx: int, *, terminal_status: str = "completed") -> TranscriptionQueueSnapshot:
        with self._condition:
            elapsed_ms = self._elapsed_ms(flow_idx)
            self._pending_flow_transcriptions.discard(flow_idx)
            self._canceled_flow_transcriptions.discard(flow_idx)
            self._condition.notify_all()
            self._log_event(flow_idx, "transcription_job_completed", elapsed_ms=elapsed_ms, terminal_status=terminal_status)
            self._job_started_at.pop(flow_idx, None)
            self._job_payloads.pop(flow_idx, None)
            return self._snapshot_locked(flow_idx)

    def mark_failed(self, flow_idx: int, reason: str, *, terminal_status: str = "failed") -> TranscriptionQueueSnapshot:
        with self._condition:
            if flow_idx not in self._canceled_flow_transcriptions:
                cleaned_reason = str(reason or "").strip() or "Unknown transcription error"
                self._question_transcription_errors.setdefault(flow_idx, cleaned_reason)
            elapsed_ms = self._elapsed_ms(flow_idx)
            self._pending_flow_transcriptions.discard(flow_idx)
            self._canceled_flow_transcriptions.discard(flow_idx)
            self._condition.notify_all()
            self._log_event(flow_idx, "transcription_job_failed", elapsed_ms=elapsed_ms, terminal_status=terminal_status)
            self._job_started_at.pop(flow_idx, None)
            self._job_payloads.pop(flow_idx, None)
            return self._snapshot_locked(flow_idx)

    def mark_timed_out(self, flow_idx: int, reason: str = "transcription_timeout") -> TranscriptionQueueSnapshot:
        with self._condition:
            if flow_idx not in self._canceled_flow_transcriptions:
                cleaned_reason = str(reason or "").strip() or "transcription_timeout"
                self._question_transcription_errors.setdefault(flow_idx, cleaned_reason)
            elapsed_ms = self._elapsed_ms(flow_idx)
            self._pending_flow_transcriptions.discard(flow_idx)
            self._canceled_flow_transcriptions.discard(flow_idx)
            self._condition.notify_all()
            self._log_event(flow_idx, "transcription_job_timed_out", elapsed_ms=elapsed_ms, terminal_status="timed_out")
            self._job_started_at.pop(flow_idx, None)
            self._job_payloads.pop(flow_idx, None)
            return self._snapshot_locked(flow_idx)

    def cancel(self, flow_idx: int) -> TranscriptionQueueSnapshot:
        with self._condition:
            self._canceled_flow_transcriptions.add(flow_idx)
            self._queued_flow_transcriptions = deque(
                item for item in self._queued_flow_transcriptions if item[0] != flow_idx
            )
            self._pending_flow_transcriptions.discard(flow_idx)
            self._question_transcription_errors.pop(flow_idx, None)
            self._condition.notify_all()
            self._job_started_at.pop(flow_idx, None)
            self._job_payloads.pop(flow_idx, None)
            return self._snapshot_locked(flow_idx)

    def next_payload(self) -> tuple[int, TranscriptionQueuePayload] | None:
        with self._condition:
            while self._queued_flow_transcriptions:
                flow_idx, payload = self._queued_flow_transcriptions.popleft()
                if flow_idx in self._canceled_flow_transcriptions:
                    self._pending_flow_transcriptions.discard(flow_idx)
                    continue
                return flow_idx, payload
            return None

    def wait_for_pending(self) -> None:
        with self._condition:
            while self._pending_flow_transcriptions:
                self._condition.wait(timeout=0.1)

    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending_flow_transcriptions)

    def is_pending(self, flow_idx: int) -> bool:
        with self._condition:
            return flow_idx in self._pending_flow_transcriptions

    def clear(self) -> None:
        with self._condition:
            self._pending_flow_transcriptions.clear()
            self._queued_flow_transcriptions.clear()
            self._canceled_flow_transcriptions.clear()
            self._question_transcription_errors.clear()
            self._job_started_at.clear()
            self._job_payloads.clear()
            self._condition.notify_all()

    def is_canceled(self, flow_idx: int) -> bool:
        with self._condition:
            return flow_idx in self._canceled_flow_transcriptions

    def clear_error(self, flow_idx: int) -> None:
        with self._condition:
            self._question_transcription_errors.pop(flow_idx, None)

    def error_reasons(self) -> dict[int, str]:
        with self._condition:
            return dict(self._question_transcription_errors)

    def _snapshot_locked(self, flow_idx: int) -> TranscriptionQueueSnapshot:
        return {
            "flow_index": flow_idx,
            "is_pending": flow_idx in self._pending_flow_transcriptions,
            "is_canceled": flow_idx in self._canceled_flow_transcriptions,
            "queued_count": len(self._queued_flow_transcriptions),
            "pending_count": len(self._pending_flow_transcriptions),
            "error_reason": self._question_transcription_errors.get(flow_idx),
        }

    @staticmethod
    def _prepare_payload(flow_idx: int, payload: TranscriptionQueuePayload) -> TranscriptionQueuePayload:
        prepared = dict(payload)
        prepared.setdefault("flow_idx", flow_idx)
        prepared.setdefault("job_uuid", uuid4().hex)
        prepared.setdefault("retry_count", 0)
        prepared.setdefault("interview_session_id", "")
        prepared.setdefault("finalize_correlation_id", "")
        return prepared

    def _elapsed_ms(self, flow_idx: int) -> int:
        started_at = self._job_started_at.get(flow_idx)
        if started_at is None:
            return 0
        return max(0, int((monotonic() - started_at) * 1000))

    def _log_event(
        self,
        flow_idx: int,
        event_name: str,
        *,
        elapsed_ms: int,
        terminal_status: str,
        payload: TranscriptionQueuePayload | None = None,
    ) -> None:
        source_payload = payload if payload is not None else self._job_payloads.get(flow_idx, {})
        logger.info(
            event_name,
            extra={
                "flow_idx": flow_idx,
                "job_uuid": str(source_payload.get("job_uuid") or ""),
                "retry_count": int(source_payload.get("retry_count") or 0),
                "interview_session_id": str(source_payload.get("interview_session_id") or ""),
                "finalize_correlation_id": str(source_payload.get("finalize_correlation_id") or ""),
                "elapsed_ms": int(elapsed_ms),
                "terminal_status": terminal_status,
            },
        )
