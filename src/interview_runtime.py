from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
import threading
import queue
import importlib.util
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from importlib import import_module
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, Callable, Deque, Mapping, Optional, Sequence, TypedDict
from uuid import uuid4

from candidate_report import build_candidate_report_snapshot
from data_store import InterviewHistoryStore
from scoring_reporting import (
    CandidateQualification,
    DocxExporter,
    DraftManager,
    ReportingValidationError,
    ScoringEngine,
    append_communication_log,
    build_director_packet,
    build_integration_payload,
    load_trait_signal_ui_definition,
    send_director_packet,
    serialize_integration_payload,
)
from ui_feedback import TRANSCRIPTION_PARTIAL_WARNING_COPY


class _RuntimeMessageBox:
    def showwarning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def showerror(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def showinfo(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def askyesnocancel(self, *_args: Any, **_kwargs: Any) -> bool | None:
        return True

    def askretrycancel(self, *_args: Any, **_kwargs: Any) -> bool:
        return False


messagebox = _RuntimeMessageBox()
from platform_services import EVENT_INTERVIEW_FINALIZED, is_valid_date_yyyy_mm_dd


CURRENT_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPTION_TIMEOUT_REASON = "transcription_timeout"
DEFAULT_WINDOWS_MIC_DEVICE = "Microphone (Realtek USB Audio)"
_WINDOWS_MIC_DEVICE_ALIASES = (
    "Microphone (Realtek USB Audio)",
    "Microphone Array (Intel Smart Sound Technology)",
    "Microphone (USB Audio Device)",
    "Microphone",
)
_WINDOWS_AUDIO_DEVICE_ALIASES = (
    "VB-Audio Virtual Cable (CABLE Input)",
    "CABLE Output (VB-Audio Virtual Cable)",
    "CABLE Input (VB-Audio Virtual Cable)",
)
_WINDOWS_DSHOW_AUDIO_DEVICE_CACHE: dict[str, list[str]] = {}
_WINDOWS_DSHOW_AUDIO_DEVICE_CACHE_LOCK = threading.Lock()


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_token(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return cleaned or fallback


def _sanitize_session_token(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return cleaned or fallback


@dataclass
class InterviewState:
    candidate_name: str = ""
    interview_date: str = ""
    school: str = ""
    track: str = ""
    qualification: CandidateQualification = field(default_factory=CandidateQualification)
    current_index: int = 0
    trait_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    flow_time_marks: list[dict[str, Any]] = field(default_factory=list)
    flow_candidate_transcripts: dict[int, str] = field(default_factory=dict)
    flow_recordings: dict[int, dict[str, Any]] = field(default_factory=dict)
    referral_packet: dict[str, str] = field(
        default_factory=lambda: {
            "resume_path": "",
            "interview_notes_path": "",
            "transcript_path": "",
        }
    )
    communication_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": {
                "name": self.candidate_name,
                "interview_date": self.interview_date,
                "school": self.school,
                "track": self.track,
                "qualification": self.qualification.to_dict(),
            },
            "current_index": self.current_index,
            "trait_inputs": self.trait_inputs,
            "custom_inputs": self.custom_inputs,
            "flow_time_marks": self.flow_time_marks,
            "flow_candidate_transcripts": self.flow_candidate_transcripts,
            "flow_recordings": self.flow_recordings,
            "referral_packet": self.referral_packet,
            "communication_log": self.communication_log,
        }


class InterviewSessionContext:
    """Centralized helpers for runtime path policy and session identity values."""

    def __init__(
        self,
        *,
        app_root: Path,
        default_base_dir: Path,
        today_provider: Callable[[], date] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._app_root = Path(app_root).resolve()
        self._default_base_dir = Path(default_base_dir).expanduser()
        self._today_provider = today_provider or date.today
        self._now_provider = now_provider or datetime.now

    def safe_interview_date(self, raw_date: str) -> str:
        text = str(raw_date or "").strip()
        return text or self._today_provider().isoformat()

    def safe_base_name(self, candidate_name: str, interview_date: str) -> str:
        name = _sanitize_session_token(candidate_name, "Candidate")
        date_value = self.safe_interview_date(interview_date)
        return f"Candidate_{name}_{date_value}"

    def active_session_key(
        self,
        interview_session_id: str,
        candidate_name: str,
        interview_date: str,
    ) -> tuple[str, str, str]:
        date_value = self.safe_interview_date(interview_date)
        fallback_id = self.safe_base_name(candidate_name, date_value)
        interview_id = str(interview_session_id or "").strip() or fallback_id
        return interview_id, str(candidate_name or "").strip(), date_value

    def validate_runtime_base_dir(self, raw_base_dir: str, *, write_probe: bool = True) -> Path:
        sanitized = str(raw_base_dir or "").strip()
        if not sanitized:
            base_dir = self._default_base_dir
        else:
            base_dir = Path(sanitized).expanduser()
        if base_dir.is_absolute():
            resolved = base_dir.resolve()
        else:
            resolved = (self._app_root / base_dir).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise OSError(f"Configured base directory is not a folder: {resolved}")
        if write_probe:
            self._probe_writable(resolved)
        return resolved

    def runtime_init_log_path(self) -> Path:
        stamp = self._now_provider().strftime("%Y%m%d-%H%M%S")
        return self._app_root / "logs" / f"interview-runtime-init-{stamp}.log"

    def _probe_writable(self, base_dir: Path) -> None:
        probe_path = base_dir / f".runtime-write-test-{uuid4().hex}.tmp"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)


@dataclass(slots=True)
class InterviewSessionRecordingContext:
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


@dataclass(slots=True)
class AppSharedState:
    session: InterviewSessionRecordingContext = field(default_factory=InterviewSessionRecordingContext)
    transcription: TranscriptionRuntimeState = field(default_factory=TranscriptionRuntimeState)
    history_rows: list[dict[str, Any]] = field(default_factory=list)


class InterviewSessionStore:
    def __init__(self, base_dir: Path):
        self._root = Path(base_dir).expanduser() / "interview_sessions"
        self._root.mkdir(parents=True, exist_ok=True)

    def session_path(self, interview_id: str, candidate_name: str, interview_date: str) -> Path:
        key = self._session_key(interview_id, candidate_name, interview_date)
        return self._root / f"{key}.json"

    def load(self, interview_id: str, candidate_name: str, interview_date: str) -> dict[str, Any]:
        path = self.session_path(interview_id, candidate_name, interview_date)
        if not path.exists():
            return self._default_payload(interview_id, candidate_name, interview_date)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_payload(interview_id, candidate_name, interview_date)
        return self._migrate(dict(data or {}), interview_id, candidate_name, interview_date)

    def save_question_snapshot(
        self,
        *,
        interview_id: str,
        candidate_name: str,
        interview_date: str,
        flow_idx: int,
        item_type: str,
        item_id: str,
        notes: dict[str, Any] | None,
        candidate_transcript: str,
    ) -> Path:
        payload = self.load(interview_id, candidate_name, interview_date)
        questions = payload.setdefault("questions", {})
        record = questions.setdefault(str(int(flow_idx)), {})
        record["flow_idx"] = int(flow_idx)
        record["item_type"] = str(item_type or "")
        record["item_id"] = str(item_id or "")
        record["notes"] = dict(notes or {})
        record["candidate_transcript"] = str(candidate_transcript or "").strip()
        record["updated_at"] = _utc_timestamp()
        payload["updated_at"] = record["updated_at"]
        return self._write_payload(payload)

    def _write_payload(self, payload: dict[str, Any]) -> Path:
        interview = payload.get("interview", {}) if isinstance(payload.get("interview"), dict) else {}
        path = self.session_path(
            str(interview.get("interview_id") or ""),
            str(interview.get("candidate_name") or ""),
            str(interview.get("interview_date") or ""),
        )
        payload["schema_version"] = CURRENT_SCHEMA_VERSION
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _default_payload(self, interview_id: str, candidate_name: str, interview_date: str) -> dict[str, Any]:
        now = _utc_timestamp()
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "interview": {
                "interview_id": str(interview_id or ""),
                "candidate_name": str(candidate_name or ""),
                "interview_date": str(interview_date or ""),
            },
            "questions": {},
        }

    def _migrate(
        self,
        payload: dict[str, Any],
        interview_id: str,
        candidate_name: str,
        interview_date: str,
    ) -> dict[str, Any]:
        version = int(payload.get("schema_version") or 0)
        if version >= CURRENT_SCHEMA_VERSION:
            return payload
        default_payload = self._default_payload(interview_id, candidate_name, interview_date)
        merged = dict(default_payload)
        merged["questions"] = payload.get("questions", {}) if isinstance(payload.get("questions"), dict) else {}
        existing_interview = payload.get("interview", {}) if isinstance(payload.get("interview"), dict) else {}
        merged["interview"] = {
            "interview_id": str(existing_interview.get("interview_id") or interview_id or ""),
            "candidate_name": str(existing_interview.get("candidate_name") or candidate_name or ""),
            "interview_date": str(existing_interview.get("interview_date") or interview_date or ""),
        }
        merged["created_at"] = str(payload.get("created_at") or default_payload["created_at"])
        merged["updated_at"] = _utc_timestamp()
        merged["schema_version"] = CURRENT_SCHEMA_VERSION
        return merged

    def _session_key(self, interview_id: str, candidate_name: str, interview_date: str) -> str:
        return "__".join(
            [
                _safe_token(interview_id, "interview"),
                _safe_token(candidate_name, "candidate"),
                _safe_token(interview_date, "date"),
            ]
        )


class SessionPayloadValidationError(ValueError):
    """Raised when a persisted draft/session payload is not valid for hydration."""


@dataclass(frozen=True)
class ResumeInstruction:
    target: str
    flow_index: int | None = None


class InterviewSessionManager:
    """Validates, normalizes, hydrates, and resumes persisted interview session state."""

    def __init__(
        self,
        *,
        draft_manager: DraftManager,
        session_store: InterviewSessionStore | None = None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self._draft_manager = draft_manager
        self._session_store = session_store
        self._today_provider = today_provider

    def load_draft_payload(self, draft_path: Path) -> dict[str, Any]:
        payload = self._draft_manager.load_draft(draft_path)
        return self.normalize_payload(payload)

    def load_session_payload(
        self,
        *,
        interview_id: str,
        candidate_name: str,
        interview_date: str,
    ) -> dict[str, Any]:
        if self._session_store is None:
            raise SessionPayloadValidationError("Interview session store is not configured.")
        payload = self._session_store.load(interview_id, candidate_name, interview_date)
        return self.normalize_session_payload(payload)

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SessionPayloadValidationError("Draft payload must be a JSON object.")
        candidate = self._require_mapping(payload.get("candidate"), "Draft payload candidate")
        candidate_name = self._normalize_string(candidate.get("name"))
        if not candidate_name:
            raise SessionPayloadValidationError("Draft payload candidate.name is required.")

        return {
            "candidate": {
                "name": candidate_name,
                "interview_date": self._normalize_string(candidate.get("interview_date")) or self._today_iso(),
                "school": self._normalize_string(candidate.get("school")),
                "track": self._normalize_string(candidate.get("track")),
                "qualification": self._normalize_qualification(candidate.get("qualification")),
            },
            "current_index": self._normalize_non_negative_int(payload.get("current_index")),
            "trait_inputs": self._normalize_mapping(payload.get("trait_inputs")),
            "custom_inputs": self._normalize_mapping(payload.get("custom_inputs")),
            "flow_time_marks": self._normalize_list(payload.get("flow_time_marks")),
            "flow_candidate_transcripts": self._normalize_transcripts(payload.get("flow_candidate_transcripts")),
            "flow_recordings": self._normalize_recordings(payload.get("flow_recordings")),
            "referral_packet": self._normalize_referral_packet(payload.get("referral_packet")),
            "communication_log": self._normalize_list(payload.get("communication_log")),
        }

    def normalize_session_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SessionPayloadValidationError("Interview session payload must be a JSON object.")
        questions_raw = payload.get("questions")
        questions = questions_raw if isinstance(questions_raw, dict) else {}
        normalized_questions: dict[str, dict[str, Any]] = {}
        for key, raw_entry in questions.items():
            if not isinstance(raw_entry, dict):
                continue
            flow_idx = self._normalize_non_negative_int(raw_entry.get("flow_idx", key))
            normalized_questions[str(flow_idx)] = {
                "flow_idx": flow_idx,
                "item_type": self._normalize_string(raw_entry.get("item_type")),
                "item_id": self._normalize_string(raw_entry.get("item_id")),
                "notes": self._normalize_mapping(raw_entry.get("notes")),
                "candidate_transcript": self._normalize_string(raw_entry.get("candidate_transcript")),
            }
        normalized = dict(payload)
        normalized["questions"] = normalized_questions
        return normalized

    def hydrate_state(self, payload: dict[str, Any]) -> InterviewState:
        normalized = self.normalize_payload(payload)
        candidate = normalized["candidate"]
        return InterviewState(
            candidate_name=candidate["name"],
            interview_date=candidate["interview_date"],
            school=candidate["school"],
            track=candidate["track"],
            qualification=CandidateQualification.from_dict(candidate["qualification"]),
            current_index=normalized["current_index"],
            trait_inputs=normalized["trait_inputs"],
            custom_inputs=normalized["custom_inputs"],
            flow_time_marks=normalized["flow_time_marks"],
            flow_candidate_transcripts=normalized["flow_candidate_transcripts"],
            flow_recordings=normalized["flow_recordings"],
            referral_packet=normalized["referral_packet"],
            communication_log=normalized["communication_log"],
        )

    def hydrate_state_from_session_payload(self, state: InterviewState, payload: dict[str, Any]) -> None:
        normalized = self.normalize_session_payload(payload)
        questions = normalized.get("questions", {})
        for entry in questions.values():
            flow_idx = int(entry.get("flow_idx", 0))
            notes = self._normalize_mapping(entry.get("notes"))
            item_type = entry.get("item_type")
            if item_type == "trait":
                state.trait_inputs[str(entry.get("item_id") or "")] = notes
            if item_type == "custom":
                state.custom_inputs[str(entry.get("item_id") or "")] = notes
            state.flow_candidate_transcripts[flow_idx] = self._normalize_string(entry.get("candidate_transcript"))

    def build_resume_instruction(self, state: InterviewState, flow_length: int) -> ResumeInstruction:
        if not state.track:
            return ResumeInstruction(target="candidate_info")
        if flow_length <= 0:
            return ResumeInstruction(target="candidate_info")
        if state.current_index <= 0:
            return ResumeInstruction(target="flow_screen", flow_index=0)
        if state.current_index <= flow_length:
            return ResumeInstruction(target="flow_screen", flow_index=state.current_index - 1)
        return ResumeInstruction(target="flow_screen", flow_index=flow_length - 1)

    def _today_iso(self) -> str:
        return self._today_provider().isoformat()

    @staticmethod
    def _normalize_string(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_non_negative_int(value: Any) -> int:
        try:
            raw = int(value or 0)
        except (TypeError, ValueError):
            raw = 0
        if raw < 0:
            return 0
        return raw

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return dict(value)

    @staticmethod
    def _normalize_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return list(value)
        return []

    def _normalize_qualification(self, value: Any) -> dict[str, Any]:
        mapping = self._normalize_mapping(value)
        return CandidateQualification.from_dict(mapping).to_dict()

    def _normalize_referral_packet(self, value: Any) -> dict[str, str]:
        mapping = self._normalize_mapping(value)
        return {
            "resume_path": self._normalize_string(mapping.get("resume_path")),
            "interview_notes_path": self._normalize_string(mapping.get("interview_notes_path")),
            "transcript_path": self._normalize_string(mapping.get("transcript_path")),
        }

    def _normalize_transcripts(self, value: Any) -> dict[int, str]:
        mapping = self._normalize_mapping(value)
        normalized: dict[int, str] = {}
        for key, transcript in mapping.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            normalized[idx] = self._normalize_string(transcript)
        return normalized

    def _normalize_recordings(self, value: Any) -> dict[int, dict[str, Any]]:
        mapping = self._normalize_mapping(value)
        normalized: dict[int, dict[str, Any]] = {}
        for key, raw_entry in mapping.items():
            if not isinstance(raw_entry, dict):
                continue
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            entry = dict(raw_entry)
            attempts = entry.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
            if not attempts and entry.get("base_name"):
                attempts = [entry]
            entry["attempts"] = [dict(item) for item in attempts if isinstance(item, dict)]
            entry["candidate_transcript"] = self._normalize_string(entry.get("candidate_transcript"))
            normalized[idx] = entry
        return normalized

    @staticmethod
    def _require_mapping(value: Any, label: str) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        raise SessionPayloadValidationError(f"{label} must be a JSON object.")


DIAGNOSTIC_LOG_MARKER = "Diagnostic log:"


def append_candidate_segment_text(
    existing_text: str,
    segments: list[Any],
    *,
    candidate_label: str,
) -> str:
    chunks: list[str] = []
    for seg in segments:
        speaker = str(getattr(seg, "speaker", "") or (seg.get("speaker") if isinstance(seg, dict) else ""))
        if speaker != candidate_label:
            continue

        text = str(getattr(seg, "text", "") or (seg.get("text") if isinstance(seg, dict) else "")).strip()
        if text:
            chunks.append(text)

    if not chunks:
        return (existing_text or "").strip()

    prefix = (existing_text or "").strip()
    addition = " ".join(chunks).strip()
    if not prefix:
        return addition
    return f"{prefix} {addition}".strip()


def clip_diagnostic_text(text: str, *, max_length: int) -> str:
    normalized = " ".join(str(text or "").split())
    if max_length <= 0:
        return ""
    return normalized[:max_length].strip()


def extract_diagnostic_filename(text: str, *, marker: str = DIAGNOSTIC_LOG_MARKER) -> str:
    reason = str(text or "")
    if marker not in reason:
        return ""
    after_marker = reason.split(marker, 1)[1].strip()
    candidate = after_marker.split()[0] if after_marker else ""
    if not candidate:
        return ""
    return re.split(r"[\\/]", candidate)[-1]


def redact_paths(text: str) -> str:
    sanitized = str(text or "")
    patterns: tuple[tuple[str, str], ...] = (
        (r"[A-Za-z]:\\[^\s\"]+", "[path]"),
        (r"\\Users\\[^\s\"]+", "[path]"),
        (r"/(?:[^\s/]+/)+[^\s/]+", "[path]"),
        (r"/Users/[^/\s]+", "/Users/[user]"),
        (r"/home/[^/\s]+", "/home/[user]"),
    )
    for pattern, replacement in patterns:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def sanitize_transcription_error_reason(raw_reason: str, *, max_length: int = 300) -> str:
    reason = str(raw_reason or "").strip() or "Unknown transcription error"
    normalized = reason.replace("\r", " ").replace("\n", " ")
    diagnostic_name = extract_diagnostic_filename(normalized)
    sanitized = redact_paths(normalized)
    suffix = f" (diagnostic file: {diagnostic_name})" if diagnostic_name else ""
    clipped = clip_diagnostic_text(f"{sanitized}{suffix}", max_length=max_length)
    if not suffix or diagnostic_name in clipped:
        return clipped
    remaining = max(max_length - len(suffix), 0)
    base = clip_diagnostic_text(sanitized, max_length=remaining)
    return clip_diagnostic_text(f"{base}{suffix}", max_length=max_length)


def build_transcription_log_hint(log_path: Path | str | None) -> str:
    if log_path is None:
        return "See application logs for detailed tracebacks."
    safe_path = redact_paths(str(Path(log_path)))
    return f"See log file '{safe_path}' for detailed tracebacks."


def format_transcription_health_summary(
    *,
    transcription_errors: Mapping[int, str],
    question_labeler: Callable[[int], str],
    log_path: Path | str | None,
) -> tuple[str, str, str]:
    if not transcription_errors:
        return "", "", ""
    labels: list[str] = []
    details: list[str] = []
    for flow_idx in sorted(transcription_errors.keys()):
        label = question_labeler(flow_idx)
        labels.append(label)
        reason = transcription_errors.get(flow_idx, "")
        details.append(f"{label}: {sanitize_transcription_error_reason(reason)}")
    return ", ".join(labels), "\n".join(details), build_transcription_log_hint(log_path)


def format_runtime_init_error_message(log_path: Path | None) -> str:
    hint = f"{DIAGNOSTIC_LOG_MARKER} {redact_paths(str(log_path))}" if log_path is not None else "Diagnostic log unavailable."
    return (
        "Unable to prepare interview recording/transcript files.\n\n"
        "Next steps:\n"
        "1) Check that your base directory exists and is writable.\n"
        "2) Open Settings and confirm the base directory path.\n"
        "3) Click Start Interview again after fixing access.\n\n"
        f"{hint}"
    )


def write_transcription_diagnostic(
    *,
    output_dir: Path,
    base_name: str,
    stage: str,
    error: Exception,
    context: Mapping[str, Any],
) -> Path:
    diagnostics_dir = output_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_stage = str(stage or "unknown").strip().replace(" ", "_")
    file_path = diagnostics_dir / f"{base_name}_{safe_stage}_{stamp}.json"
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "stage": safe_stage,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "context": dict(context or {}),
        "traceback": traceback.format_exc().strip() or "<empty>",
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def probe_audio_file(path: Path) -> dict[str, Any]:
    exists = path.exists()
    size = path.stat().st_size if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": size,
    }


def normalize_label(value: Any) -> str:
    return str(value or "").strip().upper()


def extract_candidate_text_from_jsonl(jsonl_path: Path, candidate_label: str) -> str:
    segments = load_candidate_segments(jsonl_path, candidate_label)
    parts = [segment["text"] for segment in segments]
    return " ".join(parts).strip()


def load_candidate_segments(jsonl_path: Path, candidate_label: str) -> list[dict[str, Any]]:
    normalized_label = normalize_label(candidate_label)
    results: list[dict[str, Any]] = []
    for segment in _iter_jsonl_dict_segments(jsonl_path):
        if normalize_label(segment.get("speaker")) != normalized_label:
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start_seconds = _safe_float(segment.get("start"), default=0.0)
        results.append({"start": start_seconds, "text": text})
    return results


def load_jsonl_segments_for_merge(jsonl_path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl_dict_segments(jsonl_path))


def build_flow_time_windows(flow_time_marks: list[dict[str, Any]]) -> list[tuple[int, float, float]]:
    ordered_marks = sorted(flow_time_marks or [], key=lambda mark: _safe_float(mark.get("t"), default=0.0))
    windows: list[tuple[int, float, float]] = []
    for index, mark in enumerate(ordered_marks):
        start_seconds = _safe_float(mark.get("t"), default=0.0)
        end_seconds = _resolve_end_seconds(mark, ordered_marks, index, start_seconds)
        flow_index = int(_safe_float(mark.get("flow_index"), default=index))
        windows.append((flow_index, start_seconds, end_seconds))
    return windows


def map_segments_to_flow_indices(
    segments: list[dict[str, Any]],
    windows: list[tuple[int, float, float]],
) -> dict[int, str]:
    by_flow_index: dict[int, str] = {}
    for segment in segments:
        start_seconds = _safe_float(segment.get("start"), default=0.0)
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        flow_index = _match_window_index(start_seconds, windows)
        if flow_index is None:
            continue
        previous = by_flow_index.get(flow_index, "")
        by_flow_index[flow_index] = f"{previous} {text}".strip()
    return by_flow_index


def write_merged_timestamped_transcript(
    transcript_path: Path,
    segments: list[dict[str, Any]],
) -> Path:
    ordered_segments = sorted(
        segments,
        key=lambda segment: (
            _safe_float(segment.get("start"), default=0.0),
            _safe_float(segment.get("end"), default=_safe_float(segment.get("start"), default=0.0)),
        ),
    )
    with transcript_path.open("w", encoding="utf-8") as handle:
        handle.write("TIMESTAMPED INTERLEAVED TRANSCRIPT\n\n")
        for segment in ordered_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            timestamp = format_seconds_for_transcript(segment.get("start"))
            speaker = str(segment.get("speaker") or "UNKNOWN").strip() or "UNKNOWN"
            handle.write(f"[{timestamp}] {speaker}: {text}\n")
    return transcript_path


def format_seconds_for_transcript(seconds: Any) -> str:
    value = _safe_float(seconds, default=0.0)
    whole_seconds = int(max(0.0, value) + 0.5)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    remaining_seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def _iter_jsonl_dict_segments(jsonl_path: Path) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    try:
        raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    parsed_segments: list[dict[str, Any]] = []
    for line in raw_lines:
        payload = _parse_segment(line)
        if payload is None:
            continue
        parsed_segments.append(payload)
    return parsed_segments


def _resolve_end_seconds(
    mark: dict[str, Any],
    ordered_marks: list[dict[str, Any]],
    index: int,
    start_seconds: float,
) -> float:
    explicit_end = mark.get("end_t")
    if explicit_end is not None:
        return _safe_float(explicit_end, default=start_seconds)
    if index + 1 >= len(ordered_marks):
        return 1e12
    return _safe_float(ordered_marks[index + 1].get("t"), default=1e12)


def _parse_segment(line: str) -> dict[str, Any] | None:
    cleaned = line.strip()
    if not cleaned:
        return None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _match_window_index(start_seconds: float, windows: list[tuple[int, float, float]]) -> int | None:
    for flow_index, window_start, window_end in windows:
        if window_start <= start_seconds < window_end:
            return flow_index
    return None


@dataclass(frozen=True)
class IndeedTranscriptTurn:
    speaker: str
    text: str


@dataclass(frozen=True)
class IndeedTranscriptQuestionMatch:
    flow_index: int
    question_id: str
    prompt: str
    interviewer_text: str
    candidate_transcript: str


@dataclass(frozen=True)
class IndeedTranscriptImportResult:
    interviewer_speaker: str
    candidate_speaker: str
    mapped_count: int
    unmatched_question_ids: list[str]
    matches: list[IndeedTranscriptQuestionMatch]


_INDEED_SPEAKER_RE = re.compile(r"^\s*(Speaker\s+\d+)\s*:\s*(.*)$", re.IGNORECASE)
_QUESTION_MATCH_STOP_WORDS = {
    "about",
    "and",
    "are",
    "did",
    "for",
    "how",
    "our",
    "that",
    "the",
    "what",
    "when",
    "why",
    "with",
    "would",
    "you",
    "your",
}


def parse_indeed_transcript_text(text: str, *, max_chars: int = 750_000) -> list[IndeedTranscriptTurn]:
    raw = str(text or "")
    if len(raw) > max_chars:
        raise ValueError("Indeed transcript is too large to import.")
    turns: list[IndeedTranscriptTurn] = []
    current_speaker = ""
    current_lines: list[str] = []
    for line in raw.splitlines():
        match = _INDEED_SPEAKER_RE.match(line)
        if match:
            if current_speaker and current_lines:
                joined = " ".join(part.strip() for part in current_lines if part.strip()).strip()
                if joined:
                    turns.append(IndeedTranscriptTurn(speaker=current_speaker, text=joined))
            current_speaker = f"Speaker {match.group(1).split()[-1]}"
            current_lines = [match.group(2).strip()]
            continue
        if current_speaker:
            current_lines.append(line.strip())
    if current_speaker and current_lines:
        joined = " ".join(part.strip() for part in current_lines if part.strip()).strip()
        if joined:
            turns.append(IndeedTranscriptTurn(speaker=current_speaker, text=joined))
    if not turns:
        raise ValueError("No Indeed speaker turns found.")
    return turns


def _question_match_tokens(value: Any) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    return {token for token in tokens if len(token) > 2 and token not in _QUESTION_MATCH_STOP_WORDS}


def _question_prompt_score(prompt: str, spoken: str) -> float:
    prompt_tokens = _question_match_tokens(prompt)
    spoken_tokens = _question_match_tokens(spoken)
    if not prompt_tokens or not spoken_tokens:
        return 0.0
    prompt_coverage = len(prompt_tokens & spoken_tokens) / len(prompt_tokens)
    partial_coverage = sum(
        1
        for prompt_token in prompt_tokens
        if any(prompt_token in spoken_token or spoken_token in prompt_token for spoken_token in spoken_tokens)
    ) / len(prompt_tokens)
    exact = 1.0 if str(prompt).lower().strip() and str(prompt).lower().strip() in str(spoken).lower() else 0.0
    fuzzy = SequenceMatcher(None, " ".join(sorted(prompt_tokens)), " ".join(sorted(spoken_tokens))).ratio()
    fuzzy_with_evidence = fuzzy if partial_coverage >= 0.35 else 0.0
    return max(prompt_coverage, partial_coverage, exact, fuzzy_with_evidence)


def infer_indeed_interviewer_speaker(
    turns: Sequence[IndeedTranscriptTurn],
    questions: Sequence[Mapping[str, Any]],
) -> str:
    prompts = [str(item.get("prompt") or "").strip() for item in questions if str(item.get("prompt") or "").strip()]
    scores: dict[str, float] = {}
    for turn in turns:
        best = max((_question_prompt_score(prompt, turn.text) for prompt in prompts), default=0.0)
        scores[turn.speaker] = scores.get(turn.speaker, 0.0) + best
    if not scores:
        raise ValueError("Could not infer interviewer speaker from transcript.")
    speaker, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        raise ValueError("Could not infer interviewer speaker from transcript.")
    return speaker


def map_indeed_transcript_to_questions(
    turns: Sequence[IndeedTranscriptTurn],
    questions: Sequence[Mapping[str, Any]],
    *,
    interviewer_speaker: str | None = None,
    min_question_score: float = 0.45,
) -> IndeedTranscriptImportResult:
    cleaned_questions = [
        {
            "flow_index": int(item.get("flow_index", index)),
            "question_id": str(item.get("question_id") or item.get("id") or "").strip(),
            "prompt": str(item.get("prompt") or "").strip(),
        }
        for index, item in enumerate(questions)
        if str(item.get("question_id") or item.get("id") or "").strip() and str(item.get("prompt") or "").strip()
    ]
    if not cleaned_questions:
        raise ValueError("No interview questions are available for transcript import.")
    interviewer = str(interviewer_speaker or "").strip() or infer_indeed_interviewer_speaker(turns, cleaned_questions)
    candidate = next((turn.speaker for turn in turns if turn.speaker != interviewer), "")
    if not candidate:
        raise ValueError("Could not infer candidate speaker from transcript.")

    used_question_ids: set[str] = set()
    matches: list[IndeedTranscriptQuestionMatch] = []
    active_question: dict[str, Any] | None = None
    active_question_position = -1
    active_interviewer_text = ""
    active_candidate_parts: list[str] = []

    def flush_active() -> None:
        nonlocal active_question, active_interviewer_text, active_candidate_parts
        if active_question is None:
            return
        transcript = " ".join(part.strip() for part in active_candidate_parts if part.strip()).strip()
        if transcript:
            question_id = str(active_question["question_id"])
            used_question_ids.add(question_id)
            matches.append(
                IndeedTranscriptQuestionMatch(
                    flow_index=int(active_question["flow_index"]),
                    question_id=question_id,
                    prompt=str(active_question["prompt"]),
                    interviewer_text=active_interviewer_text,
                    candidate_transcript=transcript,
                )
            )
        active_question = None
        active_interviewer_text = ""
        active_candidate_parts = []

    for turn in turns:
        if turn.speaker == interviewer:
            best_question: dict[str, Any] | None = None
            best_score = 0.0
            best_position = -1
            for position, question in enumerate(cleaned_questions):
                question_id = str(question["question_id"])
                if question_id in used_question_ids or position <= active_question_position:
                    continue
                score = _question_prompt_score(str(question["prompt"]), turn.text)
                if score > best_score:
                    best_score = score
                    best_question = question
                    best_position = position
            active_score = (
                _question_prompt_score(str(active_question["prompt"]), turn.text)
                if active_question is not None
                else 0.0
            )
            if active_question is not None and active_score >= min_question_score and active_score >= best_score:
                active_interviewer_text = f"{active_interviewer_text} {turn.text}".strip()
                continue
            if best_question is not None and best_score >= min_question_score:
                flush_active()
                active_question = best_question
                active_question_position = best_position
                used_question_ids.add(str(best_question["question_id"]))
                active_interviewer_text = turn.text
                active_candidate_parts = []
            continue
        if active_question is not None and turn.speaker == candidate:
            active_candidate_parts.append(turn.text)
    flush_active()

    matched_ids = {match.question_id for match in matches}
    unmatched = [str(item["question_id"]) for item in cleaned_questions if str(item["question_id"]) not in matched_ids]
    return IndeedTranscriptImportResult(
        interviewer_speaker=interviewer,
        candidate_speaker=candidate,
        mapped_count=len(matches),
        unmatched_question_ids=unmatched,
        matches=matches,
    )


_SUMMARY_UNAVAILABLE_PREFIX = "Summary unavailable:"
_DEFAULT_MISSING_SUMMARY = (
    f"{_SUMMARY_UNAVAILABLE_PREFIX} transformers summarization model/runtime is unavailable."
)
_SUMMARY_TASK = "summarization"
_TEXT2TEXT_TASK = "text2text-generation"
_SUMMARIZATION_PREFIX = "summarize: "
_UNKNOWN_TASK_MARKER = "Unknown task"


class TranscriptionQueueState:
    def __init__(self) -> None:
        self._pending_flow_transcriptions: set[int] = set()
        self._queued_flow_transcriptions: Deque[tuple[int, dict[str, Any]]] = deque()
        self._canceled_flow_transcriptions: set[int] = set()
        self._question_transcription_errors: dict[int, str] = {}
        self._job_started_at: dict[int, float] = {}
        self._job_payloads: dict[int, dict[str, Any]] = {}
        self._condition = threading.Condition()

    def enqueue(self, flow_idx: int, payload: dict[str, Any]) -> dict[str, Any]:
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

    def mark_started(self, flow_idx: int) -> dict[str, Any]:
        with self._condition:
            self._job_started_at[flow_idx] = monotonic()
            self._log_event(flow_idx, "transcription_job_started", elapsed_ms=0, terminal_status="started")
            return self._snapshot_locked(flow_idx)

    def mark_completed(self, flow_idx: int, *, terminal_status: str = "completed") -> dict[str, Any]:
        with self._condition:
            elapsed_ms = self._elapsed_ms(flow_idx)
            self._pending_flow_transcriptions.discard(flow_idx)
            self._canceled_flow_transcriptions.discard(flow_idx)
            self._condition.notify_all()
            self._log_event(flow_idx, "transcription_job_completed", elapsed_ms=elapsed_ms, terminal_status=terminal_status)
            self._job_started_at.pop(flow_idx, None)
            self._job_payloads.pop(flow_idx, None)
            return self._snapshot_locked(flow_idx)

    def mark_failed(self, flow_idx: int, reason: str, *, terminal_status: str = "failed") -> dict[str, Any]:
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

    def mark_timed_out(self, flow_idx: int, reason: str = "transcription_timeout") -> dict[str, Any]:
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

    def cancel(self, flow_idx: int) -> dict[str, Any]:
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

    def next_payload(self) -> tuple[int, dict[str, Any]] | None:
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

    def _snapshot_locked(self, flow_idx: int) -> dict[str, Any]:
        return {
            "flow_index": flow_idx,
            "is_pending": flow_idx in self._pending_flow_transcriptions,
            "is_canceled": flow_idx in self._canceled_flow_transcriptions,
            "queued_count": len(self._queued_flow_transcriptions),
            "pending_count": len(self._pending_flow_transcriptions),
            "error_reason": self._question_transcription_errors.get(flow_idx),
        }

    @staticmethod
    def _prepare_payload(flow_idx: int, payload: dict[str, Any]) -> dict[str, Any]:
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
        payload: dict[str, Any] | None = None,
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


@dataclass(frozen=True, slots=True)
class TranscriptionJobStatusEvent:
    flow_idx: int
    status: str
    snapshot: dict[str, Any]


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

    def submit(self, flow_idx: int, payload: dict[str, Any]) -> None:
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

    def _run_job(self, flow_idx: int, payload: dict[str, Any]) -> None:
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

    def _runtime_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "flow_idx": int(payload["flow_idx"]),
            "session": payload["session"],
            "base_dir": payload["base_dir"],
            "base_name": str(payload["base_name"]),
            "candidate_label": str(payload["candidate_label"]),
            "job_timeout_seconds": float(payload.get("job_timeout_seconds", 180.0)),
        }

    def _emit(self, flow_idx: int, status: str, snapshot: dict[str, Any]) -> None:
        callback = self._on_status_change
        if callback is None:
            return
        callback(TranscriptionJobStatusEvent(flow_idx=flow_idx, status=status, snapshot=snapshot))

    def _discard_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)


class TranscriptWriterController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def append_live_segment(self, flow_idx: int, segment_text: str) -> None:
        self.app._append_live_transcript_for_flow(flow_idx, segment_text)

    def rewrite_from_flow(self) -> None:
        flow_tx = self.app._build_flow_transcript()
        self.app._rewrite_live_transcript_docx_from_flow(flow_tx)


@dataclass(frozen=True)
class RuntimeConfig:
    model: str
    device: str
    compute_type: str
    backend: str = "faster_whisper"


def resolve_runtime(settings: dict[str, Any]) -> RuntimeConfig:
    model = _value_or_default(
        settings,
        runtime_key="whisper_runtime_model",
        preferred_key="whisper_model",
        default_value="large-v3",
    )
    device = _value_or_default(
        settings,
        runtime_key="whisper_runtime_device",
        preferred_key="whisper_device",
        default_value="cuda",
    ).lower()
    compute_type = _value_or_default(
        settings,
        runtime_key="whisper_runtime_compute_type",
        preferred_key="whisper_compute_type",
        default_value="float16",
    ).lower()
    runtime = RuntimeConfig(model=model, device=device, compute_type=compute_type)
    vendor = os.environ.get("INTERVIEW_GPU_VENDOR", "").strip().lower()
    backend = str(settings.get("whisper_backend") or os.environ.get("INTERVIEW_WHISPER_BACKEND") or "").strip().lower()
    whisper_cpp_exe = str(os.environ.get("INTERVIEW_WHISPERCPP_EXE") or "").strip()
    whisper_cpp_model = str(os.environ.get("INTERVIEW_WHISPERCPP_MODEL") or "").strip()
    if backend == "whisper_cpp" or (vendor == "amd" and whisper_cpp_exe and whisper_cpp_model):
        return RuntimeConfig(
            model=whisper_cpp_model or model,
            device="vulkan",
            compute_type="int8",
            backend="whisper_cpp",
        )
    explicit_runtime = any(
        settings.get(key)
        for key in (
            "whisper_backend",
            "whisper_runtime_backend",
            "whisper_runtime_model",
            "whisper_runtime_device",
            "whisper_runtime_compute_type",
        )
    )
    if backend == "openvino_genai" or (
        not explicit_runtime and not backend and _openvino_genai_available()
    ) or (vendor == "intel" and runtime.device == "cuda"):
        openvino_model = str(
            settings.get("whisper_openvino_model")
            or os.environ.get("INTERVIEW_OPENVINO_WHISPER_MODEL")
            or "OpenVINO/whisper-small-int8-ov"
        ).strip()
        return RuntimeConfig(
            model=openvino_model,
            device="GPU",
            compute_type="fp16",
            backend="openvino_genai",
        )
    if runtime.device == "cuda" and vendor and vendor != "nvidia":
        return _resolve_cpu_fallback(preferred=runtime, settings=settings)
    return runtime


def _openvino_genai_available() -> bool:
    return importlib.util.find_spec("openvino_genai") is not None


def fallback_from_exception(
    exc: Exception,
    preferred: RuntimeConfig,
    settings: dict[str, Any],
) -> RuntimeConfig | None:
    if not _is_device_runtime_exception(exc):
        return None
    return _resolve_cpu_fallback(preferred=preferred, settings=settings)


def persist_runtime_choice(
    settings: dict[str, Any],
    runtime_config: RuntimeConfig,
    mode: str,
) -> None:
    settings["whisper_runtime_model"] = runtime_config.model
    settings["whisper_runtime_device"] = runtime_config.device
    settings["whisper_runtime_compute_type"] = runtime_config.compute_type
    settings["whisper_runtime_backend"] = runtime_config.backend
    settings["whisper_runtime_mode"] = mode


def _value_or_default(
    settings: dict[str, Any],
    *,
    runtime_key: str,
    preferred_key: str,
    default_value: str,
) -> str:
    value = str(settings.get(runtime_key) or settings.get(preferred_key) or default_value).strip()
    return value or default_value


def _is_device_runtime_exception(exc: Exception) -> bool:
    text = str(exc).lower()
    return _contains_runtime_error_marker(text)


def _contains_runtime_error_marker(text: str) -> bool:
    markers = (
        "cuda",
        "cudnn",
        "cublas",
        "cublas64_12.dll",
        "device",
        "not enough gpu",
        "no gpu",
        "nvidia driver",
        "no nvidia driver",
        "amd gpu",
        "rocm",
        "hip runtime",
        "intel gpu",
        "invalid device",
        "torch.cuda",
    )
    return any(marker in text for marker in markers)


def _resolve_cpu_fallback(*, preferred: RuntimeConfig, settings: dict[str, Any]) -> RuntimeConfig:
    fallback_model = str(settings.get("whisper_fallback_model") or "").strip()
    model = fallback_model or preferred.model or "small"
    return RuntimeConfig(model=model, device="cpu", compute_type="int8", backend="faster_whisper")


def _extract_dshow_audio_device_names(stderr_text: str) -> list[str]:
    names: list[str] = []
    marker = '"'
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        if "DirectShow audio devices" in line:
            continue
        if "Alternative name" in line:
            continue
        if marker not in line:
            continue
        start = line.find(marker)
        end = line.rfind(marker)
        if start < 0 or end <= start:
            continue
        candidate = line[start + 1 : end].strip()
        if candidate:
            names.append(candidate)
    return names


def list_windows_dshow_audio_devices(ffmpeg_exe: str | None = None) -> list[str]:
    ffmpeg = ffmpeg_exe or shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    cache_key = str(ffmpeg)
    with _WINDOWS_DSHOW_AUDIO_DEVICE_CACHE_LOCK:
        cached = _WINDOWS_DSHOW_AUDIO_DEVICE_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    cmd = [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        logger.warning("windows_audio_device_probe_failed")
        return []

    stderr_text = completed.stderr or ""
    devices = _extract_dshow_audio_device_names(stderr_text)
    with _WINDOWS_DSHOW_AUDIO_DEVICE_CACHE_LOCK:
        _WINDOWS_DSHOW_AUDIO_DEVICE_CACHE[cache_key] = list(devices)
    return devices


def resolve_preferred_windows_audio_device(
    *,
    preferred_name: str,
    aliases: Sequence[str] | None = None,
    available_devices: Sequence[str] | None = None,
) -> str:
    candidates = [preferred_name] + [item for item in (aliases or ()) if item]
    devices = list(available_devices) if available_devices is not None else list_windows_dshow_audio_devices()
    if not devices:
        return preferred_name

    by_folded = {name.casefold(): name for name in devices}
    for candidate in candidates:
        resolved = by_folded.get(candidate.casefold())
        if resolved:
            return resolved

    folded_devices = [(name.casefold(), name) for name in devices]
    for candidate in candidates:
        token = candidate.casefold()
        for folded, original in folded_devices:
            if token in folded or folded in token:
                return original

    return preferred_name


def resolve_default_windows_system_device() -> str:
    preferred = _WINDOWS_AUDIO_DEVICE_ALIASES[0]
    device_probe = _resolve_audio_devices_symbol(
        "list_windows_dshow_audio_devices",
        list_windows_dshow_audio_devices,
    )
    resolved = resolve_preferred_windows_audio_device(
        preferred_name=preferred,
        aliases=_WINDOWS_AUDIO_DEVICE_ALIASES[1:],
        available_devices=device_probe(),
    )
    if resolved != preferred:
        logger.info("windows_system_device_fallback_selected", extra={"resolved_device": resolved})
    return resolved


def resolve_default_windows_microphone_device() -> str:
    preferred = DEFAULT_WINDOWS_MIC_DEVICE
    device_probe = _resolve_audio_devices_symbol(
        "list_windows_dshow_audio_devices",
        list_windows_dshow_audio_devices,
    )
    resolved = resolve_preferred_windows_audio_device(
        preferred_name=preferred,
        aliases=_WINDOWS_MIC_DEVICE_ALIASES[1:],
        available_devices=device_probe(),
    )
    if resolved != preferred:
        logger.info("windows_microphone_device_fallback_selected", extra={"resolved_device": resolved})
    return resolved


def _resolve_audio_runtime_symbol(symbol_name: str, fallback: Any) -> Any:
    module = sys.modules.get("interview_app.audio_runtime")
    if module is not None and hasattr(module, symbol_name):
        return getattr(module, symbol_name)
    return fallback


def _resolve_audio_devices_symbol(symbol_name: str, fallback: Any) -> Any:
    module = sys.modules.get("interview_app.audio_devices")
    if module is not None and hasattr(module, symbol_name):
        return getattr(module, symbol_name)
    return fallback


class AudioRuntimeController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def wait_for_pending_transcriptions(self) -> None:
        self.app._transcription_queue_state.wait_for_pending()

    def background_transcribe_question(
        self,
        *,
        flow_idx: int,
        session: Any,
        base_dir: Path,
        base_name: str,
        candidate_label: str,
        job_timeout_seconds: float = 180.0,
    ) -> None:
        queue_state = self.app._transcription_queue_state
        if queue_state.is_canceled(flow_idx):
            return
        queue_state.mark_started(flow_idx)
        try:
            result = self._stop_and_transcribe_with_timeout(
                flow_idx=flow_idx,
                session=session,
                base_dir=base_dir,
                base_name=base_name,
                timeout_seconds=job_timeout_seconds,
            )
            if queue_state.is_canceled(flow_idx):
                self._cleanup_canceled_result(result)
                queue_state.mark_completed(flow_idx, terminal_status="canceled")
                return
            payload = self._build_payload(flow_idx, base_name, base_dir, result, candidate_label)
            with self.app._audio_state_lock:
                entry = self.app._append_recording_attempt(flow_idx, payload)
                self.app.state.flow_candidate_transcripts[flow_idx] = str(entry.get("candidate_transcript") or "").strip()
            self.app._persist_interview_session_snapshot(flow_idx)
            queue_state.mark_completed(flow_idx)
        except TimeoutError:
            queue_state.mark_timed_out(flow_idx, TRANSCRIPTION_TIMEOUT_REASON)
            raise
        except Exception as exc:
            queue_state.mark_failed(flow_idx, str(exc))
            raise

    def _stop_and_transcribe_with_timeout(
        self,
        *,
        flow_idx: int,
        session: Any,
        base_dir: Path,
        base_name: str,
        timeout_seconds: float,
    ) -> Any:
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def _target() -> None:
            try:
                outcome["result"] = session.stop_and_transcribe(
                    output_dir=base_dir,
                    base_name=base_name,
                    language="en",
                )
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        if done.wait(timeout=max(0.1, float(timeout_seconds))):
            error = outcome.get("error")
            if error is not None:
                raise error
            return outcome["result"]
        logger.error(
            "transcription_stop_and_transcribe_timeout",
            extra={
                "flow_idx": flow_idx,
                "timeout_seconds": float(timeout_seconds),
                "reason": TRANSCRIPTION_TIMEOUT_REASON,
            },
        )
        raise TimeoutError(TRANSCRIPTION_TIMEOUT_REASON)

    def start_recording_session(
        self,
        start_recording: Any,
        *,
        base_dir: Path,
        base_name: str,
        runtime_config: RuntimeConfig,
    ) -> Any:
        settings = getattr(self.app, "settings", {})
        mic_device = str(
            settings.get("windows_microphone_device")
            or _resolve_audio_runtime_symbol(
                "resolve_default_windows_microphone_device",
                resolve_default_windows_microphone_device,
            )()
        ).strip()
        return start_recording(
            os_name="windows" if sys.platform.startswith("win") else "linux",
            output_dir=base_dir,
            base_name=base_name,
            win_mic_device=mic_device or resolve_default_windows_microphone_device(),
            win_sys_device=_resolve_audio_runtime_symbol(
                "resolve_default_windows_system_device",
                resolve_default_windows_system_device,
            )(),
            whisper_model=runtime_config.model,
            whisper_device=runtime_config.device,
            whisper_compute_type=runtime_config.compute_type,
            whisper_backend=runtime_config.backend,
            whisper_settings=self.app._current_whisper_transcription_settings(),
        )

    def start_recording_with_runtime_fallback(
        self,
        start_recording: Any,
        *,
        base_dir: Path,
        base_name: str,
    ) -> Any:
        preferred_runtime = resolve_runtime(self.app.settings)
        try:
            session = self.start_recording_session(
                start_recording,
                base_dir=base_dir,
                base_name=base_name,
                runtime_config=preferred_runtime,
            )
            persist_runtime_choice(self.app.settings, preferred_runtime, "preferred")
            return session
        except Exception as exc:
            fallback_runtime = fallback_from_exception(exc, preferred_runtime, self.app.settings)
            if fallback_runtime is None:
                raise
            session = self.start_recording_session(
                start_recording,
                base_dir=base_dir,
                base_name=base_name,
                runtime_config=fallback_runtime,
            )
            persist_runtime_choice(self.app.settings, fallback_runtime, "cpu_fallback")
            self.app._warn_whisper_fallback_once()
            return session

    def _cleanup_canceled_result(self, result: Any) -> None:
        self.app._delete_file_if_exists(Path(result.mic_wav))
        self.app._delete_file_if_exists(Path(result.sys_wav))
        self.app._delete_file_if_exists(Path(result.transcript_txt))
        self.app._delete_file_if_exists(Path(result.transcript_jsonl))

    def _build_payload(
        self,
        flow_idx: int,
        base_name: str,
        base_dir: Path,
        result: Any,
        candidate_label: str,
    ) -> dict[str, Any]:
        candidate_transcript = self.app._extract_candidate_transcript_from_jsonl(result.transcript_jsonl, candidate_label)
        return {
            "flow_index": flow_idx,
            "base_name": base_name,
            "output_dir": str(base_dir),
            "mic_wav": str(result.mic_wav),
            "sys_wav": str(result.sys_wav),
            "transcript_txt": str(result.transcript_txt),
            "transcript_jsonl": str(result.transcript_jsonl),
            "candidate_label": candidate_label,
            "candidate_transcript": candidate_transcript,
        }


class FlowController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def go_next(self) -> None:
        self.app.next_question()

    def go_back(self) -> None:
        self.app.prev_question()

    def active_flow(self) -> list[dict[str, Any]]:
        return list(self.app.active_flow)


class DashboardController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def refresh_dashboard(self) -> None:
        self.app._refresh_dashboard_snapshot()


def _history_path_exists(path_value: str) -> bool:
    try:
        return Path(str(path_value)).is_file()
    except (OSError, ValueError):
        return False


class HistoryController:
    def __init__(
        self,
        app: Any,
        shared_state: Any,
        grid_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.app = app
        self.shared_state = shared_state
        self._grid_factory = grid_factory
        self.history_grid: Any | None = None

    def build_history_table(self, parent: Any) -> None:
        if self._grid_factory is None:
            raise RuntimeError("Legacy history table UI has been removed; use the PySide history view.")
        box = parent
        self.history_grid = self._grid_factory(
            box,
            on_offer_action=self._on_offer_action,
            on_retranscribe_action=self._on_retranscribe_action,
            on_open_transcript_link=self._on_open_transcript_link,
            on_open_notes_link=self._on_open_notes_link,
            on_regenerate_notes_action=self._on_regenerate_notes_action,
            on_delete_action=self._on_delete_action,
            on_row_selected=self._on_row_selected,
            on_sort_changed=self._on_sort_changed,
            sort_column=self.app.history_sort_column,
            sort_desc=self.app.history_sort_desc,
        )
        self.history_grid.pack(fill="both", expand=True)

    def refresh_history_tree(self) -> None:
        if self.history_grid is None:
            return
        search_text = str(self.app.history_search_var.get() or "")
        load_filtered = getattr(self.app.history_store, "load_filtered", None)
        if callable(load_filtered) and search_text.strip():
            self.history_grid.set_rows(load_filtered(search=search_text))
            self.history_grid.set_filter_text("")
        else:
            self.history_grid.set_rows(self.app.history_store.load())
            self.history_grid.set_filter_text(search_text)
        rows = self.history_grid.visible_rows()
        self.app.history_rows = rows
        self.shared_state.history_rows = rows

    def selected_history_row(self) -> dict[str, Any] | None:
        if self.history_grid is None:
            return None
        return self.history_grid.selected_row()

    def _on_sort_changed(self, column: str, desc: bool) -> None:
        self.app.history_sort_column = column
        self.app.history_sort_desc = desc
        if self.history_grid is None:
            return
        rows = self.history_grid.visible_rows()
        self.app.history_rows = rows
        self.shared_state.history_rows = rows

    def _on_row_selected(self, row: dict[str, Any]) -> None:
        self.app.history_selected_row = row

    def _on_offer_action(self, row: dict[str, Any]) -> None:
        self.app._history_actions_service().handle_offer_action_for_row(row)

    def _on_retranscribe_action(self, row: dict[str, Any]) -> None:
        return

    def _on_delete_action(self, row: dict[str, Any]) -> None:
        self.app._history_actions_service().handle_delete_for_row(row)

    def _on_open_transcript_link(self, row: dict[str, Any]) -> None:
        self._open_history_link(row, "transcript_path")

    def _on_open_notes_link(self, row: dict[str, Any]) -> None:
        if _history_path_exists(str(row.get("interview_notes_path", ""))):
            self._open_history_link(row, "interview_notes_path")
            return
        self._regenerate_history_notes(row)

    def _on_regenerate_notes_action(self, row: dict[str, Any]) -> None:
        self._regenerate_history_notes(row)

    def _open_history_link(self, row: dict[str, Any], key: str) -> None:
        path_value = str(row.get(key, "")).strip()
        if not _history_path_exists(path_value):
            return
        self.app._open_path_in_default_app(path_value)

    def _regenerate_history_notes(self, row: dict[str, Any]) -> None:
        messagebox.showwarning(
            "Regenerate Notes",
            "Regeneration is unavailable for existing interview records. Finalized reports remain unchanged.",
        )


PENDING_TRANSCRIPTION_WARNING = TRANSCRIPTION_PARTIAL_WARNING_COPY
LEGACY_FINALIZE_GUARDRAIL_MESSAGE = "Legacy finalize scoring is disabled; use FinalizePipelineController instead."


@dataclass(slots=True)
class FinalizeContext:
    payload: dict[str, Any]
    scoring: dict[str, Any]
    flow_transcript: list[dict[str, Any]]
    recording_metadata: list[dict[str, Any]]
    transcript_path: str
    transcript_metadata: dict[str, Any]
    transcript_complete: bool
    remaining_question_indices: list[int]
    interview_notes_document_path: str = ""


def build_finalize_context(
    app: Any,
    scoring: dict[str, Any],
    warnings: list[str],
    transcript_metadata: dict[str, Any],
) -> FinalizeContext:
    payload = app.state.to_dict()
    recording_metadata = app._serialize_flow_audio_recordings()
    payload["flow_recordings"] = app.state.flow_recordings
    payload["audio_recording"] = recording_metadata
    if not app.state.flow_recordings:
        warnings.append("Recording/transcription did not complete. Interview was finalized without transcript text.")

    payload["custom_answers"] = app._ordered_custom_answers()
    flow_tx = app._build_flow_transcript()
    app._apply_candidate_transcripts_to_flow(flow_tx)
    app._rewrite_live_transcript_docx_from_flow(flow_tx)
    payload["flow_transcript"] = flow_tx
    payload["transcript_metadata"] = transcript_metadata
    payload["transcript_complete"] = bool(transcript_metadata["transcript_complete"])
    payload["remaining_question_indices"] = list(transcript_metadata["remaining_question_indices"])
    trait_inputs = getattr(app.state, "trait_inputs", {})
    if not isinstance(trait_inputs, dict):
        trait_inputs = {}
    payload["trait_inputs"] = trait_inputs

    transcript_path = ""
    app.state.referral_packet["transcript_path"] = ""

    return FinalizeContext(
        payload=payload,
        scoring=scoring,
        flow_transcript=flow_tx,
        recording_metadata=recording_metadata,
        transcript_path=transcript_path,
        transcript_metadata=transcript_metadata,
        transcript_complete=bool(transcript_metadata["transcript_complete"]),
        remaining_question_indices=list(transcript_metadata["remaining_question_indices"]),
    )


FINALIZE_PROGRESS_STATUS_LABELS = {
    "processing": "Processing",
    "running": "Processing",
    "queued": "Queued",
    "pending": "Queued",
    "complete": "Finished",
    "completed": "Finished",
    "finished": "Finished",
    "generated": "Finished",
    "failed": "Timed-out",
    "timeout": "Timed-out",
    "timed-out": "Timed-out",
    "timed_out": "Timed-out",
}


def finalize_progress_status_label(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    return FINALIZE_PROGRESS_STATUS_LABELS.get(normalized, "Queued")


def build_finalize_progress_tasks(
    step: Any,
    status: Any = "processing",
    *,
    existing_tasks: Any = None,
    queued_steps: Any = None,
) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in existing_tasks or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("step") or "").strip()
        if not name or name in seen:
            continue
        tasks.append({"name": name, "status": finalize_progress_status_label(item.get("status"))})
        seen.add(name)
    for name_value in queued_steps or []:
        name = str(name_value or "").strip()
        if name and name not in seen:
            tasks.append({"name": name, "status": "Queued"})
            seen.add(name)

    current_step = str(step or "").strip()
    current_status = finalize_progress_status_label(status)
    if current_step:
        found = False
        current_index = -1
        for index, item in enumerate(tasks):
            if item["name"] == current_step:
                item["status"] = current_status
                found = True
                current_index = index
                break
        if found and current_status in {"Processing", "Finished", "Timed-out"}:
            for item in tasks[:current_index]:
                if item["status"] in {"Queued", "Processing"}:
                    item["status"] = "Finished"
            for item in tasks[current_index + 1 :]:
                if item["status"] == "Processing" and current_status == "Processing":
                    item["status"] = "Finished"
        else:
            for item in tasks:
                if item["status"] == "Processing" and current_status == "Processing":
                    item["status"] = "Finished"
        if not found:
            tasks.append({"name": current_step, "status": current_status})
    if current_status == "Finished":
        for item in tasks:
            if item["status"] in {"Processing", "Queued"}:
                item["status"] = "Finished"
    return tasks


def format_finalize_progress_tasks(tasks: Any, *, fallback: str = "") -> str:
    rows = [item for item in tasks or [] if isinstance(item, dict) and str(item.get("name") or "").strip()]
    if not rows:
        return str(fallback or "").strip()
    width = min(max(len(str(item.get("name") or "").strip()) for item in rows), 34)
    lines: list[str] = []
    for item in rows:
        name = str(item.get("name") or "").strip()
        status = finalize_progress_status_label(item.get("status"))
        if len(name) > width:
            name = f"{name[: max(width - 1, 1)]}..."
        lines.append(f"{name:<{width + 3}}{status:>10}")
    return "\n".join(lines)


def _resolve_finalize_gateway_symbol(symbol_name: str, fallback: Any) -> Any:
    module = sys.modules.get("interview_app.finalize_gateways")
    if module is not None and hasattr(module, symbol_name):
        return getattr(module, symbol_name)
    return fallback


def _resolve_finalize_pipeline_symbol(symbol_name: str, fallback: Any) -> Any:
    module = sys.modules.get("interview_app.finalize_pipeline")
    if module is not None and hasattr(module, symbol_name):
        return getattr(module, symbol_name)
    return fallback


def _app_module_symbol(app: Any, symbol_name: str, fallback: Any) -> Any:
    module = sys.modules.get(type(app).__module__)
    if module is not None and hasattr(module, symbol_name):
        return getattr(module, symbol_name)
    app_init = getattr(type(app), "__init__", None)
    app_globals = getattr(app_init, "__globals__", {})
    if isinstance(app_globals, dict) and symbol_name in app_globals:
        return app_globals[symbol_name]
    return fallback


@dataclass(slots=True)
class FinalizeGateways:
    sent_referral_keys: set[str] = field(default_factory=set)

    def export_report(self, app: Any, context: FinalizeContext) -> str:
        exporter_fallback = _resolve_finalize_gateway_symbol("DocxExporter", DocxExporter)
        exporter_cls = _app_module_symbol(app, "DocxExporter", exporter_fallback)
        output_dir_resolver = app.__dict__.get("_interview_notes_output_dir")
        if output_dir_resolver is None:
            output_dir_resolver = getattr(type(app), "_interview_notes_output_dir", None)
            output_dir = (
                Path(output_dir_resolver(app))
                if callable(output_dir_resolver)
                else Path(app.settings["base_dir"]) / "Indeed Interview Notes"
            )
        else:
            output_dir = (
                Path(output_dir_resolver())
                if callable(output_dir_resolver)
                else Path(app.settings["base_dir"]) / "Indeed Interview Notes"
            )
        exporter = exporter_cls(output_dir)
        out_path = exporter.export(app._rubric_with_question_overrides(), context.payload, context.scoring)
        normalized_path = Path(out_path).as_posix().strip()
        app.state.referral_packet["interview_notes_path"] = normalized_path
        context.interview_notes_document_path = normalized_path
        context.payload["interview_notes_document_path"] = normalized_path
        return out_path

    def export_basic_report(self, app: Any, context: FinalizeContext) -> str:
        exporter_fallback = _resolve_finalize_gateway_symbol("DocxExporter", DocxExporter)
        exporter_cls = _app_module_symbol(app, "DocxExporter", exporter_fallback)
        output_dir_resolver = app.__dict__.get("_interview_notes_output_dir")
        if output_dir_resolver is None:
            output_dir_resolver = getattr(type(app), "_interview_notes_output_dir", None)
            output_dir = (
                Path(output_dir_resolver(app))
                if callable(output_dir_resolver)
                else Path(app.settings["base_dir"]) / "Indeed Interview Notes"
            )
        else:
            output_dir = (
                Path(output_dir_resolver())
                if callable(output_dir_resolver)
                else Path(app.settings["base_dir"]) / "Indeed Interview Notes"
            )
        exporter = exporter_cls(output_dir)
        export_basic = getattr(exporter, "export_basic_interview_notes", None)
        out_path = (
            export_basic(app._rubric_with_question_overrides(), context.payload, context.scoring)
            if callable(export_basic)
            else exporter.export(app._rubric_with_question_overrides(), context.payload, context.scoring)
        )
        normalized_path = Path(out_path).as_posix().strip()
        app.state.referral_packet["interview_notes_path"] = normalized_path
        context.interview_notes_document_path = normalized_path
        context.payload["interview_notes_document_path"] = normalized_path
        return out_path

    def export_integration(self, app: Any, context: FinalizeContext) -> Path:
        builder_fallback = _resolve_finalize_gateway_symbol("build_integration_payload", build_integration_payload)
        serializer_fallback = _resolve_finalize_gateway_symbol("serialize_integration_payload", serialize_integration_payload)
        payload_builder = _app_module_symbol(app, "build_integration_payload", builder_fallback)
        payload_serializer = _app_module_symbol(app, "serialize_integration_payload", serializer_fallback)
        integration_payload = payload_builder(context.payload, context.scoring, include_flow_slices=True)
        return payload_serializer(
            Path(app.settings["base_dir"]),
            integration_payload,
            candidate_name=app.state.candidate_name,
        )

    def persist_finalize_history(self, app: Any, context: FinalizeContext, out_path: str) -> str:
        payload_candidate = context.payload.get("candidate", {})
        saved_at = _utc_timestamp()
        history_id = str(uuid4())
        history_entry = {
            "history_id": history_id,
            "interview_date": payload_candidate.get("interview_date", ""),
            "candidate_name": payload_candidate.get("name", ""),
            "interview_score": context.scoring.get("percent_of_max", 0),
            "determination": context.scoring.get("outcome", ""),
            "school": payload_candidate.get("school", ""),
            "track": payload_candidate.get("track", ""),
            "saved_report_path": str(out_path),
            "transcript_path": context.transcript_path,
            "interview_notes_path": app.state.referral_packet.get("interview_notes_path", "") or str(out_path),
            "saved_at": saved_at,
            "offer_status": "not_generated",
            "offer_path": "",
            "offer_letter_path": "",
            "flow_recordings": context.recording_metadata,
        }
        report_snapshot = build_candidate_report_snapshot(
            context.payload,
            context.scoring,
            history_entry,
            report_path=history_entry["interview_notes_path"],
        )
        actor = str(os.environ.get("USERNAME") or os.environ.get("USER") or "admin").strip() or "admin"
        app_version = str(getattr(app, "app_version", "") or getattr(app, "version", "") or "")
        structured_append = getattr(app.history_store, "append_with_candidate_report", None)
        if callable(structured_append):
            structured_append(
                history_entry,
                report_snapshot,
                actor=actor,
                actor_role="admin",
                app_version=app_version,
            )
        else:
            app.history_store.append(history_entry)
        return history_id

    def send_referral(
        self,
        app: Any,
        context: FinalizeContext,
        out_path: str,
        integration_path: Path,
    ) -> tuple[dict[str, Any], Path | None]:
        builder_fallback = _resolve_finalize_gateway_symbol("build_director_packet", build_director_packet)
        packet_builder = _app_module_symbol(app, "build_director_packet", builder_fallback)
        director_packet = packet_builder(
            payload=context.payload,
            scoring=context.scoring,
            report_path=out_path,
            integration_path=integration_path,
            referral_packet=app.state.referral_packet,
            generated_transcript_path=None,
        )
        send_enabled = bool(app.settings.get("send_director_referral_on_finalize", False))
        endpoint = str(app.settings.get("director_referral_endpoint", "")).strip()
        if not send_enabled:
            return director_packet, None

        dedupe_key = self._referral_dedupe_key(director_packet, endpoint)
        if dedupe_key in self.sent_referral_keys:
            return director_packet, None

        sender = _resolve_finalize_gateway_symbol("send_director_packet", send_director_packet)
        log_appender = _resolve_finalize_gateway_symbol("append_communication_log", append_communication_log)
        send_result = sender(director_packet, endpoint)
        self.sent_referral_keys.add(dedupe_key)

        log_event = {
            "event": "director_referral_sent",
            "timestamp": _utc_timestamp(),
            "endpoint": endpoint,
            "status": send_result.get("status", "unknown"),
        }
        comm_log_path = log_appender(Path(app.settings["base_dir"]), log_event)
        return director_packet, comm_log_path

    def _referral_dedupe_key(self, director_packet: dict[str, Any], endpoint: str) -> str:
        packet_json = json.dumps(director_packet, sort_keys=True, default=str)
        return f"{endpoint}:{packet_json}"


def raise_legacy_finalize_guardrail() -> None:
    raise ReportingValidationError(LEGACY_FINALIZE_GUARDRAIL_MESSAGE)


class FinalizePipelineController:
    def __init__(self, app: Any, shared_state: Any, gateways: FinalizeGateways | None = None) -> None:
        self.app = app
        self.shared_state = shared_state
        self.gateways = gateways or FinalizeGateways()

    def finalize_interview(self) -> None:
        messagebox_module = _resolve_finalize_pipeline_symbol("messagebox", messagebox)
        try:
            self._dispatch_finalize_work()
        except ReportingValidationError as exc:
            messagebox_module.showerror("Finalize Error", str(exc))
        except Exception as exc:
            messagebox_module.showerror("Finalize Error", f"{exc}\n\n{traceback.format_exc()}")

    def _dispatch_finalize_work(self) -> None:
        if bool(getattr(self.app, "_finalize_worker_running", False)):
            return
        self.app.validate_before_finalize()
        self._warn_if_finalize_starts_with_pending_transcriptions()
        self.app.current_finalize_correlation_id = uuid4().hex
        self.app._show_finalize_progress()
        self._start_finalize_worker_non_blocking(attempt=1)

    def _start_finalize_worker_non_blocking(self, attempt: int) -> None:
        self.app._start_finalize_worker(attempt=attempt)
        if hasattr(self.app, "show_start_screen"):
            self.app.show_start_screen()
        self._restore_main_window_focus()

    def _warn_if_finalize_starts_with_pending_transcriptions(self) -> None:
        if not self._pending_transcription_indices():
            return
        self.app._show_finalize_partial_transcript_warning(PENDING_TRANSCRIPTION_WARNING)

    def _restore_main_window_focus(self) -> None:
        root = self.app.winfo_toplevel() if hasattr(self.app, "winfo_toplevel") else self.app
        if hasattr(root, "lift"):
            root.lift()
        if hasattr(root, "focus_force"):
            root.focus_force()

    def run_finalize_pipeline(self) -> dict[str, Any]:
        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Scoring interview")
        scoring_engine = _resolve_finalize_pipeline_symbol("ScoringEngine", ScoringEngine)
        scoring = scoring_engine.evaluate(
            self.app._rubric_with_question_overrides(),
            self.app.state.track,
            self.app.state.trait_inputs,
        )
        warnings: list[str] = []
        pending_snapshot = self._pending_transcription_snapshot()

        recording_flow_idx = self.app._safe_attr("recording_flow_idx")
        if recording_flow_idx is not None:
            if hasattr(self.app, "_report_finalize_progress"):
                self.app._report_finalize_progress(f"Transcribing Q{int(recording_flow_idx) + 1}")
            self.app._finalize_current_question_audio_and_doc(recording_flow_idx)

        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Preparing transcript")
        warnings.extend(self.app._collect_transcription_health_warnings())
        transcript_metadata = self._build_transcript_metadata(pending_snapshot)
        if not transcript_metadata["transcript_complete"]:
            warnings.append(PENDING_TRANSCRIPTION_WARNING)
        self.app._hydrate_state_from_session_store()

        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Building interview notes")
        context = build_finalize_context(self.app, scoring, warnings, transcript_metadata)
        out_path = self.gateways.export_report(self.app, context)
        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Writing integration export")
        integration_path = self.gateways.export_integration(self.app, context)
        integration_path_str = Path(integration_path).as_posix()
        director_packet, comm_log_path = self.gateways.send_referral(self.app, context, out_path, integration_path)
        self.gateways.persist_finalize_history(self.app, context, out_path)
        return {
            "scoring": scoring,
            "out_path": out_path,
            "integration_path": integration_path_str,
            "transcript_path": context.transcript_path,
            "director_packet": director_packet,
            "warnings": warnings,
            "communication_log_path": str(comm_log_path) if comm_log_path else None,
            "transcript_complete": transcript_metadata["transcript_complete"],
            "transcript_completeness_status": transcript_metadata["transcript_completeness_status"],
            "remaining_question_indices": transcript_metadata["remaining_question_indices"],
        }

    def _build_transcript_metadata(self, pending_snapshot: dict[str, int | list[int]]) -> dict[str, Any]:
        pending_indices = list(pending_snapshot.get("indices", []))
        is_complete = int(pending_snapshot.get("count", 0)) == 0
        status = "complete" if is_complete else "partial"
        return {
            "transcript_complete": is_complete,
            "transcript_completeness_status": status,
            "remaining_question_indices": pending_indices,
        }

    def _pending_transcription_indices(self) -> list[int]:
        pending_flow_indices = self._collect_pending_flow_indices()
        return sorted(idx + 1 for idx in pending_flow_indices)

    def _collect_pending_flow_indices(self) -> set[int]:
        queue_state = getattr(self.app, "_transcription_queue_state", None)
        shared_transcription = getattr(self.shared_state, "transcription", None)
        flow_indices = set(self._safe_int_indices(getattr(queue_state, "_pending_flow_transcriptions", set())))
        flow_indices.update(self._safe_int_indices(getattr(shared_transcription, "pending_flow_transcriptions", set())))
        return flow_indices

    @staticmethod
    def _safe_int_indices(values: Any) -> list[int]:
        if not values:
            return []
        normalized: list[int] = []
        for value in values:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        return normalized

    def _pending_transcription_snapshot(self) -> dict[str, int | list[int]]:
        pending_indices = self._pending_transcription_indices()
        return {"count": len(pending_indices), "indices": pending_indices}

    def poll_finalize_worker(self, q: queue.Queue[dict[str, Any]]) -> None:
        try:
            status = q.get_nowait()
        except queue.Empty:
            self.app._refresh_finalize_processing_state()
            self.app.after(150, lambda: self.poll_finalize_worker(q))
            return
        self.app._finalize_worker_running = False
        if status.get("ok"):
            self._handle_finalize_success(status)
            return
        self._handle_finalize_failure(status)

    def _handle_finalize_success(self, status: dict[str, Any]) -> None:
        messagebox_module = _resolve_finalize_pipeline_symbol("messagebox", messagebox)
        result = status["result"]
        self.app.last_finalize_result = result
        self.app.metrics_logger.log_ux_completion(app="interview", surface="finalize", outcome="completed", track=self.app.state.track)
        self.app.metrics_logger.log_event(EVENT_INTERVIEW_FINALIZED, track=self.app.state.track)
        scoring = result["scoring"]
        warnings = result.get("warnings", [])
        if result.get("transcript_completeness_status") == "partial":
            self.app._show_finalize_partial_transcript_warning(PENDING_TRANSCRIPTION_WARNING)
        warning_text = "\n\nWarnings:\n- " + "\n- ".join(str(w) for w in warnings) if warnings else ""
        self.app._prompt_resume_if_outcome_requires_it(scoring)
        messagebox_module.showinfo("Finalized", f"Outcome: {scoring['outcome']}\nWeighted Total: {scoring['weighted_total']}/{scoring['max_weighted_total']}\nPercent: {scoring.get('percent_of_max_label', str(scoring['percent_of_max']) + '%')}\nSkipped scored questions: {scoring.get('skipped_traits_count', 0)}\n\nReport saved to:\n{result['out_path']}\n\nJSON export saved to:\n{result['integration_path']}{warning_text}")
        transcript_path = str(result.get("transcript_path") or "").strip()
        self.app._delete_interview_recording_artifacts()
        self.app.current_finalize_correlation_id = ""

    def _handle_finalize_failure(self, status: dict[str, Any]) -> None:
        messagebox_module = _resolve_finalize_pipeline_symbol("messagebox", messagebox)
        err = status.get("error")
        if int(status.get("attempt", 1)) == 1:
            self._start_finalize_worker_non_blocking(attempt=2)
            return
        self.app._close_finalize_progress()
        if isinstance(err, Exception) and self.app.recording_session is not None:
            self.app.recording_session = None
            self.app.recording_base_name = ""
        if isinstance(err, ReportingValidationError):
            self.app.current_finalize_correlation_id = ""
            messagebox_module.showerror("Finalize Error", str(err))
            return
        should_retry = messagebox_module.askretrycancel("Finalize Error", f"{err}\n\n{status.get('tb', '')}")
        if should_retry:
            self._start_finalize_worker_non_blocking(attempt=1)
            return
        self.app.current_finalize_correlation_id = ""


def validate_before_finalize(app: Any) -> None:
    if not app.state.candidate_name.strip():
        raise ValueError("Candidate Name is required.")
    if not is_valid_date_yyyy_mm_dd(app.state.interview_date.strip()):
        raise ValueError("Interview Date must be valid YYYY-MM-DD.")
    if not app.state.school.strip():
        raise ValueError("School selection is required.")
    if not app.state.track:
        raise ValueError("Track selection is required.")
    qualification = app.state.qualification
    if qualification.has_degree is None:
        raise ValueError("Please confirm whether the candidate has a degree.")
    if qualification.ece_units_completed is None and not qualification.degree_in_ece:
        raise ValueError("ECE units completed is required unless degree in ECE is checked.")
    if qualification.has_degree and not qualification.degree_type:
        raise ValueError("Degree type is required when a degree is reported.")
    if (not qualification.has_degree) and qualification.total_units_completed is None:
        raise ValueError("Total units completed is required when no degree is reported.")
    for trait in app.rubric_loader.get_traits_for_track(app.state.track):
        tid = trait["id"]
        tstate = app.state.trait_inputs.get(tid)
        if not tstate:
            raise ValueError(f"Missing state for trait: {trait['name']}")
        skipped = bool(tstate.get("skipped", False))
        dq_on = bool(tstate.get("absolute_disqualifier"))
        raw = tstate.get("raw_score")
        if skipped:
            continue
        if not dq_on and raw not in {1, 2, 3, 4, 5}:
            raise ValueError(f"Missing raw score for trait: {trait['name']}")
        if dq_on and not (tstate.get("verbatim_notes") or "").strip():
            raise ValueError(f"Trait '{trait['name']}' has disqualifier checked but no verbatim notes.")


_COMPAT_MODULES: tuple[str, ...] = (
    "interview_audio_recorder",
    "interview_app.audio_devices",
    "interview_app.audio_runtime",
    "interview_app.dashboard_controller",
    "interview_app.finalize_context",
    "interview_app.finalize_gateways",
    "interview_app.finalize_pipeline",
    "interview_app.flow_controller",
    "interview_app.history_actions",
    "interview_app.history_controller",
    "interview_app.session_context",
    "interview_app.session_manager",
    "interview_app.state",
    "interview_app.transcript_processor",
    "interview_app.transcript_writer",
    "interview_app.transcription_executor",
    "interview_app.transcription_queue",
    "interview_app.types",
    "interview_app.whisper_runtime_policy",
)

_WRAPPER_POLICY = (
    "Legacy interview runtime modules are compatibility wrappers during flattening. "
    "New production imports should prefer interview_runtime."
)


def available_modules() -> tuple[str, ...]:
    return _COMPAT_MODULES


def module_ownership() -> dict[str, str]:
    return {module_name: "interview_runtime" for module_name in _COMPAT_MODULES}


def wrapper_policy() -> str:
    return _WRAPPER_POLICY


def load_compat_module(module_name: str) -> ModuleType:
    if module_name not in _COMPAT_MODULES:
        raise AttributeError(f"{module_name!r} is not part of interview_runtime")
    return import_module(module_name)


def public_symbols(module_name: str | None = None) -> tuple[str, ...]:
    module_names = (module_name,) if module_name is not None else _COMPAT_MODULES
    symbols: set[str] = set()
    for compat_name in module_names:
        module = load_compat_module(compat_name)
        symbols.update(name for name in dir(module) if not name.startswith("_"))
    return tuple(sorted(symbols))


def resolve_compat_symbol(symbol_name: str) -> Any:
    for module_name in _COMPAT_MODULES:
        module = import_module(module_name)
        if hasattr(module, symbol_name):
            return getattr(module, symbol_name)
    raise AttributeError(f"interview_runtime has no attribute {symbol_name!r}")


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"interview_runtime has no attribute {name!r}")
    return resolve_compat_symbol(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(public_symbols()))
