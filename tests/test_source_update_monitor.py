import os
from pathlib import Path

import pytest

from source_update_monitor import SourceUpdateDetector, build_source_update_banner, relaunch_application
from tools.update_source_version import DEFAULT_SOURCE_ROOT, DEFAULT_VERSION_PATH, source_digest, write_source_version


def test_source_update_detector_latches_changed_version_stamp(tmp_path: Path) -> None:
    version_file = tmp_path / "source_version.txt"
    version_file.write_text("updated_at=2026-07-16T12:00:00Z\nsource_sha256=one\n", encoding="utf-8")
    detector = SourceUpdateDetector(version_file)

    assert detector.poll() == ()

    version_file.write_text("updated_at=2026-07-16T12:05:00Z\nsource_sha256=two\n", encoding="utf-8")

    assert detector.poll() == (version_file.resolve(),)
    assert detector.poll() == (version_file.resolve(),)


def test_source_update_detector_waits_until_delayed_source_matches_stamp(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    module = source_root / "feature.py"
    module.write_text("VERSION = 1\n", encoding="utf-8")
    version_file = tmp_path / "source_version.txt"
    write_source_version(
        source_root=source_root,
        version_path=version_file,
        updated_at="2026-07-16T12:00:00Z",
    )
    detector = SourceUpdateDetector(version_file, source_root=source_root)
    module.write_text("VERSION = 2\n", encoding="utf-8")
    delayed_digest = source_digest(source_root)
    module.write_text("VERSION = 1\n", encoding="utf-8")
    version_file.write_text(
        f"updated_at=2026-07-16T12:05:00Z\nsource_sha256={delayed_digest}\n",
        encoding="utf-8",
    )

    assert detector.poll() == ()

    module.write_text("VERSION = 2\n", encoding="utf-8")
    assert detector.poll() == (version_file.resolve(),)


@pytest.mark.pyside_gui
def test_source_update_banner_explains_update_and_restarts_on_click() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    restarted: list[bool] = []

    banner, button = build_source_update_banner(qt_widgets, lambda: restarted.append(True))

    assert banner.isHidden()
    assert "updated" in banner.findChild(qt_widgets.QLabel).text().lower()
    assert button.text() == "Restart App"
    button.click()
    app.processEvents()
    assert restarted == [True]


def test_relaunch_application_starts_replacement_before_closing_current(tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeQProcess:
        @staticmethod
        def startDetached(program: str, arguments: list[str], cwd: str) -> tuple[bool, int]:
            calls.append(("start", program, arguments, cwd))
            return True, 42

    class FakeQtCore:
        QProcess = FakeQProcess

    started = relaunch_application(
        FakeQtCore,
        lambda: calls.append(("close",)),
        executable="python.exe",
        argv=["src/app.py", "--director-school", "Palmdale"],
        frozen=False,
        cwd=tmp_path,
    )

    assert started is True
    assert calls == [
        ("start", "python.exe", [str((Path.cwd() / "src/app.py").resolve()), "--director-school", "Palmdale"], str(tmp_path.resolve())),
        ("close",),
    ]


def test_source_version_tool_writes_time_and_source_digest(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    module = source_root / "feature.py"
    module.write_text("VERSION = 1\n", encoding="utf-8")
    version_path = tmp_path / "source_version.txt"

    first = write_source_version(
        source_root=source_root,
        version_path=version_path,
        updated_at="2026-07-16T12:00:00Z",
    )
    module.write_text("VERSION = 2\n", encoding="utf-8")
    second = write_source_version(
        source_root=source_root,
        version_path=version_path,
        updated_at="2026-07-16T12:05:00Z",
    )

    assert first["updated_at"] == "2026-07-16T12:00:00Z"
    assert second["updated_at"] == "2026-07-16T12:05:00Z"
    assert first["source_sha256"] != second["source_sha256"]
    assert version_path.read_text(encoding="utf-8") == (
        f"updated_at={second['updated_at']}\nsource_sha256={second['source_sha256']}\n"
    )


def test_source_version_tool_preserves_stamp_when_source_is_unchanged(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "feature.py").write_text("VERSION = 1\n", encoding="utf-8")
    version_path = tmp_path / "source_version.txt"
    first = write_source_version(
        source_root=source_root,
        version_path=version_path,
        updated_at="2026-07-16T12:00:00Z",
    )

    second = write_source_version(
        source_root=source_root,
        version_path=version_path,
        updated_at="2026-07-16T12:05:00Z",
    )

    assert second == first
    assert "updated_at=2026-07-16T12:00:00Z" in version_path.read_text(encoding="utf-8")


def test_repository_source_version_stamp_matches_current_source() -> None:
    payload = dict(
        line.split("=", 1)
        for line in DEFAULT_VERSION_PATH.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )

    assert payload["updated_at"].endswith("Z")
    assert payload["source_sha256"] == source_digest(DEFAULT_SOURCE_ROOT)
