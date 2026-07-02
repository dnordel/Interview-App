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


def test_runtime_wrapper_forwards_target_args(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    output = tmp_path / "argv.json"
    target.write_text(
        "import json, sys\n"
        f"open({str(output)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv))\n",
        encoding="utf-8",
    )

    exit_code = runtime_wrapper.main(
        [
            "--target",
            str(target),
            "--app-root",
            str(tmp_path),
            "--director-staffing",
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == [str(target.resolve()), "--director-staffing"]


def test_runtime_wrapper_treats_zero_system_exit_as_success(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("raise SystemExit(0)\n", encoding="utf-8")

    assert runtime_wrapper.main(["--target", str(target), "--app-root", str(tmp_path)]) == 0
    assert not list(tmp_path.glob("logs/diagnostics/runtime_wrapper_main_*.json"))


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
