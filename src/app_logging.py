from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import threading
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Any

_APP_LOG_PATH: Path | None = None
_INITIALIZED = False

_ENV_DEBUG_VALUES = {"1", "true", "yes", "on", "debug"}
_DEFAULT_LEVEL = logging.INFO
_DEBUG_LEVEL = logging.DEBUG
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_SENSITIVE_KEYS = {
    "candidate_name",
    "candidate_first_name",
    "candidate_last_name",
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "address",
}

_STANDARD_RECORD_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
}

_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)")


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        redacted_extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS:
                continue
            redacted_extras[key] = _redact_field(key, value)

        for key, value in redacted_extras.items():
            setattr(record, key, value)

        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(_redact_value(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_value(value) for key, value in record.args.items()}

        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "thread": record.threadName,
            "message": record.getMessage(),
        }

        extras = self._collect_extras(record)
        if extras:
            payload["context"] = extras

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)

    def _collect_extras(self, record: logging.LogRecord) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS:
                continue
            extras[key] = _serialize(value)
        return extras


def initialize_app_logging(*, app_root: Path | None = None) -> Path:
    global _APP_LOG_PATH, _INITIALIZED
    if _INITIALIZED and _APP_LOG_PATH is not None:
        return _APP_LOG_PATH

    root = app_root or Path(__file__).resolve().parent.parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / "interview-app.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(RedactionFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(_resolve_log_level())
    _attach_handler_once(root_logger, handler)

    _APP_LOG_PATH = log_path
    _INITIALIZED = True
    install_uncaught_exception_hooks()
    logging.getLogger(__name__).info(
        "app_logging_initialized",
        extra={"log_path": str(log_path), "log_level": logging.getLevelName(root_logger.level)},
    )
    return log_path


def get_configured_log_path() -> Path | None:
    return _APP_LOG_PATH


def write_crash_report(
    *,
    source: str,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
    app_root: Path | None = None,
) -> Path | None:
    root = app_root or Path(__file__).resolve().parent.parent
    crash_dir = root / "logs" / "crash-reports"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    file_path = crash_dir / f"crash-{stamp}.log"
    payload = _build_crash_payload(source, exc_type, exc_value, exc_traceback)
    try:
        crash_dir.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return file_path
    except Exception:
        logging.getLogger(__name__).exception("crash_report_write_failed")
        return None


def _build_crash_payload(
    source: str,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> dict[str, Any]:
    origin = _traceback_origin(exc_traceback)
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "error_type": exc_type.__name__,
        "error_message": str(exc_value),
        "origin": origin,
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
        "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).strip(),
    }


def _traceback_origin(exc_traceback: TracebackType | None) -> dict[str, Any]:
    if exc_traceback is None:
        return {"function": "<unknown>", "line": None, "file": "<unknown>"}
    last_tb = exc_traceback
    while last_tb.tb_next is not None:
        last_tb = last_tb.tb_next
    frame = last_tb.tb_frame
    code = frame.f_code
    return {
        "function": code.co_name,
        "line": int(last_tb.tb_lineno),
        "file": str(code.co_filename),
    }


def install_uncaught_exception_hooks() -> None:
    def _sys_hook(exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType | None) -> None:
        logging.getLogger(__name__).error(
            "uncaught_main_exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logging.getLogger(__name__).error(
            "uncaught_thread_exception",
            extra={"thread_name": args.thread.name if args.thread else "unknown"},
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def _attach_handler_once(root_logger: logging.Logger, handler: logging.Handler) -> None:
    target_path = getattr(handler, "baseFilename", None)
    for existing in root_logger.handlers:
        existing_path = getattr(existing, "baseFilename", None)
        if target_path is not None and existing_path == target_path:
            return
    root_logger.addHandler(handler)


def _resolve_log_level() -> int:
    value = os.getenv("INTERVIEW_APP_LOG_LEVEL", "").strip()
    if value:
        parsed_level = logging.getLevelName(value.upper())
        if isinstance(parsed_level, int):
            return parsed_level
    debug_enabled = os.getenv("INTERVIEW_APP_DEBUG", "").strip().lower() in _ENV_DEBUG_VALUES
    if debug_enabled:
        return _DEBUG_LEVEL
    return _DEFAULT_LEVEL


def _redact_field(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    return _redact_value(value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _redact_text(text: str) -> str:
    masked = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    return _PHONE_PATTERN.sub("[REDACTED_PHONE]", masked)


def _serialize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return str(value)
