from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict


@dataclass(slots=True)
class InterviewSessionContext:
    interview_session_id: str = ""
    recording_base_name: str = ""
    recording_flow_idx: int | None = None
    recording_started_monotonic: float | None = None
    recording_candidate_label: str = "CANDIDATE"
    live_transcript_docx: Path | None = None


class FlowItem(TypedDict, total=False):
    id: str
    flow_index: int
    question_type: str
    trait_id: str
    trait_name: str
    prompt: str
    raw: dict[str, Any]


class RecordingTranscriptionPayload(TypedDict):
    flow_index: int
    base_name: str
    output_dir: str
    mic_wav: str
    sys_wav: str
    transcript_txt: str
    transcript_jsonl: str
    candidate_label: str
    candidate_transcript: str


class TranscriptionQueuePayload(TypedDict, total=False):
    flow_idx: int
    session: Any
    base_dir: Path
    base_name: str
    candidate_label: str
    job_timeout_seconds: float
    job_uuid: str
    retry_count: int
    interview_session_id: str
    finalize_correlation_id: str


class TranscriptionQueueSnapshot(TypedDict):
    flow_index: int
    is_pending: bool
    is_canceled: bool
    queued_count: int
    pending_count: int
    error_reason: str | None


HistoryRowKey = str


class OfferTransitionResult(TypedDict):
    next_status: str
    done_message: str


class RetranscribeResultPayload(TypedDict):
    row_key: HistoryRowKey
    transcript_path: str
    flow_recordings: list[dict[str, Any]]


class FinalizePipelineResult(TypedDict, total=False):
    scoring: dict[str, Any]
    out_path: str
    integration_path: str
    transcript_path: str
    director_packet: dict[str, Any]
    warnings: list[str]
    communication_log_path: str | None
    transcript_complete: bool
    transcript_completeness_status: str
    remaining_question_indices: list[int]


class FinalizeTranscriptMetadata(TypedDict):
    transcript_complete: bool
    transcript_completeness_status: str
    remaining_question_indices: list[int]


@dataclass(slots=True)
class TranscriptionRuntimeState:
    pending_flow_transcriptions: set[int] = field(default_factory=set)
    canceled_flow_transcriptions: set[int] = field(default_factory=set)
    queued_flow_transcriptions: list[dict[str, Any]] = field(default_factory=list)
    question_transcription_errors: dict[int, str] = field(default_factory=dict)
