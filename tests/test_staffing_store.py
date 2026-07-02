from __future__ import annotations

import json
from pathlib import Path

import pytest

from staffing_store import StaffingStore


def test_import_seed_file_is_idempotent_and_lists_assignments(tmp_path: Path) -> None:
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "program": "Infant",
                                "licensed_capacity": 12,
                                "positions": [
                                    {
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Angie", "permit_status": "teacher_permit_approved"},
                                    },
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now"},
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()

    first = store.import_seed_file(seed_path)
    second = store.import_seed_file(seed_path)
    rows = store.list_assignments()

    assert first == {"schools": 1, "classrooms": 1, "assignments": 2}
    assert second == {"schools": 1, "classrooms": 1, "assignments": 2}
    assert len(rows) == 2
    assert rows[0].school == "Hawthorne"
    assert rows[0].classroom == "Tranquility"
    assert {row.position_name for row in rows} == {"Teacher 1", "Teacher 2"}
    assert store.active_history_count(rows[1].id) == 1


def test_import_seed_file_refuses_unknown_status_without_partial_write(tmp_path: Path) -> None:
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "positions": [
                                    {"position_name": "Teacher 2", "position_type": "Teacher", "status": "urgent"}
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()

    with pytest.raises(ValueError, match="Unknown assignment status"):
        store.import_seed_file(seed_path)

    assert store.list_assignments() == []
