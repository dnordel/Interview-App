from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping


DIAGNOSTIC_LOG_MARKER = "Diagnostic log:"


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
        (r"/(?:[^\s/]+/)+[^\s/]+", "[path]"),
        (r"\\Users\\[^\\\s]+", r"\\Users\\[user]"),
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
