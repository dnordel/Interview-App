from __future__ import annotations

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
