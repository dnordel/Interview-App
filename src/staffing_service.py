from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from notification_service import NotificationService
from staffing_models import PERMIT_STATUSES, StaffingAssignment, StaffingMetricRow, StaffingMetrics, StaffingTransitionResult
from staffing_store import StaffingStore


STAFFING_NOTIFICATION_EVENTS = {
    "open_position": "staffing.assignment.need_now",
    "mark_coming": "staffing.assignment.coming",
    "mark_filled": "staffing.assignment.filled",
    "mark_replacing": "staffing.assignment.replace",
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

    def open_position(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.connect() as conn:
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

    def mark_coming(self, assignment_id: int, *, person_name: str, start_date: str) -> StaffingTransitionResult:
        now = self.clock()
        start_date = _valid_date(start_date, "Start date")
        with self.store.connect() as conn:
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

    def revert_coming(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.connect() as conn:
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

    def mark_filled(self, assignment_id: int) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.connect() as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "coming" or assignment.person_id is None:
                raise ValueError("Invalid transition.")
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
                (now, now, assignment_id),
            )
            conn.execute(
                """
                UPDATE assignment_history
                SET filled_date = ?, days_to_fill = ?, closed_reason = 'filled', updated_at = ?
                WHERE id = ?
                """,
                (now, _days_between(str(history["opened_date"]), now), now, int(history["id"])),
            )
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_assignment_event("mark_filled", updated)
        return _result(updated)

    def mark_replacing(self, assignment_id: int, *, notice_given: str, final_working_day: str) -> StaffingTransitionResult:
        now = self.clock()
        notice_given = _valid_date(notice_given, "Notice given")
        final_working_day = _valid_date(final_working_day, "Final working day")
        with self.store.connect() as conn:
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

    def mark_not_needed(self, assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.connect() as conn:
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

    def update_permit_status(self, person_id: int, permit_status: str) -> StaffingTransitionResult:
        now = self.clock()
        if permit_status not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        with self.store.connect() as conn:
            person = self.store.person_context(conn, person_id)
            conn.execute(
                "UPDATE people SET permit_status = ?, updated_at = ? WHERE id = ?",
                (permit_status, now, person.id),
            )
            assignment = replace(self._assignment_for_person(conn, person.id, now), updated_at=now)
        self._emit_person_event(person.id, assignment)
        return _result(assignment)

    def staffing_metrics(self, *, today: date) -> StaffingMetrics:
        rows: list[StaffingMetricRow] = []
        open_count = 0
        open_over_7_days = 0
        for assignment in self.store.list_assignments():
            days_open = None
            if assignment.status in {"need_now", "replace"} and assignment.current_opened_date:
                days_open = max(0, (today - _parse_timestamp(assignment.current_opened_date).date()).days)
                open_count += 1
                if days_open > 7:
                    open_over_7_days += 1
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
                    days_open=days_open,
                )
            )
        closed_days = self.store.closed_days_to_fill()
        avg_days = round(sum(closed_days) / len(closed_days), 1) if closed_days else 0.0
        return StaffingMetrics(
            open_count=open_count,
            avg_days_to_fill=avg_days,
            open_over_7_days=open_over_7_days,
            rows=rows,
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
        "assignment_status": assignment.status,
        "person_name": assignment.person_name,
        "start_date": assignment.start_date,
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


def _days_between(start: str, end: str) -> int:
    return max(0, (_parse_timestamp(end).date() - _parse_timestamp(start).date()).days)


def _parse_timestamp(value: str) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
