from __future__ import annotations

import tests.conftest as test_conftest


class _FakeItem:
    def __init__(self, name: str, slow: bool = False) -> None:
        self.name = name
        self.nodeid = name
        self.slow = slow

    def get_closest_marker(self, marker_name: str) -> object | None:
        return object() if marker_name == "slow_pyside" and self.slow else None


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
            "slow_long": {"duration_seconds_n2": 50.0},
            "slow_middle": {"duration_seconds_n2": 30.0},
            "slow_medium": {"duration_seconds_n2": 20.0},
            "slow_short": {"duration_seconds_n2": 10.0},
            "slow_tiny": {"duration_seconds_n2": 1.0},
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
