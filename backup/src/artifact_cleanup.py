from __future__ import annotations

from pathlib import Path
from typing import Any

SAFE_EXTENSIONS = {".wav", ".mp3", ".json", ".jsonl", ".npy", ".npz", ".pt", ".bin", ".tmp", ".chunk"}
SAFE_NAME_TOKENS = ("candidate_", "_transcript", "embedding", "chunk", "recording")


def _is_within(base_dir: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return False
    return True


def _safe_to_delete(base_dir: Path, path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if not _is_within(base_dir, path):
        return False
    if path.suffix.lower() in SAFE_EXTENSIONS:
        return True
    name = path.name.lower()
    return any(token in name for token in SAFE_NAME_TOKENS)


def extract_artifact_paths(flow_recordings: dict[int, dict[str, Any]] | dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    for entry in (flow_recordings or {}).values():
        item = dict(entry or {})
        attempts = item.get("attempts")
        if not isinstance(attempts, list):
            attempts = [item]
        for attempt in attempts:
            rec = dict(attempt or {})
            for key in ("mic_wav", "sys_wav", "transcript_txt", "transcript_jsonl"):
                raw = str(rec.get(key) or "").strip()
                if raw:
                    out.append(Path(raw).expanduser())
            output_dir = str(rec.get("output_dir") or "").strip()
            base_name = str(rec.get("base_name") or "").strip()
            if not output_dir or not base_name:
                continue
            root = Path(output_dir).expanduser()
            for ext in (".mp3", ".json", ".npy", ".npz", ".pt", ".bin", ".tmp", ".chunk"):
                out.append(root / f"{base_name}{ext}")
    return out


def delete_recording_artifacts(base_dir: Path, flow_recordings: dict[int, dict[str, Any]] | dict[str, Any]) -> list[Path]:
    deleted: list[Path] = []
    for path in extract_artifact_paths(flow_recordings):
        if not _safe_to_delete(base_dir, path):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        deleted.append(path)
    return deleted


def cleanup_stale_artifacts(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    deleted: list[Path] = []
    for path in base_dir.glob("*"):
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        stale_name = lower_name.startswith("candidate_") or any(token in lower_name for token in SAFE_NAME_TOKENS)
        if not stale_name:
            continue
        if not _safe_to_delete(base_dir, path):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        deleted.append(path)
    return deleted
