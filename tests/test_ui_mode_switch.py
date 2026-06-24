from __future__ import annotations

import json
from pathlib import Path

import ui_mode_switch


def test_ui_mode_config_defaults_to_tk_and_persists_selected_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "setup_config.json"

    assert ui_mode_switch.read_preferred_ui_mode(config_path) == "tk"

    ui_mode_switch.write_preferred_ui_mode(config_path, "pyside")

    assert ui_mode_switch.read_preferred_ui_mode(config_path) == "pyside"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["App"]["PreferredUiMode"] == "pyside"


def test_ui_mode_relaunch_command_targets_setup_script_and_selected_mode(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()

    command = ui_mode_switch.build_ui_mode_relaunch_command(app_root=app_root, mode="pyside")

    assert command[-3:] == [str(app_root / "setup_and_run.ps1"), "-UiMode", "pyside"]
    assert "-ExecutionPolicy" in command


def test_tk_and_pyside_sources_expose_ui_switch_controls() -> None:
    tk_source = Path("src/interview_app.pyw").read_text(encoding="utf-8")
    pyside_source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")

    assert "Tk UI" in tk_source
    assert "PySide UI" in tk_source
    assert "switch_to_ui_mode(\"pyside\"" in tk_source
    assert "Tk UI" in pyside_source
    assert "PySide UI" in pyside_source
    assert "switch_to_ui_mode(\"tk\"" in pyside_source
