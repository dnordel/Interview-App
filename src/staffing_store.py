from __future__ import annotations

import sqlite3
from pathlib import Path

from staffing_models import ASSIGNMENT_STATUSES, PERMIT_STATUSES, StaffingAssignment, StaffingPerson


class StaffingStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schools (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS classrooms (
                    id INTEGER PRIMARY KEY,
                    school_id INTEGER NOT NULL REFERENCES schools(id),
                    name TEXT NOT NULL,
                    program TEXT NOT NULL DEFAULT '',
                    licensed_capacity INTEGER,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(school_id, name)
                );
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    permit_status TEXT NOT NULL DEFAULT 'unknown',
                    notice_given TEXT,
                    final_working_day TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY,
                    classroom_id INTEGER NOT NULL REFERENCES classrooms(id),
                    person_id INTEGER REFERENCES people(id),
                    position_name TEXT NOT NULL,
                    position_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_opened_date TEXT,
                    current_filled_date TEXT,
                    start_date TEXT,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignment_history (
                    id INTEGER PRIMARY KEY,
                    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
                    classroom_id INTEGER NOT NULL REFERENCES classrooms(id),
                    position_name TEXT NOT NULL,
                    opened_date TEXT NOT NULL,
                    filled_date TEXT,
                    days_to_fill INTEGER,
                    closed_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assignments_classroom_id ON assignments(classroom_id);
                CREATE INDEX IF NOT EXISTS idx_assignments_person_id ON assignments(person_id);
                CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status);
                CREATE INDEX IF NOT EXISTS idx_history_assignment_id ON assignment_history(assignment_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_history_one_active
                    ON assignment_history(assignment_id)
                    WHERE filled_date IS NULL AND closed_reason IS NULL;
                """
            )

    def seed_assignment(
        self,
        *,
        school: str,
        classroom: str,
        position_name: str,
        position_type: str,
        status: str = "dont_need_now",
        person_name: str = "",
        permit_status: str = "unknown",
    ) -> int:
        school = _required_text(school, "School")
        classroom = _required_text(classroom, "Classroom")
        position_name = _required_text(position_name, "Position name")
        position_type = _required_text(position_type, "Position type")
        if status not in ASSIGNMENT_STATUSES:
            raise ValueError("Unknown assignment status.")
        if permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        now = "1970-01-01T00:00:00Z"
        with self.connect() as conn:
            school_id = self._ensure_school(conn, school)
            classroom_id = self._ensure_classroom(conn, school_id, classroom)
            person_id = None
            if person_name.strip():
                person_id = self._ensure_person(conn, person_name, position_type, permit_status, now)
            cursor = conn.execute(
                """
                INSERT INTO assignments (
                    classroom_id, person_id, position_name, position_type, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (classroom_id, person_id, position_name, position_type, status, now, now),
            )
            return int(cursor.lastrowid)

    def get_assignment(self, assignment_id: int) -> StaffingAssignment:
        with self.connect() as conn:
            return self.assignment_context(conn, assignment_id)

    def active_history_count(self, assignment_id: int) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM assignment_history
                    WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                    """,
                    (assignment_id,),
                ).fetchone()[0]
            )

    def assignment_context(self, conn: sqlite3.Connection, assignment_id: int) -> StaffingAssignment:
        row = conn.execute(
            """
            SELECT a.*, c.name AS classroom, s.name AS school, p.name AS person_name,
                   p.permit_status AS permit_status
            FROM assignments a
            JOIN classrooms c ON c.id = a.classroom_id
            JOIN schools s ON s.id = c.school_id
            LEFT JOIN people p ON p.id = a.person_id
            WHERE a.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Assignment not found.")
        return StaffingAssignment(
            id=int(row["id"]),
            school=str(row["school"] or ""),
            classroom=str(row["classroom"] or ""),
            position_name=str(row["position_name"] or ""),
            position_type=str(row["position_type"] or ""),
            status=str(row["status"] or ""),
            person_id=int(row["person_id"]) if row["person_id"] is not None else None,
            person_name=str(row["person_name"] or ""),
            start_date=str(row["start_date"] or ""),
            permit_status=str(row["permit_status"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def person_context(self, conn: sqlite3.Connection, person_id: int) -> StaffingPerson:
        row = conn.execute("SELECT * FROM people WHERE id = ? AND active = 1", (person_id,)).fetchone()
        if row is None:
            raise ValueError("Person not found.")
        return StaffingPerson(
            id=int(row["id"]),
            name=str(row["name"] or ""),
            permit_status=str(row["permit_status"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def ensure_person(self, conn: sqlite3.Connection, name: str, role: str, permit_status: str, now: str) -> int:
        return self._ensure_person(conn, name, role, permit_status, now)

    def _ensure_school(self, conn: sqlite3.Connection, name: str) -> int:
        conn.execute("INSERT OR IGNORE INTO schools (name) VALUES (?)", (name,))
        return int(conn.execute("SELECT id FROM schools WHERE name = ?", (name,)).fetchone()["id"])

    def _ensure_classroom(self, conn: sqlite3.Connection, school_id: int, name: str) -> int:
        conn.execute("INSERT OR IGNORE INTO classrooms (school_id, name) VALUES (?, ?)", (school_id, name))
        return int(
            conn.execute(
                "SELECT id FROM classrooms WHERE school_id = ? AND name = ?",
                (school_id, name),
            ).fetchone()["id"]
        )

    def _ensure_person(self, conn: sqlite3.Connection, name: str, role: str, permit_status: str, now: str) -> int:
        name = _required_text(name, "Person name")
        normalized = name.casefold()
        row = conn.execute(
            "SELECT id FROM people WHERE normalized_name = ? AND active = 1 ORDER BY id LIMIT 1",
            (normalized,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO people (name, normalized_name, role, permit_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, normalized, role, permit_status, now, now),
        )
        return int(cursor.lastrowid)


def _required_text(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text
