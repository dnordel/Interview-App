from __future__ import annotations

import json
from pathlib import Path

import platform_services
import runtime_wrapper


def _explode() -> None:
    raise ValueError("trace me")


def test_runtime_wrapper_forwards_to_platform_services() -> None:
    assert runtime_wrapper.parse_args is platform_services.parse_args
    assert runtime_wrapper.main is platform_services.main
    assert runtime_wrapper.write_wrapper_crash_report is platform_services.write_wrapper_crash_report
    assert runtime_wrapper.traceback_origin is platform_services.traceback_origin


def test_write_wrapper_crash_report_captures_origin(tmp_path) -> None:
    try:
        _explode()
    except ValueError as exc:
        report_path = runtime_wrapper.write_wrapper_crash_report(
            app_root=tmp_path,
            source="unit",
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
        )

    assert report_path is not None
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["source"] == "unit"
    assert payload["error_type"] == "ValueError"
    assert payload["origin"]["function"] == "_explode"
    assert Path(payload["origin"]["file"]).name == Path(__file__).name
    assert "ValueError: trace me" in payload["traceback"]


def test_traceback_origin_without_traceback() -> None:
    origin = runtime_wrapper.traceback_origin(None)
    assert origin["function"] == "<unknown>"
    assert origin["line"] is None
