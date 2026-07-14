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
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, Callable, Deque, Mapping, Optional, Sequence, TypedDict
from uuid import uuid4

from candidate_report import build_candidate_report_snapshot
from data_store import InterviewHistoryStore, InterviewMLDatasetStore, RubricLoader, ml_dataset_path_for_history_path
from scoring_reporting import (
    CandidateQualification,
    DEFAULT_ENGINE_MODULE_CONTRACT,
    DEFAULT_ENGINE_RUNTIME_CONTRACT,
    DocxExporter,
    DraftManager,
    ReportingValidationError,
    ScoringEngine,
    append_communication_log,
    build_director_packet,
    build_integration_payload,
    canonical_trait_id,
    load_module_contract_runtime_bundle,
    load_trait_signal_ui_definition,
    normalize_model_signal_suggestions,
    send_director_packet,
    serialize_integration_payload,
    write_canonical_model_signal_suggestions,
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
from platform_services import EVENT_INTERVIEW_FINALIZED, atomic_write_json, is_valid_date_yyyy_mm_dd


CURRENT_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEEPSEEK_PROMPTS_CONFIG_PATH = REPO_ROOT / "config" / "deepseek_prompts.json"
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


@lru_cache(maxsize=1)
def _build_summarizer_pipeline() -> tuple[Optional[Callable[..., Any]], Optional[str]]:
    try:
        from transformers import pipeline
    except Exception as exc:  # pragma: no cover - exercised with fallback tests
        return None, str(exc)

    try:
        return pipeline(_SUMMARY_TASK), None
    except Exception as summary_exc:
        text2text_pipeline, text2text_error = _build_text2text_summarizer_pipeline(pipeline)
        if text2text_pipeline is not None:
            return text2text_pipeline, None
        detail = _format_runtime_error(summary_exc, text2text_error)
        return None, detail


def _build_text2text_summarizer_pipeline(
    pipeline_factory: Callable[..., Any],
) -> tuple[Optional[Callable[..., Any]], Optional[str]]:
    try:
        base_pipeline = pipeline_factory(_TEXT2TEXT_TASK)
    except Exception as exc:  # pragma: no cover - exercised with fallback tests
        return None, str(exc)

    def summarize_with_prefix(text: str, **kwargs: Any) -> Any:
        prompt = f"{_SUMMARIZATION_PREFIX}{text}"
        return base_pipeline(prompt, **kwargs)

    return summarize_with_prefix, None


def _format_runtime_error(primary_error: Exception, secondary_error: Optional[str]) -> str:
    primary_message = _normalize_runtime_error_text(str(primary_error))
    if secondary_error is None:
        return primary_message

    fallback_message = _normalize_runtime_error_text(secondary_error)
    return f"{primary_message}; fallback {_TEXT2TEXT_TASK} failed: {fallback_message}"


def _normalize_runtime_error_text(message: str) -> str:
    if _UNKNOWN_TASK_MARKER not in message:
        return message
    if _SUMMARY_TASK not in message:
        return message
    return (
        "transformers runtime does not expose the 'summarization' task. "
        f"Attempted fallback task '{_TEXT2TEXT_TASK}'."
    )


def _normalize_transcript_text(transcript_text: Any) -> str:
    if not isinstance(transcript_text, str):
        return ""
    return " ".join(transcript_text.split()).strip()


def _chunk_text(text: str, max_chars: int = 2600, overlap_chars: int = 200) -> list[str]:
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return [chunk for chunk in chunks if chunk]


def summarize_transcript(
    transcript_text: Any,
    summarizer: Optional[Callable[..., Any]] = None,
    max_chars: int = 2600,
) -> str:
    normalized_text = _normalize_transcript_text(transcript_text)
    if not normalized_text:
        return "No candidate transcript available for summarization."

    active_summarizer = summarizer
    runtime_error: Optional[str] = None
    if active_summarizer is None:
        active_summarizer, runtime_error = _build_summarizer_pipeline()

    if active_summarizer is None:
        if runtime_error:
            normalized_runtime_error = _normalize_runtime_error_text(runtime_error)
            return f"{_DEFAULT_MISSING_SUMMARY} ({normalized_runtime_error})"
        return _DEFAULT_MISSING_SUMMARY

    summary_chunks: list[str] = []
    for chunk in _chunk_text(normalized_text, max_chars=max_chars):
        try:
            chunk_summary = active_summarizer(chunk, max_length=90, min_length=20, do_sample=False)
        except Exception as exc:
            return f"{_DEFAULT_MISSING_SUMMARY} ({exc})"

        text = ""
        if isinstance(chunk_summary, list) and chunk_summary:
            text = str(chunk_summary[0].get("summary_text") or "").strip()
        if text:
            summary_chunks.append(text)

    if not summary_chunks:
        return "Summary unavailable: no summary output generated."
    return " ".join(summary_chunks).strip()


@dataclass(slots=True)
class DeepSeekSummaryConfig:
    enabled: bool
    api_key: str
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "deepseek-r1:8b"
    timeout_seconds: float = 600.0
    prompt_templates: dict[str, Any] = field(default_factory=dict)
    debug_log_dir: Path | None = None
    trace_events: list[dict[str, Any]] = field(default_factory=list)


_LOCAL_DEEPSEEK_BASE_URL = "http://127.0.0.1:11434/v1"
_LOCAL_DEEPSEEK_MODEL = "deepseek-r1:8b"
_LOCAL_DEEPSEEK_API_KEY = "ollama"
_LOCAL_DEEPSEEK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_DEEPSEEK_TIMEOUT_SECONDS = 600.0
MAX_DEEPSEEK_TIMEOUT_SECONDS = 1800.0
DEEPSEEK_REPAIR_PROMPT = (
    "Return only valid JSON matching the required schema. "
    "Do not include markdown, comments, explanations, or extra text."
)
DEEPSEEK_SUMMARY_FALLBACK_TEXT = (
    "Executive summary could not be generated automatically. "
    "Please review transcript and scores manually."
)
ROLE_CONTEXT: dict[str, str] = {
    "Preschool Teacher": (
        "Emphasize child-centeredness, classroom management, supervision, safety, "
        "parent communication, warmth, and developmentally appropriate practice."
    ),
    "Infant/Toddler Teacher": (
        "Emphasize nurturing care, routines, safe sleep awareness, diapering/hygiene, "
        "responsiveness, patience, and family communication."
    ),
    "Behavior Support Specialist": (
        "Emphasize de-escalation, observation, family communication, documentation, "
        "teacher collaboration, inclusion, ethical boundaries, and avoiding diagnosis."
    ),
    "Lead Teacher": (
        "Emphasize classroom leadership, mentoring assistants, curriculum planning, "
        "parent communication, compliance, and consistency."
    ),
    "Director": (
        "Emphasize leadership judgment, licensing/compliance, parent relations, staff "
        "supervision, accountability, conflict resolution, and operational follow-through."
    ),
    "Assistant Director": (
        "Emphasize reliability, communication, staff support, parent-facing professionalism, "
        "compliance support, and follow-through."
    ),
    "Office/Admin": (
        "Emphasize organization, confidentiality, accuracy, communication, professionalism, "
        "and customer service."
    ),
}
DEEPSEEK_PROMPT_TEMPLATE_KEYS = (
    "answer_summary_system",
    "answer_summary_user",
    "executive_summary_system",
    "executive_summary_user",
    "trait_suggestion_system",
    "trait_suggestion_user",
    "trait_scoring_system",
    "trait_scoring_user",
)
DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS = (
    "answer_summary_system_by_question",
    "answer_summary_user_by_question",
    "trait_suggestion_system_by_question",
    "trait_suggestion_user_by_question",
    "trait_scoring_system_by_question",
    "trait_scoring_user_by_question",
)
DEFAULT_DEEPSEEK_PROMPT_TEMPLATES: dict[str, str] = {
    "answer_summary_system": (
        "You review individual preschool teacher interview answers for Little People's Landing hiring notes. "
        "Return only one JSON object. Do not use markdown. Do not reveal reasoning. Do not echo input. "
        "Do not invent facts, generic praise, diagnoses, or hiring recommendations. Use concrete "
        "candidate evidence tied to preschool classroom practice, child safety, co-regulation, "
        "family communication, teamwork, coachability, accountability, gentleness, curiosity, "
        "behavior guidance, and flexibility."
    ),
    "answer_summary_user": (
        "Create evidence-first summaries from candidate_transcript values. Return exactly this JSON shape. "
        "JSON output template:\n"
        "{\n"
        '  "answer_summaries": [\n'
        "    {\n"
        '      "flow_index": "number-or-string-from-input",\n'
        '      "question_label": "exact question text from input question or prompt",\n'
        '      "summary": "one evidence-first sentence",\n'
        '      "evidence_quotes": ["exact short candidate phrase"],\n'
        '      "rubric_alignment": "observed preschool-teacher behavior for scored trait answers, or empty string for non-scored answers",\n'
        '      "risks_or_gaps": "missing or weak evidence, or empty string"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules: each answer summary is one sentence; question_label must be the exact input question or prompt text, never a generic label like Non-scored question; "
        "evidence_quotes must be exact short phrases from candidate_transcript; "
        "if skipped is true, summarize only available evidence and do not treat missing or thin evidence as a candidate weakness; "
        "rubric_alignment names observed preschool-teacher behavior for scored trait answers and must be empty for non-scored answers; risks_or_gaps names missing or "
        "weak evidence, or empty string if none. Data: {payload_json}"
    ),
    "executive_summary_system": (
        "You are an expert hiring analyst and structured interview evaluator writing the executive summary section. "
        "Return only one JSON object. Do not reveal reasoning. "
        "Do not invent facts that are not in the transcript, scores, role context, or answer summaries."
    ),
    "executive_summary_user": (
        "Use this executive summary template and write in a professional hiring tone. "
        "Keep it concise enough for a director to scan in the first page of the interview notes.\n\n"
        "JOB TITLE:\n{track}\n\n"
        "CANDIDATE NAME:\n{candidate_name}\n\n"
        "INTERVIEW TRANSCRIPT:\n{transcript_text}\n\n"
        "QUESTION SCORES / RATINGS:\n{scoring_json}\n\n"
        "AI ANALYSIS SUMMARIES:\n{ai_analysis_summaries_json}\n\n"
        "DEEPSEEK ANSWER SUMMARIES:\n{answer_summaries_json}\n\n"
        "OPTIONAL ROLE CONTEXT OR RUBRIC:\n"
        "Tailor summary to role. For preschool/lead/infant-toddler roles emphasize child-centeredness, "
        "classroom management, warmth, safety, supervision, parent communication, reliability, "
        "developmentally appropriate practice, routines, mentoring, and compliance as applicable.\n\n"
        "Use these exact sections in executive_summary_sections and mirrored executive_summary text:\n"
        "Recommendation: Strongly Recommend, Recommend, Recommend with Reservations, "
        "Do Not Recommend, or Insufficient Information. Base this on transcript and scores.\n"
        "Overall Fit: 3 to 5 sentences.\n"
        "Role-Specific Match: 2 to 4 sentences matching evidence to role demands.\n"
        "Score Pattern: 2 to 4 sentences on highest/lowest areas, consistency, and score/transcript mismatches.\n"
        "Key Strengths: exactly 3 bullets, evidence-grounded.\n"
        "Key Concerns or Risks: exactly 3 bullets, including verification areas when evidence is weak.\n"
        "Suggested Follow-Up Questions: exactly 4 numbered questions.\n"
        "Final Hiring Notes: 1 to 2 practical closing sentences.\n\n"
        "Important Writing Rules: be specific and evidence-based; do not overstate confidence; "
        "Use only scored_questions in QUESTION SCORES / RATINGS for Key Strengths and Key Concerns or Risks; "
        "mention skipped_questions only as not evaluated, never as weakness or risk evidence; "
        "do not include long quotes; do not mention AI; do not produce a full question-by-question report.\n\n"
        "Return exactly this JSON shape. JSON output template:\n"
        "{\n"
        '  "executive_summary_sections": {\n'
        '    "recommendation": "Strongly Recommend | Recommend | Recommend with Reservations | Do Not Recommend | Insufficient Information, with candidate name when useful",\n'
        '    "overall_fit": "3 to 5 evidence-based sentences",\n'
        '    "role_specific_match": "2 to 4 evidence-based sentences",\n'
        '    "score_pattern": "2 to 4 evidence-based sentences",\n'
        '    "key_strengths": ["strength 1", "strength 2", "strength 3"],\n'
        '    "key_concerns_or_risks": ["risk 1", "risk 2", "risk 3"],\n'
        '    "suggested_follow_up_questions": [string, string, string, string],\n'
        '    "final_hiring_notes": "1 to 2 practical closing sentences"\n'
        "  },\n"
        '  "executive_summary": "same content as section text, using the exact section headings",\n'
        '  "interview_highlights": ["0 to 5 concrete evidence bullets"]\n'
        "}\n"
        "Use interview_highlights for 0-5 concrete evidence bullets."
    ),
    "trait_suggestion_system": (
        "Evaluate preschool and early childhood interview answers for advisory signal evidence. "
        "Return only one JSON object. Do not use markdown. Do not reveal reasoning. Do not echo input. "
        "Use question text, candidate answer content, job title, role context, and valid_signals. "
        "Treat rubric wording as reference context, not a grading rubric. Prioritize preschool employee "
        "readiness: emotional intelligence, empathy, warmth, respectful child language, self-regulation, "
        "co-regulation, patience, safe supervision, developmentally appropriate guidance, family communication, "
        "teamwork, coachability, accountability, reliability, flexibility, and ethical boundaries. Use only "
        "provided signal_id values. Do not invent evidence, consider interviewer raw scores, change human "
        "selections, or decide hiring/scoring. Signals are for DeepSeek advisory scoring only; do not imply "
        "interviewer checkbox selections."
    ),
    "trait_suggestion_user": (
        "Create trait_suggestions by reading each actual question and candidate_transcript, then mapping supported "
        "preschool or early childhood employee evidence to valid_signals labels. Return exactly this JSON shape. "
        "JSON output template:\n"
        "{\n"
        '  "trait_suggestions": [\n'
        "    {\n"
        '      "trait_id": "trait id from input",\n'
        '      "analysis_summary": "one short neutral summary of signal evidence",\n'
        '      "suggestions": [\n'
        "        {\n"
        '          "signal_id": "valid signal_id from input",\n'
        '          "confidence": 0.0,\n'
        '          "evidence_quote": "exact short candidate wording",\n'
        '          "rationale": "connection between evidence and signal label"\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "Evaluation priorities: start from question and answer content, not numeric rubric descriptors; weight "
        "emotional intelligence and social-emotional care heavily, including empathy, attunement, warmth, "
        "respect for children's feelings and autonomy, self-awareness under stress, co-regulation, patient "
        "guidance, and reflective ownership; also evaluate safety, supervision, developmentally appropriate "
        "practice, positive behavior guidance, family communication, teamwork, coachability, accountability, "
        "reliability, flexibility, and ethical boundaries. Rules: evidence_quote must be exact short candidate "
        "wording; rationale must connect evidence to the signal label and preschool role expectations; include "
        "automatic no-hire signal IDs only when directly supported by the transcript; do not guess from silence, "
        "but map concrete paraphrased evidence to the closest valid signal even when wording differs from rubric; "
        "confidence 0 to 1. If skipped is true, return no suggestions and say skipped/not evaluated in analysis_summary. Data: {payload_json}"
    ),
    "trait_scoring_system": (
        "You are a strict JSON scoring API. Score preschool teacher interview trait answers "
        "using rubric.json descriptors. "
        "Return only one JSON object. Do not use markdown. Do not reveal reasoning. Do not echo input. "
        "Use only candidate_transcript and rubric descriptors. Raw score must be integer 1-5 where 5 is best. "
        "Do not invent evidence, do not consider interviewer raw scores, do not replace interviewer score, "
        "and do not make hiring decisions."
    ),
    "trait_scoring_user": (
        "Create advisory trait_scores using each trait rubric descriptors and scoring_policy. Return exactly this JSON shape. "
        "JSON output template:\n"
        "{\n"
        '  "trait_scores": [\n'
        "    {\n"
        '      "trait_id": "trait id from input",\n'
        '      "raw_score": 1,\n'
        '      "evidence_quote": "exact short candidate wording",\n'
        '      "analysis_summary": "one short neutral summary of advisory scoring evidence",\n'
        '      "rationale": "descriptor match",\n'
        '      "risks_or_gaps": "missing evidence or disqualifier risk if present",\n'
        '      "risk_flag_evidence": "evidence explaining any risk flag, or empty string if no risk flag"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules: choose 1, 2, 3, 4, or 5 only; 5 is best; evidence_quote must be exact short candidate wording; "
        "analysis_summary must be short and safe to pass to the executive summary generator; "
        "rationale must cite descriptor match; risks_or_gaps names missing evidence or disqualifier risk if present; "
        "risk_flag_evidence must explain why a risk flag should display when raw_score is 1 or 2 or risks_or_gaps is non-empty. "
        "Return no keys except trait_scores. Data: {payload_json}"
    ),
}
DEFAULT_DEEPSEEK_QUESTION_PROMPT_TEMPLATES: dict[str, dict[str, str]] = {
    key: {} for key in DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS
}


def load_deepseek_prompt_templates(path: Path | None = None) -> dict[str, Any]:
    path = DEEPSEEK_PROMPTS_CONFIG_PATH if path is None else Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return normalize_deepseek_prompt_templates(raw)


def save_deepseek_prompt_templates(
    templates: Mapping[str, Any],
    path: Path | None = None,
) -> dict[str, Any]:
    path = DEEPSEEK_PROMPTS_CONFIG_PATH if path is None else Path(path)
    normalized = normalize_deepseek_prompt_templates(dict(templates))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, normalized, indent=2, ensure_ascii=False)
    return normalized


def normalize_deepseek_prompt_templates(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    output: dict[str, Any] = {
        **DEFAULT_DEEPSEEK_PROMPT_TEMPLATES,
        **{key: dict(value) for key, value in DEFAULT_DEEPSEEK_QUESTION_PROMPT_TEMPLATES.items()},
    }
    for key in DEEPSEEK_PROMPT_TEMPLATE_KEYS:
        raw = source.get(key)
        if isinstance(raw, str) and raw.strip():
            output[key] = raw
    for key in DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS:
        raw_map = source.get(key)
        if not isinstance(raw_map, dict):
            continue
        cleaned: dict[str, str] = {}
        for question_key, prompt in raw_map.items():
            clean_key = str(question_key or "").strip()
            if clean_key and isinstance(prompt, str) and prompt.strip():
                cleaned[clean_key] = prompt
        output[key] = cleaned
    return output


def format_deepseek_question_prompt_overrides(value: Any) -> str:
    overrides = normalize_deepseek_prompt_templates({"answer_summary_user_by_question": value})[
        "answer_summary_user_by_question"
    ]
    if not overrides:
        return ""
    blocks: list[str] = []
    for question_key, prompt in sorted(overrides.items()):
        blocks.append(f"Question: {question_key}\nPrompt:\n{prompt.strip()}")
    return "\n\n---\n\n".join(blocks)


def parse_deepseek_question_prompt_overrides(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(key).strip(): str(prompt).strip()
            for key, prompt in value.items()
            if str(key).strip() and str(prompt).strip()
        }
    raw_text = str(value or "").strip()
    if not raw_text:
        return {}
    if raw_text.startswith("{"):
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError("DeepSeek per-question prompt overrides must be a JSON object.")
        return parse_deepseek_question_prompt_overrides(parsed)
    output: dict[str, str] = {}
    for block in re.split(r"(?m)^\s*---\s*$", raw_text):
        lines = block.strip().splitlines()
        if not lines:
            continue
        if not lines[0].lower().startswith("question:"):
            raise ValueError("Each DeepSeek per-question prompt block must start with 'Question: <id>'.")
        question_key = lines[0].split(":", 1)[1].strip()
        prompt_lines = lines[1:]
        if prompt_lines and prompt_lines[0].strip().lower() == "prompt:":
            prompt_lines = prompt_lines[1:]
        prompt = "\n".join(prompt_lines).strip()
        if question_key and prompt:
            output[question_key] = prompt
    return output


def _render_deepseek_prompt_template(template: str, values: Mapping[str, Any]) -> str:
    rendered = str(template or "")
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def _deepseek_question_keys(item: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in ("id", "trait_id", "flow_index"):
        value = str(item.get(key) or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _deepseek_progress_label(action: str, item: Mapping[str, Any], fallback: str = "") -> str:
    raw_index = item.get("flow_index") or item.get("trait_id") or fallback
    question_part = f"Q{raw_index}" if str(raw_index or "").strip() else str(fallback or "").strip()
    raw_title = str(item.get("title") or item.get("trait_name") or "").strip()
    if not raw_title:
        raw_title = str(item.get("question") or "").strip()
    title = re.sub(r"\s+", " ", raw_title)[:80].strip()
    if question_part and title:
        return f"{action} {question_part}: {title}"
    if question_part:
        return f"{action} {question_part}"
    if title:
        return f"{action}: {title}"
    return action


def _resolve_deepseek_question_prompt(
    prompt_templates: Mapping[str, Any],
    map_key: str,
    default_key: str,
    item: Mapping[str, Any],
) -> str:
    prompt_map = prompt_templates.get(map_key, {})
    if isinstance(prompt_map, dict):
        for question_key in _deepseek_question_keys(item):
            prompt = prompt_map.get(question_key)
            if isinstance(prompt, str) and prompt.strip():
                return prompt
    return str(prompt_templates.get(default_key) or DEFAULT_DEEPSEEK_PROMPT_TEMPLATES[default_key])


def _local_deepseek_base_url(value: str | None) -> str:
    raw = str(value or "").strip().rstrip("/") or _LOCAL_DEEPSEEK_BASE_URL
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return _LOCAL_DEEPSEEK_BASE_URL
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOCAL_DEEPSEEK_HOSTS:
        return _LOCAL_DEEPSEEK_BASE_URL
    return raw


def _env_flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(value: str | None, default: float, *, minimum: float = 1.0, maximum: float = 120.0) -> float:
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def build_deepseek_summary_config(env: Mapping[str, Any] | None = None) -> DeepSeekSummaryConfig:
    source = os.environ if env is None else env
    api_key = str(source.get("DEEPSEEK_API_KEY", "") or _LOCAL_DEEPSEEK_API_KEY).strip()
    enabled = _env_flag(source.get("DEEPSEEK_SUMMARY_ENABLED"), False)
    base_url = _local_deepseek_base_url(source.get("DEEPSEEK_API_BASE_URL"))
    model = str(source.get("DEEPSEEK_SUMMARY_MODEL", "") or _LOCAL_DEEPSEEK_MODEL).strip()
    timeout = _env_float(
        source.get("DEEPSEEK_SUMMARY_TIMEOUT_SECONDS"),
        DEFAULT_DEEPSEEK_TIMEOUT_SECONDS,
        maximum=MAX_DEEPSEEK_TIMEOUT_SECONDS,
    )
    prompt_source = source.get("DEEPSEEK_PROMPT_TEMPLATES")
    prompt_templates = (
        normalize_deepseek_prompt_templates(prompt_source)
        if prompt_source
        else load_deepseek_prompt_templates()
    )
    raw_debug_log_dir = str(source.get("DEEPSEEK_DEBUG_LOG_DIR", "") or "").strip()
    return DeepSeekSummaryConfig(
        enabled=enabled and bool(api_key),
        api_key=api_key,
        base_url=base_url,
        model=model or _LOCAL_DEEPSEEK_MODEL,
        timeout_seconds=timeout,
        prompt_templates=prompt_templates,
        debug_log_dir=Path(raw_debug_log_dir) if raw_debug_log_dir else REPO_ROOT / "logs" / "deepseek_model_outputs",
    )


def _deepseek_config_source_from_app(app: Any) -> dict[str, Any]:
    source = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_SUMMARY_ENABLED": os.environ.get("DEEPSEEK_SUMMARY_ENABLED", ""),
        "DEEPSEEK_API_BASE_URL": os.environ.get("DEEPSEEK_API_BASE_URL", ""),
        "DEEPSEEK_SUMMARY_MODEL": os.environ.get("DEEPSEEK_SUMMARY_MODEL", ""),
        "DEEPSEEK_SUMMARY_TIMEOUT_SECONDS": os.environ.get("DEEPSEEK_SUMMARY_TIMEOUT_SECONDS", ""),
        "DEEPSEEK_PROMPT_TEMPLATES": {},
    }
    settings = getattr(app, "settings", {}) or {}
    if not isinstance(settings, dict):
        return source
    key_map = {
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "deepseek_summary_enabled": "DEEPSEEK_SUMMARY_ENABLED",
        "deepseek_api_base_url": "DEEPSEEK_API_BASE_URL",
        "deepseek_summary_model": "DEEPSEEK_SUMMARY_MODEL",
        "deepseek_summary_timeout_seconds": "DEEPSEEK_SUMMARY_TIMEOUT_SECONDS",
    }
    for settings_key, env_key in key_map.items():
        if settings_key in settings and not source.get(env_key):
            source[env_key] = str(settings.get(settings_key, "") or "")
    source["DEEPSEEK_PROMPT_TEMPLATES"] = load_deepseek_prompt_templates()
    return source


def _blank_deepseek_summary(status: str, warning: str) -> dict[str, Any]:
    executive_summary = DEEPSEEK_SUMMARY_FALLBACK_TEXT if str(status or "").strip().lower() == "failed" else ""
    return {
        "answer_summaries": [],
        "executive_summary": executive_summary,
        "executive_summary_sections": {},
        "interview_highlights": [],
        "summary_status": status,
        "summary_warnings": [warning] if warning else [],
    }


def _summary_transcript_items(flow_transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(flow_transcript, start=1):
        if not isinstance(item, dict):
            continue
        transcript = _normalize_transcript_text(item.get("candidate_transcript"))
        if not transcript:
            continue
        flow_index = item.get("flow_index", idx)
        items.append(
            {
                "id": str(item.get("id") or item.get("trait_id") or flow_index).strip(),
                "type": str(item.get("type") or item.get("question_type") or "").strip(),
                "trait_id": canonical_trait_id(item.get("trait_id") or item.get("id")),
                "flow_index": flow_index,
                "title": str(item.get("title") or "").strip(),
                "question": str(item.get("question") or item.get("prompt") or "").strip(),
                "prompt": str(item.get("prompt") or item.get("question") or "").strip(),
                "candidate_transcript": transcript,
                "skipped": bool(item.get("skipped", False)),
                "scored": str(item.get("type") or item.get("question_type") or "").strip() == "trait",
            }
        )
    return items


def _candidate_deepseek_context(candidate: dict[str, Any]) -> dict[str, str]:
    track = str(candidate.get("track") or candidate.get("job_title") or "").strip()
    return {
        "name": str(candidate.get("name") or "").strip(),
        "school": str(candidate.get("school") or "").strip(),
        "track": track,
        "job_title": str(candidate.get("job_title") or track).strip(),
        "role_context": _role_context_for_track(track),
        "interview_date": str(candidate.get("interview_date") or "").strip(),
    }


def _attach_deepseek_role_context_to_flow(
    flow_transcript: list[dict[str, Any]],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidate_context = _candidate_deepseek_context(dict(candidate))
    job_title = candidate_context.get("job_title") or candidate_context.get("track") or ""
    role_context = candidate_context.get("role_context") or ""
    if not job_title and not role_context:
        return flow_transcript
    for item in flow_transcript:
        if not isinstance(item, dict):
            continue
        item.setdefault("job_title", job_title)
        item.setdefault("track", candidate_context.get("track") or job_title)
        item.setdefault("role_context", role_context)
    return flow_transcript


def _role_context_for_track(track: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(track or "").strip()).lower()
    if not normalized:
        return ""
    for key, value in ROLE_CONTEXT.items():
        if normalized == key.lower():
            return value
    aliases = (
        ("assistant director", "Assistant Director"),
        ("behavior", "Behavior Support Specialist"),
        ("support specialist", "Behavior Support Specialist"),
        ("infant", "Infant/Toddler Teacher"),
        ("toddler", "Infant/Toddler Teacher"),
        ("lead", "Lead Teacher"),
        ("director", "Director"),
        ("office", "Office/Admin"),
        ("admin", "Office/Admin"),
        ("preschool", "Preschool Teacher"),
        ("teacher", "Preschool Teacher"),
    )
    for needle, key in aliases:
        if needle in normalized:
            return ROLE_CONTEXT[key]
    return ""


def _deepseek_summary_messages(items: list[dict[str, Any]], candidate: dict[str, Any]) -> list[dict[str, str]]:
    return _deepseek_answer_summary_messages(items, candidate)


def _deepseek_answer_summary_messages(
    items: list[dict[str, Any]],
    candidate: dict[str, Any],
    prompt_templates: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    candidate_context = _candidate_deepseek_context(candidate)
    payload = {"candidate": candidate_context, "answers": items}
    templates = normalize_deepseek_prompt_templates(prompt_templates)
    item = items[0] if items else {}
    system_template = _resolve_deepseek_question_prompt(
        templates,
        "answer_summary_system_by_question",
        "answer_summary_system",
        item,
    )
    user_template = _resolve_deepseek_question_prompt(
        templates,
        "answer_summary_user_by_question",
        "answer_summary_user",
        item,
    )
    return [
        {
            "role": "system",
            "content": system_template,
        },
        {
            "role": "user",
            "content": _render_deepseek_prompt_template(
                user_template,
                {
                    "payload_json": json.dumps(payload, ensure_ascii=False),
                    "candidate_json": json.dumps(candidate_context, ensure_ascii=False),
                    "answers_json": json.dumps(items, ensure_ascii=False),
                    "track": candidate_context.get("track") or "Unspecified role",
                    "job_title": candidate_context.get("job_title") or candidate_context.get("track") or "Unspecified role",
                    "role_context_or_rubric": candidate_context.get("role_context") or "No role-specific context available.",
                },
            ),
        },
    ]


def _deepseek_executive_summary_messages(
    answer_summaries: list[dict[str, Any]],
    candidate: dict[str, Any],
    scoring: dict[str, Any] | None = None,
    transcript_items: list[dict[str, Any]] | None = None,
    prompt_templates: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    candidate_context = _candidate_deepseek_context(candidate)
    templates = normalize_deepseek_prompt_templates(prompt_templates)
    return [
        {
            "role": "system",
            "content": templates["executive_summary_system"],
        },
        {
            "role": "user",
            "content": _render_deepseek_prompt_template(
                templates["executive_summary_user"],
                {
                    "track": candidate_context.get("track") or "Unspecified role",
                    "job_title": candidate_context.get("job_title") or candidate_context.get("track") or "Unspecified role",
                    "candidate_name": candidate_context.get("name") or "Candidate",
                    "candidate_json": json.dumps(candidate_context, ensure_ascii=False),
                    "transcript_text": _format_deepseek_transcript_for_prompt(transcript_items or []),
                    "scoring_json": _format_deepseek_scoring_for_prompt(scoring),
                    "ai_analysis_summaries_json": _deepseek_ai_analysis_summaries_for_prompt(scoring),
                    "answer_summaries_json": json.dumps(answer_summaries, ensure_ascii=False),
                    "role_context_or_rubric": candidate_context.get("role_context") or "No role-specific context available.",
                },
            ),
        },
    ]


def _format_deepseek_transcript_for_prompt(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        question = str(item.get("question") or item.get("title") or "").strip()
        transcript = str(item.get("candidate_transcript") or "").strip()
        if not transcript:
            continue
        label = f"Q{item.get('flow_index')}: {question}".strip()
        lines.append(f"{label}\n{transcript}")
    return "\n\n".join(lines) or "No transcript provided."


def _format_deepseek_scoring_for_prompt(scoring: dict[str, Any] | None) -> str:
    if not isinstance(scoring, dict) or not scoring:
        return "No scoring provided."
    summary = {
        "outcome": scoring.get("outcome"),
        "percent_of_max": scoring.get("percent_of_max"),
        "weighted_total": scoring.get("weighted_total"),
        "max_weighted_total": scoring.get("max_weighted_total"),
    }
    scored_questions: list[dict[str, Any]] = []
    skipped_questions: list[dict[str, Any]] = []
    ai_analysis_summaries: list[dict[str, Any]] = []
    for row in scoring.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        trait_label = row.get("name") or row.get("trait_name") or row.get("trait_id") or row.get("id")
        trait_id = row.get("trait_id") or row.get("id")
        if row.get("skipped", False) or row.get("raw_score") is None:
            skipped_questions.append({"trait": trait_label, "trait_id": trait_id, "status": "skipped_not_evaluated"})
            continue
        model_trait_score = row.get("model_trait_score") if isinstance(row.get("model_trait_score"), dict) else {}
        signal_analysis = str(row.get("model_signal_analysis_summary") or "").strip()
        advisory_analysis = str(model_trait_score.get("analysis_summary") or "").strip()
        scored_questions.append(
            {
                "trait": trait_label,
                "trait_id": trait_id,
                "interviewer_raw_score": row.get("raw_score"),
                "interviewer_weighted_score": row.get("calculated_score") or row.get("weighted_score"),
                "deepseek_raw_score": row.get("deepseek_raw_score"),
                "deepseek_weighted_score": row.get("deepseek_calculated_score"),
                "deepseek_rationale": model_trait_score.get("rationale"),
                "signal_analysis_summary": signal_analysis,
                "advisory_analysis_summary": advisory_analysis,
            }
        )
        if signal_analysis or advisory_analysis:
            ai_analysis_summaries.append(
                {
                    "trait": trait_label,
                    "trait_id": trait_id,
                    "signal_analysis_summary": signal_analysis,
                    "advisory_analysis_summary": advisory_analysis,
                }
            )
    return json.dumps(
        {
            "summary": summary,
            "scored_questions": scored_questions,
            "skipped_questions": skipped_questions,
            "ai_analysis_summaries": ai_analysis_summaries,
            "instructions": "Use only scored_questions for key_strengths and key_concerns_or_risks. Skipped questions are not negative evidence.",
        },
        ensure_ascii=False,
    )


def _deepseek_ai_analysis_summaries_for_prompt(scoring: dict[str, Any] | None) -> str:
    if not isinstance(scoring, dict):
        return "[]"
    try:
        payload = json.loads(_format_deepseek_scoring_for_prompt(scoring))
    except (TypeError, json.JSONDecodeError):
        return "[]"
    return json.dumps(payload.get("ai_analysis_summaries", []), ensure_ascii=False)


def _request_deepseek_chat_completion(config: DeepSeekSummaryConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    parsed_base_url = urllib.parse.urlparse(config.base_url)
    if parsed_base_url.hostname in _LOCAL_DEEPSEEK_HOSTS:
        return _request_local_ollama_json_completion(config, messages)

    body = {
        "model": config.model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "format": "json",
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    request = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_local_ollama_json_completion(config: DeepSeekSummaryConfig, messages: list[dict[str, str]]) -> dict[str, Any]:
    parsed_base_url = urllib.parse.urlparse(config.base_url)
    host = parsed_base_url.netloc or "127.0.0.1:11434"
    scheme = parsed_base_url.scheme or "http"
    prompt_parts = []
    for message in messages:
        role = str(message.get("role") or "user").strip().upper()
        content = str(message.get("content") or "").strip()
        if content:
            prompt_parts.append(f"{role}:\n{content}")
    prompt_parts.append("ASSISTANT:\nReturn exactly one valid JSON object. No markdown.")
    body = {
        "model": config.model,
        "prompt": "\n\n".join(prompt_parts),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": 32768,
            "num_predict": 4096,
        },
    }
    request = urllib.request.Request(
        f"{scheme}://{host}/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    content = str(parsed.get("response") or "").strip() if isinstance(parsed, dict) else ""
    return {"choices": [{"message": {"content": content}}]}


def _extract_deepseek_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "").strip()


def _request_deepseek_content(
    config: DeepSeekSummaryConfig,
    messages: list[dict[str, str]],
    chat_completion: Optional[Callable[[DeepSeekSummaryConfig, list[dict[str, str]]], dict[str, Any]]] = None,
) -> str:
    completion = chat_completion(config, messages) if chat_completion else _request_deepseek_chat_completion(config, messages)
    return _extract_deepseek_content(completion)


def _deepseek_debug_log_path(config: DeepSeekSummaryConfig) -> Path | None:
    if config.debug_log_dir is None:
        return None
    return Path(config.debug_log_dir) / f"deepseek-model-output-{datetime.now(UTC).date().isoformat()}.jsonl"


def _write_deepseek_debug_log(
    config: DeepSeekSummaryConfig,
    *,
    prompt_name: str,
    model_response: str,
    parse_success: bool,
    validation_errors: list[str],
    messages: list[dict[str, str]] | None = None,
    normalized_output: dict[str, Any] | list[dict[str, Any]] | None = None,
    step_label: str = "",
    candidate_name: str = "",
    job_title: str = "",
) -> None:
    prompt_messages = list(messages or [])
    event = {
        "timestamp": _utc_timestamp(),
        "prompt_name": str(prompt_name or "").strip(),
        "stage": str(step_label or prompt_name or "").strip(),
        "candidate_name": str(candidate_name or "").strip(),
        "job_title": str(job_title or "").strip(),
        "model": str(config.model or "").strip(),
        "base_url": str(config.base_url or "").strip(),
        "prompt_text": _deepseek_trace_prompt_text(prompt_messages),
        "prompt_messages": prompt_messages,
        "model_response": str(model_response or ""),
        "parse_success": bool(parse_success),
        "validation_errors": [str(error) for error in validation_errors],
        "normalized_output": normalized_output,
    }
    config.trace_events.append(event)
    log_path = _deepseek_debug_log_path(config)
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    except OSError:
        logger.warning("DeepSeek debug output log write failed: OSError")


def _deepseek_trace_prompt_text(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role or content:
            parts.append(f"{role}:\n{content}".strip())
    return "\n\n".join(parts)


def _normalize_deepseek_completion_until_valid(
    config: DeepSeekSummaryConfig,
    messages: list[dict[str, str]],
    normalizer: Callable[[str], dict[str, Any] | list[dict[str, Any]]],
    *,
    chat_completion: Optional[Callable[[DeepSeekSummaryConfig, list[dict[str, str]]], dict[str, Any]]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    step_label: str = "DeepSeek prompt",
    max_attempts: int = 2,
    prompt_name: str = "",
    candidate_name: str = "",
    job_title: str = "",
) -> dict[str, Any] | list[dict[str, Any]]:
    attempts = max(1, int(max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        active_messages = messages
        if attempt > 1:
            active_messages = [*messages, {"role": "user", "content": DEEPSEEK_REPAIR_PROMPT}]
        try:
            content = _request_deepseek_content(config, active_messages, chat_completion)
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            _write_deepseek_debug_log(
                config,
                prompt_name=prompt_name or step_label,
                model_response="",
                parse_success=False,
                validation_errors=[type(exc).__name__],
                messages=active_messages,
                step_label=step_label,
                candidate_name=candidate_name,
                job_title=job_title,
            )
            raise
        try:
            normalized = normalizer(content)
            _write_deepseek_debug_log(
                config,
                prompt_name=prompt_name or step_label,
                model_response=content,
                parse_success=True,
                validation_errors=[],
                messages=active_messages,
                normalized_output=normalized,
                step_label=step_label,
                candidate_name=candidate_name,
                job_title=job_title,
            )
            return normalized
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            _write_deepseek_debug_log(
                config,
                prompt_name=prompt_name or step_label,
                model_response=content,
                parse_success=False,
                validation_errors=[str(exc)],
                messages=active_messages,
                step_label=step_label,
                candidate_name=candidate_name,
                job_title=job_title,
            )
            logger.warning("DeepSeek response normalization failed; retrying %s: %s", step_label, type(exc).__name__)
            if progress_callback and attempt < attempts:
                progress_callback(f"{step_label} returned invalid JSON; retrying")
    raise ValueError(f"{step_label} returned invalid JSON after {attempts} attempts") from last_error


def _strip_deepseek_reasoning_wrappers(content: str) -> str:
    text = str(content or "").strip()
    return re.sub(r"(?is)<think>.*?</think>", "", text).strip()


def _first_json_object_text(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    raise json.JSONDecodeError("Unterminated JSON object", text, start)


def _load_deepseek_json_object(content: str) -> dict[str, Any]:
    text = _strip_deepseek_reasoning_wrappers(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
        last_error: json.JSONDecodeError | None = None
        for match in re.finditer(r"\{", text):
            try:
                parsed = json.loads(_first_json_object_text(text[match.start() :]))
                break
            except json.JSONDecodeError as exc:
                last_error = exc
        if parsed is None:
            raise last_error or json.JSONDecodeError("No JSON object found", text, 0)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek response was not a JSON object.")
    return parsed


def _clamp_deepseek_text(value: Any, field_name: str) -> str:
    return str(value or "").strip()


def _normalize_deepseek_confidence(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(parsed, 0.0), 1.0)


def _quote_supported_by_transcript(quote: str, transcript_text: str | None) -> bool:
    text = str(transcript_text or "").strip()
    if not text:
        return True
    return str(quote or "").strip() in text


def _exact_supported_quotes(quotes: Any, transcript_text: str | None, *, limit: int = 3) -> list[str]:
    if not isinstance(quotes, list):
        return []
    output: list[str] = []
    for quote in quotes:
        cleaned = _clamp_deepseek_text(quote, "evidence_quote")
        if cleaned and _quote_supported_by_transcript(cleaned, transcript_text) and cleaned not in output:
            output.append(cleaned)
        if len(output) >= limit:
            break
    return output


def _normalize_deepseek_answer_summary_payload(
    content: str,
    valid_flow_indices: set[Any],
    transcript_text: str | None = None,
) -> list[dict[str, Any]]:
    parsed = _load_deepseek_json_object(content)
    if "answer_summaries" not in parsed:
        raise ValueError("DeepSeek answer summary response missing required fields.")
    answer_summaries: list[dict[str, Any]] = []
    seen_flow_indices: set[Any] = set()
    valid_by_text = {str(flow_index): flow_index for flow_index in valid_flow_indices}
    raw_summaries = parsed.get("answer_summaries", [])
    if not isinstance(raw_summaries, list):
        raise ValueError("DeepSeek answer summary response answer_summaries was not a list.")
    if isinstance(raw_summaries, list):
        for item in raw_summaries:
            if not isinstance(item, dict):
                continue
            flow_index = valid_by_text.get(str(item.get("flow_index")), item.get("flow_index"))
            summary = _clamp_deepseek_text(item.get("summary"), "summary")
            if flow_index not in valid_flow_indices or flow_index in seen_flow_indices or not summary:
                continue
            seen_flow_indices.add(flow_index)
            question_id = _clamp_deepseek_text(item.get("question_id"), "question_id")
            question_label = _clamp_deepseek_text(item.get("question_label"), "question_label")
            normalized_item = {
                "flow_index": flow_index,
                "summary": summary,
                "evidence_quotes": _exact_supported_quotes(item.get("evidence_quotes", []), transcript_text),
                "rubric_alignment": _clamp_deepseek_text(item.get("rubric_alignment"), "rubric_alignment"),
                "risks_or_gaps": _clamp_deepseek_text(item.get("risks_or_gaps"), "risks_or_gaps"),
            }
            if question_id:
                normalized_item["question_id"] = question_id
            if question_label:
                normalized_item["question_label"] = question_label
            confidence = _normalize_deepseek_confidence(item.get("confidence"))
            if confidence is not None:
                normalized_item["confidence"] = confidence
            answer_summaries.append(
                normalized_item
            )
    return answer_summaries


def _normalize_deepseek_executive_summary_payload(content: str) -> dict[str, Any]:
    parsed = _load_deepseek_json_object(content)
    if "executive_summary_sections" not in parsed:
        raise ValueError("DeepSeek executive summary response missing required fields.")
    sections = _normalize_deepseek_executive_summary_sections(parsed.get("executive_summary_sections"))
    executive_summary = _clamp_deepseek_text(parsed.get("executive_summary"), "summary")
    if sections and not executive_summary:
        executive_summary = _executive_summary_sections_to_text(sections)
    interview_highlights: list[str] = []
    raw_highlights = parsed.get("interview_highlights", [])
    if not isinstance(raw_highlights, list):
        raise ValueError("DeepSeek executive summary interview_highlights was not a list.")
    for item in raw_highlights:
        highlight = _clamp_deepseek_text(item, "summary")
        if highlight:
            interview_highlights.append(highlight)
    output = {
        "executive_summary": executive_summary,
        "executive_summary_sections": sections,
        "interview_highlights": interview_highlights[:5],
    }
    confidence = _normalize_deepseek_confidence(parsed.get("confidence"))
    if confidence is not None:
        output["confidence"] = confidence
    return output


def _normalize_deepseek_executive_summary_sections(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scalar_keys = ("overall_fit", "role_specific_match", "score_pattern", "final_hiring_notes")
    list_keys = ("key_strengths", "key_concerns_or_risks", "suggested_follow_up_questions")
    output: dict[str, Any] = {}
    recommendation = value.get("recommendation")
    if isinstance(recommendation, dict):
        rating = _clamp_deepseek_text(recommendation.get("rating"), "summary")
        rationale = _clamp_deepseek_text(recommendation.get("rationale"), "summary")
        if rating or rationale:
            output["recommendation"] = {"rating": rating, "rationale": rationale}
    else:
        text = _clamp_deepseek_text(recommendation, "summary")
        if text:
            output["recommendation"] = text
    for key in scalar_keys:
        text = _clamp_deepseek_text(value.get(key), "summary")
        if text:
            output[key] = text
    for key in list_keys:
        raw_items = value.get(key, [])
        if not isinstance(raw_items, list):
            continue
        limit = 4 if key == "suggested_follow_up_questions" else 3
        items = [_normalize_deepseek_section_list_item(item, key) for item in raw_items]
        items = [item for item in items if item][:limit]
        if items:
            output[key] = items
    return output


def _normalize_deepseek_section_list_item(item: Any, key: str) -> str:
    if not isinstance(item, dict):
        return _clamp_deepseek_text(item, "summary")
    if key == "key_strengths":
        title = _clamp_deepseek_text(item.get("strength"), "summary")
        evidence = _clamp_deepseek_text(item.get("evidence"), "summary")
    elif key == "key_concerns_or_risks":
        title = _clamp_deepseek_text(item.get("concern"), "summary")
        evidence = _clamp_deepseek_text(item.get("evidence_or_gap"), "summary")
    else:
        return _clamp_deepseek_text(item, "summary")
    if title and evidence:
        return f"{title}: {evidence}"
    return title or evidence


def _executive_summary_sections_to_text(sections: dict[str, Any]) -> str:
    lines: list[str] = []
    scalar_map = [
        ("recommendation", "Recommendation"),
        ("overall_fit", "Overall Fit"),
        ("role_specific_match", "Role-Specific Match"),
        ("score_pattern", "Score Pattern"),
    ]
    for key, label in scalar_map:
        raw_value = sections.get(key)
        if key == "recommendation" and isinstance(raw_value, dict):
            rating = str(raw_value.get("rating") or "").strip()
            rationale = str(raw_value.get("rationale") or "").strip()
            value = f"{rating}: {rationale}".strip(": ")
        else:
            value = str(raw_value or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    list_map = [
        ("key_strengths", "Key Strengths"),
        ("key_concerns_or_risks", "Key Concerns or Risks"),
    ]
    for key, label in list_map:
        values = [str(item or "").strip() for item in sections.get(key, []) or [] if str(item or "").strip()]
        if values:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in values)
    questions = [str(item or "").strip() for item in sections.get("suggested_follow_up_questions", []) or [] if str(item or "").strip()]
    if questions:
        lines.append("Suggested Follow-Up Questions:")
        lines.extend(f"{index}. {item}" for index, item in enumerate(questions, start=1))
    final_notes = str(sections.get("final_hiring_notes") or "").strip()
    if final_notes:
        lines.append(f"Final Hiring Notes: {final_notes}")
    return "\n".join(lines)


def _normalize_deepseek_summary_payload(content: str, valid_flow_indices: set[Any]) -> dict[str, Any]:
    answer_summaries = _normalize_deepseek_answer_summary_payload(content, valid_flow_indices)
    executive = {"executive_summary": "", "interview_highlights": []}
    has_summary_output = bool(answer_summaries)
    return {
        "answer_summaries": answer_summaries,
        "executive_summary": executive["executive_summary"],
        "executive_summary_sections": {},
        "interview_highlights": executive["interview_highlights"],
        "summary_status": "generated" if has_summary_output else "failed",
        "summary_warnings": [] if has_summary_output else ["DeepSeek summary response was empty."],
    }


def generate_deepseek_interview_summaries(
    flow_transcript: list[dict[str, Any]],
    candidate: dict[str, Any],
    config: DeepSeekSummaryConfig | None = None,
    chat_completion: Optional[Callable[[DeepSeekSummaryConfig, list[dict[str, str]]], dict[str, Any]]] = None,
    scoring: dict[str, Any] | None = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    active_config = config or build_deepseek_summary_config()
    if not active_config.enabled:
        return _blank_deepseek_summary("disabled", "Local DeepSeek summary is disabled.")

    items = _summary_transcript_items(flow_transcript)
    if not items:
        return _blank_deepseek_summary("no_transcript", "No candidate transcript available for DeepSeek summary.")

    answer_summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    first_error_type = ""
    candidate_context = _candidate_deepseek_context(candidate)
    candidate_name = candidate_context.get("name", "")
    job_title = candidate_context.get("job_title") or candidate_context.get("track", "")
    try:
        for item in items:
            try:
                step_label = _deepseek_progress_label("Summarizing", item)
                if progress_callback:
                    progress_callback(step_label)
                answer_messages = _deepseek_answer_summary_messages([item], candidate, active_config.prompt_templates)
                answer_result = _normalize_deepseek_completion_until_valid(
                    active_config,
                    answer_messages,
                    lambda content: _normalize_deepseek_answer_summary_payload(
                        content,
                        {item["flow_index"]},
                        str(item.get("candidate_transcript") or ""),
                    ),
                    chat_completion=chat_completion,
                    progress_callback=progress_callback,
                    step_label=step_label,
                    prompt_name="answer_summary",
                    candidate_name=candidate_name,
                    job_title=job_title,
                )
                answer_summaries.extend(answer_result)
            except Exception as exc:
                if not first_error_type:
                    first_error_type = type(exc).__name__
                warnings.append(f"DeepSeek answer summary failed: {type(exc).__name__}")
                logger.warning("DeepSeek answer summary generation failed: %s", type(exc).__name__)
        if not answer_summaries:
            return _blank_deepseek_summary("failed", f"DeepSeek summary failed: {first_error_type or 'ValueError'}")
        if progress_callback:
            progress_callback("Generating Executive Summary")
        executive_messages = _deepseek_executive_summary_messages(answer_summaries, candidate, scoring, items, active_config.prompt_templates)
        executive = _normalize_deepseek_completion_until_valid(
            active_config,
            executive_messages,
            _normalize_deepseek_executive_summary_payload,
            chat_completion=chat_completion,
            progress_callback=progress_callback,
            step_label="Generating Executive Summary",
            prompt_name="executive_summary",
            candidate_name=candidate_name,
            job_title=job_title,
        )
        has_summary_output = bool(executive["executive_summary"] or executive["interview_highlights"] or answer_summaries)
        return {
            "answer_summaries": answer_summaries,
            "executive_summary": executive["executive_summary"],
            "executive_summary_sections": executive.get("executive_summary_sections", {}),
            "interview_highlights": executive["interview_highlights"],
            "confidence": executive.get("confidence"),
            "summary_status": "generated" if has_summary_output else "failed",
            "summary_warnings": warnings if has_summary_output else ["DeepSeek summary response was empty."],
        }
    except Exception as exc:
        logger.warning("DeepSeek summary generation failed: %s", type(exc).__name__)
        if answer_summaries:
            return {
                "answer_summaries": answer_summaries,
                "executive_summary": DEEPSEEK_SUMMARY_FALLBACK_TEXT,
                "executive_summary_sections": {},
                "interview_highlights": [],
                "confidence": None,
                "summary_status": "generated",
                "summary_warnings": [*warnings, f"DeepSeek executive summary failed: {type(exc).__name__}"],
            }
        return _blank_deepseek_summary("failed", f"DeepSeek summary failed: {type(exc).__name__}")


def _rubric_trait_context(rubric: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(rubric, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for trait in rubric.get("traits", []) or []:
        if not isinstance(trait, dict):
            continue
        trait_id = canonical_trait_id(trait.get("id"))
        if not trait_id:
            continue
        output[trait_id] = {
            "trait_id": trait_id,
            "name": str(trait.get("name") or "").strip(),
            "priority": str(trait.get("priority") or "").strip(),
            "weight": trait.get("weight"),
            "primary_question": str(trait.get("primary_question") or "").strip(),
            "descriptors": trait.get("descriptors", {}) if isinstance(trait.get("descriptors"), dict) else {},
        }
    return output


@lru_cache(maxsize=1)
def _trait_based_scoring_json_context() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    try:
        runtime_bundle = load_module_contract_runtime_bundle(
            engine_module_contract_path=DEFAULT_ENGINE_MODULE_CONTRACT,
            engine_runtime_contract_path=DEFAULT_ENGINE_RUNTIME_CONTRACT,
        )
    except Exception:
        runtime_bundle = {}
    for payload in runtime_bundle.get("trait_definitions", []) or []:
        if not isinstance(payload, dict):
            continue
        trait_id = canonical_trait_id(payload.get("trait_id") or payload.get("id"))
        if trait_id:
            output[trait_id] = payload
    return output


def _trait_suggestion_items(
    flow_transcript: list[dict[str, Any]],
    rubric: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    items: list[dict[str, Any]] = []
    valid_ids_by_trait: dict[str, list[str]] = {}
    rubric_by_trait = _rubric_trait_context(rubric)
    trait_based_scoring_by_trait = _trait_based_scoring_json_context()
    for idx, item in enumerate(flow_transcript, start=1):
        if not isinstance(item, dict) or str(item.get("type") or "").strip() != "trait":
            continue
        trait_id = canonical_trait_id(item.get("trait_id") or item.get("id"))
        transcript = _normalize_transcript_text(item.get("candidate_transcript"))
        if not trait_id or not transcript or bool(item.get("skipped", False)):
            continue
        try:
            signal_definition = load_trait_signal_ui_definition(trait_id)
        except Exception:
            continue
        valid_signal_ids = list(signal_definition.get("valid_signal_ids", []) or [])
        if not valid_signal_ids:
            continue
        valid_ids_by_trait[trait_id] = valid_signal_ids
        signals = []
        for signal in list(signal_definition.get("core_signals", []) or []):
            signals.append(
                {
                    "signal_id": signal.get("signal_id"),
                    "label": signal.get("label"),
                    "group": "core",
                    "weight": signal.get("weight"),
                }
            )
        for group in signal_definition.get("extended_groups", []) or []:
            for signal in group.get("signals", []) or []:
                signals.append(
                    {
                        "signal_id": signal.get("signal_id"),
                        "label": signal.get("label"),
                        "group": group.get("group_label") or "extended",
                        "weight": signal.get("weight"),
                    }
                )
        items.append(
            {
                "trait_id": trait_id,
                "flow_index": item.get("flow_index", idx),
                "title": str(item.get("title") or "").strip(),
                "question": str(item.get("question") or "").strip(),
                "candidate_transcript": transcript,
                "skipped": bool(item.get("skipped", False)),
                "job_title": str(item.get("job_title") or item.get("track") or "").strip(),
                "role_context": _role_context_for_track(item.get("job_title") or item.get("track")),
                "valid_signals": signals,
                "rubric": rubric_by_trait.get(trait_id, {}),
                "trait_based_scoring_json": trait_based_scoring_by_trait.get(trait_id, {}),
            }
        )
    return items, valid_ids_by_trait


def _deepseek_trait_suggestion_messages(
    items: list[dict[str, Any]],
    prompt_templates: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    templates = normalize_deepseek_prompt_templates(prompt_templates)
    payload = {"job_title": _job_title_from_trait_items(items), "role_context": _role_context_from_trait_items(items), "traits": items}
    item = items[0] if items else {}
    system_template = _resolve_deepseek_question_prompt(
        templates,
        "trait_suggestion_system_by_question",
        "trait_suggestion_system",
        item,
    )
    user_template = _resolve_deepseek_question_prompt(
        templates,
        "trait_suggestion_user_by_question",
        "trait_suggestion_user",
        item,
    )
    return [
        {
            "role": "system",
            "content": system_template,
        },
        {
            "role": "user",
            "content": _render_deepseek_prompt_template(
                user_template,
                {
                    "payload_json": json.dumps(payload, ensure_ascii=False),
                    "traits_json": json.dumps(items, ensure_ascii=False),
                    "track": payload["job_title"] or "Unspecified role",
                    "job_title": payload["job_title"] or "Unspecified role",
                    "role_context_or_rubric": payload["role_context"] or "No role-specific context available.",
                },
            ),
        },
    ]


def _deepseek_trait_scoring_messages(
    items: list[dict[str, Any]],
    rubric: dict[str, Any] | None,
    prompt_templates: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    scoring_policy = {}
    if isinstance(rubric, dict):
        scoring_policy = {
            "scoring": rubric.get("scoring", {}),
            "absolute_disqualifiers": rubric.get("absolute_disqualifiers", []),
            "interviewer_guidance": rubric.get("interviewer_guidance", {}),
        }
    payload = {
        "job_title": _job_title_from_trait_items(items),
        "role_context": _role_context_from_trait_items(items),
        "scoring_policy": scoring_policy,
        "traits": items,
    }
    templates = normalize_deepseek_prompt_templates(prompt_templates)
    item = items[0] if items else {}
    required_trait_id = str(item.get("trait_id") or "").strip()
    system_template = _resolve_deepseek_question_prompt(
        templates,
        "trait_scoring_system_by_question",
        "trait_scoring_system",
        item,
    )
    user_template = _resolve_deepseek_question_prompt(
        templates,
        "trait_scoring_user_by_question",
        "trait_scoring_user",
        item,
    )
    return [
        {
            "role": "system",
            "content": system_template,
        },
        {
            "role": "user",
            "content": _render_deepseek_prompt_template(
                user_template,
                {
                    "payload_json": json.dumps(payload, ensure_ascii=False),
                    "scoring_policy_json": json.dumps(scoring_policy, ensure_ascii=False),
                    "traits_json": json.dumps(items, ensure_ascii=False),
                    "track": payload["job_title"] or "Unspecified role",
                    "job_title": payload["job_title"] or "Unspecified role",
                    "role_context_or_rubric": payload["role_context"] or "No role-specific context available.",
                },
            ),
        },
        {
            "role": "user",
            "content": (
                f"Required current trait_id: {required_trait_id}. "
                "Return exactly one score for this trait_id. "
                "Do not return placeholder values such as 'from input' or 'exact short candidate wording'."
            ),
        },
    ]


def _job_title_from_trait_items(items: list[dict[str, Any]]) -> str:
    for item in items:
        value = str(item.get("job_title") or item.get("track") or "").strip()
        if value:
            return value
    return ""


def _role_context_from_trait_items(items: list[dict[str, Any]]) -> str:
    job_title = _job_title_from_trait_items(items)
    return _role_context_for_track(job_title)


def _blank_deepseek_trait_suggestions(status: str, warning: str) -> dict[str, Any]:
    return {
        "model_signal_suggestions_by_trait": {},
        "model_signal_analysis_by_trait": {},
        "model_suggestion_status": status,
        "model_suggestion_warnings": [warning] if warning else [],
        "model_trait_scores_by_trait": {},
        "model_scoring_status": status,
        "model_scoring_warnings": [warning] if warning else [],
    }


def _normalize_deepseek_trait_suggestion_payload(
    content: str,
    valid_ids_by_trait: dict[str, list[str]],
    transcript_by_trait: dict[str, str] | None = None,
) -> dict[str, Any]:
    parsed = _load_deepseek_json_object(content)
    if "trait_suggestions" not in parsed or not isinstance(parsed.get("trait_suggestions"), list):
        raise ValueError("DeepSeek trait suggestion response missing required fields.")
    output: dict[str, list[dict[str, Any]]] = {}
    analysis_by_trait: dict[str, str] = {}
    for item in parsed.get("trait_suggestions", []) or []:
        if not isinstance(item, dict):
            continue
        trait_id = canonical_trait_id(item.get("trait_id"))
        valid_ids = valid_ids_by_trait.get(trait_id, [])
        suggestions = normalize_model_signal_suggestions(_dedupe_deepseek_suggestions(item.get("suggestions", [])), valid_ids)
        transcript_text = (transcript_by_trait or {}).get(trait_id, "")
        if transcript_text:
            suggestions = [
                suggestion
                for suggestion in suggestions
                if not suggestion.get("evidence_quote")
                or _quote_supported_by_transcript(str(suggestion.get("evidence_quote") or ""), transcript_text)
            ]
        if trait_id:
            output[trait_id] = suggestions
            analysis_summary = _clamp_deepseek_text(item.get("analysis_summary"), "rationale")
            if analysis_summary:
                analysis_by_trait[trait_id] = analysis_summary
    return {
        "model_signal_suggestions_by_trait": output,
        "model_signal_analysis_by_trait": analysis_by_trait,
        "model_suggestion_status": "generated" if output else "failed",
        "model_suggestion_warnings": [] if output else ["DeepSeek trait suggestion response was empty."],
    }


def _normalize_deepseek_trait_score_payload(
    content: str,
    valid_traits: set[str],
    transcript_by_trait: dict[str, str] | None = None,
) -> dict[str, Any]:
    parsed = _load_deepseek_json_object(content)
    if "trait_scores" not in parsed or not isinstance(parsed.get("trait_scores"), list):
        raise ValueError("DeepSeek trait scoring response missing required fields.")
    output: dict[str, dict[str, Any]] = {}
    for item in parsed.get("trait_scores", []) or []:
        if not isinstance(item, dict):
            continue
        trait_id = canonical_trait_id(item.get("trait_id"))
        if trait_id not in valid_traits:
            continue
        try:
            raw_score = int(item.get("raw_score"))
        except (TypeError, ValueError):
            continue
        if raw_score not in {1, 2, 3, 4, 5}:
            continue
        evidence_quote = _clamp_deepseek_text(item.get("evidence_quote"), "evidence_quote")
        if evidence_quote and not _quote_supported_by_transcript(evidence_quote, (transcript_by_trait or {}).get(trait_id, "")):
            evidence_quote = ""
        normalized_score = {
            "raw_score": raw_score,
            "evidence_quote": evidence_quote,
            "analysis_summary": _clamp_deepseek_text(item.get("analysis_summary"), "rationale"),
            "rationale": _clamp_deepseek_text(item.get("rationale"), "rationale"),
            "risks_or_gaps": _clamp_deepseek_text(item.get("risks_or_gaps"), "risks_or_gaps"),
            "risk_flag_evidence": _clamp_deepseek_text(item.get("risk_flag_evidence"), "rationale"),
        }
        confidence = _normalize_deepseek_confidence(item.get("confidence"))
        if confidence is not None:
            normalized_score["confidence"] = confidence
        output[trait_id] = normalized_score
    if not output:
        raise ValueError("DeepSeek trait scoring response did not include the requested trait.")
    return {
        "model_trait_scores_by_trait": output,
        "model_scoring_status": "generated",
        "model_scoring_warnings": [],
    }


def _deepseek_generation_status(output: dict[str, Any], attempted_count: int) -> str:
    if not output:
        return "failed"
    if len(output) < attempted_count:
        return "partial"
    return "generated"


def _dedupe_deepseek_suggestions(suggestions: Any) -> list[dict[str, Any]]:
    if not isinstance(suggestions, list):
        return []
    best_by_signal: dict[str, dict[str, Any]] = {}
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        signal_id = str(item.get("signal_id") or item.get("id") or item.get("ref") or "").strip()
        if not signal_id:
            continue
        candidate = dict(item)
        candidate["evidence_quote"] = _clamp_deepseek_text(candidate.get("evidence_quote"), "evidence_quote")
        candidate["rationale"] = _clamp_deepseek_text(candidate.get("rationale"), "rationale")
        existing = best_by_signal.get(signal_id)
        if existing is None or float(candidate.get("confidence") or 0) > float(existing.get("confidence") or 0):
            best_by_signal[signal_id] = candidate
    return list(best_by_signal.values())


def generate_deepseek_trait_signal_suggestions(
    flow_transcript: list[dict[str, Any]],
    trait_state: dict[str, dict[str, Any]],
    rubric: dict[str, Any] | None = None,
    config: DeepSeekSummaryConfig | None = None,
    chat_completion: Optional[Callable[[DeepSeekSummaryConfig, list[dict[str, str]]], dict[str, Any]]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    active_config = config or build_deepseek_summary_config()
    if not active_config.enabled:
        return _blank_deepseek_trait_suggestions(
            "disabled",
            "Local DeepSeek trait suggestions are disabled.",
        )
    items, valid_ids_by_trait = _trait_suggestion_items(flow_transcript, rubric)
    items = [
        item
        for item in items
        if not bool(trait_state.get(str(item.get("trait_id") or "").strip(), {}).get("skipped", False))
    ]
    valid_ids_by_trait = {
        str(item.get("trait_id") or "").strip(): valid_ids_by_trait.get(str(item.get("trait_id") or "").strip(), [])
        for item in items
        if str(item.get("trait_id") or "").strip()
    }
    if not items:
        return _blank_deepseek_trait_suggestions(
            "no_transcript",
            "No trait transcript with runtime signal definitions available for DeepSeek suggestions.",
        )

    suggestions_by_trait: dict[str, list[dict[str, Any]]] = {}
    signal_analysis_by_trait: dict[str, str] = {}
    suggestion_warnings: list[str] = []
    transcript_by_trait = {
        str(item.get("trait_id") or "").strip(): str(item.get("candidate_transcript") or "")
        for item in items
        if str(item.get("trait_id") or "").strip()
    }
    for item in items:
        trait_id = str(item.get("trait_id") or "").strip()
        step_label = _deepseek_progress_label("Analyzing Traits", item, trait_id)
        if progress_callback:
            progress_callback(step_label)
        messages = _deepseek_trait_suggestion_messages([item], active_config.prompt_templates)
        try:
            result = _normalize_deepseek_completion_until_valid(
                active_config,
                messages,
                lambda content: _normalize_deepseek_trait_suggestion_payload(
                    content,
                    {trait_id: valid_ids_by_trait.get(trait_id, [])},
                    transcript_by_trait,
                ),
                chat_completion=chat_completion,
                progress_callback=progress_callback,
                step_label=step_label,
                prompt_name="trait_suggestion",
                job_title=_job_title_from_trait_items([item]),
            )
            suggestions_by_trait.update(result["model_signal_suggestions_by_trait"])
            signal_analysis_by_trait.update(result.get("model_signal_analysis_by_trait", {}))
        except Exception as exc:
            logger.warning("DeepSeek trait suggestion generation failed: %s", type(exc).__name__)
            suggestion_warnings.append(f"DeepSeek trait suggestions failed: {type(exc).__name__}")

    for trait_id, suggestions in suggestions_by_trait.items():
        state = trait_state.setdefault(trait_id, {})
        write_canonical_model_signal_suggestions(state, suggestions)
        if signal_analysis_by_trait.get(trait_id):
            state["model_signal_analysis_summary"] = signal_analysis_by_trait[trait_id]

    scores_by_trait: dict[str, dict[str, Any]] = {}
    scoring_warnings: list[str] = []
    for item in items:
        trait_id = str(item.get("trait_id") or "").strip()
        step_label = _deepseek_progress_label("Scoring", item, trait_id)
        try:
            if progress_callback:
                progress_callback(step_label)
            score_messages = _deepseek_trait_scoring_messages([item], rubric, active_config.prompt_templates)
            score_result = _normalize_deepseek_completion_until_valid(
                active_config,
                score_messages,
                lambda content: _normalize_deepseek_trait_score_payload(content, {trait_id}, transcript_by_trait),
                chat_completion=chat_completion,
                progress_callback=progress_callback,
                step_label=step_label,
                prompt_name="trait_scoring",
                job_title=_job_title_from_trait_items([item]),
            )
            scores_by_trait.update(score_result["model_trait_scores_by_trait"])
        except Exception as exc:
            logger.warning("DeepSeek trait scoring failed: %s", type(exc).__name__)
            scoring_warnings.append(f"DeepSeek trait scoring failed: {type(exc).__name__}")

    for trait_id, score in scores_by_trait.items():
        state = trait_state.setdefault(trait_id, {})
        state["model_trait_score"] = score
        state["deepseek_raw_score"] = score["raw_score"]
    suggestion_status = _deepseek_generation_status(suggestions_by_trait, len(items))
    scoring_status = _deepseek_generation_status(scores_by_trait, len(items))
    if suggestion_status == "failed" and not suggestion_warnings:
        suggestion_warnings.append("DeepSeek trait suggestion response was empty.")
    if scoring_status == "failed" and not scoring_warnings:
        scoring_warnings.append("DeepSeek trait scoring response was empty.")
    return {
        "model_signal_suggestions_by_trait": suggestions_by_trait,
        "model_signal_analysis_by_trait": signal_analysis_by_trait,
        "model_suggestion_status": suggestion_status,
        "model_suggestion_warnings": suggestion_warnings,
        "model_trait_scores_by_trait": scores_by_trait,
        "model_scoring_status": scoring_status,
        "model_scoring_warnings": scoring_warnings,
    }


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
        mode = self._choose_notes_regeneration_mode(row)
        if mode is None:
            return
        job_path = self._deepseek_job_path_for_row(row)
        if job_path is None or not job_path.exists():
            messagebox.showwarning("Regenerate Notes", "DeepSeek job file was not found.")
            return
        try:
            progress_path = regenerate_interview_notes_job(job_path, mode=mode)
        except (OSError, ValueError) as exc:
            messagebox.showwarning("Regenerate Notes", f"Could not regenerate interview notes: {exc}")
            return
        if hasattr(self.app, "_show_finalize_progress"):
            self.app._show_finalize_progress()
        if hasattr(self.app, "_watch_deepseek_finalize_progress"):
            self.app._watch_deepseek_finalize_progress(progress_path)

    def _choose_notes_regeneration_mode(self, row: dict[str, Any]) -> str | None:
        candidate = str(row.get("candidate_name", "") or "this interview").strip()
        return self._show_notes_regeneration_mode_dialog(candidate)

    def _show_notes_regeneration_mode_dialog(self, candidate: str) -> str | None:
        choice = messagebox.askyesnocancel(
            "Regenerate Notes",
            "Regenerate interview notes for "
            f"{candidate}?\n\n"
            "Yes: rerun local DeepSeek and rebuild the document.\n"
            "No: rebuild only the document from saved data.\n"
            "Cancel: do nothing.",
        )
        if choice is True:
            return "full"
        if choice is False:
            return "document_only"
        return None

    def _deepseek_job_path_for_row(self, row: dict[str, Any]) -> Path | None:
        history_path = Path(str(getattr(self.app.history_store, "path", "") or ""))
        base_dir = str(getattr(self.app, "settings", {}).get("base_dir", "")).strip()
        return resolve_deepseek_regeneration_job_path(
            row,
            history_path=history_path,
            base_dir=Path(base_dir) if base_dir else None,
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
    *,
    run_deepseek: bool = True,
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
    _attach_deepseek_role_context_to_flow(
        flow_tx,
        payload.get("candidate", {}) if isinstance(payload.get("candidate"), dict) else {},
    )
    payload["flow_transcript"] = flow_tx
    payload["transcript_metadata"] = transcript_metadata
    payload["transcript_complete"] = bool(transcript_metadata["transcript_complete"])
    payload["remaining_question_indices"] = list(transcript_metadata["remaining_question_indices"])
    trait_inputs = getattr(app.state, "trait_inputs", {})
    if not isinstance(trait_inputs, dict):
        trait_inputs = {}
    if run_deepseek:
        deepseek_config = build_deepseek_summary_config(_deepseek_config_source_from_app(app))
        suggestion_result = generate_deepseek_trait_signal_suggestions(
            flow_tx,
            trait_inputs,
            rubric=app._rubric_with_question_overrides() if hasattr(app, "_rubric_with_question_overrides") else None,
            config=deepseek_config,
        )
        payload.update(suggestion_result)
        payload.update(
            generate_deepseek_interview_summaries(
                flow_tx,
                payload.get("candidate", {}) if isinstance(payload.get("candidate"), dict) else {},
                scoring=scoring,
                config=deepseek_config,
            )
        )
    else:
        payload.update(_deepseek_processing_payload())
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


def _deepseek_processing_payload() -> dict[str, Any]:
    return {
        "answer_summaries": [],
        "executive_summary": "",
        "interview_highlights": [],
        "summary_status": "processing",
        "summary_warnings": ["Local DeepSeek summary is still processing."],
        "model_signal_suggestions_by_trait": {},
        "model_suggestion_status": "processing",
        "model_suggestion_warnings": ["Local DeepSeek trait suggestions are still processing."],
        "model_trait_scores_by_trait": {},
        "model_scoring_status": "processing",
        "model_scoring_warnings": ["Local DeepSeek trait scoring is still processing."],
    }


def _python_executable_for_worker() -> str:
    executable = str(sys.executable or "").strip()
    if executable:
        return executable
    return "python"


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


DEFAULT_DEEPSEEK_PROGRESS_TASKS = (
    "Launching local DeepSeek worker",
    "Waiting for DeepSeek queue",
    "Checking local Ollama service",
    "Starting local Ollama service",
    "Local Ollama service ready",
    "Starting DeepSeek processing",
    "Analyzing traits",
    "Scoring traits",
    "Calculating final score",
    "Summarizing answers",
    "Generating Executive Summary",
    "Updating interview notes document",
    "Complete",
)


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


def _write_deepseek_launch_progress(progress_path: Path, step: str, status: str = "processing") -> None:
    tasks = build_finalize_progress_tasks(
        step,
        status,
        queued_steps=DEFAULT_DEEPSEEK_PROGRESS_TASKS,
    )
    atomic_write_json(
        progress_path,
        {
            "status": status,
            "step": str(step or "").strip(),
            "tasks": tasks,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        indent=2,
        ensure_ascii=False,
    )


def enqueue_deepseek_finalize_job(app: Any, context: FinalizeContext, out_path: str, history_id: str) -> Path:
    history_path = str(getattr(app.history_store, "path", "")).strip()
    if not history_path:
        return Path()
    base_dir = Path(app.settings["base_dir"])
    jobs_dir = base_dir / "deepseek_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_path = jobs_dir / f"deepseek-finalize-{history_id}.json"
    progress_path = jobs_dir / f"deepseek-finalize-{history_id}.progress.json"
    job_payload = {
        "history_id": history_id,
        "history_path": history_path,
        "base_dir": str(base_dir),
        "report_path": str(out_path),
        "rubric": app._rubric_with_question_overrides() if hasattr(app, "_rubric_with_question_overrides") else {},
        "payload": context.payload,
        "scoring": context.scoring,
        "deepseek_settings": _deepseek_config_source_from_app(app),
        "progress_path": str(progress_path),
    }
    atomic_write_json(job_path, job_payload, indent=2, ensure_ascii=False)
    InterviewHistoryStore(Path(history_path)).update_row(
        history_id,
        {
            "deepseek_job_path": str(job_path),
            "deepseek_progress_path": str(progress_path),
        },
    )
    InterviewMLDatasetStore(ml_dataset_path_for_history_path(Path(history_path))).upsert_interview(
        {
            "history_id": history_id,
            "deepseek_processing_status": "queued",
            "deepseek_job_path": str(job_path),
            "deepseek_progress_path": str(progress_path),
            "saved_report_path": str(out_path),
        },
        context.payload,
        context.scoring,
        source_job_path=job_path,
    )
    _write_deepseek_launch_progress(progress_path, "Launching local DeepSeek worker")
    _start_deepseek_finalize_worker(job_path)
    return job_path


def resolve_deepseek_regeneration_job_path(
    row: dict[str, Any],
    *,
    history_path: Path | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    row_key = str(row.get("history_id", "")).strip()
    if not row_key:
        return None
    job_name = f"deepseek-finalize-{row_key}.json"
    if Path(job_name).name != job_name:
        return None

    candidates = _deepseek_job_path_candidates(row, job_name, history_path=history_path, base_dir=base_dir)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if not candidates:
        return None
    return _synthesize_deepseek_job_from_session(
        candidates[0],
        row,
        history_path=history_path,
        base_dir=base_dir,
    )


def _deepseek_job_path_candidates(
    row: dict[str, Any],
    job_name: str,
    *,
    history_path: Path | None,
    base_dir: Path | None,
) -> list[Path]:
    candidates: list[Path] = []
    row_key = str(row.get("history_id", "")).strip()
    stored_job_path = str(row.get("deepseek_job_path", "")).strip()
    if stored_job_path:
        stored_path = Path(stored_job_path)
        if stored_path.name == f"deepseek-finalize-{row_key}.json" and stored_path.exists():
            candidates.append(stored_path)
    if history_path is not None and str(history_path):
        normalized_history_path = Path(history_path)
        candidates.append(normalized_history_path.parent / "deepseek_jobs" / job_name)
        if normalized_history_path.parent.name == "user_artifacts":
            candidates.insert(0, normalized_history_path.parent / "interviews" / "deepseek_jobs" / job_name)
    if base_dir is not None and str(base_dir):
        candidates.append(Path(base_dir) / "deepseek_jobs" / job_name)

    output: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(Path(candidate))
        if key in seen:
            continue
        seen.add(key)
        output.append(Path(candidate))
    return output


def _synthesize_deepseek_job_from_session(
    job_path: Path,
    row: dict[str, Any],
    *,
    history_path: Path | None,
    base_dir: Path | None,
) -> Path | None:
    session = _load_matching_interview_session(row, history_path=history_path, base_dir=base_dir)
    if session is None:
        return None
    flow_transcript, trait_inputs, custom_answers = _session_deepseek_inputs(session)
    if not any(str(item.get("candidate_transcript") or "").strip() for item in flow_transcript):
        return None

    resolved_base_dir = Path(base_dir) if base_dir is not None and str(base_dir) else Path(job_path).parent.parent
    progress_path = Path(job_path).with_suffix(".progress.json")
    job = {
        "history_id": str(row.get("history_id", "")).strip(),
        "history_path": str(history_path or ""),
        "base_dir": str(resolved_base_dir),
        "report_path": str(row.get("interview_notes_path") or row.get("saved_report_path") or ""),
        "rubric": _load_current_rubric_for_deepseek_job(),
        "payload": {
            "candidate": _candidate_payload_from_history_row(row, session),
            "flow_transcript": flow_transcript,
            "custom_answers": custom_answers,
            "trait_inputs": trait_inputs,
            "audio_recording": row.get("flow_recordings", []) if isinstance(row.get("flow_recordings"), list) else [],
            "transcript_complete": True,
            "remaining_question_indices": [],
            **_deepseek_processing_payload(),
        },
        "scoring": _scoring_payload_from_history_row(row),
        "deepseek_settings": _local_deepseek_settings_source(),
        "progress_path": str(progress_path),
        "source_session_path": str(session.get("_source_path", "")),
    }
    Path(job_path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(Path(job_path), job, indent=2, ensure_ascii=False)
    if history_path and str(row.get("history_id", "")).strip():
        InterviewHistoryStore(Path(history_path)).update_row(
            str(row.get("history_id", "")).strip(),
            {
                "deepseek_job_path": str(job_path),
                "deepseek_progress_path": str(progress_path),
            },
        )
    return Path(job_path)


def _load_matching_interview_session(
    row: dict[str, Any],
    *,
    history_path: Path | None,
    base_dir: Path | None,
) -> dict[str, Any] | None:
    roots: list[Path] = []
    if base_dir is not None and str(base_dir):
        roots.append(Path(base_dir) / "interview_sessions")
    if history_path is not None and str(history_path):
        history_parent = Path(history_path).parent
        roots.append(history_parent / "interviews" / "interview_sessions")
        roots.append(history_parent / "interview_sessions")

    candidate_name = _normalize_session_match_text(row.get("candidate_name"))
    interview_date = str(row.get("interview_date") or "").strip()
    best: tuple[float, Path, dict[str, Any]] | None = None
    for root in _dedupe_paths(roots):
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            interview = data.get("interview", {}) if isinstance(data.get("interview"), dict) else {}
            name_matches = _normalize_session_match_text(interview.get("candidate_name")) == candidate_name
            date_matches = str(interview.get("interview_date") or "").strip() == interview_date
            if not name_matches or not date_matches:
                continue
            transcript_count = sum(
                1
                for item in (data.get("questions", {}) if isinstance(data.get("questions"), dict) else {}).values()
                if isinstance(item, dict) and str(item.get("candidate_transcript") or "").strip()
            )
            if transcript_count <= 0:
                continue
            mtime = path.stat().st_mtime
            candidate = (mtime, path, {**data, "_source_path": str(path)})
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best is None:
        return None
    return best[2]


def _session_deepseek_inputs(session: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    questions = session.get("questions", {}) if isinstance(session.get("questions"), dict) else {}
    flow_transcript: list[dict[str, Any]] = []
    trait_inputs: dict[str, dict[str, Any]] = {}
    custom_answers: list[dict[str, Any]] = []
    for fallback_idx, record in enumerate(questions.values()):
        if not isinstance(record, dict):
            continue
        flow_idx = _coerce_flow_index(record.get("flow_idx"), fallback_idx)
        item_type = str(record.get("item_type") or "").strip()
        item_id = str(record.get("item_id") or flow_idx).strip()
        notes = record.get("notes", {}) if isinstance(record.get("notes"), dict) else {}
        question_text = str(notes.get("question_text") or "").strip()
        candidate_transcript = str(record.get("candidate_transcript") or "").strip()
        entry = {
            "flow_index": flow_idx,
            "type": item_type,
            "id": item_id,
            "title": _session_question_title(item_type, item_id),
            "question": question_text,
            "prompt": question_text,
            "candidate_transcript": candidate_transcript,
        }
        flow_transcript.append(entry)
        if item_type == "trait":
            trait_inputs[item_id] = {
                "raw_score": notes.get("raw_score"),
                "question_notes": str(notes.get("question_notes") or ""),
                "trait_notes": str(notes.get("trait_notes") or ""),
                "verbatim_notes": str(notes.get("verbatim_notes") or ""),
                "absolute_disqualifier": bool(notes.get("absolute_disqualifier", False)),
                "no_example_after_followups": bool(notes.get("no_example_after_followups", False)),
                "skipped": bool(notes.get("skipped", False)),
                "selected_signal_ids": list(notes.get("selected_signal_ids", []) or []),
            }
        else:
            custom_answers.append(
                {
                    "id": item_id,
                    "question": question_text,
                    "answer": str(notes.get("answer") or ""),
                    "skipped": bool(notes.get("skipped", False)),
                }
            )
    flow_transcript.sort(key=lambda item: int(item.get("flow_index", 0) or 0))
    return flow_transcript, trait_inputs, custom_answers


def _candidate_payload_from_history_row(row: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    interview = session.get("interview", {}) if isinstance(session.get("interview"), dict) else {}
    return {
        "name": str(row.get("candidate_name") or interview.get("candidate_name") or "").strip(),
        "candidate_name": str(row.get("candidate_name") or interview.get("candidate_name") or "").strip(),
        "interview_date": str(row.get("interview_date") or interview.get("interview_date") or "").strip(),
        "school": str(row.get("school") or "").strip(),
        "track": str(row.get("track") or "").strip(),
        "qualification": row.get("qualification", {}) if isinstance(row.get("qualification"), dict) else {},
    }


def _scoring_payload_from_history_row(row: dict[str, Any]) -> dict[str, Any]:
    scoring = row.get("scoring")
    if isinstance(scoring, dict) and isinstance(scoring.get("rows"), list):
        return dict(scoring)
    return {
        "percent_of_max": row.get("interview_score", 0),
        "outcome": str(row.get("determination") or ""),
        "rows": [],
    }


def _load_current_rubric_for_deepseek_job() -> dict[str, Any]:
    try:
        return RubricLoader(REPO_ROOT / "config" / "rubric.json").data
    except Exception:
        return {}


def _local_deepseek_settings_source(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    source = dict(settings or {})
    source["DEEPSEEK_SUMMARY_ENABLED"] = str(source.get("DEEPSEEK_SUMMARY_ENABLED") or "1")
    source["DEEPSEEK_API_KEY"] = str(source.get("DEEPSEEK_API_KEY") or _LOCAL_DEEPSEEK_API_KEY)
    source["DEEPSEEK_API_BASE_URL"] = str(source.get("DEEPSEEK_API_BASE_URL") or _LOCAL_DEEPSEEK_BASE_URL)
    source["DEEPSEEK_SUMMARY_MODEL"] = str(source.get("DEEPSEEK_SUMMARY_MODEL") or _LOCAL_DEEPSEEK_MODEL)
    try:
        timeout = float(source.get("DEEPSEEK_SUMMARY_TIMEOUT_SECONDS") or DEFAULT_DEEPSEEK_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS
    timeout = max(timeout, DEFAULT_DEEPSEEK_TIMEOUT_SECONDS)
    source["DEEPSEEK_SUMMARY_TIMEOUT_SECONDS"] = str(int(min(timeout, MAX_DEEPSEEK_TIMEOUT_SECONDS)))
    source["DEEPSEEK_PROMPT_TEMPLATES"] = load_deepseek_prompt_templates()
    return source


def _normalize_session_match_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    output: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path))
        if key in seen:
            continue
        seen.add(key)
        output.append(Path(path))
    return output


def _coerce_flow_index(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _session_question_title(item_type: str, item_id: str) -> str:
    if item_type == "trait":
        return str(item_id or "Trait Question").replace("_", " ").title()
    return "Custom Question"


def retry_deepseek_finalize_job(job_path: Path) -> Path:
    path = Path(job_path)
    with path.open("r", encoding="utf-8") as handle:
        job = json.load(handle)
    if not isinstance(job, dict):
        raise ValueError("DeepSeek finalize job must be a JSON object.")
    progress_path = Path(str(job.get("progress_path") or path.with_suffix(".progress.json")))
    history_path = str(job.get("history_path", "")).strip()
    history_id = str(job.get("history_id", "")).strip()
    if history_path and history_id:
        InterviewHistoryStore(Path(history_path)).update_row(
            history_id,
            {
                "deepseek_processing_status": "processing",
                "deepseek_processing_warning": "",
                "deepseek_completed_at": None,
            },
        )
    _recover_failed_deepseek_retry_lock(path, progress_path)
    _write_deepseek_launch_progress(progress_path, "Retrying local DeepSeek worker")
    _start_deepseek_finalize_worker(path)
    return progress_path


def regenerate_interview_notes_job(job_path: Path, *, mode: str) -> Path:
    path = Path(job_path)
    with path.open("r", encoding="utf-8") as handle:
        job = json.load(handle)
    if not isinstance(job, dict):
        raise ValueError("DeepSeek finalize job must be a JSON object.")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"full", "document_only"}:
        raise ValueError("Regenerate mode must be 'full' or 'document_only'.")

    progress_path = Path(str(job.get("progress_path") or path.with_suffix(".progress.json")))
    job = _repair_regeneration_job_from_history(job)
    job["rerun_mode"] = normalized_mode
    if normalized_mode == "full":
        payload = job.get("payload")
        if isinstance(payload, dict):
            payload.update(_deepseek_processing_payload())
        deepseek_settings = job.get("deepseek_settings")
        if not isinstance(deepseek_settings, dict):
            deepseek_settings = {}
        job["deepseek_settings"] = _local_deepseek_settings_source(deepseek_settings)
    atomic_write_json(path, job, indent=2, ensure_ascii=False)
    _mark_deepseek_history_processing(job)
    _recover_failed_deepseek_retry_lock(path, progress_path)
    step = "Regenerating interview notes document"
    if normalized_mode == "full":
        step = "Regenerating local DeepSeek output and interview notes document"
    _write_deepseek_launch_progress(progress_path, step)
    _start_deepseek_finalize_worker(path)
    return progress_path


def _repair_regeneration_job_from_history(job: dict[str, Any]) -> dict[str, Any]:
    history_path = str(job.get("history_path", "")).strip()
    history_id = str(job.get("history_id", "")).strip()
    if not history_path or not history_id:
        return job
    row = _history_row_for_job(Path(history_path), history_id)
    if row is None:
        return job
    history_scoring = row.get("scoring")
    if not isinstance(history_scoring, dict):
        return job
    job_scoring = job.get("scoring") if isinstance(job.get("scoring"), dict) else {}
    if _usable_scored_rating_count(history_scoring) <= _usable_scored_rating_count(job_scoring):
        return job

    repaired = dict(job)
    repaired["scoring"] = dict(history_scoring)
    repaired["payload"] = _history_payload_for_regeneration(row, job.get("payload") if isinstance(job.get("payload"), dict) else {})
    repaired["history_regeneration_repaired_from_history_at"] = _utc_timestamp()
    return repaired


def _history_row_for_job(history_path: Path, history_id: str) -> dict[str, Any] | None:
    try:
        rows = InterviewHistoryStore(Path(history_path)).load()
    except Exception:
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("history_id") or "").strip() == history_id:
            return row
    return None


def _usable_scored_rating_count(scoring: Any) -> int:
    if not isinstance(scoring, dict):
        return 0
    count = 0
    for row in scoring.get("rows", []) or []:
        if not isinstance(row, dict) or row.get("skipped", False):
            continue
        raw_score = row.get("final_raw_score", row.get("raw_score"))
        if str(raw_score if raw_score is not None else "").strip():
            count += 1
    return count


def _history_payload_for_regeneration(row: dict[str, Any], existing_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(existing_payload)
    candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
    payload["candidate"] = {
        **dict(candidate),
        "name": str(row.get("candidate_name") or candidate.get("name") or candidate.get("candidate_name") or "").strip(),
        "candidate_name": str(row.get("candidate_name") or candidate.get("candidate_name") or candidate.get("name") or "").strip(),
        "interview_date": str(row.get("interview_date") or candidate.get("interview_date") or "").strip(),
        "school": str(row.get("school") or candidate.get("school") or "").strip(),
        "track": str(row.get("track") or candidate.get("track") or "").strip(),
        "qualification": row.get("qualification", candidate.get("qualification", {}))
        if isinstance(row.get("qualification", candidate.get("qualification", {})), dict)
        else {},
    }
    flow_transcript = row.get("flow_transcript")
    if isinstance(flow_transcript, list) and any(
        isinstance(item, dict) and str(item.get("candidate_transcript") or item.get("evaluator_notes") or "").strip()
        for item in flow_transcript
    ):
        payload["flow_transcript"] = list(flow_transcript)
    custom_answers = row.get("custom_answers")
    if isinstance(custom_answers, list):
        payload["custom_answers"] = list(custom_answers)
    flow_recordings = row.get("flow_recordings")
    if isinstance(flow_recordings, list):
        payload["audio_recording"] = list(flow_recordings)
    payload["trait_inputs"] = _history_trait_inputs_for_regeneration(row, payload.get("trait_inputs") if isinstance(payload.get("trait_inputs"), dict) else {})
    payload["transcript_complete"] = True
    payload["remaining_question_indices"] = []
    return payload


def _history_trait_inputs_for_regeneration(row: dict[str, Any], existing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    trait_inputs: dict[str, dict[str, Any]] = {
        str(key): dict(value) for key, value in existing.items() if isinstance(value, dict)
    }
    scoring = row.get("scoring") if isinstance(row.get("scoring"), dict) else {}
    for score_row in scoring.get("rows", []) or []:
        if not isinstance(score_row, dict):
            continue
        trait_id = str(score_row.get("trait_id") or score_row.get("id") or "").strip()
        if not trait_id:
            continue
        raw_score = score_row.get("final_raw_score", score_row.get("raw_score"))
        trait_inputs[trait_id] = {
            **trait_inputs.get(trait_id, {}),
            "raw_score": raw_score,
            "question_notes": str(score_row.get("question_notes") or score_row.get("verbatim_notes") or ""),
            "trait_notes": str(score_row.get("trait_notes") or score_row.get("verbatim_notes") or ""),
            "verbatim_notes": str(score_row.get("verbatim_notes") or score_row.get("question_notes") or ""),
            "absolute_disqualifier": bool(score_row.get("absolute_disqualifier", False)),
            "no_example_after_followups": bool(score_row.get("no_example_after_followups", False)),
            "skipped": bool(score_row.get("skipped", False)),
            "selected_signal_ids": list(score_row.get("selected_signal_ids", []) or []),
        }
    return trait_inputs


def _mark_deepseek_history_processing(job: dict[str, Any]) -> None:
    history_path = str(job.get("history_path", "")).strip()
    history_id = str(job.get("history_id", "")).strip()
    if not history_path or not history_id:
        return
    InterviewHistoryStore(Path(history_path)).update_row(
        history_id,
        {
            "deepseek_processing_status": "processing",
            "deepseek_processing_warning": "",
            "deepseek_completed_at": None,
        },
    )


def _recover_failed_deepseek_retry_lock(job_path: Path, progress_path: Path) -> None:
    lock_path = Path(job_path).resolve().parent / "deepseek-finalize.lock"
    if not lock_path.exists():
        return
    try:
        progress = json.loads(Path(progress_path).read_text(encoding="utf-8"))
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(progress, dict) or not isinstance(metadata, dict):
        return
    if str(progress.get("status") or "").strip().lower() != "failed":
        return
    if str(metadata.get("job") or "").strip() != Path(job_path).stem:
        return
    try:
        lock_path.unlink()
    except OSError:
        return


def _start_deepseek_finalize_worker(job_path: Path) -> None:
    script_path = Path(__file__).resolve().with_name("deepseek_finalize_worker.py")
    log_dir = job_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{job_path.stem}.out.log"
    stderr_path = log_dir / f"{job_path.stem}.err.log"
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        stdout_context = stdout_path.open("ab")
        stderr_context = stderr_path.open("ab")
    except PermissionError:
        stdout_context = nullcontext(subprocess.DEVNULL)
        stderr_context = nullcontext(subprocess.DEVNULL)
    with stdout_context as stdout, stderr_context as stderr:
        subprocess.Popen(
            [_python_executable_for_worker(), str(script_path), str(job_path)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=os.name != "nt",
            creationflags=creationflags,
        )


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
            "deepseek_processing_status": "not_started",
            "deepseek_processing_warning": "",
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
        history_path = str(getattr(app.history_store, "path", "")).strip()
        if history_path:
            InterviewMLDatasetStore(ml_dataset_path_for_history_path(Path(history_path))).upsert_interview(
                history_entry,
                context.payload,
                context.scoring,
                source_job_path=history_entry.get("deepseek_job_path", ""),
            )
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
        context = build_finalize_context(self.app, scoring, warnings, transcript_metadata, run_deepseek=False)
        out_path = self.gateways.export_report(self.app, context)
        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Writing integration export")
        integration_path = self.gateways.export_integration(self.app, context)
        integration_path_str = Path(integration_path).as_posix()
        director_packet, comm_log_path = self.gateways.send_referral(self.app, context, out_path, integration_path)
        history_id = self.gateways.persist_finalize_history(self.app, context, out_path)
        if hasattr(self.app, "_report_finalize_progress"):
            self.app._report_finalize_progress("Queueing DeepSeek processing")
        deepseek_job_path = enqueue_deepseek_finalize_job(self.app, context, out_path, history_id)
        deepseek_job_available = bool(getattr(deepseek_job_path, "name", ""))
        deepseek_progress_path = deepseek_job_path.with_suffix(".progress.json") if deepseek_job_available else Path()
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
            "deepseek_job_path": str(deepseek_job_path) if deepseek_job_available else "",
            "deepseek_progress_path": str(deepseek_progress_path) if deepseek_job_available else "",
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
        if hasattr(self.app, "_watch_deepseek_finalize_progress"):
            self.app._watch_deepseek_finalize_progress(result.get("deepseek_progress_path"))
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
    "interview_app.transcript_summary",
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
