from __future__ import annotations

import ast
from pathlib import Path

from tools import pytest_duration_catalog


def test_determine_placement_uses_duration_and_gui_weight() -> None:
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=31.0, gui_heavy=True) == "gui_wave_1"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=21.0, gui_heavy=True) == "gui_wave_2"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=1.0, gui_heavy=True) == "gui_wave_3"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=16.0, gui_heavy=False) == "non_gui_tail"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=6.0, gui_heavy=False) == "non_gui_middle"
    assert pytest_duration_catalog.determine_placement(duration_seconds_n2=0.5, gui_heavy=False) == "fast"


def test_duration_catalog_covers_collected_tests() -> None:
    nodeids = set(pytest_duration_catalog.collect_nodeids())
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()

    assert not nodeids.difference(entries)
    assert not set(entries).difference(nodeids)
    for entry in entries.values():
        assert isinstance(entry["duration_seconds_n2"], (int, float))
        assert isinstance(entry["gui_heavy"], bool)
        assert entry["placement"] == pytest_duration_catalog.determine_placement(
            duration_seconds_n2=float(entry["duration_seconds_n2"]),
            gui_heavy=entry["gui_heavy"],
        )


def test_qt_gui_tests_are_cataloged_as_gui_scenarios() -> None:
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()
    offenders: list[str] = []
    gui_surface_tokens = ("QApplication", "PySideInterviewWindow", "StaffingDashboardV2Page")
    test_paths = sorted(Path("tests").glob("test_*.py"))

    for path in test_paths:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if path.name == "test_pytest_duration_catalog.py" and node.name == "test_qt_gui_tests_are_cataloged_as_gui_scenarios":
                continue
            source = ast.get_source_segment(text, node) or ""
            if not any(token in source for token in gui_surface_tokens):
                continue
            nodeid = f"{path.as_posix()}::{node.name}"
            if not bool(entries.get(nodeid, {}).get("gui_heavy", False)):
                offenders.append(nodeid)

    assert offenders == [], "Qt GUI tests must be cataloged with gui_heavy: true:\n" + "\n".join(offenders)


def test_gui_scenario_catalog_entries_have_measured_scheduler_durations() -> None:
    entries = pytest_duration_catalog.catalog_entries_by_nodeid()
    offenders = [
        nodeid
        for nodeid, entry in entries.items()
        if bool(entry.get("gui_heavy", False)) and entry.get("duration_source") != "measured"
    ]

    assert offenders == [], "GUI scenario tests must have measured scheduler durations:\n" + "\n".join(offenders)
