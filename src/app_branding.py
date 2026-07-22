from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
APP_ICON_PATH = APP_ROOT / "assets" / "staffing_app.ico"
WINDOWS_APP_USER_MODEL_ID = "LaunchPadLearning.StaffingApp"


def set_windows_app_user_model_id() -> bool:
    """Set taskbar identity before Qt creates any native windows."""
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        return False
    return True


def apply_staffing_app_icon(QtGui: Any, app: Any, window: Any | None = None) -> bool:
    """Apply shared Staffing icon and Windows taskbar identity when available."""
    set_windows_app_user_model_id()
    icon = QtGui.QIcon(str(APP_ICON_PATH))
    if icon.isNull():
        return False
    app.setWindowIcon(icon)
    if window is not None:
        window.setWindowIcon(icon)
    return True
