from __future__ import annotations

import json
from pathlib import Path

import ui_mode_switch


def test_ui_mode_config_defaults_to_pyside_and_persists_selected_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "setup_config.json"

    assert ui_mode_switch.read_preferred_ui_mode(config_path) == "pyside"

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


def test_switch_to_ui_mode_normalizes_legacy_tk_to_pyside(tmp_path: Path) -> None:
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

    assert selected == "pyside"
    assert ui_mode_switch.read_preferred_ui_mode(config_path) == "pyside"
    assert calls == [
        (
            [ui_mode_switch.current_python_executable(), str(app_root / "src" / "pyside_interview_app.py")],
            {"cwd": str(app_root)},
        )
    ]


def test_pyside_source_does_not_expose_tk_switch() -> None:
    pyside_source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")

    assert "Current UI: PySide" not in pyside_source
    assert "Switch to " + "Tk UI" not in pyside_source
    assert "switch_to_ui_mode(\"tk\"" not in pyside_source
