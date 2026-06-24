from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


UI_MODE_TK = "tk"
UI_MODE_PYSIDE = "pyside"
DEFAULT_UI_MODE = UI_MODE_TK
VALID_UI_MODES = {UI_MODE_TK, UI_MODE_PYSIDE}
UI_MODE_APP_FILES = {
    UI_MODE_TK: "interview_app.pyw",
    UI_MODE_PYSIDE: "pyside_interview_app.py",
}


def normalize_ui_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in VALID_UI_MODES:
        return mode
    return DEFAULT_UI_MODE


def default_setup_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "LPL_InterviewTool" / "setup_config.json"


def read_preferred_ui_mode(config_path: Path | None = None) -> str:
    path = Path(config_path) if config_path is not None else default_setup_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_UI_MODE
    if not isinstance(payload, dict):
        return DEFAULT_UI_MODE
    app = payload.get("App", {})
    if not isinstance(app, dict):
        return DEFAULT_UI_MODE
    return normalize_ui_mode(app.get("PreferredUiMode"))


def write_preferred_ui_mode(config_path: Path | None, mode: str) -> str:
    selected = normalize_ui_mode(mode)
    path = Path(config_path) if config_path is not None else default_setup_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    app = payload.get("App")
    if not isinstance(app, dict):
        app = {}
    app["PreferredUiMode"] = selected
    app["PreferredInterviewAppFile"] = "pyside_interview_app.py" if selected == UI_MODE_PYSIDE else "interview_app.pyw"
    app["InterviewAppPath"] = None
    payload["App"] = app
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return selected


def current_python_executable() -> str:
    return sys.executable or "python"


def build_ui_mode_relaunch_command(*, app_root: Path, mode: str, debug: bool = False) -> list[str]:
    selected = normalize_ui_mode(mode)
    app_path = Path(app_root) / "src" / UI_MODE_APP_FILES[selected]
    return [current_python_executable(), str(app_path)]


def switch_to_ui_mode(
    mode: str,
    *,
    app_root: Path | None = None,
    config_path: Path | None = None,
    debug: bool = False,
    popen: Any = subprocess.Popen,
) -> str:
    selected = write_preferred_ui_mode(config_path, mode)
    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parent.parent
    popen(build_ui_mode_relaunch_command(app_root=root, mode=selected, debug=debug), cwd=str(root))
    return selected


def exit_current_process() -> None:
    sys.exit(0)
