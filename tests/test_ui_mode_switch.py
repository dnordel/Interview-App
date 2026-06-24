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


def test_ui_mode_relaunch_command_targets_selected_app_without_setup(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()

    command = ui_mode_switch.build_ui_mode_relaunch_command(app_root=app_root, mode="pyside")

    assert command == [ui_mode_switch.current_python_executable(), str(app_root / "src" / "pyside_interview_app.py")]
    assert "setup_and_run.ps1" not in command


def test_switch_to_ui_mode_persists_preference_and_launches_selected_app(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    config_path = tmp_path / "setup_config.json"
    calls = []

    selected = ui_mode_switch.switch_to_ui_mode(
        "tk",
        app_root=app_root,
        config_path=config_path,
        popen=lambda command, **kwargs: calls.append((command, kwargs)),
    )

    assert selected == "tk"
    assert ui_mode_switch.read_preferred_ui_mode(config_path) == "tk"
    assert calls == [
        (
            [ui_mode_switch.current_python_executable(), str(app_root / "src" / "interview_app.pyw")],
            {"cwd": str(app_root)},
        )
    ]


def test_tk_and_pyside_sources_expose_ui_switch_controls() -> None:
    tk_source = Path("src/interview_app.pyw").read_text(encoding="utf-8")
    pyside_source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")

    assert "Current UI: Tk" in tk_source
    assert "Switch to PySide UI" in tk_source
    assert "switch_to_ui_mode(\"pyside\"" in tk_source
    assert "Current UI: PySide" in pyside_source
    assert "Switch to Tk UI" in pyside_source
    assert "switch_to_ui_mode(\"tk\"" in pyside_source
