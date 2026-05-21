from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    """Write JSON atomically using temp file + flush/fsync + replace."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path_str = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, indent=indent, ensure_ascii=ensure_ascii)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        temp_path.replace(target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def safe_read_json(path: Path, default: Any, expected_type: type[Any] | None = None) -> Any:
    """Read JSON with fallback default if missing/invalid/wrong type."""
    target_path = Path(path)
    if not target_path.exists():
        return default
    try:
        with target_path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (json.JSONDecodeError, OSError):
        return default

    if expected_type is None:
        return payload
    if isinstance(payload, expected_type):
        return payload
    return default
