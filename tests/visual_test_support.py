from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any


_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TYPOGRAPHY_STRESS_TEXT = "Gghjypq bdfhklt ÅÉQJ"


@dataclass(frozen=True)
class _ExpectedDatabase:
    path: Path
    table: str
    minimum_rows: int


class VisualTestDatabaseRegistry:
    """Allocate visual-test DB paths and verify scenario-specific seeded tables."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._expected: list[_ExpectedDatabase] = []

    def database(self, name: str) -> Path:
        clean = str(name or "").strip()
        if not clean or Path(clean).name != clean or not clean.endswith(".sqlite3"):
            raise ValueError("Visual test database name must be a safe .sqlite3 filename.")
        return self.root / clean

    def expect_seeded(self, path: Path, *, table: str, minimum_rows: int = 1) -> None:
        resolved = Path(path).resolve()
        if self.root.resolve() not in resolved.parents:
            raise ValueError("Visual test database must stay inside its fixture directory.")
        if not _SQL_IDENTIFIER.fullmatch(table):
            raise ValueError("Visual test table must be a safe SQL identifier.")
        if minimum_rows < 1:
            raise ValueError("Visual test database must require at least one row.")
        self._expected.append(_ExpectedDatabase(resolved, table, minimum_rows))

    def verify(self) -> None:
        assert self._expected, "visual test did not register any required test database"
        for expected in self._expected:
            assert expected.path.is_file(), f"visual test database was not created: {expected.path.name}"
            with sqlite3.connect(expected.path) as conn:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (expected.table,),
                ).fetchone()
                assert table is not None, f"visual test database missing table: {expected.table}"
                row_count = int(conn.execute(f'SELECT COUNT(*) FROM "{expected.table}"').fetchone()[0])
            assert row_count >= expected.minimum_rows, (
                f"visual test database {expected.path.name} requires "
                f"{expected.minimum_rows} representative rows in {expected.table}"
            )


def configure_visual_test_app(app: Any) -> None:
    """Use an installed text font so offscreen screenshots remain readable."""

    from PySide6 import QtGui

    family = ""
    font_candidates = (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in font_candidates:
        if not path.is_file():
            continue
        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            family = families[0]
            break
    assert family, "visual test could not load a font with real glyphs"
    app.setFont(QtGui.QFont(family, 10))
    raw_font = QtGui.QRawFont.fromFont(app.font())
    assert raw_font.isValid()
    assert all(index > 1 for index in raw_font.glyphIndexesForString(TYPOGRAPHY_STRESS_TEXT))


def assert_vertical_text_fits(widget: Any, text: str = "") -> None:
    """Assert ascenders and descenders fit inside one rendered widget content box."""

    visible_text = text or widget.text()
    metrics = widget.fontMetrics()
    assert all(letter in visible_text for letter in "ygjhpq")
    assert widget.contentsRect().height() >= metrics.boundingRect(visible_text).height()
