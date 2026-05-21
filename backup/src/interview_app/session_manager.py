from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from candidate_profile import CandidateQualification
from interview_session_store import InterviewSessionStore
from interview_state import InterviewState
from reporting import DraftManager


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

        normalized: dict[str, Any] = {
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
        return normalized

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
        state = InterviewState(
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
        return state

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
