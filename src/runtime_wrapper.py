from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import os
import runpy
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_TRACE_EVENTS = {"call", "exception", "return"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime wrapper for GUI entrypoints")
    parser.add_argument("--target", required=True, help="Path to the target Python script to execute")
    parser.add_argument("--app-root", default="", help="Optional repository/app root for logs")
    parser.add_argument("--debug", action="store_true", help="Enable deep call tracing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_path = Path(args.target).expanduser().resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target script not found: {target_path}")

    app_root = _resolve_app_root(target_path, args.app_root)
    log_paths = _configure_runtime_logging(app_root)
    _install_global_exception_hooks(app_root, log_paths["runtime_log"])
    _install_tk_callback_hook(app_root, log_paths["runtime_log"])
    _enable_faulthandler(log_paths["fault_log"])

    debug_enabled = bool(args.debug or _env_debug_enabled())
    if debug_enabled:
        _install_trace_logging(app_root, log_paths["trace_log"])

    logger = logging.getLogger("runtime_wrapper")
    logger.info("runtime_wrapper_start", extra={"target": str(target_path), "debug": debug_enabled})

    try:
        runpy.run_path(str(target_path), run_name="__main__")
    except BaseException as exc:  # noqa: BLE001
        report_path = write_wrapper_crash_report(
            app_root=app_root,
            source="runtime_wrapper_main",
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
        )
        logger.error("runtime_wrapper_fatal_exception", exc_info=(type(exc), exc, exc.__traceback__))
        logger.error("runtime_wrapper_crash_report", extra={"path": str(report_path) if report_path else ""})
        raise

    logger.info("runtime_wrapper_exit")
    return 0


def _resolve_app_root(target_path: Path, arg_root: str) -> Path:
    if arg_root.strip():
        return Path(arg_root).expanduser().resolve()
    candidate = target_path.parent.parent
    if (candidate / "src").exists():
        return candidate
    return target_path.parent


def _configure_runtime_logging(app_root: Path) -> dict[str, Path]:
    log_dir = app_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    runtime_log = log_dir / "runtime_wrapper.log"
    fault_log = log_dir / "runtime_faults.log"
    trace_log = log_dir / "runtime_trace.log"

    logging.basicConfig(
        level=logging.DEBUG if _env_debug_enabled() else logging.INFO,
        format=LOG_FORMAT,
        handlers=[logging.FileHandler(runtime_log, encoding="utf-8")],
        force=True,
    )
    return {"runtime_log": runtime_log, "fault_log": fault_log, "trace_log": trace_log}


def _enable_faulthandler(fault_log_path: Path) -> None:
    stream = fault_log_path.open("a", encoding="utf-8")
    faulthandler.enable(file=stream, all_threads=True)


def _install_global_exception_hooks(app_root: Path, runtime_log_path: Path) -> None:
    logger = logging.getLogger("runtime_wrapper")

    def _sys_hook(exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType | None) -> None:
        report = write_wrapper_crash_report(
            app_root=app_root,
            source="sys_excepthook",
            exc_type=exc_type,
            exc_value=exc_value,
            exc_traceback=exc_traceback,
        )
        logger.error("uncaught_main_exception", exc_info=(exc_type, exc_value, exc_traceback))
        logger.error("crash_report_written", extra={"path": str(report) if report else "", "runtime_log": str(runtime_log_path)})

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        report = write_wrapper_crash_report(
            app_root=app_root,
            source="thread_excepthook",
            exc_type=args.exc_type,
            exc_value=args.exc_value,
            exc_traceback=args.exc_traceback,
        )
        logger.error(
            "uncaught_thread_exception",
            extra={"thread_name": args.thread.name if args.thread else "unknown", "report": str(report) if report else ""},
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def _install_tk_callback_hook(app_root: Path, runtime_log_path: Path) -> None:
    try:
        import tkinter as tk
    except Exception:
        logging.getLogger("runtime_wrapper").debug("tkinter_not_available")
        return

    original = tk.Tk.report_callback_exception

    def _wrapped(self: Any, exc: type[BaseException], val: BaseException, tb: TracebackType | None) -> None:
        report = write_wrapper_crash_report(
            app_root=app_root,
            source="tk_callback_global",
            exc_type=exc,
            exc_value=val,
            exc_traceback=tb,
        )
        logging.getLogger("runtime_wrapper").error(
            "tk_global_callback_exception",
            extra={"report": str(report) if report else "", "runtime_log": str(runtime_log_path)},
            exc_info=(exc, val, tb),
        )
        original(self, exc, val, tb)

    tk.Tk.report_callback_exception = _wrapped


def _install_trace_logging(app_root: Path, trace_path: Path) -> None:
    trace_handle = trace_path.open("a", encoding="utf-8")
    trace_handle.write(f"\n=== TRACE START {datetime.now().isoformat(timespec='seconds')} ===\n")

    def _trace(frame: FrameType, event: str, arg: Any) -> Any:
        if event not in _TRACE_EVENTS:
            return _trace
        code = frame.f_code
        file_path = Path(code.co_filename)
        if not _is_under_root(file_path, app_root):
            return _trace
        if event == "return" and code.co_name == "<module>":
            return _trace
        line = frame.f_lineno
        trace_handle.write(f"{time.time():.3f} {event:<9} {file_path}:{line} {code.co_name}\n")
        return _trace

    sys.settrace(_trace)
    threading.settrace(_trace)


def _is_under_root(file_path: Path, app_root: Path) -> bool:
    try:
        file_path.resolve().relative_to(app_root.resolve())
    except Exception:
        return False
    return True


def _env_debug_enabled() -> bool:
    return str(os.getenv("INTERVIEW_APP_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on", "debug"}


def write_wrapper_crash_report(
    *,
    app_root: Path,
    source: str,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> Path | None:
    crash_dir = app_root / "logs" / "crash-reports"
    crash_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = crash_dir / f"wrapper-crash-{stamp}.json"
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "error_type": exc_type.__name__,
        "error_message": str(exc_value),
        "origin": traceback_origin(exc_traceback),
        "python": sys.version,
        "cwd": str(Path.cwd()),
        "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).strip(),
    }
    try:
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("runtime_wrapper").exception("wrapper_crash_report_write_failed")
        return None
    return report_path


def traceback_origin(exc_traceback: TracebackType | None) -> dict[str, Any]:
    if exc_traceback is None:
        return {"function": "<unknown>", "line": None, "file": "<unknown>"}
    current = exc_traceback
    while current.tb_next is not None:
        current = current.tb_next
    code = current.tb_frame.f_code
    return {"function": code.co_name, "line": int(current.tb_lineno), "file": str(code.co_filename)}


if __name__ == "__main__":
    raise SystemExit(main())
