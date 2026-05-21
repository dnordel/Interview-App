from __future__ import annotations

from pathlib import Path
from typing import Collection


def validate_existing_file_path(
    path_text: str,
    *,
    allowed_suffixes: Collection[str] | None = None,
) -> tuple[str, str]:
    text = str(path_text or '').strip()
    if not text:
        return '', 'empty'

    candidate = Path(text).expanduser()
    if not candidate.exists():
        return '', 'missing'
    if not candidate.is_file():
        return '', 'not_file'

    normalized_suffixes = _normalized_suffixes(allowed_suffixes)
    if normalized_suffixes and candidate.suffix.lower() not in normalized_suffixes:
        return '', 'unsupported_extension'
    return str(candidate), ''


def _normalized_suffixes(allowed_suffixes: Collection[str] | None) -> set[str]:
    if not allowed_suffixes:
        return set()
    return {str(suffix).strip().lower() for suffix in allowed_suffixes if str(suffix).strip()}
