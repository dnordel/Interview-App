from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4


def _sanitize_token(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return cleaned or fallback


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
        name = _sanitize_token(candidate_name, "Candidate")
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
