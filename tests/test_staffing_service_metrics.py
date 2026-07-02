from __future__ import annotations

from datetime import date
from pathlib import Path

from staffing_service import StaffingService
from staffing_store import StaffingStore


class _Clock:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self) -> str:
        return next(self.values)


def test_staffing_metrics_count_open_age_and_fill_time(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    open_recent = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    open_old = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
    )
    filled = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 3",
        position_type="Teacher",
    )
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-06T09:00:00Z",
                "2026-06-20T09:00:00Z",
                "2026-06-01T09:00:00Z",
                "2026-06-05T09:00:00Z",
                "2026-06-08T09:00:00Z",
            ]
        ),
    )

    service.open_position(open_recent)
    service.open_position(open_old)
    service.open_position(filled)
    service.mark_coming(filled, person_name="Jane Doe", start_date="2026-06-07")
    service.mark_filled(filled)

    metrics = service.staffing_metrics(today=date(2026, 7, 10))
    open_ages = {row.position_name: row.days_open for row in metrics.rows}

    assert metrics.open_count == 2
    assert metrics.open_over_7_days == 1
    assert metrics.avg_days_to_fill == 7.0
    assert open_ages["Teacher 1"] == 4
    assert open_ages["Teacher 2"] == 20


def test_staffing_metrics_can_filter_to_one_school(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    hawthorne_open = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    palmdale_open = store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    palmdale_filled = store.seed_assignment(
        school="Palmdale",
        classroom="Harmony",
        position_name="Teacher 2",
        position_type="Teacher",
    )
    service = StaffingService(
        store,
        clock=_Clock(
            [
                "2026-07-01T09:00:00Z",
                "2026-06-01T09:00:00Z",
                "2026-06-02T09:00:00Z",
                "2026-06-05T09:00:00Z",
                "2026-06-08T09:00:00Z",
            ]
        ),
    )

    service.open_position(hawthorne_open)
    service.open_position(palmdale_open)
    service.open_position(palmdale_filled)
    service.mark_coming(palmdale_filled, person_name="Jane Doe", start_date="2026-06-07")
    service.mark_filled(palmdale_filled)

    metrics = service.staffing_metrics(today=date(2026, 7, 10), school="Palmdale")

    assert metrics.open_count == 1
    assert metrics.open_over_7_days == 1
    assert metrics.avg_days_to_fill == 6.0
    assert {row.school for row in metrics.rows} == {"Palmdale"}
    assert {row.position_name for row in metrics.rows} == {"Teacher 1", "Teacher 2"}
