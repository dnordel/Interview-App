from __future__ import annotations

import platform_services as _platform_services

if globals().get("_APP_LOGGING_WRAPPER_LOADED"):
    _platform_services._APP_LOG_PATH = None
    _platform_services._INITIALIZED = False
_APP_LOGGING_WRAPPER_LOADED = True

from platform_services import (
    JsonLogFormatter,
    RedactionFilter,
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    _SENSITIVE_KEYS,
    _attach_handler_once,
    _build_crash_payload,
    _redact_field,
    _redact_text,
    _redact_value,
    _resolve_log_level,
    _serialize,
    _traceback_origin,
    get_configured_log_path,
    initialize_app_logging,
    install_uncaught_exception_hooks,
    write_crash_report,
)

__all__ = [
    "JsonLogFormatter",
    "RedactionFilter",
    "_EMAIL_PATTERN",
    "_PHONE_PATTERN",
    "_SENSITIVE_KEYS",
    "_attach_handler_once",
    "_build_crash_payload",
    "_redact_field",
    "_redact_text",
    "_redact_value",
    "_resolve_log_level",
    "_serialize",
    "_traceback_origin",
    "get_configured_log_path",
    "initialize_app_logging",
    "install_uncaught_exception_hooks",
    "write_crash_report",
]
