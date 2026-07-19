from __future__ import annotations

from pathlib import Path

from app_branding import APP_ICON_PATH, WINDOWS_APP_USER_MODEL_ID, apply_staffing_app_icon


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
