from __future__ import annotations

from types import SimpleNamespace

import tests.conftest as test_conftest


class _FakeConfig:
    class Option:
        maxschedchunk = None

    option = Option()


class _FakeItem:
    def __init__(self, name: str, slow: bool = False) -> None:
        self.name = name
        self.nodeid = name
        self.slow = slow

    def get_closest_marker(self, marker_name: str) -> object | None:
        return object() if marker_name == "slow_pyside" and self.slow else None


def test_gui_cleanup_deletes_surviving_windows_and_flushes_deferred_events(monkeypatch) -> None:
    calls: list[object] = []

    class Widget:
        def deleteLater(self) -> None:
            calls.append("delete")

    class Application:
        @staticmethod
        def instance():
            return Application()

        def topLevelWidgets(self):
            return [Widget(), Widget()]

        def processEvents(self) -> None:
            calls.append("process")

    class CoreApplication:
        @staticmethod
        def sendPostedEvents(receiver, event_type) -> None:
            calls.append((receiver, event_type))

    class Event:
        class Type:
            DeferredDelete = "deferred-delete"

    qt_widgets = type("QtWidgets", (), {})
    setattr(qt_widgets, "Q" + "Application", Application)
    qt_core = type("QtCore", (), {"QCoreApplication": CoreApplication, "QEvent": Event})
    monkeypatch.setattr(test_conftest.gc, "collect", lambda: calls.append("collect"))

    deleted = test_conftest._dispose_qt_top_level_widgets(qt_widgets, qt_core)

    assert deleted == 2
    assert calls == [
        "process",
        "delete",
        "delete",
        (None, "deferred-delete"),
        "collect",
    ]


def test_duration_reporting_includes_setup_call_and_teardown(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_DURATION_CATALOG_OUT", "timings.yaml")
    test_conftest._DURATION_REPORTS.clear()

    for phase, duration in (("setup", 0.1), ("call", 0.2), ("teardown", 0.3)):
        test_conftest.pytest_runtest_logreport(
            SimpleNamespace(nodeid="tests/test_gui.py::test_window", when=phase, duration=duration)
        )

    assert round(test_conftest._DURATION_REPORTS["tests/test_gui.py::test_window"], 3) == 0.6


def test_spread_slow_pyside_items_interleaves_gui_heavy_tests() -> None:
    items = [_FakeItem(f"fast_{index}") for index in range(12)] + [
        _FakeItem(f"slow_{index}", slow=True) for index in range(3)
    ]

    ordered = test_conftest._spread_slow_pyside_items(items, worker_count=12)

    assert [item.name for item in ordered] == [
        "slow_0",
        "slow_1",
        "slow_2",
        "fast_0",
        "fast_1",
        "fast_2",
        "fast_3",
        "fast_4",
        "fast_5",
        "fast_6",
        "fast_7",
        "fast_8",
        "fast_9",
        "fast_10",
        "fast_11",
    ]


def test_pytest_config_forces_single_xdist_schedule_chunk() -> None:
    config = _FakeConfig()

    test_conftest._force_xdist_maxschedchunk_one(config)

    assert config.option.maxschedchunk == 1


def test_spread_slow_pyside_items_uses_duration_weights_for_heavy_waves(monkeypatch) -> None:
    items = [_FakeItem(f"fast_{index}") for index in range(8)] + [
        _FakeItem("slow_short", slow=True),
        _FakeItem("slow_long", slow=True),
        _FakeItem("slow_medium", slow=True),
        _FakeItem("slow_tiny", slow=True),
        _FakeItem("slow_middle", slow=True),
    ]
    monkeypatch.setattr(
        test_conftest,
        "_duration_catalog_by_nodeid",
        lambda: {
            "slow_long": {"duration_seconds_n2": 50.0, "duration_source": "measured"},
            "slow_middle": {"duration_seconds_n2": 30.0, "duration_source": "measured"},
            "slow_medium": {"duration_seconds_n2": 20.0, "duration_source": "measured"},
            "slow_short": {"duration_seconds_n2": 10.0, "duration_source": "measured"},
            "slow_tiny": {"duration_seconds_n2": 1.0, "duration_source": "measured"},
        },
    )

    ordered = test_conftest._spread_slow_pyside_items(items, worker_count=12)

    assert [item.name for item in ordered] == [
        "slow_long",
        "slow_middle",
        "slow_medium",
        "slow_short",
        "fast_0",
        "fast_1",
        "fast_2",
        "fast_3",
        "slow_tiny",
        "fast_4",
        "fast_5",
        "fast_6",
        "fast_7",
    ]


def test_gui_order_runs_long_tests_then_unmeasured_new_tests_then_short_tests(monkeypatch) -> None:
    items = [_FakeItem("short"), _FakeItem("new"), _FakeItem("long")]
    monkeypatch.setattr(
        test_conftest,
        "_duration_catalog_by_nodeid",
        lambda: {
            "long": {"duration_seconds_n2": 12.0, "duration_source": "measured"},
            "new": {"duration_seconds_n2": 0.001, "duration_source": "collection_default"},
            "short": {"duration_seconds_n2": 1.0, "duration_source": "measured"},
        },
    )

    ordered = test_conftest._order_pyside_gui_items(items)

    assert [item.name for item in ordered] == ["long", "new", "short"]
