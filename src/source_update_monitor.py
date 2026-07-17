from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
from typing import Any, Callable


class SourceUpdateDetector:
    """Detect a changed source-version stamp, then latch the result."""

    def __init__(self, version_path: Path, *, source_root: Path | None = None) -> None:
        self.version_path = Path(version_path).resolve()
        self.source_root = None if source_root is None else Path(source_root).resolve()
        self._baseline = self._read_version()
        self._detected: tuple[Path, ...] = ()

    def poll(self) -> tuple[Path, ...]:
        if self._detected:
            return self._detected
        current = self._read_version()
        if current is not None and current != self._baseline:
            if self.source_root is not None:
                expected = _stamp_value(current, "source_sha256")
                try:
                    ready = bool(expected) and source_digest(self.source_root) == expected
                except (OSError, ValueError):
                    ready = False
                if not ready:
                    return ()
            self._detected = (self.version_path,)
        return self._detected

    def _read_version(self) -> str | None:
        try:
            return self.version_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None


def source_digest(source_root: Path) -> str:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError("Source root must be an existing directory.")
    digest = sha256()
    files = sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts and path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _stamp_value(stamp: str, key: str) -> str:
    for line in stamp.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == key:
            return value.strip()
    return ""


def build_source_update_banner(
    QtWidgets: Any,
    restart_callback: Callable[[], None],
) -> tuple[Any, Any]:
    banner = QtWidgets.QFrame()
    banner.setObjectName("SourceUpdateBanner")
    banner.setStyleSheet(
        "QFrame#SourceUpdateBanner { background: #fff4cc; border: 1px solid #e7b84b; }"
        "QLabel { color: #5c4300; font-weight: 600; }"
        "QPushButton { background: #2563eb; color: white; border: 0; border-radius: 6px;"
        " padding: 7px 14px; font-weight: 700; }"
    )
    layout = QtWidgets.QHBoxLayout(banner)
    layout.setContentsMargins(14, 8, 14, 8)
    label = QtWidgets.QLabel("App source was updated. Restart to load latest version.")
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    button = QtWidgets.QPushButton("Restart App")
    button.setObjectName("SourceUpdateRestartButton")
    button.clicked.connect(restart_callback)
    layout.addWidget(button)
    banner.hide()
    return banner, button


def relaunch_application(
    QtCore: Any,
    close_current: Callable[[], None],
    *,
    executable: str | None = None,
    argv: list[str] | None = None,
    frozen: bool | None = None,
    cwd: Path | None = None,
) -> bool:
    program = str(executable or sys.executable).strip()
    arguments = list(sys.argv if argv is None else argv)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not program:
        raise ValueError("Application executable is required.")
    if not arguments:
        raise ValueError("Application launch arguments are required.")
    restart_arguments = arguments[1:] if is_frozen else [str(Path(arguments[0]).resolve()), *arguments[1:]]
    working_directory = str(Path(cwd or Path.cwd()).resolve())
    result = QtCore.QProcess.startDetached(program, restart_arguments, working_directory)
    started = bool(result[0]) if isinstance(result, tuple) else bool(result)
    if started:
        close_current()
    return started
