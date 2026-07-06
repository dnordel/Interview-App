from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone
import json
from typing import TYPE_CHECKING, Any

from staffing_models import (
    PERMIT_STATUSES,
    StaffingAssignment,
    StaffingClassroom,
    StaffingMetricRow,
    StaffingMetrics,
    StaffingPerson,
    StaffingTransitionResult,
)
from staffing_store import StaffingEditLock, StaffingStore

if TYPE_CHECKING:
    from notification_service import NotificationService


STAFFING_NOTIFICATION_EVENTS = {
    "add_position": "staffing.assignment.created",
    "open_position": "staffing.assignment.need_now",
    "mark_coming": "staffing.assignment.coming",
    "mark_filled": "staffing.assignment.filled",
    "mark_replacing": "staffing.assignment.replace",
    "clear_replacement": "staffing.assignment.need_now",
    "mark_not_needed": "staffing.assignment.not_needed",
    "update_permit_status": "staffing.permit.updated",
}


class StaffingService:
    def __init__(
        self,
        store: StaffingStore,
        *,
        notification_service: NotificationService | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.notification_service = notification_service
        self.clock = clock or _utc_now_iso
        self._replaying_pending_operations = False

    def open_position(self, assignment_id: int) -> StaffingTransitionResult:
        return self._run_or_queue(
            "open_position",
            {"assignment_id": int(assignment_id)},
            lambda: self._open_position_impl(assignment_id),
        )

    def add_position(
        self,
        *,
        school: str,
        classroom: str,
        classroom_program: str = "",
        licensed_capacity: int | None = None,
        position_name: str,
        position_type: str,
        initial_status: str = "dont_need_now",
        person_name: str = "",
        permit_status: str = "unknown",
        start_date: str = "",
        notes: str = "",
    ) -> StaffingTransitionResult:
        return self._add_position_impl(
            school=school,
            classroom=classroom,
            classroom_program=classroom_program,
            licensed_capacity=licensed_capacity,
            position_name=position_name,
            position_type=position_type,
            initial_status=initial_status,
            person_name=person_name,
            permit_status=permit_status,
            start_date=start_date,
            notes=notes,
        )

    def add_person(
        self,
        *,
        name: str,
        role: str,
        permit_status: str = "unknown",
        units: float | int | None = None,
    ) -> StaffingPerson:
        return self._add_person_impl(
            name=name,
            role=role,
            permit_status=permit_status,
            units=units,
        )

    def add_classroom(
        self,
        *,
        school: str,
        name: str,
        program: str = "",
        ratio_group: str = "",
        licensed_capacity: int | None = None,
    ) -> StaffingClassroom:
        classroom_id = self.store.create_classroom(
            school=school,
            name=name,
            program=program,
            ratio_group=ratio_group,
            licensed_capacity=licensed_capacity,
        )
        with self.store.connect() as conn:
            return self.store.classroom_context(conn, classroom_id)

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
        return self.store.update_classroom(
            classroom_id=int(classroom_id),
            school=school,
            name=name,
            program=program,
            ratio_group=ratio_group,
            licensed_capacity=licensed_capacity,
            display_order=int(display_order),
        )

    def deactivate_classroom(self, classroom_id: int) -> StaffingClassroom:
        assignment_count = self.store.classroom_active_assignment_count(int(classroom_id))
        if assignment_count > 0:
            raise ValueError("Cannot deactivate classroom with active assignments.")
        return self.store.deactivate_classroom(int(classroom_id))

    def mark_coming(self, assignment_id: int, *, person_name: str, start_date: str) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_coming",
            {"assignment_id": int(assignment_id), "person_name": person_name, "start_date": start_date},
            lambda: self._mark_coming_impl(assignment_id, person_name=person_name, start_date=start_date),
        )

    def revert_coming(self, assignment_id: int) -> StaffingTransitionResult:
        return self._run_or_queue(
            "revert_coming",
            {"assignment_id": int(assignment_id)},
            lambda: self._revert_coming_impl(assignment_id),
        )

    def mark_filled(self, assignment_id: int) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_filled",
            {"assignment_id": int(assignment_id)},
            lambda: self._mark_filled_impl(assignment_id),
        )

    def mark_replacing(self, assignment_id: int, *, notice_given: str, final_working_day: str) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_replacing",
            {
                "assignment_id": int(assignment_id),
                "notice_given": notice_given,
                "final_working_day": final_working_day,
            },
            lambda: self._mark_replacing_impl(
                assignment_id,
                notice_given=notice_given,
                final_working_day=final_working_day,
            ),
        )

    def clear_replacement(self, assignment_id: int) -> StaffingTransitionResult:
        return self._run_or_queue(
            "clear_replacement",
            {"assignment_id": int(assignment_id)},
            lambda: self._clear_replacement_impl(assignment_id),
        )

    def mark_not_needed(self, assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_not_needed",
            {"assignment_id": int(assignment_id), "confirmed": bool(confirmed)},
            lambda: self._mark_not_needed_impl(assignment_id, confirmed=confirmed),
        )

    def update_permit_status(
        self,
        person_id: int,
        permit_status: str,
        *,
        effective_date: str | None = None,
        units: float | int | None = None,
        documentation_received: bool = False,
        notes: str = "",
    ) -> StaffingTransitionResult:
        return self._run_or_queue(
            "update_permit_status",
            {
                "person_id": int(person_id),
                "permit_status": permit_status,
                "effective_date": effective_date,
                "units": units,
                "documentation_received": bool(documentation_received),
                "notes": notes,
            },
            lambda: self._update_permit_status_impl(
                person_id,
                permit_status,
                effective_date=effective_date,
                units=units,
                documentation_received=documentation_received,
                notes=notes,
            ),
            assignment_id=0,
        )

    def move_person(self, source_assignment_id: int, target_assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        return self._run_or_queue(
            "move_person",
            {
                "source_assignment_id": int(source_assignment_id),
                "target_assignment_id": int(target_assignment_id),
                "confirmed": bool(confirmed),
            },
            lambda: self._move_person_impl(source_assignment_id, target_assignment_id, confirmed=confirmed),
            assignment_id=int(target_assignment_id),
        )

    def update_assignment_details(
        self,
        assignment_id: int,
        *,
        classroom: str,
        classroom_program: str | None = None,
        position_name: str | None = None,
        position_type: str | None = None,
        status: str | None = None,
        person_name: str | None = None,
        start_date: str | None = None,
        shift_start: str = "",
        shift_end: str = "",
        permit_status: str | None = None,
        notes: str | None = None,
    ) -> StaffingTransitionResult:
        return self._run_or_queue(
            "update_assignment_details",
            {
                "assignment_id": int(assignment_id),
                "classroom": classroom,
                "classroom_program": classroom_program,
                "position_name": position_name,
                "position_type": position_type,
                "status": status,
                "person_name": person_name,
                "start_date": start_date,
                "shift_start": shift_start,
                "shift_end": shift_end,
                "permit_status": permit_status,
                "notes": notes,
            },
            lambda: self._update_assignment_details_impl(
                assignment_id,
                classroom=classroom,
                classroom_program=classroom_program,
                position_name=position_name,
                position_type=position_type,
                status=status,
                person_name=person_name,
                start_date=start_date,
                shift_start=shift_start,
                shift_end=shift_end,
                permit_status=permit_status,
                notes=notes,
            ),
        )

    def _add_position_impl(
        self,
        *,
        school: str,
        classroom: str,
        classroom_program: str = "",
        licensed_capacity: int | None = None,
        position_name: str,
        position_type: str,
        initial_status: str = "dont_need_now",
        person_name: str = "",
        permit_status: str = "unknown",
        start_date: str = "",
        notes: str = "",
    ) -> StaffingTransitionResult:
        status = str(initial_status or "").strip() or "dont_need_now"
        if status not in {"dont_need_now", "need_now", "coming", "filled", "replace"}:
            raise ValueError("Unknown assignment status.")
        now = self.clock()
        assignment_id = self.store.create_assignment(
            school=school,
            classroom=classroom,
            classroom_program=classroom_program,
            licensed_capacity=licensed_capacity,
            position_name=position_name,
            position_type=position_type,
            status=status,
            person_name=person_name,
            permit_status=permit_status,
            start_date=start_date,
            notes=notes,
            now=now,
        )
        assignment = self.store.get_assignment(assignment_id)
        self._emit_assignment_event("add_position", assignment)
        return _result(assignment)

    def _add_person_impl(
        self,
        *,
        name: str,
        role: str,
        permit_status: str = "unknown",
        units: float | int | None = None,
    ) -> StaffingPerson:
        name = str(name or "").strip()
        role = str(role or "").strip()
        permit_status = str(permit_status or "unknown").strip() or "unknown"
        if not name:
            raise ValueError("Person name is required.")
        if not role:
            raise ValueError("Role is required.")
        if permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        units_value = None if units is None else float(units)
        now = self.clock()
        with self.store.write_connection("add_person") as conn:
            person_id = self.store.ensure_person(conn, name, role, permit_status, now)
            conn.execute(
                """
                UPDATE people
                SET role = ?, permit_status = ?, units = ?, updated_at = ?
                WHERE id = ?
                """,
                (role, permit_status, units_value, now, person_id),
            )
            return self.store.person_context(conn, person_id)

    def _open_position_impl(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("open_position") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status not in {"dont_need_now", "replace"}:
                raise ValueError("Invalid transition.")
            self._require_active_history_count(conn, assignment_id, 0)
            conn.execute(
                """
                UPDATE assignments
                SET status = 'need_now', person_id = NULL, current_opened_date = ?,
                    current_filled_date = NULL, start_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, assignment_id),
            )
            self._create_history(conn, assignment_id, now)
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("open_position", updated)
        return _result(updated)

    def _mark_coming_impl(self, assignment_id: int, *, person_name: str, start_date: str) -> StaffingTransitionResult:
        now = self.clock()
        start_date = _valid_date(start_date, "Start date")
        if date.fromisoformat(start_date) < _parse_timestamp(now).date():
            raise ValueError("Start date cannot be in the past.")
        with self.store.write_connection("mark_coming") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "need_now":
                raise ValueError("Invalid transition.")
            person_id = self.store.ensure_person(conn, person_name, assignment.position_type, "unknown", now)
            conn.execute(
                """
                UPDATE assignments
                SET status = 'coming', person_id = ?, start_date = ?, updated_at = ?
                WHERE id = ?
                """,
                (person_id, start_date, now, assignment_id),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("mark_coming", updated)
        return _result(updated)

    def _revert_coming_impl(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("revert_coming") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "coming":
                raise ValueError("Invalid transition.")
            conn.execute(
                """
                UPDATE assignments
                SET status = 'need_now', person_id = NULL, start_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, assignment_id),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        return _result(updated)

    def _mark_filled_impl(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("mark_filled") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "coming" or assignment.person_id is None:
                raise ValueError("Invalid transition.")
            close_date = _valid_date(assignment.start_date, "Start date")
            self._require_active_history_count(conn, assignment_id, 1)
            history = conn.execute(
                """
                SELECT id, opened_date FROM assignment_history
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (assignment_id,),
            ).fetchone()
            conn.execute(
                "UPDATE assignments SET status = 'filled', current_filled_date = ?, updated_at = ? WHERE id = ?",
                (close_date, now, assignment_id),
            )
            conn.execute(
                """
                UPDATE assignment_history
                SET filled_date = ?, days_to_fill = ?, closed_reason = 'filled', updated_at = ?
                WHERE id = ?
                """,
                (close_date, _days_between(str(history["opened_date"]), close_date), now, int(history["id"])),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("mark_filled", updated)
        return _result(updated)

    def _mark_replacing_impl(self, assignment_id: int, *, notice_given: str, final_working_day: str) -> StaffingTransitionResult:
        now = self.clock()
        notice_given = _valid_date(notice_given, "Notice given")
        final_working_day = _valid_date(final_working_day, "Final working day")
        with self.store.write_connection("mark_replacing") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "filled" or assignment.person_id is None:
                raise ValueError("Invalid transition.")
            conn.execute(
                """
                UPDATE people
                SET notice_given = ?, final_working_day = ?, active = 0, updated_at = ?
                WHERE id = ?
                """,
                (notice_given, final_working_day, now, assignment.person_id),
            )
            conn.execute(
                """
                UPDATE assignments
                SET status = 'replace', current_opened_date = ?, current_filled_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, assignment_id),
            )
            self._create_history(conn, assignment_id, now)
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("mark_replacing", updated)
        return _result(updated)

    def _clear_replacement_impl(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("clear_replacement") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "replace":
                raise ValueError("Invalid transition.")
            conn.execute(
                """
                UPDATE assignments
                SET status = 'need_now', person_id = NULL, start_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, assignment_id),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("clear_replacement", updated)
        return _result(updated)

    def _mark_not_needed_impl(self, assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("mark_not_needed") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status in {"coming", "filled", "replace"} and not confirmed:
                raise ValueError("Confirmation is required.")
            conn.execute(
                """
                UPDATE assignment_history
                SET closed_reason = 'cancelled', updated_at = ?
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (now, assignment_id),
            )
            conn.execute(
                """
                UPDATE assignments
                SET status = 'dont_need_now', person_id = NULL, start_date = NULL,
                    current_opened_date = NULL, current_filled_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, assignment_id),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("mark_not_needed", updated)
        return _result(updated)

    def _update_permit_status_impl(
        self,
        person_id: int,
        permit_status: str,
        *,
        effective_date: str | None = None,
        units: float | int | None = None,
        documentation_received: bool = False,
        notes: str = "",
    ) -> StaffingTransitionResult:
        now = self.clock()
        if permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        effective_date_text = "" if effective_date is None else _valid_date(effective_date, "Effective date")
        units_value = None if units is None else float(units)
        with self.store.write_connection("update_permit_status") as conn:
            person = self.store.person_context(conn, person_id)
            conn.execute(
                """
                UPDATE people
                SET permit_status = ?, permit_effective_date = ?, units = ?,
                    permit_documentation_received = ?, permit_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    permit_status,
                    effective_date_text,
                    units_value,
                    1 if documentation_received else 0,
                    str(notes or "").strip(),
                    now,
                    person.id,
                ),
            )
            assignment = replace(self._assignment_for_person(conn, person.id, now), updated_at=now)
        self._emit_person_event(person.id, assignment)
        return _result(assignment)

    def _move_person_impl(self, source_assignment_id: int, target_assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        if not confirmed:
            raise ValueError("Confirmation is required.")
        if int(source_assignment_id) == int(target_assignment_id):
            raise ValueError("Source and target positions must be different.")
        now = self.clock()
        with self.store.write_connection("move_person") as conn:
            source = self.store.assignment_context(conn, source_assignment_id)
            target = self.store.assignment_context(conn, target_assignment_id)
            if source.person_id is None:
                raise ValueError("Source position has no person to move.")
            if target.person_id is not None:
                raise ValueError("Target position already has a person.")
            if source.status not in {"filled", "coming"}:
                raise ValueError("Only filled or coming people can be moved.")
            self._close_active_history(conn, target_assignment_id, now)
            target_status = source.status
            target_filled_date = now if target_status == "filled" else None
            conn.execute(
                """
                UPDATE assignments
                SET person_id = ?, status = ?, start_date = ?, current_filled_date = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (source.person_id, target_status, source.start_date, target_filled_date, now, target_assignment_id),
            )
            conn.execute(
                """
                UPDATE assignments
                SET person_id = NULL, status = 'need_now', start_date = NULL,
                    current_opened_date = ?, current_filled_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, source_assignment_id),
            )
            self._create_history_if_missing(conn, source_assignment_id, now)
            updated = self.store.assignment_context(conn, target_assignment_id)
        return _result(updated)

    def _update_assignment_details_impl(
        self,
        assignment_id: int,
        *,
        classroom: str,
        classroom_program: str | None = None,
        position_name: str | None = None,
        position_type: str | None = None,
        status: str | None = None,
        person_name: str | None = None,
        start_date: str | None = None,
        shift_start: str = "",
        shift_end: str = "",
        permit_status: str | None = None,
        notes: str | None = None,
    ) -> StaffingTransitionResult:
        now = self.clock()
        classroom = str(classroom or "").strip()
        if not classroom:
            raise ValueError("Classroom is required.")
        classroom_program = None if classroom_program is None else str(classroom_program or "").strip()
        position_name = None if position_name is None else str(position_name or "").strip()
        position_type = None if position_type is None else str(position_type or "").strip()
        status = None if status is None else str(status or "").strip()
        person_name = None if person_name is None else str(person_name or "").strip()
        notes = None if notes is None else str(notes or "").strip()
        if position_name == "":
            raise ValueError("Position name is required.")
        if position_type == "":
            raise ValueError("Position type is required.")
        if status is not None and status not in {"dont_need_now", "need_now", "coming", "filled", "replace"}:
            raise ValueError("Unknown assignment status.")
        if start_date is not None:
            start_date = "" if str(start_date or "").strip() == "" else _valid_date(str(start_date), "Start date")
        shift_start = _valid_time_or_blank(shift_start, "Shift start")
        shift_end = _valid_time_or_blank(shift_end, "Shift end")
        if permit_status is not None and permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        with self.store.write_connection("update_assignment_details") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            school_row = conn.execute(
                """
                SELECT s.id AS school_id FROM assignments a
                JOIN classrooms c ON c.id = a.classroom_id
                JOIN schools s ON s.id = c.school_id
                WHERE a.id = ?
                """,
                (assignment_id,),
            ).fetchone()
            if school_row is None:
                raise ValueError("Assignment not found.")
            classroom_id = self.store._ensure_classroom(
                conn,
                int(school_row["school_id"]),
                classroom,
                program=assignment.classroom_program if classroom_program is None else classroom_program,
                ratio_group=assignment.ratio_group,
                licensed_capacity=assignment.classroom_capacity,
            )
            conn.execute(
                """
                UPDATE assignments
                SET classroom_id = ?,
                    position_name = COALESCE(?, position_name),
                    position_type = COALESCE(?, position_type),
                    status = COALESCE(?, status),
                    start_date = COALESCE(?, start_date),
                    shift_start = ?,
                    shift_end = ?,
                    notes = COALESCE(?, notes),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    classroom_id,
                    position_name,
                    position_type,
                    status,
                    start_date,
                    shift_start,
                    shift_end,
                    notes,
                    now,
                    assignment_id,
                ),
            )
            if assignment.person_id is not None:
                if person_name:
                    conn.execute(
                        """
                        UPDATE people
                        SET name = ?, normalized_name = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (person_name, person_name.casefold(), now, assignment.person_id),
                    )
                if permit_status is not None:
                    conn.execute(
                        "UPDATE people SET permit_status = ?, updated_at = ? WHERE id = ?",
                        (permit_status, now, assignment.person_id),
                    )
            elif person_name:
                new_person_id = self.store.ensure_person(
                    conn,
                    person_name,
                    position_type or assignment.position_type,
                    permit_status or "unknown",
                    now,
                )
                conn.execute(
                    "UPDATE assignments SET person_id = ?, updated_at = ? WHERE id = ?",
                    (new_person_id, now, assignment_id),
                )
            updated = self.store.assignment_context(conn, assignment_id)
        if permit_status is not None and updated.person_id is not None:
            self._emit_person_event(updated.person_id, updated)
        return _result(updated)

    def staffing_metrics(self, *, today: date, school: str = "") -> StaffingMetrics:
        self.flush_pending_operations()
        rows: list[StaffingMetricRow] = []
        school_filter = str(school or "").strip()
        for assignment in self.store.list_assignments():
            if school_filter and assignment.school != school_filter:
                continue
            days_open = None
            if assignment.status in {"need_now", "replace"} and assignment.current_opened_date:
                opened_date = _parse_timestamp(assignment.current_opened_date).date()
                if opened_date.year > 1971:
                    days_open = max(0, (today - opened_date).days)
            rows.append(
                StaffingMetricRow(
                    assignment_id=assignment.id,
                    school=assignment.school,
                    classroom=assignment.classroom,
                    position_name=assignment.position_name,
                    position_type=assignment.position_type,
                    status=assignment.status,
                    person_name=assignment.person_name,
                    permit_status=assignment.permit_status,
                    start_date=assignment.start_date,
                    shift_start=assignment.shift_start,
                    shift_end=assignment.shift_end,
                    days_open=days_open,
                    classroom_capacity=assignment.classroom_capacity,
                    classroom_program=assignment.classroom_program,
                    ratio_group=assignment.ratio_group,
                    slot_group=assignment.slot_group,
                    notes=assignment.notes,
                    display_order=assignment.display_order,
                )
            )
        rows = self._project_pending_metric_rows(rows)
        if school_filter:
            rows = [row for row in rows if row.school == school_filter]
        open_count = 0
        open_over_7_days = 0
        projected_rows: list[StaffingMetricRow] = []
        for row in rows:
            days_open = row.days_open
            if row.status in {"need_now", "replace"}:
                open_count += 1
                if days_open is not None and days_open > 7:
                    open_over_7_days += 1
            else:
                days_open = None
            projected_rows.append(replace(row, days_open=days_open))
        closed_days = self.store.closed_days_to_fill(school=school_filter)
        avg_days = round(sum(closed_days) / len(closed_days), 1) if closed_days else 0.0
        return StaffingMetrics(
            open_count=open_count,
            avg_days_to_fill=avg_days,
            open_over_7_days=open_over_7_days,
            rows=projected_rows,
        )

    def _create_history(self, conn: Any, assignment_id: int, now: str) -> None:
        row = conn.execute("SELECT classroom_id, position_name FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO assignment_history (
                assignment_id, classroom_id, position_name, opened_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (assignment_id, int(row["classroom_id"]), str(row["position_name"]), now, now, now),
        )

    def _create_history_if_missing(self, conn: Any, assignment_id: int, now: str) -> None:
        count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM assignment_history
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (assignment_id,),
            ).fetchone()[0]
        )
        if count == 0:
            self._create_history(conn, assignment_id, now)

    def _close_active_history(self, conn: Any, assignment_id: int, now: str) -> None:
        conn.execute(
            """
            UPDATE assignment_history
            SET closed_reason = 'filled', filled_date = COALESCE(filled_date, ?), updated_at = ?
            WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
            """,
            (now, now, assignment_id),
        )

    def _require_active_history_count(self, conn: Any, assignment_id: int, expected: int) -> None:
        count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM assignment_history
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (assignment_id,),
            ).fetchone()[0]
        )
        if count != expected:
            raise ValueError("Invalid assignment history state.")

    def flush_pending_operations(self) -> int:
        if self._replaying_pending_operations:
            return 0
        records = self.store.pop_pending_operations()
        if not records:
            return 0
        applied = 0
        self._replaying_pending_operations = True
        try:
            for index, record in enumerate(records):
                try:
                    self._apply_pending_operation(record)
                except StaffingEditLock:
                    self.store.restore_pending_operations(records[index:])
                    return applied
                except (TypeError, ValueError, KeyError):
                    continue
                applied += 1
        finally:
            self._replaying_pending_operations = False
        return applied

    def _run_or_queue(
        self,
        operation: str,
        payload: dict[str, Any],
        action: Callable[[], StaffingTransitionResult],
        *,
        assignment_id: int | None = None,
    ) -> StaffingTransitionResult:
        if not self._replaying_pending_operations:
            self.flush_pending_operations()
        try:
            return action()
        except StaffingEditLock:
            self.store.enqueue_pending_operation(operation, payload)
            queued_assignment_id = assignment_id
            if queued_assignment_id is None:
                queued_assignment_id = int(payload.get("assignment_id", 0) or 0)
            return StaffingTransitionResult(
                assignment_id=queued_assignment_id,
                status="queued",
                person_id=None,
                updated_at=self.clock(),
            )

    def _apply_pending_operation(self, record: dict[str, Any]) -> None:
        operation = str(record.get("operation", ""))
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("Invalid pending staffing operation.")
        if operation == "open_position":
            self._open_position_impl(int(payload["assignment_id"]))
        elif operation == "mark_coming":
            self._mark_coming_impl(
                int(payload["assignment_id"]),
                person_name=str(payload["person_name"]),
                start_date=str(payload["start_date"]),
            )
        elif operation == "revert_coming":
            self._revert_coming_impl(int(payload["assignment_id"]))
        elif operation == "mark_filled":
            self._mark_filled_impl(int(payload["assignment_id"]))
        elif operation == "mark_replacing":
            self._mark_replacing_impl(
                int(payload["assignment_id"]),
                notice_given=str(payload["notice_given"]),
                final_working_day=str(payload["final_working_day"]),
            )
        elif operation == "clear_replacement":
            self._clear_replacement_impl(int(payload["assignment_id"]))
        elif operation == "mark_not_needed":
            self._mark_not_needed_impl(int(payload["assignment_id"]), confirmed=bool(payload.get("confirmed", False)))
        elif operation == "update_permit_status":
            self._update_permit_status_impl(
                int(payload["person_id"]),
                str(payload["permit_status"]),
                effective_date=str(payload["effective_date"]) if payload.get("effective_date") is not None else None,
                units=payload.get("units"),
                documentation_received=bool(payload.get("documentation_received", False)),
                notes=str(payload.get("notes", "")),
            )
        elif operation == "move_person":
            self._move_person_impl(
                int(payload["source_assignment_id"]),
                int(payload["target_assignment_id"]),
                confirmed=bool(payload.get("confirmed", False)),
            )
        elif operation == "update_assignment_details":
            permit_status = payload.get("permit_status")
            self._update_assignment_details_impl(
                int(payload["assignment_id"]),
                classroom=str(payload["classroom"]),
                shift_start=str(payload.get("shift_start", "")),
                shift_end=str(payload.get("shift_end", "")),
                permit_status=str(permit_status) if permit_status is not None else None,
            )
        else:
            raise ValueError("Unknown pending staffing operation.")

    def _project_pending_metric_rows(self, rows: list[StaffingMetricRow]) -> list[StaffingMetricRow]:
        projected = {row.assignment_id: row for row in rows}
        try:
            records = self.store.peek_pending_operations()
        except (OSError, json.JSONDecodeError):
            return rows
        for record in records:
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            assignment_id = int(payload.get("assignment_id", payload.get("target_assignment_id", 0)) or 0)
            row = projected.get(assignment_id)
            if row is None:
                continue
            operation = str(record.get("operation", ""))
            if operation == "open_position":
                projected[assignment_id] = replace(row, status="need_now", person_name="", start_date="")
            elif operation == "mark_coming":
                projected[assignment_id] = replace(
                    row,
                    status="coming",
                    person_name=str(payload.get("person_name", "")),
                    start_date=str(payload.get("start_date", "")),
                )
            elif operation == "revert_coming":
                projected[assignment_id] = replace(row, status="need_now", person_name="", start_date="")
            elif operation == "mark_filled":
                projected[assignment_id] = replace(row, status="filled")
            elif operation == "mark_replacing":
                projected[assignment_id] = replace(row, status="replace")
            elif operation == "clear_replacement":
                projected[assignment_id] = replace(row, status="need_now", person_name="", start_date="")
            elif operation == "mark_not_needed":
                projected[assignment_id] = replace(row, status="dont_need_now", person_name="", start_date="")
            elif operation == "update_assignment_details":
                projected[assignment_id] = replace(
                    row,
                    classroom=str(payload.get("classroom", row.classroom)),
                    shift_start=str(payload.get("shift_start", row.shift_start)),
                    shift_end=str(payload.get("shift_end", row.shift_end)),
                    permit_status=str(payload.get("permit_status", row.permit_status) or row.permit_status),
                )
        return list(projected.values())

    def _assignment_for_person(self, conn: Any, person_id: int, fallback_updated_at: str) -> StaffingAssignment:
        row = conn.execute(
            """
            SELECT id FROM assignments
            WHERE person_id = ? AND active = 1
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (person_id,),
        ).fetchone()
        if row is None:
            person = self.store.person_context(conn, person_id)
            return StaffingAssignment(
                id=0,
                school="",
                classroom="",
                position_name="",
                position_type="",
                status="",
                person_id=person.id,
                person_name=person.name,
                shift_start="",
                shift_end="",
                notice_given="",
                final_working_day="",
                permit_status=person.permit_status,
                updated_at=fallback_updated_at,
            )
        return self.store.assignment_context(conn, int(row["id"]))

    def _emit_assignment_event(self, action: str, assignment: StaffingAssignment) -> None:
        event_type = STAFFING_NOTIFICATION_EVENTS[action]
        key = f"staffing:{assignment.id}:{event_type}:{assignment.updated_at}"
        self._emit(event_type, _payload(assignment), key)

    def _emit_person_event(self, person_id: int, assignment: StaffingAssignment) -> None:
        event_type = STAFFING_NOTIFICATION_EVENTS["update_permit_status"]
        key = f"staffing:person:{person_id}:{event_type}:{assignment.updated_at}"
        self._emit(event_type, _payload(assignment), key)

    def _emit(self, event_type: str, payload: dict[str, str], idempotency_key: str) -> None:
        if self.notification_service is None:
            return
        try:
            self.notification_service.emit_event(event_type, payload, idempotency_key)
        except Exception:
            return


def _payload(assignment: StaffingAssignment) -> dict[str, str]:
    return {
        "school": assignment.school,
        "classroom": assignment.classroom,
        "position_name": assignment.position_name,
        "position_type": assignment.position_type,
        "slot_group": assignment.slot_group,
        "assignment_status": assignment.status,
        "person_name": assignment.person_name,
        "start_date": assignment.start_date,
        "shift_start": assignment.shift_start,
        "shift_end": assignment.shift_end,
        "notice_given": assignment.notice_given,
        "final_working_day": assignment.final_working_day,
        "permit_status": assignment.permit_status,
    }


def _result(assignment: StaffingAssignment) -> StaffingTransitionResult:
    return StaffingTransitionResult(
        assignment_id=assignment.id,
        status=assignment.status,
        person_id=assignment.person_id,
        updated_at=assignment.updated_at,
    )


def _valid_date(value: str, label: str) -> str:
    text = str(value or "").strip()
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date.") from exc
    return text


def _valid_time_or_blank(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{label} must be HH:MM.") from exc
    return text


def _days_between(start: str, end: str) -> int:
    return max(0, (_parse_timestamp(end).date() - _parse_timestamp(start).date()).days)


def _parse_timestamp(value: str) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
