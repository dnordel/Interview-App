from __future__ import annotations

from typing import Any


TITLE_OPTIONS: tuple[str, str] = ("Mr.", "Ms.")
DEFAULT_CANDIDATE_TITLE = "Ms."


def normalize_candidate_title(value: Any) -> str:
    text = str(value or "").strip()
    if text in TITLE_OPTIONS:
        return text
    return DEFAULT_CANDIDATE_TITLE
