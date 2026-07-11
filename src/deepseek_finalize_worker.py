from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_store import InterviewHistoryStore, InterviewMLDatasetStore, ml_dataset_path_for_history_path
from interview_runtime import (
    DEFAULT_DEEPSEEK_PROGRESS_TASKS,
    _attach_deepseek_role_context_to_flow,
    build_finalize_progress_tasks,
    build_deepseek_summary_config,
    generate_deepseek_interview_summaries,
    generate_deepseek_trait_signal_suggestions,
)
from platform_services import atomic_write_json
from scoring_reporting import DocxExporter, ScoringEngine

_LOCK_FILENAME = "deepseek-finalize.lock"
_LOCK_STALE_SECONDS = 60 * 60 * 2
_LOCK_POLL_SECONDS = 1.0
_REGENERATED_REPORT_SUFFIX = "regenerated"
_LOCAL_DEEPSEEK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_OLLAMA_READY_TIMEOUT_SECONDS = 30.0


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


def _ml_dataset_store(job: dict[str, Any]) -> InterviewMLDatasetStore | None:
    history_path = str(job.get("history_path", "")).strip()
    if not history_path:
        return None
    return InterviewMLDatasetStore(ml_dataset_path_for_history_path(Path(history_path)))


def _update_history(job: dict[str, Any], updates: dict[str, Any]) -> None:
    store = _history_store(job)
    history_id = str(job.get("history_id", "")).strip()
    if store is None or not history_id:
        return
    store.update_row(history_id, updates)


def _update_ml_dataset(
    job: dict[str, Any],
    payload: dict[str, Any],
    scoring: dict[str, Any],
    updates: dict[str, Any],
    config: Any | None = None,
) -> None:
    store = _ml_dataset_store(job)
    history_id = str(job.get("history_id", "")).strip()
    if store is None or not history_id:
        return
    history_entry = {
        "history_id": history_id,
        "deepseek_job_path": str(job.get("job_path", "") or ""),
        "saved_report_path": str(updates.get("interview_notes_path") or updates.get("saved_report_path") or job.get("report_path", "") or ""),
        **updates,
    }
    store.upsert_interview(
        history_entry,
        payload,
        scoring,
        source_job_path=str(job.get("_job_path", "") or ""),
        source_session_path=str(job.get("source_session_path", "") or ""),
    )
    trace_events = list(getattr(config, "trace_events", []) or []) if config is not None else []
    trace_events.extend(item for item in job.get("deepseek_trace_events", []) or [] if isinstance(item, dict))
    if trace_events:
        store.record_deepseek_traces(history_id, trace_events, source_path=str(job.get("_job_path", "") or ""))


def _write_progress(job: dict[str, Any], step: str, status: str = "processing") -> None:
    progress_path = str(job.get("progress_path", "")).strip()
    if not progress_path:
        return
    existing_tasks: list[dict[str, Any]] = []
    progress_file = Path(progress_path)
    try:
        existing_payload = json.loads(progress_file.read_text(encoding="utf-8"))
        if isinstance(existing_payload, dict) and isinstance(existing_payload.get("tasks"), list):
            existing_tasks = existing_payload["tasks"]
    except (OSError, json.JSONDecodeError):
        existing_tasks = []
    payload = {
        "status": status,
        "step": str(step or "").strip(),
        "tasks": build_finalize_progress_tasks(
            step,
            status,
            existing_tasks=existing_tasks,
            queued_steps=job.get("progress_tasks") or DEFAULT_DEEPSEEK_PROGRESS_TASKS,
        ),
        "updated_at": _utc_timestamp(),
    }
    try:
        atomic_write_json(progress_file, payload, indent=2, ensure_ascii=False)
    except OSError:
        return


def _local_ollama_api_ready(config: Any) -> bool:
    parsed = urllib.parse.urlparse(str(getattr(config, "base_url", "") or "http://127.0.0.1:11434/v1"))
    if parsed.hostname not in _LOCAL_DEEPSEEK_HOSTS:
        return True
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or "127.0.0.1:11434"
    try:
        with urllib.request.urlopen(f"{scheme}://{netloc}/api/tags", timeout=2) as response:
            return 200 <= int(getattr(response, "status", 200)) < 500
    except OSError:
        return False


def _resolve_ollama_executable() -> str:
    found = shutil.which("ollama.exe") or shutil.which("ollama")
    if found:
        return found
    if os.name != "nt":
        return "ollama"
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "ollama.exe"


def _start_local_ollama_service(ollama_exe: str) -> None:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )
    subprocess.Popen([ollama_exe, "serve"], **kwargs)


def _ensure_local_deepseek_runtime(job: dict[str, Any], config: Any) -> None:
    parsed = urllib.parse.urlparse(str(getattr(config, "base_url", "") or ""))
    if parsed.hostname not in _LOCAL_DEEPSEEK_HOSTS:
        return
    _write_progress(job, "Checking local Ollama service")
    if _local_ollama_api_ready(config):
        _write_progress(job, "Local Ollama service ready")
        return
    _write_progress(job, "Starting local Ollama service")
    _start_local_ollama_service(_resolve_ollama_executable())
    deadline = time.monotonic() + _OLLAMA_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _local_ollama_api_ready(config):
            _write_progress(job, "Local Ollama service ready")
            return
        time.sleep(0.5)
    raise RuntimeError("Local Ollama service did not become ready")


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
    current_time = time.time() if now is None else now
    if not metadata:
        try:
            age_seconds = current_time - lock_path.stat().st_mtime
        except OSError:
            return True
        return age_seconds >= stale_seconds
    try:
        pid = int(metadata.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    created_at = float(metadata.get("created_at_epoch", 0) or 0)
    age_seconds = current_time - created_at if created_at else stale_seconds + 1
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
        and _deepseek_status_value(payload.get("model_suggestion_status")) == "generated"
        and _deepseek_status_value(payload.get("model_scoring_status")) == "generated"
    )


def _deepseek_history_status(payload: dict[str, Any]) -> tuple[str, str]:
    if _deepseek_complete(payload):
        return "complete", ""
    if _deepseek_generated(payload):
        return "partial", "DeepSeek processing partially completed."
    return "failed", "DeepSeek processing failed to generate output."


def _require_deepseek_statuses(
    payload: dict[str, Any],
    required: tuple[str, ...],
    allowed: tuple[str, ...] = ("generated",),
) -> None:
    allowed_statuses = {str(status).strip().lower() for status in allowed}
    incomplete = [name for name in required if _deepseek_status_value(payload.get(name)) not in allowed_statuses]
    if incomplete:
        raise RuntimeError(f"DeepSeek prompts incomplete: {', '.join(incomplete)}")


def _trait_advisory_ready(payload: dict[str, Any]) -> bool:
    suggestion_status = _deepseek_status_value(payload.get("model_suggestion_status"))
    scoring_status = _deepseek_status_value(payload.get("model_scoring_status"))
    return suggestion_status in {"generated", "partial", "no_transcript"} and scoring_status in {
        "generated",
        "no_transcript",
    }


def _deepseek_failure_warning(exc: Exception) -> str:
    message = str(exc).strip()
    if isinstance(exc, RuntimeError) and message.startswith("DeepSeek prompts incomplete:"):
        return f"DeepSeek processing failed: {message}"
    return f"DeepSeek processing failed: {type(exc).__name__}"


def _checkpoint_job(job_path: Path, job: dict[str, Any], payload: dict[str, Any], scoring: dict[str, Any]) -> None:
    updated = dict(job)
    updated["payload"] = payload
    updated["scoring"] = scoring
    atomic_write_json(Path(job_path), updated, indent=2, ensure_ascii=False)


def _timestamp_for_filename() -> str:
    return _utc_timestamp().replace("-", "").replace(":", "").replace("T", "-").replace("Z", "").split(".")[0]


def _regenerated_report_path(report_path: Path) -> Path:
    base_path = Path(report_path)
    timestamp = _timestamp_for_filename()
    candidate = base_path.with_name(f"{base_path.stem} - {_REGENERATED_REPORT_SUFFIX} {timestamp}{base_path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = base_path.with_name(
            f"{base_path.stem} - {_REGENERATED_REPORT_SUFFIX} {timestamp} ({counter}){base_path.suffix}"
        )
        counter += 1
    return candidate


def _export_interview_notes(
    job: dict[str, Any],
    job_path: Path,
    rubric: dict[str, Any],
    payload: dict[str, Any],
    scoring: dict[str, Any],
) -> Path:
    report_path = Path(str(job.get("report_path", "")).strip())
    output_dir = report_path.parent if str(report_path) else Path(str(job.get("base_dir", "."))) / "Indeed Interview Notes"
    try:
        return DocxExporter(output_dir).export(rubric, payload, scoring)
    except PermissionError:
        if not str(report_path):
            raise
        fallback_path = _regenerated_report_path(report_path)
        temp_dir = Path(job_path).resolve().parent / f"{Path(job_path).stem}-report-retry"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            temp_report = DocxExporter(temp_dir).export(rubric, payload, scoring)
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_report), str(fallback_path))
            return fallback_path
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _delete_superseded_basic_notes(original_path: Path, generated_path: Path) -> None:
    original = Path(original_path)
    generated = Path(generated_path)
    if original == generated or original.suffix.lower() != ".docx":
        return
    if original.parent != generated.parent:
        return
    if "basic interview notes" not in original.name.lower():
        return
    try:
        original.unlink(missing_ok=True)
    except OSError:
        return


def _run_job_unlocked(job: dict[str, Any], job_path: Path) -> None:
    payload = dict(job.get("payload", {}) or {})
    scoring = dict(job.get("scoring", {}) or {})
    rubric = dict(job.get("rubric", {}) or {})
    rerun_mode = str(job.get("rerun_mode", "") or "").strip().lower()
    if rerun_mode == "document_only":
        _write_progress(job, "Updating interview notes document")
        out_path = _export_interview_notes(job, job_path, rubric, payload, scoring)
        processing_status, processing_warning = _deepseek_history_status(payload)
        updates = {
            "saved_report_path": str(out_path),
            "interview_notes_path": str(out_path),
            "deepseek_processing_status": processing_status,
            "deepseek_processing_warning": processing_warning,
            "deepseek_completed_at": _utc_timestamp(),
        }
        _update_history(job, updates)
        _update_ml_dataset(job, payload, scoring, updates)
        _write_progress(job, "Complete", "complete")
        return

    flow_transcript = [item for item in payload.get("flow_transcript", []) or [] if isinstance(item, dict)]
    candidate = payload.get("candidate", {}) if isinstance(payload.get("candidate"), dict) else {}
    _attach_deepseek_role_context_to_flow(flow_transcript, candidate)
    trait_inputs = payload.get("trait_inputs", {}) if isinstance(payload.get("trait_inputs"), dict) else {}

    config = build_deepseek_summary_config(job.get("deepseek_settings", {}) if isinstance(job.get("deepseek_settings"), dict) else {})
    _ensure_local_deepseek_runtime(job, config)
    _write_progress(job, "Starting DeepSeek processing")
    if not _trait_advisory_ready(payload):
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
        _checkpoint_job(job_path, job, payload, scoring)
        _require_deepseek_statuses(
            payload,
            ("model_suggestion_status", "model_scoring_status"),
            allowed=("generated", "partial", "no_transcript"),
        )
    else:
        _write_progress(job, "Resuming after trait analysis checkpoint")

    track = str(candidate.get("track", "") or "")
    if track:
        _write_progress(job, "Calculating final score")
        scoring = ScoringEngine.evaluate(rubric, track, trait_inputs)
        _checkpoint_job(job_path, job, payload, scoring)
    if _deepseek_status_value(payload.get("summary_status")) != "generated":
        payload.update(
            generate_deepseek_interview_summaries(
                flow_transcript,
                candidate,
                scoring=scoring,
                config=config,
                progress_callback=lambda step: _write_progress(job, step),
            )
        )
        _checkpoint_job(job_path, job, payload, scoring)
    else:
        _write_progress(job, "Resuming after summary checkpoint")

    _write_progress(job, "Updating interview notes document")
    out_path = _export_interview_notes(job, job_path, rubric, payload, scoring)
    report_path = Path(str(job.get("report_path", "")).strip())
    if str(report_path):
        _delete_superseded_basic_notes(report_path, out_path)
    processing_status, processing_warning = _deepseek_history_status(payload)
    updates = {
        "interview_score": scoring.get("percent_of_max", 0),
        "determination": scoring.get("outcome", ""),
        "saved_report_path": str(out_path),
        "interview_notes_path": str(out_path),
        "deepseek_processing_status": processing_status,
        "deepseek_processing_warning": processing_warning,
        "deepseek_completed_at": _utc_timestamp(),
    }
    _update_history(job, updates)
    job["deepseek_trace_events"] = list(getattr(config, "trace_events", []) or [])
    _checkpoint_job(job_path, job, payload, scoring)
    _update_ml_dataset(job, payload, scoring, updates, config)
    _write_progress(job, "Complete", "complete")


def run_job(job_path: Path) -> None:
    job_path = Path(job_path)
    job = _load_job(job_path)
    job["_job_path"] = str(job_path)
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
            warning = _deepseek_failure_warning(exc)
            _write_progress(job, warning, "failed")
            _update_history(
                job,
                {
                    "deepseek_processing_status": "failed",
                    "deepseek_processing_warning": warning,
                    "deepseek_completed_at": _utc_timestamp(),
                },
            )
            payload = job.get("payload", {}) if isinstance(job.get("payload"), dict) else {}
            scoring = job.get("scoring", {}) if isinstance(job.get("scoring"), dict) else {}
            _update_ml_dataset(
                job,
                payload,
                scoring,
                {
                    "deepseek_processing_status": "failed",
                    "deepseek_processing_warning": warning,
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
