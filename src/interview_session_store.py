from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

CURRENT_SCHEMA_VERSION = 1


def _safe_token(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return cleaned or fallback


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
        record["updated_at"] = datetime.utcnow().isoformat() + "Z"
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
        now = datetime.utcnow().isoformat() + "Z"
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
        merged["updated_at"] = datetime.utcnow().isoformat() + "Z"
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
