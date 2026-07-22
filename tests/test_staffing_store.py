from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from staffing_store import StaffingEditLock, StaffingStore


def test_concurrent_initialize_tolerates_verified_column_migration_races(tmp_path: Path) -> None:
    path = tmp_path / "staffing.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(StaffingStore(path).initialize) for _ in range(8)]
        for future in futures:
            future.result()

    with StaffingStore(path).connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(people)").fetchall()}
    assert "permit_document_path" in columns


def test_initialize_migrates_active_aide_positions_and_people_to_teacher_without_rewriting_history(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.create_assignment(
        school="Hawthorne", classroom="Harmony 1", position_name="Aide 1",
        position_type="Aide", status="coming", person_name="Jordan Lee",
        start_date="2026-08-01", now="2026-07-06T09:00:00Z",
    )

    store.initialize()

    assignment = store.get_assignment(assignment_id)
    assert assignment.position_name == "Teacher 1"
    assert assignment.position_type == "Teacher"
    assert assignment.slot_group == "teacher"
    assert store.list_people()[0].role == "Teacher"
    with store.connect() as conn:
        historical_name = conn.execute(
            "SELECT position_name FROM assignment_history WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()[0]
    assert historical_name == "Aide 1"


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
    assert rows[1].slot_group == "teacher"
    assert rows[1].position_name == "Teacher 2"
    assert rows[1].position_type == "Teacher"
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
    assert result["assignments"] == 89
    assert {"Hawthorne", "Palmdale", "North Long Beach"} == {row.school for row in rows}
    assert {"Angie", "Amy", "Madisan", "Edith", "Netsi", "Claudia", "Naomi*", "Ruby", "Miriam*", "Ebony"} <= names
    assert any(row.school == "North Long Beach" and row.classroom == "Destiny - 4YO" for row in rows)
    assert any(row.school == "Hawthorne" and row.classroom == "Custodian" and row.person_name == "Antonio" for row in rows)
    assert any(row.school == "Palmdale" and row.classroom == "Swim Instructor" and row.person_name == "Ebony" for row in rows)
    assert any(
        row.school == "Hawthorne"
        and row.classroom == "Director"
        and row.position_type == "Director"
        and row.person_name == "Netsi"
        for row in rows
    )
    assert any(
        row.school == "North Long Beach"
        and row.classroom == "Director"
        and row.position_type == "Director"
        and row.person_name == "Claudia"
        for row in rows
    )
    assert any(row.status == "need_now" and row.notes for row in rows)


def test_list_people_returns_employee_dashboard_rows_with_current_assignment(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    assignment_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
        status="filled",
        person_name="Maria Gonzalez",
        permit_status="teacher_permit_approved",
    )
    person_id = store.get_assignment(assignment_id).person_id
    assert person_id is not None
    with store.connect() as conn:
        conn.execute("UPDATE people SET units = 18, updated_at = '2026-07-05T09:00:00Z' WHERE id = ?", (person_id,))

    people = store.list_people()

    assert len(people) == 1
    person = people[0]
    assert person.name == "Maria Gonzalez"
    assert person.role == "Teacher"
    assert person.permit_status == "teacher_permit_approved"
    assert person.units == 18
    assert person.active is True
    assert person.assignment_school == "Hawthorne"
    assert person.assignment_classroom == "Harmony 1"
    assert person.assignment_position == "Teacher 1"
    assert person.current_assignment == "Hawthorne\nHarmony 1 - Teacher 1"


def test_list_assignment_history_returns_dashboard_records(tmp_path: Path) -> None:
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    closed_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Harmony 1",
        position_name="Teacher 1",
        position_type="Teacher",
    )
    open_id = store.seed_assignment(
        school="Hawthorne",
        classroom="Quest",
        position_name="Teacher 2",
        position_type="Teacher",
        status="need_now",
    )
    with store.connect() as conn:
        person_id = store.ensure_person(conn, "Emily Carter", "Teacher", "permit_in_process", "2026-07-05T09:00:00Z")
        classroom_id = conn.execute("SELECT classroom_id FROM assignments WHERE id = ?", (closed_id,)).fetchone()["classroom_id"]
        conn.execute(
            """
            UPDATE assignments
            SET person_id = ?, status = 'filled', current_opened_date = '2026-05-08',
                current_filled_date = '2026-05-20'
            WHERE id = ?
            """,
            (person_id, closed_id),
        )
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, filled_date,
                days_to_fill, closed_reason, created_at, updated_at
            ) VALUES (?, ?, 'Teacher 1', '2026-05-08', '2026-05-20', 12, 'filled',
                '2026-05-08T09:15:00Z', '2026-05-20T14:05:00Z')
            """,
            (closed_id, classroom_id),
        )
        open_classroom_id = conn.execute("SELECT classroom_id FROM assignments WHERE id = ?", (open_id,)).fetchone()["classroom_id"]
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
            ) VALUES (?, ?, 'Teacher 2', '2026-07-01', '2026-07-01T09:15:00Z', '2026-07-01T09:15:00Z')
            """,
            (open_id, open_classroom_id),
        )

    records = store.list_assignment_history()

    assert [record.assignment_id for record in records] == [open_id, closed_id]
    open_record = records[0]
    assert open_record.school == "Hawthorne"
    assert open_record.classroom == "Quest"
    assert open_record.position_name == "Teacher 2"
    assert open_record.cycle_status == "Open"
    assert open_record.employee == "OPEN POSITION"
    assert open_record.data_integrity == "Warning"
    closed_record = records[1]
    assert closed_record.classroom == "Harmony 1"
    assert closed_record.employee == "Emily Carter"
    assert closed_record.opened_date == "2026-05-08"
    assert closed_record.filled_date == "2026-05-20"
    assert closed_record.days_to_fill == 12
    assert closed_record.cycle_status == "Closed"
    assert closed_record.data_integrity == "Healthy"


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
