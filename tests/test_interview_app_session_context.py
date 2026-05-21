from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from interview_app.session_context import InterviewSessionContext


class _FrozenDate:
    def __call__(self) -> date:
        return date(2026, 3, 11)


class _FrozenDateTime:
    def __call__(self) -> datetime:
        return datetime(2026, 3, 11, 9, 8, 7)


def _build_context(tmp_path: Path) -> InterviewSessionContext:
    return InterviewSessionContext(
        app_root=tmp_path,
        default_base_dir=tmp_path / "default-output",
        today_provider=_FrozenDate(),
        now_provider=_FrozenDateTime(),
    )


def test_candidate_name_sanitization(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    base_name = context.safe_base_name("  Jane / Doe??  ", "2026-03-10")
    assert base_name == "Candidate_Jane_Doe_2026-03-10"


def test_missing_date_fallback_is_deterministic(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    assert context.safe_interview_date("") == "2026-03-11"


def test_validate_runtime_base_dir_rejects_file_path(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        context.validate_runtime_base_dir(str(file_path))


def test_active_session_key_is_deterministic(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    key = context.active_session_key("", "Jane Doe", "")
    assert key == ("Candidate_Jane_Doe_2026-03-11", "Jane Doe", "2026-03-11")


def test_runtime_base_dir_relative_paths_are_anchored(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    resolved = context.validate_runtime_base_dir("runtime/files")
    assert resolved == (tmp_path / "runtime/files").resolve()


def test_runtime_init_log_path_uses_timestamp_provider(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    path = context.runtime_init_log_path()
    assert path == tmp_path / "logs" / "interview-runtime-init-20260311-090807.log"
