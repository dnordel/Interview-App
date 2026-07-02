from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from staffing_store import StaffingEditLock, StaffingStore


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


def test_import_seed_file_supports_workbook_layout_metadata_and_real_school_names(tmp_path: Path) -> None:
    seed_path = tmp_path / "staffing_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "schools": [
                    {
                        "name": "Hawthorne",
                        "display_order": 2,
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "program": "Infant",
                                "ratio_group": "3 to 1 (infant units needed)",
                                "licensed_capacity": 12,
                                "display_order": 1,
                                "slots": [
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "display_order": 1,
                                        "status": "filled",
                                        "person": {"name": "Angie", "permit_status": "teacher_permit_approved"},
                                    },
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 2",
                                        "position_type": "Teacher",
                                        "display_order": 2,
                                        "status": "need_now",
                                        "notes": "Visible workbook ? cell.",
                                    },
                                ],
                            }
                        ],
                        "support_rows": [
                            {
                                "name": "Infant Floater",
                                "display_order": 90,
                                "slots": [
                                    {
                                        "slot_group": "support",
                                        "position_name": "Infant Floater",
                                        "position_type": "Support",
                                        "status": "filled",
                                        "person": {"name": "Amy", "permit_status": "teacher_permit_approved"},
                                        "notes": "Full time",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "name": "North Long Beach",
                        "display_order": 1,
                        "classrooms": [
                            {
                                "name": "Tranquility",
                                "program": "Infant",
                                "ratio_group": "3 to 1 (infant units needed)",
                                "licensed_capacity": 16,
                                "slots": [
                                    {
                                        "slot_group": "teacher",
                                        "position_name": "Teacher 1",
                                        "position_type": "Teacher",
                                        "status": "filled",
                                        "person": {"name": "Naomi*", "permit_status": "teacher_permit_approved"},
                                    },
                                    {
                                        "slot_group": "aide",
                                        "position_name": "Aide 1",
                                        "position_type": "Aide",
                                        "status": "filled",
                                        "person": {"name": "Ruby", "permit_status": "teacher_permit_approved"},
                                    },
                                ],
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()

    result = store.import_seed_file(seed_path)
    rows = store.list_assignments()

    assert result == {"schools": 2, "classrooms": 3, "assignments": 5}
    assert rows[0].school == "North Long Beach"
    assert rows[0].classroom_capacity == 16
    assert rows[0].ratio_group == "3 to 1 (infant units needed)"
    assert rows[0].slot_group == "teacher"
    assert rows[1].slot_group == "aide"
    hawthorne_open = [row for row in rows if row.school == "Hawthorne" and row.status == "need_now"][0]
    assert hawthorne_open.notes == "Visible workbook ? cell."
    support = [row for row in rows if row.classroom == "Infant Floater"][0]
    assert support.slot_group == "support"
    assert support.notes == "Full time"


def test_default_staffing_seed_imports_visible_excel_names(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()

    result = store.import_seed_file(Path("config") / "staffing_seed.json")
    rows = store.list_assignments()
    names = {row.person_name for row in rows if row.person_name}

    assert result["schools"] == 3
    assert result["assignments"] == 87
    assert {"Hawthorne", "Palmdale", "North Long Beach"} == {row.school for row in rows}
    assert {"Angie", "Amy", "Madisan", "Telma", "Naomi*", "Ruby", "Miriam*", "Ebony"} <= names
    assert any(row.school == "North Long Beach" and row.classroom == "Destiny - 4YO" for row in rows)
    assert any(row.school == "Hawthorne" and row.classroom == "Custodian" and row.person_name == "Antonio" for row in rows)
    assert any(row.school == "Palmdale" and row.classroom == "Swim Instructor" and row.person_name == "Ebony" for row in rows)
    assert any(row.status == "need_now" and row.notes for row in rows)


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


def test_staffing_schema_enforces_unique_school_classroom_and_active_history(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Tranquility",
        position_name="Teacher 2",
        position_type="Teacher",
        status="need_now",
    )

    with store.connect() as conn:
        school_id = conn.execute("SELECT id FROM schools WHERE name = 'Hawthorne'").fetchone()["id"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO classrooms (school_id, name) VALUES (?, ?)", (school_id, "Tranquility"))
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
            )
            SELECT id, classroom_id, position_name, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
            FROM assignments WHERE id = ?
            """,
            (assignment_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO assignment_history (
                    assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
                )
                SELECT id, classroom_id, position_name, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
                FROM assignments WHERE id = ?
                """,
                (assignment_id,),
            )


def test_write_connection_refuses_second_editor_when_dropbox_lock_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "staffing.sqlite3"
    first_store = StaffingStore(db_path)
    second_store = StaffingStore(db_path)
    first_store.initialize()

    with first_store.write_connection("first-user"):
        with pytest.raises(StaffingEditLock, match="Staffing database is being edited"):
            with second_store.write_connection("second-user"):
                pass


def test_write_connection_removes_stale_dropbox_lock(tmp_path: Path) -> None:
    db_path = tmp_path / "staffing.sqlite3"
    store = StaffingStore(db_path)
    store.initialize()
    lock_path = db_path.with_suffix(db_path.suffix + ".editing.lock")
    lock_path.write_text(
        json.dumps({"owner": "old-user", "created_at": "2000-01-01T00:00:00Z"}),
        encoding="utf-8",
    )

    with store.write_connection("new-user") as conn:
        conn.execute("SELECT 1")

    assert not lock_path.exists()
