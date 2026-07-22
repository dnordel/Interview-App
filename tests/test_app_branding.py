from __future__ import annotations

from pathlib import Path

import app_branding
from app_branding import (
    APP_ICON_PATH,
    WINDOWS_APP_USER_MODEL_ID,
    apply_staffing_app_icon,
    set_windows_app_user_model_id,
)


def test_staffing_icon_asset_is_portable_multisize_ico() -> None:
    payload = Path(APP_ICON_PATH).read_bytes()

    assert payload[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(payload[4:6], "little") >= 5


def test_apply_staffing_app_icon_sets_application_and_window_icon() -> None:
    calls: list[tuple[str, object]] = []

    class FakeIcon:
        def __init__(self, path: str) -> None:
            calls.append(("path", path))

        def isNull(self) -> bool:
            return False

    class FakeQtGui:
        QIcon = FakeIcon

    class FakeTarget:
        def setWindowIcon(self, icon: object) -> None:
            calls.append(("icon", icon))

    app = FakeTarget()
    window = FakeTarget()

    assert apply_staffing_app_icon(FakeQtGui, app, window) is True
    assert calls[0] == ("path", str(APP_ICON_PATH))
    assert [kind for kind, _value in calls].count("icon") == 2
    assert WINDOWS_APP_USER_MODEL_ID == "LaunchPadLearning.StaffingApp"


def test_set_windows_app_user_model_id_calls_windows_shell(monkeypatch) -> None:
    calls: list[str] = []

    class FakeShell32:
        def SetCurrentProcessExplicitAppUserModelID(self, value: str) -> None:
            calls.append(value)

    class FakeWindll:
        shell32 = FakeShell32()

    monkeypatch.setattr(app_branding.sys, "platform", "win32")
    monkeypatch.setattr(app_branding.ctypes, "windll", FakeWindll(), raising=False)

    assert set_windows_app_user_model_id() is True
    assert calls == [WINDOWS_APP_USER_MODEL_ID]


def test_set_windows_app_user_model_id_skips_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(app_branding.sys, "platform", "linux")

    assert set_windows_app_user_model_id() is False


def test_entrypoints_set_identity_and_icon_before_main_window_creation() -> None:
    pyside_source = Path("src/pyside_interview_app.py").read_text(encoding="utf-8")
    pyside_launch = pyside_source[pyside_source.index("def launch_pyside_interview_app(") :]
    assert pyside_launch.index("set_windows_app_user_model_id()") < pyside_launch.index("_import_qt()")
    interview_window_constructor = "window = PySideInterview" + "Window("
    assert pyside_launch.index("apply_staffing_app_icon(_QtGui, app)") < pyside_launch.index(
        interview_window_constructor
    )

    director_source = Path("src/director_staffing_app.py").read_text(encoding="utf-8")
    director_launch = director_source[director_source.index("def launch_director_staffing_app(") :]
    assert director_launch.index("set_windows_app_user_model_id()") < director_launch.index("from PySide6 import")
    assert director_launch.index("apply_staffing_app_icon(QtGui, app)") < director_launch.index(
        "window = QtWidgets.QMainWindow()"
    )
