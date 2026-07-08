from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from staffing_models import (
    ASSIGNMENT_STATUSES,
    PERMIT_STATUSES,
    StaffingAssignment,
    StaffingClassroom,
    StaffingDirectorCandidate,
    StaffingDirectorInterview,
    StaffingHistoryRecord,
    StaffingPerson,
)

EDIT_LOCK_STALE_SECONDS = 15 * 60


class StaffingEditLock(RuntimeError):
    pass


class StaffingStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def write_connection(self, owner: str = "") -> Any:
        self._acquire_edit_lock(owner)
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            self._release_edit_lock()

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
                    ratio_group TEXT NOT NULL DEFAULT '',
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
                    permit_effective_date TEXT NOT NULL DEFAULT '',
                    permit_documentation_received INTEGER NOT NULL DEFAULT 0,
                    permit_notes TEXT NOT NULL DEFAULT '',
                    units REAL,
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
                    shift_start TEXT NOT NULL DEFAULT '',
                    shift_end TEXT NOT NULL DEFAULT '',
                    slot_group TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
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
                CREATE INDEX IF NOT EXISTS idx_assignments_opened_date ON assignments(current_opened_date);
                CREATE INDEX IF NOT EXISTS idx_history_assignment_id ON assignment_history(assignment_id);
                CREATE INDEX IF NOT EXISTS idx_history_classroom_id ON assignment_history(classroom_id);
                CREATE INDEX IF NOT EXISTS idx_history_opened_date ON assignment_history(opened_date);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_history_one_active
                    ON assignment_history(assignment_id)
                    WHERE filled_date IS NULL AND closed_reason IS NULL;
                CREATE TABLE IF NOT EXISTS director_candidate_referrals (
                    id INTEGER PRIMARY KEY,
                    history_id TEXT NOT NULL UNIQUE,
                    candidate_name TEXT NOT NULL,
                    school TEXT NOT NULL,
                    position TEXT NOT NULL DEFAULT '',
                    interviewer_rating REAL,
                    interviewer_outcome TEXT NOT NULL,
                    interview_date TEXT NOT NULL DEFAULT '',
                    candidate_email TEXT NOT NULL DEFAULT '',
                    referral_date TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS director_interviews (
                    id INTEGER PRIMARY KEY,
                    referral_id INTEGER NOT NULL UNIQUE REFERENCES director_candidate_referrals(id),
                    director_name TEXT NOT NULL DEFAULT '',
                    completed_date TEXT NOT NULL,
                    rating REAL NOT NULL,
                    decision TEXT NOT NULL,
                    decision_notes TEXT NOT NULL,
                    proposed_shift_start TEXT NOT NULL DEFAULT '',
                    proposed_shift_end TEXT NOT NULL DEFAULT '',
                    proposed_classroom TEXT NOT NULL DEFAULT '',
                    follow_up_needed INTEGER NOT NULL DEFAULT 0,
                    owner_approval_status TEXT NOT NULL DEFAULT 'pending_owner_approval',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_director_referrals_school ON director_candidate_referrals(school);
                CREATE INDEX IF NOT EXISTS idx_director_interviews_referral_id ON director_interviews(referral_id);
                """
            )
            self._ensure_column(conn, "people", "units", "REAL")
            self._ensure_column(conn, "people", "permit_effective_date", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "people", "permit_documentation_received", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "people", "permit_notes", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "classrooms", "ratio_group", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "assignments", "slot_group", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "assignments", "notes", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "assignments", "shift_start", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "assignments", "shift_end", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "director_candidate_referrals", "candidate_email", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "director_candidate_referrals", "referral_date", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                conn,
                "director_interviews",
                "owner_approval_status",
                "TEXT NOT NULL DEFAULT 'pending_owner_approval'",
            )

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @property
    def edit_lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".editing.lock")

    @property
    def pending_operations_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".pending.jsonl")

    def enqueue_pending_operation(self, operation: str, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "operation": operation,
            "payload": payload,
            "queued_at": _utc_now_iso(),
            "owner": _default_lock_owner(),
        }
        with self.pending_operations_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")

    def pop_pending_operations(self) -> list[dict[str, Any]]:
        pending_path = self.pending_operations_path
        if not pending_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        pending_path.unlink()
        return records

    def peek_pending_operations(self) -> list[dict[str, Any]]:
        pending_path = self.pending_operations_path
        if not pending_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        return records

    def restore_pending_operations(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            operation = str(record.get("operation", ""))
            payload = record.get("payload", {})
            if isinstance(payload, dict):
                self.enqueue_pending_operation(operation, payload)

    def _acquire_edit_lock(self, owner: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.edit_lock_path
        payload = json.dumps(
            {
                "owner": owner or _default_lock_owner(),
                "created_at": _utc_now_iso(),
                "database": str(self.path),
            },
            ensure_ascii=True,
        )
        for _attempt in range(2):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                if self._remove_stale_edit_lock(lock_path):
                    continue
                details = _read_lock_details(lock_path)
                raise StaffingEditLock(f"Staffing database is being edited by {details}. Try again shortly.") from exc
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
            return
        raise StaffingEditLock("Staffing database is being edited. Try again shortly.")

    def _release_edit_lock(self) -> None:
        try:
            self.edit_lock_path.unlink()
        except FileNotFoundError:
            return

    def _remove_stale_edit_lock(self, lock_path: Path) -> bool:
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(str(data.get("created_at", "")).replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        age = datetime.now(timezone.utc) - created_at
        if age.total_seconds() <= EDIT_LOCK_STALE_SECONDS:
            return False
        try:
            lock_path.unlink()
        except OSError:
            return False
        return True

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

    def create_assignment(
        self,
        *,
        school: str,
        classroom: str,
        classroom_program: str = "",
        licensed_capacity: int | None = None,
        position_name: str,
        position_type: str,
        status: str = "dont_need_now",
        person_name: str = "",
        permit_status: str = "unknown",
        start_date: str = "",
        notes: str = "",
        now: str,
    ) -> int:
        school = _required_text(school, "School")
        classroom = _required_text(classroom, "Classroom")
        position_name = _required_text(position_name, "Position name")
        position_type = _required_text(position_type, "Position type")
        if status not in ASSIGNMENT_STATUSES:
            raise ValueError("Unknown assignment status.")
        if permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        if status in {"coming", "filled"} and not person_name.strip():
            raise ValueError("Person name is required for assigned positions.")
        if status == "coming" and not start_date.strip():
            raise ValueError("Start date is required for coming positions.")
        with self.write_connection("create_assignment") as conn:
            school_id = self._ensure_school(conn, school)
            classroom_id = self._ensure_classroom(
                conn,
                school_id,
                classroom,
                program=str(classroom_program or "").strip(),
                licensed_capacity=licensed_capacity,
            )
            person_id = None
            if person_name.strip():
                person_id = self._ensure_person(conn, person_name, position_type, permit_status, now)
            opened_date = now if status in {"need_now", "replace", "coming"} else None
            filled_date = start_date.strip() if status == "filled" and start_date.strip() else None
            cursor = conn.execute(
                """
                INSERT INTO assignments (
                    classroom_id, person_id, position_name, position_type, status,
                    current_opened_date, current_filled_date, start_date, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    classroom_id,
                    person_id,
                    position_name,
                    position_type,
                    status,
                    opened_date,
                    filled_date,
                    start_date.strip() or None,
                    str(notes or "").strip(),
                    now,
                    now,
                ),
            )
            assignment_id = int(cursor.lastrowid)
            if status in {"need_now", "replace", "coming"}:
                conn.execute(
                    """
                    INSERT INTO assignment_history (
                        assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (assignment_id, classroom_id, position_name, now, now, now),
                )
            return assignment_id

    def create_classroom(
        self,
        *,
        school: str,
        name: str,
        program: str = "",
        ratio_group: str = "",
        licensed_capacity: int | None = None,
        display_order: int = 0,
    ) -> int:
        school = _required_text(school, "School")
        name = _required_text(name, "Classroom")
        with self.write_connection("create_classroom") as conn:
            school_id = self._ensure_school(conn, school)
            return self._ensure_classroom(
                conn,
                school_id,
                name,
                program=str(program or "").strip(),
                ratio_group=str(ratio_group or "").strip(),
                licensed_capacity=licensed_capacity,
                display_order=int(display_order),
            )

    def update_classroom(
        self,
        *,
        classroom_id: int,
        school: str,
        name: str,
        program: str = "",
        ratio_group: str = "",
        licensed_capacity: int | None = None,
        display_order: int = 0,
    ) -> StaffingClassroom:
        school = _required_text(school, "School")
        name = _required_text(name, "Classroom")
        with self.write_connection("update_classroom") as conn:
            current = conn.execute("SELECT id FROM classrooms WHERE id = ? AND active = 1", (int(classroom_id),)).fetchone()
            if current is None:
                raise ValueError("Classroom not found.")
            school_id = self._ensure_school(conn, school)
            duplicate = conn.execute(
                """
                SELECT id FROM classrooms
                WHERE school_id = ? AND name = ? AND id != ? AND active = 1
                """,
                (school_id, name, int(classroom_id)),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Classroom already exists for this school.")
            conn.execute(
                """
                UPDATE classrooms
                SET school_id = ?, name = ?, program = ?, ratio_group = ?, licensed_capacity = ?, display_order = ?
                WHERE id = ?
                """,
                (
                    school_id,
                    name,
                    str(program or "").strip(),
                    str(ratio_group or "").strip(),
                    licensed_capacity,
                    int(display_order),
                    int(classroom_id),
                ),
            )
            return self.classroom_context(conn, int(classroom_id))

    def classroom_active_assignment_count(self, classroom_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM assignments WHERE classroom_id = ? AND active = 1",
                (int(classroom_id),),
            ).fetchone()
            return int(row["count"] if row is not None else 0)

    def deactivate_classroom(self, classroom_id: int) -> StaffingClassroom:
        with self.write_connection("deactivate_classroom") as conn:
            classroom = self._classroom_context_any_active(conn, int(classroom_id))
            conn.execute("UPDATE classrooms SET active = 0 WHERE id = ?", (int(classroom_id),))
            return StaffingClassroom(
                id=classroom.id,
                school=classroom.school,
                name=classroom.name,
                program=classroom.program,
                ratio_group=classroom.ratio_group,
                licensed_capacity=classroom.licensed_capacity,
                active=False,
                display_order=classroom.display_order,
            )

    def delete_assignment(self, assignment_id: int, *, now: str) -> StaffingAssignment:
        with self.write_connection("delete_assignment") as conn:
            assignment = self.assignment_context(conn, int(assignment_id))
            if assignment.person_id is not None:
                raise ValueError("Cannot delete a position with an assigned person.")
            if assignment.status in {"coming", "filled", "replace"}:
                raise ValueError("Cannot delete a position with assignment history.")
            closed_history = conn.execute(
                """
                SELECT COUNT(*) FROM assignment_history
                WHERE assignment_id = ? AND closed_reason IN ('filled', 'replaced')
                """,
                (int(assignment_id),),
            ).fetchone()[0]
            if int(closed_history) > 0:
                raise ValueError("Cannot delete a position with completed history.")
            conn.execute(
                """
                UPDATE assignment_history
                SET closed_reason = 'deleted', updated_at = ?
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (now, int(assignment_id)),
            )
            conn.execute(
                """
                UPDATE assignments
                SET active = 0, person_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, int(assignment_id)),
            )
            return replace(assignment, updated_at=now)

    def import_seed_file(self, seed_path: Path) -> dict[str, int]:
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        schools = _seed_schools(data)
        with self.connect() as conn:
            for school in schools:
                school_id = self._ensure_school(conn, school["name"], display_order=school["display_order"])
                for classroom in school["classrooms"]:
                    classroom_id = self._ensure_classroom(
                        conn,
                        school_id,
                        classroom["name"],
                        program=classroom["program"],
                        ratio_group=classroom["ratio_group"],
                        licensed_capacity=classroom["licensed_capacity"],
                        display_order=classroom["display_order"],
                    )
                    for position in classroom["positions"]:
                        self._upsert_seed_assignment(conn, classroom_id, position)
        return {
            "schools": len(schools),
            "classrooms": sum(len(school["classrooms"]) for school in schools),
            "assignments": sum(
                len(classroom["positions"]) for school in schools for classroom in school["classrooms"]
            ),
        }

    def upsert_director_candidate_referral(
        self,
        *,
        history_id: str,
        candidate_name: str,
        school: str,
        position: str = "",
        interviewer_rating: float | None = None,
        interviewer_outcome: str,
        interview_date: str = "",
        candidate_email: str = "",
        referral_date: str = "",
        now: str,
    ) -> StaffingDirectorCandidate:
        history_id = _required_text(history_id, "History ID")
        candidate_name = _required_text(candidate_name, "Candidate name")
        school = _required_text(school, "School")
        with self.write_connection("director_candidate_referral") as conn:
            conn.execute(
                """
                INSERT INTO director_candidate_referrals (
                    history_id, candidate_name, school, position, interviewer_rating,
                    interviewer_outcome, interview_date, candidate_email, referral_date,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(history_id) DO UPDATE SET
                    candidate_name = excluded.candidate_name,
                    school = excluded.school,
                    position = excluded.position,
                    interviewer_rating = excluded.interviewer_rating,
                    interviewer_outcome = excluded.interviewer_outcome,
                    interview_date = excluded.interview_date,
                    candidate_email = excluded.candidate_email,
                    referral_date = excluded.referral_date,
                    updated_at = excluded.updated_at
                """,
                (
                    history_id,
                    candidate_name,
                    school,
                    str(position or "").strip(),
                    interviewer_rating,
                    interviewer_outcome,
                    str(interview_date or "").strip(),
                    str(candidate_email or "").strip(),
                    str(referral_date or "").strip(),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM director_candidate_referrals WHERE history_id = ?",
                (history_id,),
            ).fetchone()
            return self.director_candidate_context(conn, int(row["id"]))

    def list_director_candidate_referrals(
        self,
        *,
        school: str = "",
        include_completed: bool = False,
    ) -> list[StaffingDirectorCandidate]:
        school_filter = str(school or "").strip()
        clauses = []
        params: list[Any] = []
        if school_filter:
            clauses.append("r.school = ?")
            params.append(school_filter)
        if not include_completed:
            clauses.append("i.id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.id
                FROM director_candidate_referrals r
                LEFT JOIN director_interviews i ON i.referral_id = r.id
                {where}
                ORDER BY r.referral_date DESC, r.interview_date DESC, r.candidate_name
                """,
                tuple(params),
            ).fetchall()
            return [self.director_candidate_context(conn, int(row["id"])) for row in rows]

    def record_director_interview(
        self,
        referral_id: int,
        *,
        director_name: str,
        completed_date: str,
        rating: float,
        decision: str,
        decision_notes: str,
        proposed_shift_start: str = "",
        proposed_shift_end: str = "",
        proposed_classroom: str = "",
        follow_up_needed: bool = False,
        owner_approval_status: str = "pending_owner_approval",
        now: str,
    ) -> StaffingDirectorInterview:
        with self.write_connection("director_interview") as conn:
            self.director_candidate_context(conn, int(referral_id))
            conn.execute(
                """
                INSERT INTO director_interviews (
                    referral_id, director_name, completed_date, rating, decision, decision_notes,
                    proposed_shift_start, proposed_shift_end, proposed_classroom, follow_up_needed,
                    owner_approval_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(referral_id) DO UPDATE SET
                    director_name = excluded.director_name,
                    completed_date = excluded.completed_date,
                    rating = excluded.rating,
                    decision = excluded.decision,
                    decision_notes = excluded.decision_notes,
                    proposed_shift_start = excluded.proposed_shift_start,
                    proposed_shift_end = excluded.proposed_shift_end,
                    proposed_classroom = excluded.proposed_classroom,
                    follow_up_needed = excluded.follow_up_needed,
                    owner_approval_status = excluded.owner_approval_status,
                    updated_at = excluded.updated_at
                """,
                (
                    int(referral_id),
                    str(director_name or "").strip(),
                    completed_date,
                    float(rating),
                    decision,
                    decision_notes,
                    proposed_shift_start,
                    proposed_shift_end,
                    proposed_classroom,
                    1 if follow_up_needed else 0,
                    owner_approval_status,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM director_interviews WHERE referral_id = ?",
                (int(referral_id),),
            ).fetchone()
            return self.director_interview_context(conn, int(row["id"]))

    def list_director_interviews(self, *, school: str = "") -> list[StaffingDirectorInterview]:
        school_filter = str(school or "").strip()
        where = "WHERE r.school = ?" if school_filter else ""
        params: tuple[Any, ...] = (school_filter,) if school_filter else ()
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT i.id
                FROM director_interviews i
                JOIN director_candidate_referrals r ON r.id = i.referral_id
                {where}
                ORDER BY i.completed_date DESC, i.id DESC
                """,
                params,
            ).fetchall()
            return [self.director_interview_context(conn, int(row["id"])) for row in rows]

    def list_assignments(self) -> list[StaffingAssignment]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id
                FROM assignments a
                JOIN classrooms c ON c.id = a.classroom_id
                JOIN schools s ON s.id = c.school_id
                WHERE a.active = 1 AND c.active = 1 AND s.active = 1
                ORDER BY s.display_order, s.name, c.display_order, c.name, a.display_order, a.id
                """
            ).fetchall()
            return [self.assignment_context(conn, int(row["id"])) for row in rows]

    def list_classrooms(self) -> list[StaffingClassroom]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, s.name AS school
                FROM classrooms c
                JOIN schools s ON s.id = c.school_id
                WHERE c.active = 1 AND s.active = 1
                ORDER BY s.display_order, s.name, c.display_order, c.name
                """
            ).fetchall()
            return [self._classroom_from_row(row) for row in rows]

    def list_people(self) -> list[StaffingPerson]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       s.name AS assignment_school,
                       c.name AS assignment_classroom,
                       a.position_name AS assignment_position
                FROM people p
                LEFT JOIN assignments a ON a.person_id = p.id AND a.active = 1
                LEFT JOIN classrooms c ON c.id = a.classroom_id AND c.active = 1
                LEFT JOIN schools s ON s.id = c.school_id AND s.active = 1
                ORDER BY p.active DESC, p.name, a.id
                """
            ).fetchall()
            people: dict[int, StaffingPerson] = {}
            for row in rows:
                person_id = int(row["id"])
                if person_id in people:
                    continue
                people[person_id] = self._person_from_row(row)
            return list(people.values())

    def list_assignment_history(self) -> list[StaffingHistoryRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT h.*, s.name AS school, c.name AS classroom,
                       a.status AS assignment_status, p.name AS employee
                FROM assignment_history h
                JOIN classrooms c ON c.id = h.classroom_id
                JOIN schools s ON s.id = c.school_id
                LEFT JOIN assignments a ON a.id = h.assignment_id
                LEFT JOIN people p ON p.id = a.person_id
                WHERE c.active = 1 AND s.active = 1
                ORDER BY h.opened_date DESC, h.id DESC
                """
            ).fetchall()
            return [self._history_record_from_row(row) for row in rows]

    def closed_days_to_fill(self, *, school: str = "") -> list[int]:
        with self.connect() as conn:
            params: tuple[Any, ...] = ()
            school_filter = ""
            if str(school or "").strip():
                school_filter = "AND s.name = ?"
                params = (str(school).strip(),)
            rows = conn.execute(
                f"""
                SELECT h.days_to_fill FROM assignment_history h
                JOIN classrooms c ON c.id = h.classroom_id
                JOIN schools s ON s.id = c.school_id
                WHERE h.closed_reason = 'filled' AND h.days_to_fill IS NOT NULL
                {school_filter}
                ORDER BY h.id
                """,
                params,
            ).fetchall()
            return [int(row["days_to_fill"]) for row in rows]

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

    def director_candidate_context(self, conn: sqlite3.Connection, referral_id: int) -> StaffingDirectorCandidate:
        row = conn.execute(
            """
            SELECT r.*, i.completed_date AS director_interview_completed_at
            FROM director_candidate_referrals r
            LEFT JOIN director_interviews i ON i.referral_id = r.id
            WHERE r.id = ?
            """,
            (int(referral_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Director candidate referral not found.")
        return StaffingDirectorCandidate(
            id=int(row["id"]),
            history_id=str(row["history_id"] or ""),
            candidate_name=str(row["candidate_name"] or ""),
            school=str(row["school"] or ""),
            position=str(row["position"] or ""),
            interviewer_rating=float(row["interviewer_rating"]) if row["interviewer_rating"] is not None else None,
            interviewer_outcome=str(row["interviewer_outcome"] or ""),
            interview_date=str(row["interview_date"] or ""),
            candidate_email=str(row["candidate_email"] or ""),
            referral_date=str(row["referral_date"] or ""),
            director_interview_completed_at=str(row["director_interview_completed_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def director_interview_context(self, conn: sqlite3.Connection, interview_id: int) -> StaffingDirectorInterview:
        row = conn.execute(
            """
            SELECT i.*, r.candidate_name, r.school, r.position, r.interviewer_rating, r.interviewer_outcome
            FROM director_interviews i
            JOIN director_candidate_referrals r ON r.id = i.referral_id
            WHERE i.id = ?
            """,
            (int(interview_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Director interview not found.")
        return StaffingDirectorInterview(
            id=int(row["id"]),
            referral_id=int(row["referral_id"]),
            candidate_name=str(row["candidate_name"] or ""),
            school=str(row["school"] or ""),
            position=str(row["position"] or ""),
            interviewer_rating=float(row["interviewer_rating"]) if row["interviewer_rating"] is not None else None,
            interviewer_outcome=str(row["interviewer_outcome"] or ""),
            director_name=str(row["director_name"] or ""),
            completed_date=str(row["completed_date"] or ""),
            rating=float(row["rating"]),
            decision=str(row["decision"] or ""),
            decision_notes=str(row["decision_notes"] or ""),
            proposed_shift_start=str(row["proposed_shift_start"] or ""),
            proposed_shift_end=str(row["proposed_shift_end"] or ""),
            proposed_classroom=str(row["proposed_classroom"] or ""),
            follow_up_needed=bool(row["follow_up_needed"]),
            owner_approval_status=str(row["owner_approval_status"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def assignment_context(self, conn: sqlite3.Connection, assignment_id: int) -> StaffingAssignment:
        row = conn.execute(
            """
            SELECT a.*, c.name AS classroom, s.name AS school, p.name AS person_name,
                   p.permit_status AS permit_status,
                   p.notice_given AS notice_given,
                   p.final_working_day AS final_working_day,
                   c.licensed_capacity AS classroom_capacity,
                   c.program AS classroom_program,
                   c.ratio_group AS ratio_group
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
            shift_start=str(row["shift_start"] or ""),
            shift_end=str(row["shift_end"] or ""),
            notice_given=str(row["notice_given"] or ""),
            final_working_day=str(row["final_working_day"] or ""),
            permit_status=str(row["permit_status"] or ""),
            updated_at=str(row["updated_at"] or ""),
            current_opened_date=str(row["current_opened_date"] or ""),
            current_filled_date=str(row["current_filled_date"] or ""),
            classroom_capacity=int(row["classroom_capacity"]) if row["classroom_capacity"] is not None else None,
            classroom_program=str(row["classroom_program"] or ""),
            ratio_group=str(row["ratio_group"] or ""),
            slot_group=str(row["slot_group"] or ""),
            notes=str(row["notes"] or ""),
            display_order=int(row["display_order"] or 0),
        )

    def person_context(self, conn: sqlite3.Connection, person_id: int) -> StaffingPerson:
        row = conn.execute("SELECT * FROM people WHERE id = ? AND active = 1", (person_id,)).fetchone()
        if row is None:
            raise ValueError("Person not found.")
        return StaffingPerson(
            id=int(row["id"]),
            name=str(row["name"] or ""),
            permit_status=str(row["permit_status"] or ""),
            role=str(row["role"] or ""),
            active=bool(row["active"]),
            permit_effective_date=str(row["permit_effective_date"] or ""),
            units=float(row["units"]) if row["units"] is not None else None,
            permit_documentation_received=bool(row["permit_documentation_received"]),
            permit_notes=str(row["permit_notes"] or ""),
            notice_given=str(row["notice_given"] or ""),
            final_working_day=str(row["final_working_day"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def classroom_context(self, conn: sqlite3.Connection, classroom_id: int) -> StaffingClassroom:
        row = conn.execute(
            """
            SELECT c.*, s.name AS school
            FROM classrooms c
            JOIN schools s ON s.id = c.school_id
            WHERE c.id = ? AND c.active = 1 AND s.active = 1
            """,
            (classroom_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Classroom not found.")
        return self._classroom_from_row(row)

    def _classroom_context_any_active(self, conn: sqlite3.Connection, classroom_id: int) -> StaffingClassroom:
        row = conn.execute(
            """
            SELECT c.*, s.name AS school
            FROM classrooms c
            JOIN schools s ON s.id = c.school_id
            WHERE c.id = ? AND s.active = 1
            """,
            (classroom_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Classroom not found.")
        return self._classroom_from_row(row)

    def _classroom_from_row(self, row: sqlite3.Row) -> StaffingClassroom:
        return StaffingClassroom(
            id=int(row["id"]),
            school=str(row["school"] or ""),
            name=str(row["name"] or ""),
            program=str(row["program"] or ""),
            ratio_group=str(row["ratio_group"] or ""),
            licensed_capacity=int(row["licensed_capacity"]) if row["licensed_capacity"] is not None else None,
            active=bool(row["active"]),
            display_order=int(row["display_order"] or 0),
        )

    def _person_from_row(self, row: sqlite3.Row) -> StaffingPerson:
        school = str(row["assignment_school"] or "")
        classroom = str(row["assignment_classroom"] or "")
        position = str(row["assignment_position"] or "")
        assignment = ""
        if school and classroom and position:
            assignment = f"{school}\n{classroom} - {position}"
        return StaffingPerson(
            id=int(row["id"]),
            name=str(row["name"] or ""),
            permit_status=str(row["permit_status"] or ""),
            role=str(row["role"] or ""),
            active=bool(row["active"]),
            permit_effective_date=str(row["permit_effective_date"] or ""),
            units=float(row["units"]) if row["units"] is not None else None,
            permit_documentation_received=bool(row["permit_documentation_received"]),
            permit_notes=str(row["permit_notes"] or ""),
            notice_given=str(row["notice_given"] or ""),
            final_working_day=str(row["final_working_day"] or ""),
            assignment_school=school,
            assignment_classroom=classroom,
            assignment_position=position,
            current_assignment=assignment,
            updated_at=str(row["updated_at"] or ""),
        )

    def _history_record_from_row(self, row: sqlite3.Row) -> StaffingHistoryRecord:
        filled_date = str(row["filled_date"] or "")
        closed_reason = str(row["closed_reason"] or "")
        cycle_status = "Open" if not filled_date and not closed_reason else "Closed"
        employee = str(row["employee"] or "")
        if not employee:
            employee = "OPEN POSITION"
        data_integrity = "Healthy"
        if cycle_status == "Open" or closed_reason not in {"filled", "seed_closed", "moved"}:
            data_integrity = "Warning"
        return StaffingHistoryRecord(
            id=int(row["id"]),
            assignment_id=int(row["assignment_id"]),
            school=str(row["school"] or ""),
            classroom=str(row["classroom"] or ""),
            position_name=str(row["position_name"] or ""),
            opened_date=str(row["opened_date"] or ""),
            filled_date=filled_date,
            days_to_fill=int(row["days_to_fill"]) if row["days_to_fill"] is not None else None,
            cycle_status=cycle_status,
            employee=employee,
            data_integrity=data_integrity,
            closed_reason=closed_reason,
            updated_at=str(row["updated_at"] or ""),
        )

    def ensure_person(self, conn: sqlite3.Connection, name: str, role: str, permit_status: str, now: str) -> int:
        return self._ensure_person(conn, name, role, permit_status, now)

    def _ensure_school(self, conn: sqlite3.Connection, name: str, *, display_order: int = 0) -> int:
        conn.execute("INSERT OR IGNORE INTO schools (name) VALUES (?)", (name,))
        conn.execute("UPDATE schools SET display_order = ? WHERE name = ?", (display_order, name))
        return int(conn.execute("SELECT id FROM schools WHERE name = ?", (name,)).fetchone()["id"])

    def _ensure_classroom(
        self,
        conn: sqlite3.Connection,
        school_id: int,
        name: str,
        *,
        program: str = "",
        ratio_group: str = "",
        licensed_capacity: int | None = None,
        display_order: int = 0,
    ) -> int:
        conn.execute(
            """
            INSERT OR IGNORE INTO classrooms (school_id, name, program, ratio_group, licensed_capacity, display_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (school_id, name, program, ratio_group, licensed_capacity, display_order),
        )
        conn.execute(
            """
            UPDATE classrooms
            SET program = ?, ratio_group = ?, licensed_capacity = ?, display_order = ?
            WHERE school_id = ? AND name = ?
            """,
            (program, ratio_group, licensed_capacity, display_order, school_id, name),
        )
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

    def _upsert_seed_assignment(self, conn: sqlite3.Connection, classroom_id: int, position: dict[str, Any]) -> int:
        now = "1970-01-01T00:00:00Z"
        person = position["person"]
        person_id = None
        if person["name"]:
            person_id = self._ensure_person(conn, person["name"], position["position_type"], person["permit_status"], now)
        row = conn.execute(
            """
            SELECT id FROM assignments
            WHERE classroom_id = ? AND position_name = ? AND active = 1
            ORDER BY id LIMIT 1
            """,
            (classroom_id, position["position_name"]),
        ).fetchone()
        opened_date = now if position["status"] in {"need_now", "replace"} else None
        filled_date = now if position["status"] == "filled" else None
        if row is None:
            cursor = conn.execute(
                """
                INSERT INTO assignments (
                    classroom_id, person_id, position_name, position_type, status,
                    current_opened_date, current_filled_date, start_date, slot_group, notes,
                    display_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    classroom_id,
                    person_id,
                    position["position_name"],
                    position["position_type"],
                    position["status"],
                    opened_date,
                    filled_date,
                    position["start_date"],
                    position["slot_group"],
                    position["notes"],
                    position["display_order"],
                    now,
                    now,
                ),
            )
            assignment_id = int(cursor.lastrowid)
        else:
            assignment_id = int(row["id"])
            conn.execute(
                """
                UPDATE assignments
                SET person_id = ?, position_type = ?, status = ?, current_opened_date = ?,
                    current_filled_date = ?, start_date = ?, slot_group = ?, notes = ?,
                    display_order = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    person_id,
                    position["position_type"],
                    position["status"],
                    opened_date,
                    filled_date,
                    position["start_date"],
                    position["slot_group"],
                    position["notes"],
                    position["display_order"],
                    now,
                    assignment_id,
                ),
            )
        if position["status"] in {"need_now", "replace"}:
            active_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM assignment_history
                    WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                    """,
                    (assignment_id,),
                ).fetchone()[0]
            )
            if active_count == 0:
                conn.execute(
                    """
                    INSERT INTO assignment_history (
                        assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (assignment_id, classroom_id, position["position_name"], now, now, now),
                )
        else:
            conn.execute(
                """
                UPDATE assignment_history
                SET closed_reason = 'seed_closed', updated_at = ?
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (now, assignment_id),
            )
        return assignment_id


def _required_text(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _default_lock_owner() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"
    computer = os.environ.get("COMPUTERNAME") or "unknown-computer"
    return f"{user}@{computer}"


def _read_lock_details(lock_path: Path) -> str:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "another user"
    owner = str(data.get("owner") or "another user")
    created = str(data.get("created_at") or "unknown time")
    return f"{owner} since {created}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seed_schools(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("schools"), list):
        raise ValueError("Seed file must contain schools list.")
    schools: list[dict[str, Any]] = []
    for school_raw in data["schools"]:
        if not isinstance(school_raw, dict):
            raise ValueError("School entry must be an object.")
        classrooms_raw = school_raw.get("classrooms", [])
        if not isinstance(classrooms_raw, list):
            raise ValueError("Classrooms must be a list.")
        classrooms: list[dict[str, Any]] = []
        for classroom_raw in classrooms_raw:
            if not isinstance(classroom_raw, dict):
                raise ValueError("Classroom entry must be an object.")
            positions_raw = classroom_raw.get("slots", classroom_raw.get("positions", []))
            if not isinstance(positions_raw, list):
                raise ValueError("Positions must be a list.")
            positions = [_seed_position(position) for position in positions_raw]
            capacity = classroom_raw.get("licensed_capacity")
            if capacity is not None:
                capacity = int(capacity)
            classrooms.append(
                {
                    "name": _required_text(str(classroom_raw.get("name", "")), "Classroom"),
                    "program": str(classroom_raw.get("program", "") or "").strip(),
                    "ratio_group": str(classroom_raw.get("ratio_group", "") or "").strip(),
                    "licensed_capacity": capacity,
                    "display_order": int(classroom_raw.get("display_order", len(classrooms)) or 0),
                    "positions": positions,
                }
            )
        support_rows_raw = school_raw.get("support_rows", [])
        if not isinstance(support_rows_raw, list):
            raise ValueError("Support rows must be a list.")
        for support_raw in support_rows_raw:
            if not isinstance(support_raw, dict):
                raise ValueError("Support row entry must be an object.")
            positions_raw = support_raw.get("slots", support_raw.get("positions", []))
            if not isinstance(positions_raw, list):
                raise ValueError("Positions must be a list.")
            classrooms.append(
                {
                    "name": _required_text(str(support_raw.get("name", "")), "Support row"),
                    "program": "Support",
                    "ratio_group": "Support",
                    "licensed_capacity": None,
                    "display_order": int(support_raw.get("display_order", 900 + len(classrooms)) or 0),
                    "positions": [_seed_position(position, default_slot_group="support") for position in positions_raw],
                }
            )
        schools.append(
            {
                "name": _required_text(str(school_raw.get("name", "")), "School"),
                "display_order": int(school_raw.get("display_order", len(schools)) or 0),
                "classrooms": classrooms,
            }
        )
    return schools


def _seed_position(position_raw: Any, *, default_slot_group: str = "") -> dict[str, Any]:
    if not isinstance(position_raw, dict):
        raise ValueError("Position entry must be an object.")
    status = str(position_raw.get("status", "dont_need_now") or "dont_need_now").strip()
    if status not in ASSIGNMENT_STATUSES:
        raise ValueError("Unknown assignment status.")
    person_raw = position_raw.get("person") or {}
    if not isinstance(person_raw, dict):
        raise ValueError("Person entry must be an object.")
    permit_status = str(person_raw.get("permit_status", "unknown") or "unknown").strip()
    if permit_status not in PERMIT_STATUSES:
        raise ValueError("Unknown permit status.")
    return {
        "position_name": _required_text(str(position_raw.get("position_name", "")), "Position name"),
        "position_type": _required_text(str(position_raw.get("position_type", "")), "Position type"),
        "status": status,
        "start_date": str(position_raw.get("start_date", "") or "").strip(),
        "slot_group": str(position_raw.get("slot_group", default_slot_group) or default_slot_group).strip(),
        "notes": str(position_raw.get("notes", "") or "").strip(),
        "display_order": int(position_raw.get("display_order", 0) or 0),
        "person": {
            "name": str(person_raw.get("name", "") or "").strip(),
            "permit_status": permit_status,
        },
    }
