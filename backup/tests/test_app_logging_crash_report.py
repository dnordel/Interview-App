from __future__ import annotations

import json
from pathlib import Path

from app_logging import write_crash_report


def _raise_error() -> None:
    raise RuntimeError("boom")


def test_write_crash_report_includes_origin_and_traceback(tmp_path) -> None:
    try:
        _raise_error()
    except RuntimeError as exc:
        report_path = write_crash_report(
            source="unit_test",
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
            app_root=tmp_path,
        )

    assert report_path is not None
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["source"] == "unit_test"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "boom"
    assert payload["origin"]["function"] == "_raise_error"
    assert payload["origin"]["line"]
    assert Path(payload["origin"]["file"]).name == Path(__file__).name
    assert "RuntimeError: boom" in payload["traceback"]
