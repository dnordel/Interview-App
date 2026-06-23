from __future__ import annotations

import json
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_store import InterviewHistoryStore
from interview_runtime import (
    build_deepseek_summary_config,
    generate_deepseek_interview_summaries,
    generate_deepseek_trait_signal_suggestions,
)
from scoring_reporting import DocxExporter, ScoringEngine

_LOCK_FILENAME = "deepseek-finalize.lock"
_LOCK_STALE_SECONDS = 60 * 60 * 2
_LOCK_POLL_SECONDS = 1.0


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_job(job_path: Path) -> dict[str, Any]:
    with job_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek finalize job must be a JSON object.")
    return payload


def _history_store(job: dict[str, Any]) -> InterviewHistoryStore | None:
    history_path = str(job.get("history_path", "")).strip()
    if not history_path:
        return None
    return InterviewHistoryStore(Path(history_path))


def _update_history(job: dict[str, Any], updates: dict[str, Any]) -> None:
    store = _history_store(job)
    history_id = str(job.get("history_id", "")).strip()
    if store is None or not history_id:
        return
    store.update_row(history_id, updates)


def _write_progress(job: dict[str, Any], step: str, status: str = "processing") -> None:
    progress_path = str(job.get("progress_path", "")).strip()
    if not progress_path:
        return
    payload = {
        "status": status,
        "step": str(step or "").strip(),
        "updated_at": _utc_timestamp(),
    }
    path = Path(progress_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def _lock_path_for_job(job_path: Path) -> Path:
    return Path(job_path).resolve().parent / _LOCK_FILENAME


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
        except ImportError:
            return False
        synchronize = 0x00100000
        query_limited_information = 0x1000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize | query_limited_information, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _load_lock_metadata(lock_path: Path) -> dict[str, Any]:
    try:
        with lock_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _lock_is_stale(lock_path: Path, *, now: float | None = None, stale_seconds: float = _LOCK_STALE_SECONDS) -> bool:
    metadata = _load_lock_metadata(lock_path)
    try:
        pid = int(metadata.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    created_at = float(metadata.get("created_at_epoch", 0) or 0)
    age_seconds = (time.time() if now is None else now) - created_at if created_at else stale_seconds + 1
    return age_seconds >= stale_seconds or not _process_is_alive(pid)


def _write_lock_file(lock_path: Path, job_path: Path) -> dict[str, Any]:
    metadata = {
        "pid": os.getpid(),
        "created_at": _utc_timestamp(),
        "created_at_epoch": time.time(),
        "job": Path(job_path).stem,
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(lock_path), flags)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")
    return metadata


@contextmanager
def _deepseek_worker_lock(job_path: Path):
    lock_path = _lock_path_for_job(job_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] | None = None
    while metadata is None:
        try:
            metadata = _write_lock_file(lock_path, job_path)
        except FileExistsError:
            if _lock_is_stale(lock_path):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    time.sleep(_LOCK_POLL_SECONDS)
                continue
            time.sleep(_LOCK_POLL_SECONDS)
    try:
        yield lock_path
    finally:
        if metadata and _load_lock_metadata(lock_path) == metadata:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _deepseek_status_value(value: Any) -> str:
    return str(value or "").strip().lower()


def _deepseek_generated(payload: dict[str, Any]) -> bool:
    return any(
        _deepseek_status_value(payload.get(key)) in {"generated", "partial"}
        for key in ("summary_status", "model_suggestion_status", "model_scoring_status")
    )


def _deepseek_complete(payload: dict[str, Any]) -> bool:
    return (
        _deepseek_status_value(payload.get("summary_status")) == "generated"
        and _deepseek_status_value(payload.get("model_scoring_status")) == "generated"
    )


def _deepseek_history_status(payload: dict[str, Any]) -> tuple[str, str]:
    if _deepseek_complete(payload):
        return "complete", ""
    if _deepseek_generated(payload):
        return "partial", "DeepSeek processing partially completed."
    return "failed", "DeepSeek processing failed to generate output."


def _run_job_unlocked(job: dict[str, Any], job_path: Path) -> None:
    payload = dict(job.get("payload", {}) or {})
    scoring = dict(job.get("scoring", {}) or {})
    rubric = dict(job.get("rubric", {}) or {})
    flow_transcript = [item for item in payload.get("flow_transcript", []) or [] if isinstance(item, dict)]
    candidate = payload.get("candidate", {}) if isinstance(payload.get("candidate"), dict) else {}
    trait_inputs = payload.get("trait_inputs", {}) if isinstance(payload.get("trait_inputs"), dict) else {}

    config = build_deepseek_summary_config(job.get("deepseek_settings", {}) if isinstance(job.get("deepseek_settings"), dict) else {})
    _write_progress(job, "Starting DeepSeek processing")
    payload.update(
        generate_deepseek_trait_signal_suggestions(
            flow_transcript,
            trait_inputs,
            rubric=rubric,
            config=config,
            progress_callback=lambda step: _write_progress(job, step),
        )
    )
    payload["trait_inputs"] = trait_inputs

    track = str(candidate.get("track", "") or "")
    if track:
        _write_progress(job, "Calculating final score")
        scoring = ScoringEngine.evaluate(rubric, track, trait_inputs)
    payload.update(
        generate_deepseek_interview_summaries(
            flow_transcript,
            candidate,
            scoring=scoring,
            config=config,
            progress_callback=lambda step: _write_progress(job, step),
        )
    )

    report_path = Path(str(job.get("report_path", "")).strip())
    output_dir = report_path.parent if str(report_path) else Path(str(job.get("base_dir", "."))) / "Indeed Interview Notes"
    _write_progress(job, "Updating interview notes document")
    out_path = DocxExporter(output_dir).export(rubric, payload, scoring)
    processing_status, processing_warning = _deepseek_history_status(payload)
    _update_history(
        job,
        {
            "interview_score": scoring.get("percent_of_max", 0),
            "determination": scoring.get("outcome", ""),
            "saved_report_path": str(out_path),
            "interview_notes_path": str(out_path),
            "deepseek_processing_status": processing_status,
            "deepseek_processing_warning": processing_warning,
            "deepseek_completed_at": _utc_timestamp(),
        },
    )
    _write_progress(job, "Complete", "complete")


def run_job(job_path: Path) -> None:
    job_path = Path(job_path)
    job = _load_job(job_path)
    history_id = str(job.get("history_id", "")).strip()
    if not history_id:
        raise ValueError("DeepSeek finalize job missing history_id.")

    _write_progress(job, "Waiting for DeepSeek queue")
    with _deepseek_worker_lock(job_path):
        _run_job_unlocked(job, job_path)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: deepseek_finalize_worker.py JOB_PATH", file=sys.stderr)
        return 2
    job_path = Path(argv[1])
    try:
        run_job(job_path)
    except Exception as exc:
        try:
            job = _load_job(job_path)
            _write_progress(job, f"DeepSeek processing failed: {type(exc).__name__}", "failed")
            _update_history(
                job,
                {
                    "deepseek_processing_status": "failed",
                    "deepseek_processing_warning": f"DeepSeek processing failed: {type(exc).__name__}",
                    "deepseek_completed_at": _utc_timestamp(),
                },
            )
        except Exception:
            pass
        print(traceback.format_exc(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
