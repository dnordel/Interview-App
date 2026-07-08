from __future__ import annotations

from platform_services import (
    LOG_FORMAT,
    _configure_runtime_logging,
    _enable_faulthandler,
    _env_debug_enabled,
    _install_global_exception_hooks,
    _install_trace_logging,
    _is_under_root,
    _resolve_app_root,
    main,
    parse_args,
    traceback_origin,
    write_wrapper_crash_report,
)

__all__ = [
    "LOG_FORMAT",
    "_configure_runtime_logging",
    "_enable_faulthandler",
    "_env_debug_enabled",
    "_install_global_exception_hooks",
    "_install_trace_logging",
    "_is_under_root",
    "_resolve_app_root",
    "main",
    "parse_args",
    "traceback_origin",
    "write_wrapper_crash_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
