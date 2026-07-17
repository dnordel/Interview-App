from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence
from uuid import uuid4

from staffing_models import (
    DIRECTOR_INTERVIEW_DECISIONS,
    DIRECTOR_REFERRAL_OUTCOMES,
    PERMIT_STATUSES,
    StaffingAssignment,
    StaffingClassroom,
    StaffingDirectorCandidate,
    StaffingDirectorInterview,
    StaffingDirectorInterviewDifference,
    StaffingMetricRow,
    StaffingMetrics,
    StaffingPerson,
    StaffingTransitionResult,
)
from staffing_store import StaffingEditLock, StaffingStore

if TYPE_CHECKING:
    from notification_service import NotificationService
    from staffing_change_stage import StaffingChangeEvent, StaffingChangeStage


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


@dataclass(frozen=True)
class StaffingChangeConflict:
    event_id: str
    source_replica: str
    school: str
    operation: str
    base_snapshot: dict[str, Any]
    local_snapshot: dict[str, Any]
    remote_payload: dict[str, Any]


def staffing_change_conflict_message(conflict: StaffingChangeConflict) -> str:
    actor = conflict.source_replica.rsplit(":", 1)[-1] or "another user"
    labels = {
        "assignment_id": "Position ID",
        "source_assignment_id": "Source position ID",
        "target_assignment_id": "Target position ID",
        "person_name": "Person",
    }
    changes = [
        f"{labels.get(key, key.replace('_', ' ').title())}: {value if value not in (None, '') else '(blank)'}"
        for key, value in conflict.remote_payload.items()
        if not key.startswith("_")
    ]
    detail = "\n".join(f"• {line}" for line in changes) or "• No field details provided"
    return (
        "Another user's staffing change conflicts with your own.\n\n"
        f"User: {actor}\nSchool: {conflict.school}\nChange: {conflict.operation.replace('_', ' ').title()}\n\n"
        f"Other user's changes:\n{detail}\n\nWould you like to accept these changes?\n"
        "Choose No to keep your version."
    )


class StaffingService:
    def __init__(
        self,
        store: StaffingStore,
        *,
        notification_service: NotificationService | None = None,
        clock: Callable[[], str] | None = None,
        change_stage: StaffingChangeStage | None = None,
        replica: str = "",
        publisher: str = "",
        school_scope: str = "",
        conflict_resolver: Callable[[StaffingChangeConflict], bool] | None = None,
    ) -> None:
        self.store = store
        self.notification_service = notification_service
        self.clock = clock or _utc_now_iso
        self.change_stage = change_stage
        self.replica = str(replica or "").strip()
        self.publisher = str(publisher or "").strip() or self.replica
        self.school_scope = str(school_scope or "").strip()
        self.conflict_resolver = conflict_resolver
        if self.change_stage is not None and not self.replica:
            raise ValueError("Replica is required when staffing change staging is enabled.")
        self._replaying_pending_operations = False
        self._replaying_staged_changes = False

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
        payload = {
            "school": school,
            "classroom": classroom,
            "classroom_program": classroom_program,
            "licensed_capacity": licensed_capacity,
            "position_name": position_name,
            "position_type": position_type,
            "initial_status": initial_status,
            "person_name": person_name,
            "permit_status": permit_status,
            "start_date": start_date,
            "notes": notes,
        }
        result = self._add_position_impl(
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
        payload["source_assignment_id"] = result.assignment_id
        self._publish_change("add_position", payload, assignment_id=result.assignment_id)
        return result

    def add_person(
        self,
        *,
        name: str,
        role: str,
        permit_status: str = "unknown",
        units: float | int | None = None,
    ) -> StaffingPerson:
        person = self._add_person_impl(
            name=name,
            role=role,
            permit_status=permit_status,
            units=units,
        )
        self._publish_change(
            "add_person",
            {
                "school": "*",
                "name": person.name,
                "role": person.role,
                "permit_status": person.permit_status,
                "units": person.units,
                "_base_snapshot": {},
            },
        )
        return person

    def update_person(
        self,
        person_id: int,
        *,
        name: str,
        role: str,
        permit_status: str = "unknown",
        units: float | int | None = None,
    ) -> StaffingPerson:
        clean_name = str(name or "").strip()
        clean_role = str(role or "").strip()
        clean_permit = str(permit_status or "unknown").strip() or "unknown"
        if not clean_name:
            raise ValueError("Person name is required.")
        if not clean_role:
            raise ValueError("Role is required.")
        if clean_permit not in PERMIT_STATUSES:
            raise ValueError("Unknown permit status.")
        units_value = None if units is None else float(units)
        current = next((item for item in self.store.list_people() if item.id == int(person_id)), None)
        if current is None:
            raise ValueError("Person not found.")
        payload = {
            "school": "*",
            "person_id": int(person_id),
            "_person_lookup_name": current.name,
            "name": clean_name,
            "role": clean_role,
            "permit_status": clean_permit,
            "units": units_value,
        }
        payload["_base_snapshot"] = self._change_snapshot(payload, assignment_id=None)
        now = self.clock()
        with self.store.write_connection("update_person") as conn:
            person = self.store.person_context(conn, int(person_id))
            duplicate = conn.execute(
                "SELECT id FROM people WHERE normalized_name = ? AND active = 1 AND id <> ?",
                (clean_name.casefold(), person.id),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("An active person with this name already exists.")
            conn.execute(
                """
                UPDATE people
                SET name = ?, normalized_name = ?, role = ?, permit_status = ?, units = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_name, clean_name.casefold(), clean_role, clean_permit, units_value, now, person.id),
            )
            result = self.store.person_context(conn, person.id)
        self._publish_change("update_person", payload)
        return result

    def deactivate_person(self, person_id: int) -> StaffingPerson:
        current = next((item for item in self.store.list_people() if item.id == int(person_id)), None)
        if current is None:
            raise ValueError("Person not found.")
        payload = {
            "school": "*",
            "person_id": int(person_id),
            "_person_lookup_name": current.name,
        }
        payload["_base_snapshot"] = self._change_snapshot(payload, assignment_id=None)
        now = self.clock()
        with self.store.write_connection("deactivate_person") as conn:
            person = self.store.person_context(conn, int(person_id))
            if not person.active:
                raise ValueError("Person is already inactive.")
            assignment = conn.execute(
                "SELECT id FROM assignments WHERE person_id = ? AND active = 1 LIMIT 1",
                (person.id,),
            ).fetchone()
            if assignment is not None:
                raise ValueError("Cannot deactivate employee while assigned to an active position.")
            conn.execute(
                "UPDATE people SET active = 0, updated_at = ? WHERE id = ?",
                (now, person.id),
            )
            result = replace(person, active=False, updated_at=now)
        self._publish_change("deactivate_person", payload)
        return result

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
            classroom = self.store.classroom_context(conn, classroom_id)
        self._publish_change(
            "add_classroom",
            {
                "school": classroom.school,
                "name": classroom.name,
                "program": classroom.program,
                "ratio_group": classroom.ratio_group,
                "licensed_capacity": classroom.licensed_capacity,
                "_base_snapshot": {},
            },
        )
        return classroom

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
        current = next(
            (item for item in self.store.list_classrooms() if item.id == int(classroom_id)),
            None,
        )
        if current is None:
            raise ValueError("Classroom not found.")
        payload = {
            "classroom_id": int(classroom_id),
            "school": school,
            "name": name,
            "program": program,
            "ratio_group": ratio_group,
            "licensed_capacity": licensed_capacity,
            "display_order": int(display_order),
            "_classroom_lookup_school": current.school,
            "_classroom_lookup_name": current.name,
        }
        payload["_base_snapshot"] = self._change_snapshot(payload, assignment_id=None)
        result = self.store.update_classroom(
            classroom_id=int(classroom_id),
            school=school,
            name=name,
            program=program,
            ratio_group=ratio_group,
            licensed_capacity=licensed_capacity,
            display_order=int(display_order),
        )
        self._publish_change("update_classroom", payload)
        return result

    def deactivate_classroom(self, classroom_id: int) -> StaffingClassroom:
        current = next((item for item in self.store.list_classrooms() if item.id == int(classroom_id)), None)
        if current is None:
            raise ValueError("Classroom not found.")
        payload = {
            "classroom_id": int(classroom_id),
            "school": current.school,
            "_classroom_lookup_school": current.school,
            "_classroom_lookup_name": current.name,
        }
        payload["_base_snapshot"] = self._change_snapshot(payload, assignment_id=None)
        assignment_count = self.store.classroom_active_assignment_count(int(classroom_id))
        if assignment_count > 0:
            raise ValueError("Cannot deactivate classroom with active assignments.")
        result = self.store.deactivate_classroom(int(classroom_id))
        self._publish_change("deactivate_classroom", payload)
        return result

    def mark_coming(
        self,
        assignment_id: int,
        *,
        person_name: str,
        start_date: str,
        position_type: str | None = None,
    ) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_coming",
            {
                "assignment_id": int(assignment_id),
                "person_name": person_name,
                "start_date": start_date,
                "position_type": position_type,
            },
            lambda: self._mark_coming_impl(
                assignment_id,
                person_name=person_name,
                start_date=start_date,
                position_type=position_type,
            ),
        )

    def revert_coming(self, assignment_id: int) -> StaffingTransitionResult:
        return self._run_or_queue(
            "revert_coming",
            {"assignment_id": int(assignment_id)},
            lambda: self._revert_coming_impl(assignment_id),
        )

    def mark_filled(
        self,
        assignment_id: int,
        *,
        actual_start_date: str | None = None,
        repair_missing_history: bool = False,
    ) -> StaffingTransitionResult:
        return self._run_or_queue(
            "mark_filled",
            {
                "assignment_id": int(assignment_id),
                "actual_start_date": actual_start_date,
                "repair_missing_history": bool(repair_missing_history),
            },
            lambda: self._mark_filled_impl(
                assignment_id,
                actual_start_date=actual_start_date,
                repair_missing_history=repair_missing_history,
            ),
        )

    def auto_fill_due_coming(self, *, today: date) -> list[StaffingTransitionResult]:
        filled: list[StaffingTransitionResult] = []
        for assignment in self.store.list_assignments():
            if assignment.status != "coming" or not assignment.start_date:
                continue
            try:
                start_date = date.fromisoformat(assignment.start_date[:10])
            except ValueError:
                continue
            if start_date > today:
                continue
            filled.append(
                self.mark_filled(
                    assignment.id,
                    actual_start_date=start_date.isoformat(),
                    repair_missing_history=True,
                )
            )
        return filled

    def update_start_date(
        self,
        assignment_id: int,
        *,
        start_date: str,
        today: date,
    ) -> StaffingTransitionResult:
        return self._run_or_queue(
            "update_start_date",
            {
                "assignment_id": int(assignment_id),
                "start_date": start_date,
                "today": today.isoformat(),
            },
            lambda: self._update_start_date_impl(
                assignment_id,
                start_date=start_date,
                today=today,
            ),
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

    def delete_position(self, assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        return self._run_or_queue(
            "delete_position",
            {"assignment_id": int(assignment_id), "confirmed": bool(confirmed)},
            lambda: self._delete_position_impl(assignment_id, confirmed=confirmed),
        )

    def update_permit_status(
        self,
        person_id: int,
        permit_status: str,
        *,
        effective_date: str | None = None,
        units: float | int | None = None,
        documentation_received: bool = False,
        attachment_path: str | Path | None = None,
        notes: str = "",
    ) -> StaffingTransitionResult:
        person = next((item for item in self.store.list_people() if item.id == int(person_id)), None)
        if person is None:
            raise ValueError("Person not found.")
        managed_attachment_path: str | None = None
        if attachment_path is not None and str(attachment_path).strip():
            managed_attachment_path = self._store_permit_attachment(person.id, Path(attachment_path))
        return self._run_or_queue(
            "update_permit_status",
            {
                "person_id": int(person_id),
                "person_name": person.name,
                "permit_status": permit_status,
                "effective_date": effective_date,
                "units": units,
                "documentation_received": bool(documentation_received),
                "permit_document_path": managed_attachment_path,
                "notes": notes,
            },
            lambda: self._update_permit_status_impl(
                person_id,
                permit_status,
                effective_date=effective_date,
                units=units,
                documentation_received=documentation_received,
                permit_document_path=managed_attachment_path,
                notes=notes,
            ),
            assignment_id=0,
        )

    def _store_permit_attachment(self, person_id: int, source: Path) -> str:
        allowed_suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
        try:
            resolved = source.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("Permit attachment file does not exist.") from exc
        if not resolved.is_file():
            raise ValueError("Permit attachment must be a file.")
        suffix = resolved.suffix.casefold()
        if suffix not in allowed_suffixes:
            raise ValueError("Permit attachment must be PDF, image, or Word document.")
        if resolved.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Permit attachment must be 25 MB or smaller.")
        target_dir = (Path(self.store.path).resolve().parent / "staffing_attachments").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"person-{int(person_id)}-permit-{uuid4().hex}{suffix}"
        shutil.copy2(resolved, target)
        return str(target)

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

    def upsert_director_candidate_referral(
        self,
        *,
        history_id: str,
        candidate_name: str,
        school: str,
        position: str = "",
        interviewer_rating: float | int | str | None = None,
        interviewer_outcome: str,
        interview_date: str = "",
        candidate_email: str = "",
        candidate_phone: str = "",
        referral_date: str = "",
        queue_on_lock: bool = False,
    ) -> StaffingDirectorCandidate:
        outcome = str(interviewer_outcome or "").strip().lower()
        if outcome not in DIRECTOR_REFERRAL_OUTCOMES:
            raise ValueError("Director referral outcome must be hire or borderline.")
        rating = _optional_rating(interviewer_rating, "Interviewer rating")
        clean_interview_date = "" if not str(interview_date or "").strip() else _valid_date(str(interview_date), "Interview date")
        clean_referral_date = str(referral_date or "").strip() or clean_interview_date
        if clean_referral_date:
            clean_referral_date = _valid_date(clean_referral_date, "Referral date")
        now = self.clock()
        payload = {
            "history_id": str(history_id),
            "candidate_name": str(candidate_name),
            "school": str(school),
            "position": str(position or ""),
            "interviewer_rating": rating,
            "interviewer_outcome": outcome,
            "interview_date": clean_interview_date,
            "candidate_email": str(candidate_email or ""),
            "candidate_phone": str(candidate_phone or ""),
            "referral_date": clean_referral_date,
        }
        if queue_on_lock and not self._replaying_pending_operations:
            self.flush_pending_operations()
        try:
            return self.store.upsert_director_candidate_referral(
                **payload,
                now=now,
            )
        except StaffingEditLock:
            if not queue_on_lock:
                raise
            self.store.enqueue_pending_operation("director_candidate_referral", payload)
            return StaffingDirectorCandidate(
                id=0,
                history_id=payload["history_id"],
                candidate_name=payload["candidate_name"],
                school=payload["school"],
                position=payload["position"],
                interviewer_rating=rating,
                interviewer_outcome=outcome,
                interview_date=clean_interview_date,
                candidate_email=payload["candidate_email"],
                candidate_phone=payload["candidate_phone"],
                referral_date=clean_referral_date,
                updated_at=now,
            )

    def list_pending_director_interviews(self, *, school: str = "") -> list[StaffingDirectorCandidate]:
        return self.store.list_director_candidate_referrals(school=school, include_completed=False)

    def reconcile_director_referral(
        self,
        *,
        history_id: str,
        candidate_name: str,
        school: str,
        calculated_outcome: str,
        position: str = "",
        interviewer_rating: float | int | str | None = None,
        interview_date: str = "",
        candidate_email: str = "",
        candidate_phone: str = "",
        referral_date: str = "",
    ) -> StaffingDirectorCandidate | None:
        outcome = str(calculated_outcome or "").strip().lower().replace(" ", "_").replace("-", "_")
        if outcome in DIRECTOR_REFERRAL_OUTCOMES:
            return self.upsert_director_candidate_referral(
                history_id=history_id,
                candidate_name=candidate_name,
                school=school,
                position=position,
                interviewer_rating=interviewer_rating,
                interviewer_outcome=outcome,
                interview_date=interview_date,
                candidate_email=candidate_email,
                candidate_phone=candidate_phone,
                referral_date=referral_date,
            )
        if outcome == "no_hire":
            self.store.remove_pending_director_referral_for_reconciliation(
                history_id,
                removed_by="system",
                removal_source="candidate_report_reconciliation",
            )
            return None
        raise ValueError("Calculated outcome must be hire, borderline, or no_hire.")

    def list_completed_director_interviews(self, *, school: str = "") -> list[StaffingDirectorInterview]:
        return self.store.list_director_interviews(school=school)

    def find_completed_director_interview(
        self,
        *,
        history_id: str,
        school: str,
    ) -> StaffingDirectorInterview | None:
        return self.store.find_completed_director_interview(
            history_id=history_id,
            school=school,
        )

    def find_any_completed_director_interview(
        self,
        *,
        history_id: str,
        school: str,
    ) -> StaffingDirectorInterview | None:
        return self.store.find_any_completed_director_interview(history_id=history_id, school=school)

    def delete_pending_director_interviews(
        self,
        referral_ids: Sequence[int],
        *,
        removed_by: str = "",
        removal_source: str = "",
    ) -> int:
        return self.store.delete_pending_director_referrals(
            referral_ids,
            removed_by=removed_by,
            removal_source=removal_source,
        )

    def dismiss_director_referral_history_ids(
        self,
        history_ids: Sequence[str],
        *,
        removed_by: str = "",
        removal_source: str = "",
    ) -> int:
        return self.store.dismiss_director_referral_history_ids(
            history_ids,
            removed_by=removed_by,
            removal_source=removal_source,
        )

    def record_director_interview(
        self,
        referral_id: int,
        *,
        director_name: str,
        completed_date: str,
        rating: float | int | str,
        decision: str,
        decision_notes: str,
        proposed_shift_start: str = "",
        proposed_shift_end: str = "",
        proposed_classroom: str = "",
        follow_up_needed: bool = False,
        candidate_email: str = "",
        candidate_phone: str = "",
    ) -> StaffingDirectorInterview:
        clean_date = _valid_date(completed_date, "Director interview date")
        clean_rating = _required_rating(rating, "Director rating")
        clean_decision = str(decision or "").strip().lower()
        if clean_decision not in DIRECTOR_INTERVIEW_DECISIONS:
            raise ValueError("Director decision must be hire or no_hire.")
        notes = str(decision_notes or "").strip()
        if not notes:
            raise ValueError("Decision notes are required.")
        shift_start = ""
        shift_end = ""
        classroom = ""
        if clean_decision == "hire":
            shift_start = _valid_shift_time(proposed_shift_start, "Shift start")
            shift_end = _valid_shift_time(proposed_shift_end, "Shift end")
            classroom = str(proposed_classroom or "").strip()
            if not classroom:
                raise ValueError("Proposed classroom is required for hire decisions.")
        with self.store.connect() as conn:
            referral = self.store.director_candidate_context(conn, int(referral_id))
        email = str(candidate_email or referral.candidate_email or "").strip()
        phone = str(candidate_phone or referral.candidate_phone or "").strip()
        if clean_decision == "hire":
            if not email:
                raise ValueError("Candidate email is required for hire decisions.")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ValueError("Candidate email is invalid.")
            if phone:
                digits = re.sub(r"\D", "", phone)
                if len(digits) == 11 and digits.startswith("1"):
                    digits = digits[1:]
                if len(digits) != 10:
                    raise ValueError("Candidate phone must contain 10 U.S. digits.")
                phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        result = self.store.record_director_interview(
            int(referral_id),
            director_name=director_name,
            completed_date=clean_date,
            rating=clean_rating,
            decision=clean_decision,
            decision_notes=notes,
            proposed_shift_start=shift_start,
            proposed_shift_end=shift_end,
            proposed_classroom=classroom,
            candidate_email=email,
            candidate_phone=phone,
            follow_up_needed=follow_up_needed,
            owner_approval_status="pending_owner_approval",
            now=self.clock(),
        )
        self._publish_change(
            "record_director_interview",
            {
                "history_id": result.history_id,
                "school": result.school,
                "candidate_name": referral.candidate_name,
                "position": referral.position,
                "interviewer_rating": referral.interviewer_rating,
                "interviewer_outcome": referral.interviewer_outcome,
                "interview_date": referral.interview_date,
                "candidate_email": email,
                "candidate_phone": phone,
                "referral_date": referral.referral_date,
                "director_name": director_name,
                "completed_date": clean_date,
                "rating": clean_rating,
                "decision": clean_decision,
                "decision_notes": notes,
                "proposed_shift_start": shift_start,
                "proposed_shift_end": shift_end,
                "proposed_classroom": classroom,
                "follow_up_needed": bool(follow_up_needed),
            },
        )
        return result

    def reopen_director_interview(
        self,
        interview_id: int,
        *,
        expected_row_version: int,
        reason: str,
        actor: str,
        actor_role: str,
    ) -> StaffingDirectorInterview:
        return self.store.reopen_director_interview(
            int(interview_id),
            expected_row_version=int(expected_row_version),
            reason=reason,
            actor=actor,
            actor_role=actor_role,
            now=self.clock(),
        )

    def revise_director_interview(
        self,
        interview_id: int,
        *,
        expected_row_version: int,
        director_name: str,
        completed_date: str,
        rating: float | int | str,
        decision: str,
        decision_notes: str,
        reason: str,
        actor: str,
        actor_role: str,
        proposed_shift_start: str = "",
        proposed_shift_end: str = "",
        proposed_classroom: str = "",
        follow_up_needed: bool = False,
    ) -> StaffingDirectorInterview:
        clean_date = _valid_date(completed_date, "Director interview date")
        clean_rating = _required_rating(rating, "Director rating")
        clean_decision = str(decision or "").strip().lower()
        if clean_decision not in DIRECTOR_INTERVIEW_DECISIONS:
            raise ValueError("Director decision must be hire or no_hire.")
        notes = str(decision_notes or "").strip()
        if not notes:
            raise ValueError("Decision notes are required.")
        shift_start = ""
        shift_end = ""
        classroom = ""
        if clean_decision == "hire":
            shift_start = _valid_shift_time(proposed_shift_start, "Shift start")
            shift_end = _valid_shift_time(proposed_shift_end, "Shift end")
            classroom = str(proposed_classroom or "").strip()
            if not classroom:
                raise ValueError("Proposed classroom is required for hire decisions.")
        return self.store.revise_director_interview(
            int(interview_id),
            expected_row_version=int(expected_row_version),
            director_name=director_name,
            completed_date=clean_date,
            rating=clean_rating,
            decision=clean_decision,
            decision_notes=notes,
            proposed_shift_start=shift_start,
            proposed_shift_end=shift_end,
            proposed_classroom=classroom,
            follow_up_needed=follow_up_needed,
            reason=str(reason or "").strip(),
            actor=actor,
            actor_role=actor_role,
            now=self.clock(),
        )

    def list_director_interview_audit(self, interview_id: int) -> list[dict[str, Any]]:
        return self.store.list_director_interview_audit(int(interview_id))

    def compare_director_interview_version(
        self,
        local: StaffingDirectorInterview,
        *,
        saved: StaffingDirectorInterview,
    ) -> list[StaffingDirectorInterviewDifference]:
        current = self.store.get_director_interview(local.id)
        saved_values = asdict(saved)
        current_values = asdict(current)
        local_values = asdict(local)
        return [
            StaffingDirectorInterviewDifference(
                field_name=field,
                saved_value=saved_values.get(field),
                current_value=current_values.get(field),
                local_value=local_values.get(field),
            )
            for field in sorted(saved_values)
            if current_values.get(field) != saved_values.get(field) or local_values.get(field) != saved_values.get(field)
        ]

    def load_director_interview(self, interview_id: int) -> StaffingDirectorInterview:
        return self.store.get_director_interview(int(interview_id))

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
        if assignment.person_id is not None:
            self._emit_notice_event(assignment)
        self._emit_assignment_event("open_position", updated)
        return _result(updated)

    def _mark_coming_impl(
        self,
        assignment_id: int,
        *,
        person_name: str,
        start_date: str,
        position_type: str | None = None,
    ) -> StaffingTransitionResult:
        now = self.clock()
        start_date = _valid_date(start_date, "Start date")
        position_type = None if position_type is None else str(position_type).strip()
        if position_type == "":
            raise ValueError("Position type is required.")
        if date.fromisoformat(start_date) < _parse_timestamp(now).date():
            raise ValueError("Start date cannot be in the past.")
        with self.store.write_connection("mark_coming") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "need_now":
                raise ValueError("Invalid transition.")
            selected_type = position_type or assignment.position_type
            position_name = assignment.position_name
            if position_type is not None and position_type != assignment.position_type:
                old_prefix = f"{assignment.position_type} "
                if position_name == assignment.position_type:
                    position_name = position_type
                elif position_name.startswith(old_prefix):
                    position_name = f"{position_type} {position_name[len(old_prefix):]}"
            person_id = self.store.ensure_person(conn, person_name, selected_type, "unknown", now)
            conn.execute(
                """
                UPDATE assignments
                SET status = 'coming', person_id = ?, start_date = ?,
                    position_type = ?, position_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (person_id, start_date, selected_type, position_name, now, assignment_id),
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

    def _mark_filled_impl(
        self,
        assignment_id: int,
        *,
        actual_start_date: str | None = None,
        repair_missing_history: bool = False,
    ) -> StaffingTransitionResult:
        now = self.clock()
        with self.store.write_connection("mark_filled") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status != "coming" or assignment.person_id is None:
                raise ValueError("Invalid transition.")
            close_date = _valid_date(actual_start_date or assignment.start_date, "Actual start date")
            if repair_missing_history:
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
                    self._create_history(conn, assignment_id, close_date)
            self._require_active_history_count(conn, assignment_id, 1)
            history = conn.execute(
                """
                SELECT id, opened_date FROM assignment_history
                WHERE assignment_id = ? AND filled_date IS NULL AND closed_reason IS NULL
                """,
                (assignment_id,),
            ).fetchone()
            conn.execute(
                "UPDATE assignments SET status = 'filled', start_date = ?, current_filled_date = ?, updated_at = ? WHERE id = ?",
                (close_date, close_date, now, assignment_id),
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

    def _update_start_date_impl(
        self,
        assignment_id: int,
        *,
        start_date: str,
        today: date,
    ) -> StaffingTransitionResult:
        clean_start_date = _valid_date(start_date, "Start date")
        delayed = date.fromisoformat(clean_start_date) > today
        now = self.clock()
        with self.store.write_connection("update_start_date") as conn:
            assignment = self.store.assignment_context(conn, assignment_id)
            if assignment.status not in {"coming", "filled"} or assignment.person_id is None:
                raise ValueError("Start date can only be updated for Coming or Filled assignments.")
            if assignment.status == "coming":
                conn.execute(
                    "UPDATE assignments SET start_date = ?, updated_at = ? WHERE id = ?",
                    (clean_start_date, now, assignment_id),
                )
            else:
                self._require_active_history_count(conn, assignment_id, 0)
                history = conn.execute(
                    """
                    SELECT id, opened_date FROM assignment_history
                    WHERE assignment_id = ? AND closed_reason = 'filled'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (assignment_id,),
                ).fetchone()
                if history is None:
                    raise ValueError("Invalid assignment history state.")
                if delayed:
                    conn.execute(
                        """
                        UPDATE assignments
                        SET status = 'coming', start_date = ?, current_filled_date = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (clean_start_date, now, assignment_id),
                    )
                    conn.execute(
                        """
                        UPDATE assignment_history
                        SET filled_date = NULL, days_to_fill = NULL, closed_reason = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, int(history["id"])),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE assignments
                        SET start_date = ?, current_filled_date = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (clean_start_date, clean_start_date, now, assignment_id),
                    )
                    conn.execute(
                        """
                        UPDATE assignment_history
                        SET filled_date = ?, days_to_fill = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            clean_start_date,
                            _days_between(str(history["opened_date"]), clean_start_date),
                            now,
                            int(history["id"]),
                        ),
                    )
            updated = self.store.assignment_context(conn, assignment_id)
        return _result(updated)

    def _mark_replacing_impl(self, assignment_id: int, *, notice_given: str, final_working_day: str) -> StaffingTransitionResult:
        now = self.clock()
        notice_given = _valid_date(notice_given, "Notice given")
        final_working_day = _valid_date(final_working_day, "Final working day")
        target_status = "need_now" if date.fromisoformat(final_working_day) <= _parse_timestamp(now).date() else "replace"
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
                SET status = ?, person_id = CASE WHEN ? = 'need_now' THEN NULL ELSE person_id END,
                    current_opened_date = ?, current_filled_date = NULL,
                    start_date = CASE WHEN ? = 'need_now' THEN NULL ELSE start_date END,
                    updated_at = ?
                WHERE id = ?
                """,
                (target_status, target_status, now, target_status, now, assignment_id),
            )
            self._create_history(conn, assignment_id, now)
            updated = self.store.assignment_context(conn, assignment_id)
        self._emit_notice_event(
            replace(
                assignment,
                notice_given=notice_given,
                final_working_day=final_working_day,
                updated_at=now,
            )
        )
        self._emit_assignment_event("open_position" if updated.status == "need_now" else "mark_replacing", updated)
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
        if assignment.person_id is not None:
            self._emit_notice_event(assignment)
        self._emit_assignment_event("mark_not_needed", updated)
        return _result(updated)

    def _delete_position_impl(self, assignment_id: int, *, confirmed: bool = False) -> StaffingTransitionResult:
        if not confirmed:
            raise ValueError("Confirmation is required.")
        deleted = self.store.delete_assignment(int(assignment_id), now=self.clock())
        return StaffingTransitionResult(
            assignment_id=deleted.id,
            status="deleted",
            person_id=None,
            updated_at=deleted.updated_at,
        )

    def _update_permit_status_impl(
        self,
        person_id: int,
        permit_status: str,
        *,
        effective_date: str | None = None,
        units: float | int | None = None,
        documentation_received: bool = False,
        permit_document_path: str | None = None,
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
                    permit_documentation_received = ?,
                    permit_document_path = COALESCE(?, permit_document_path),
                    permit_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    permit_status,
                    effective_date_text,
                    units_value,
                    1 if documentation_received else 0,
                    permit_document_path,
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
                    person_id = CASE WHEN ? = 'need_now' THEN NULL ELSE person_id END,
                    start_date = CASE WHEN ? = 'need_now' THEN NULL ELSE COALESCE(?, start_date) END,
                    current_filled_date = CASE WHEN ? = 'need_now' THEN NULL ELSE current_filled_date END,
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
                    status,
                    status,
                    start_date,
                    status,
                    shift_start,
                    shift_end,
                    notes,
                    now,
                    assignment_id,
                ),
            )
            if status == "need_now":
                self._create_history_if_missing(conn, assignment_id, now)
            elif assignment.person_id is not None:
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
        if (
            status in {"need_now", "dont_need_now", "replace"}
            and status != assignment.status
            and assignment.person_id is not None
        ):
            self._emit_notice_event(assignment)
        status_action = {
            "need_now": "open_position",
            "dont_need_now": "mark_not_needed",
            "replace": "mark_replacing",
        }.get(status or "")
        if status_action and status != assignment.status:
            self._emit_assignment_event(status_action, updated)
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
                self._publish_change(
                    str(record.get("operation", "")),
                    record.get("payload", {}),
                )
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
        staged_payload = dict(payload)
        if "_base_snapshot" not in staged_payload:
            staged_payload["_base_snapshot"] = self._change_snapshot(staged_payload, assignment_id=assignment_id)
        if not self._replaying_pending_operations:
            self.flush_pending_operations()
        try:
            result = action()
        except StaffingEditLock:
            self.store.enqueue_pending_operation(operation, staged_payload)
            queued_assignment_id = assignment_id
            if queued_assignment_id is None:
                queued_assignment_id = int(payload.get("assignment_id", 0) or 0)
            return StaffingTransitionResult(
                assignment_id=queued_assignment_id,
                status="queued",
                person_id=None,
                updated_at=self.clock(),
            )
        self._publish_change(operation, staged_payload, assignment_id=result.assignment_id)
        return result

    def replay_staged_changes(self) -> int:
        if self.change_stage is None or self._replaying_staged_changes:
            return 0
        events = self.change_stage.pending_for(replica=self.replica, school=self.school_scope)
        applied = 0
        self._replaying_staged_changes = True
        try:
            for event in events:
                conflict = self._conflict_for_event(event)
                if conflict is not None:
                    if self.conflict_resolver is None:
                        break
                    if not bool(self.conflict_resolver(conflict)):
                        self.change_stage.acknowledge(event.id, replica=self.replica)
                        applied += 1
                        continue
                try:
                    self._apply_pending_operation({"operation": event.operation, "payload": event.payload})
                except StaffingEditLock:
                    break
                except (TypeError, ValueError, KeyError):
                    break
                self.change_stage.acknowledge(event.id, replica=self.replica)
                applied += 1
        finally:
            self._replaying_staged_changes = False
        return applied

    def _publish_change(
        self,
        operation: str,
        payload: Any,
        *,
        assignment_id: int | None = None,
    ) -> None:
        if self.change_stage is None or self._replaying_staged_changes:
            return
        if not isinstance(payload, dict):
            raise ValueError("Invalid staffing change payload.")
        school = self._school_for_change(payload, assignment_id=assignment_id)
        self.change_stage.publish(
            source_replica=self.publisher,
            source_database=self.replica,
            school=school,
            operation=operation,
            payload=payload,
        )

    def _school_for_change(self, payload: dict[str, Any], *, assignment_id: int | None) -> str:
        explicit_school = str(payload.get("school", "") or "").strip()
        if explicit_school:
            return explicit_school
        candidate_ids = [
            assignment_id,
            payload.get("assignment_id"),
            payload.get("target_assignment_id"),
            payload.get("source_assignment_id"),
        ]
        for candidate in candidate_ids:
            if candidate is None or int(candidate or 0) <= 0:
                continue
            return self.store.get_assignment(int(candidate)).school
        if self.school_scope:
            return self.school_scope
        person_id = int(payload.get("person_id", 0) or 0)
        if person_id > 0:
            schools = {row.school for row in self.store.list_assignments() if row.person_id == person_id}
            if len(schools) == 1:
                return schools.pop()
            return "*"
        raise ValueError("School is required to stage staffing change.")

    def _conflict_for_event(self, event: StaffingChangeEvent) -> StaffingChangeConflict | None:
        base = event.payload.get("_base_snapshot")
        if not isinstance(base, dict) or not base:
            return None
        local = self._change_snapshot(event.payload, assignment_id=None)
        if local == base:
            return None
        remote_payload = {key: value for key, value in event.payload.items() if not key.startswith("_")}
        return StaffingChangeConflict(
            event_id=event.id,
            source_replica=event.source_replica,
            school=event.school,
            operation=event.operation,
            base_snapshot=base,
            local_snapshot=local,
            remote_payload=remote_payload,
        )

    def _change_snapshot(self, payload: dict[str, Any], *, assignment_id: int | None) -> dict[str, Any]:
        assignment_ids: list[int] = []
        for value in (
            assignment_id,
            payload.get("assignment_id"),
            payload.get("source_assignment_id"),
            payload.get("target_assignment_id"),
        ):
            clean_id = int(value or 0)
            if clean_id > 0 and clean_id not in assignment_ids:
                assignment_ids.append(clean_id)
        snapshot: dict[str, Any] = {}
        classroom_id = int(payload.get("classroom_id", 0) or 0)
        classroom_school = str(payload.get("_classroom_lookup_school", "") or "").strip()
        classroom_name = str(payload.get("_classroom_lookup_name", "") or "").strip()
        if classroom_id > 0 or (classroom_school and classroom_name):
            classroom = next(
                (
                    item
                    for item in self.store.list_classrooms()
                    if (classroom_id > 0 and item.id == classroom_id)
                    or (
                        classroom_school
                        and classroom_name
                        and item.school.casefold() == classroom_school.casefold()
                        and item.name.casefold() == classroom_name.casefold()
                    )
                ),
                None,
            )
            snapshot[f"classroom:{classroom_school.casefold()}:{classroom_name.casefold()}"] = (
                asdict(classroom) if classroom is not None else None
            )
        for item_id in assignment_ids:
            try:
                assignment = asdict(self.store.get_assignment(item_id))
                assignment.pop("updated_at", None)
                snapshot[f"assignment:{item_id}"] = assignment
            except ValueError:
                snapshot[f"assignment:{item_id}"] = None
        person_name = str(
            payload.get("_person_lookup_name", "") or payload.get("person_name", "") or ""
        ).strip().casefold()
        person_id = int(payload.get("person_id", 0) or 0)
        if person_name or person_id > 0:
            matches = [
                item
                for item in self.store.list_people()
                if (person_name and item.name.strip().casefold() == person_name) or (not person_name and item.id == person_id)
            ]
            person = asdict(matches[0]) if len(matches) == 1 else None
            if person is not None:
                person.pop("updated_at", None)
            snapshot[f"person:{person_name or person_id}"] = person
        return snapshot

    def _apply_pending_operation(self, record: dict[str, Any]) -> None:
        operation = str(record.get("operation", ""))
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("Invalid pending staffing operation.")
        if operation == "add_person":
            self._add_person_impl(
                name=str(payload["name"]),
                role=str(payload["role"]),
                permit_status=str(payload.get("permit_status", "unknown")),
                units=payload.get("units"),
            )
            return
        if operation == "update_person":
            lookup_name = str(payload.get("_person_lookup_name") or payload["name"])
            person = next(
                (item for item in self.store.list_people() if item.name.strip().casefold() == lookup_name.casefold()),
                None,
            )
            if person is None:
                raise ValueError("Person not found.")
            self.update_person(
                person.id,
                name=str(payload["name"]),
                role=str(payload["role"]),
                permit_status=str(payload.get("permit_status", "unknown")),
                units=payload.get("units"),
            )
            return
        if operation == "deactivate_person":
            lookup_name = str(payload.get("_person_lookup_name") or "")
            person = next(
                (item for item in self.store.list_people() if item.name.strip().casefold() == lookup_name.casefold()),
                None,
            )
            if person is None:
                raise ValueError("Person not found.")
            self.deactivate_person(person.id)
            return
        if operation == "update_classroom":
            lookup_school = str(payload.get("_classroom_lookup_school") or payload["school"])
            lookup_name = str(payload.get("_classroom_lookup_name") or payload["name"])
            classroom = next(
                (
                    item
                    for item in self.store.list_classrooms()
                    if item.name.strip().casefold() == lookup_name.strip().casefold()
                ),
                None,
            )
            if classroom is None:
                raise ValueError("Classroom not found.")
            self.store.update_classroom(
                classroom_id=classroom.id,
                school=str(payload["school"]),
                name=str(payload["name"]),
                program=str(payload.get("program", "")),
                ratio_group=str(payload.get("ratio_group", "")),
                licensed_capacity=payload.get("licensed_capacity"),
                display_order=int(payload.get("display_order", 0)),
            )
            return
        if operation == "add_classroom":
            self.add_classroom(
                school=str(payload["school"]),
                name=str(payload["name"]),
                program=str(payload.get("program", "")),
                ratio_group=str(payload.get("ratio_group", "")),
                licensed_capacity=payload.get("licensed_capacity"),
            )
            return
        if operation == "deactivate_classroom":
            lookup_school = str(payload.get("_classroom_lookup_school") or payload["school"])
            lookup_name = str(payload.get("_classroom_lookup_name") or "")
            classroom = next(
                (
                    item
                    for item in self.store.list_classrooms()
                    if item.school.casefold() == lookup_school.casefold()
                    and item.name.casefold() == lookup_name.casefold()
                ),
                None,
            )
            if classroom is None:
                raise ValueError("Classroom not found.")
            self.deactivate_classroom(classroom.id)
            return
        if operation == "add_position":
            expected_id = int(payload["source_assignment_id"])
            with self.store.connect() as conn:
                next_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM assignments").fetchone()[0])
            if next_id != expected_id:
                raise ValueError("Staffing assignment IDs diverged; staged add requires reconciliation.")
            self._add_position_impl(
                school=str(payload["school"]),
                classroom=str(payload["classroom"]),
                classroom_program=str(payload.get("classroom_program", "")),
                licensed_capacity=payload.get("licensed_capacity"),
                position_name=str(payload["position_name"]),
                position_type=str(payload["position_type"]),
                initial_status=str(payload.get("initial_status", "dont_need_now")),
                person_name=str(payload.get("person_name", "")),
                permit_status=str(payload.get("permit_status", "unknown")),
                start_date=str(payload.get("start_date", "")),
                notes=str(payload.get("notes", "")),
            )
        elif operation == "open_position":
            self._open_position_impl(int(payload["assignment_id"]))
        elif operation == "mark_coming":
            self._mark_coming_impl(
                int(payload["assignment_id"]),
                person_name=str(payload["person_name"]),
                start_date=str(payload["start_date"]),
                position_type=None if payload.get("position_type") is None else str(payload["position_type"]),
            )
        elif operation == "revert_coming":
            self._revert_coming_impl(int(payload["assignment_id"]))
        elif operation == "mark_filled":
            self._mark_filled_impl(
                int(payload["assignment_id"]),
                actual_start_date=(
                    None if payload.get("actual_start_date") is None else str(payload["actual_start_date"])
                ),
                repair_missing_history=bool(payload.get("repair_missing_history", False)),
            )
        elif operation == "update_start_date":
            self._update_start_date_impl(
                int(payload["assignment_id"]),
                start_date=str(payload["start_date"]),
                today=date.fromisoformat(str(payload["today"])),
            )
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
        elif operation == "delete_position":
            self._delete_position_impl(int(payload["assignment_id"]), confirmed=bool(payload.get("confirmed", False)))
        elif operation == "update_permit_status":
            target_person_id = int(payload["person_id"])
            person_name = str(payload.get("person_name", "")).strip().casefold()
            if person_name:
                matches = [item for item in self.store.list_people() if item.name.strip().casefold() == person_name]
                if len(matches) != 1:
                    raise ValueError("Staged permit person could not be matched uniquely.")
                target_person_id = matches[0].id
            self._update_permit_status_impl(
                target_person_id,
                str(payload["permit_status"]),
                effective_date=str(payload["effective_date"]) if payload.get("effective_date") is not None else None,
                units=payload.get("units"),
                documentation_received=bool(payload.get("documentation_received", False)),
                permit_document_path=(
                    None
                    if payload.get("permit_document_path") is None
                    else str(payload["permit_document_path"])
                ),
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
                classroom_program=(
                    None if payload.get("classroom_program") is None else str(payload["classroom_program"])
                ),
                position_name=None if payload.get("position_name") is None else str(payload["position_name"]),
                position_type=None if payload.get("position_type") is None else str(payload["position_type"]),
                status=None if payload.get("status") is None else str(payload["status"]),
                person_name=None if payload.get("person_name") is None else str(payload["person_name"]),
                start_date=None if payload.get("start_date") is None else str(payload["start_date"]),
                shift_start=str(payload.get("shift_start", "")),
                shift_end=str(payload.get("shift_end", "")),
                permit_status=str(permit_status) if permit_status is not None else None,
                notes=None if payload.get("notes") is None else str(payload["notes"]),
            )
        elif operation == "record_director_interview":
            history_id = str(payload["history_id"])
            referrals = self.store.list_director_candidate_referrals(include_completed=True)
            referral = next((item for item in referrals if item.history_id == history_id), None)
            if referral is None and payload.get("candidate_name"):
                referral = self.upsert_director_candidate_referral(
                    history_id=history_id,
                    candidate_name=str(payload["candidate_name"]),
                    school=str(payload["school"]),
                    position=str(payload.get("position", "")),
                    interviewer_rating=payload.get("interviewer_rating"),
                    interviewer_outcome=str(payload["interviewer_outcome"]),
                    interview_date=str(payload.get("interview_date", "")),
                    candidate_email=str(payload.get("candidate_email", "")),
                    candidate_phone=str(payload.get("candidate_phone", "")),
                    referral_date=str(payload.get("referral_date", "")),
                )
            if referral is None:
                raise ValueError("Director referral required before staged interview completion.")
            self.record_director_interview(
                referral.id,
                director_name=str(payload["director_name"]),
                completed_date=str(payload["completed_date"]),
                rating=payload["rating"],
                decision=str(payload["decision"]),
                decision_notes=str(payload["decision_notes"]),
                proposed_shift_start=str(payload.get("proposed_shift_start", "")),
                proposed_shift_end=str(payload.get("proposed_shift_end", "")),
                proposed_classroom=str(payload.get("proposed_classroom", "")),
                follow_up_needed=bool(payload.get("follow_up_needed", False)),
                candidate_email=str(payload.get("candidate_email", "")),
                candidate_phone=str(payload.get("candidate_phone", "")),
            )
        elif operation == "director_candidate_referral":
            self.upsert_director_candidate_referral(
                history_id=str(payload["history_id"]),
                candidate_name=str(payload["candidate_name"]),
                school=str(payload["school"]),
                position=str(payload.get("position", "")),
                interviewer_rating=payload.get("interviewer_rating"),
                interviewer_outcome=str(payload["interviewer_outcome"]),
                interview_date=str(payload.get("interview_date", "")),
                candidate_email=str(payload.get("candidate_email", "")),
                candidate_phone=str(payload.get("candidate_phone", "")),
                referral_date=str(payload.get("referral_date", "")),
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
                selected_type = str(payload.get("position_type") or row.position_type)
                position_name = row.position_name
                old_prefix = f"{row.position_type} "
                if selected_type != row.position_type and position_name == row.position_type:
                    position_name = selected_type
                elif selected_type != row.position_type and position_name.startswith(old_prefix):
                    position_name = f"{selected_type} {position_name[len(old_prefix):]}"
                projected[assignment_id] = replace(
                    row,
                    status="coming",
                    person_name=str(payload.get("person_name", "")),
                    start_date=str(payload.get("start_date", "")),
                    position_type=selected_type,
                    position_name=position_name,
                )
            elif operation == "revert_coming":
                projected[assignment_id] = replace(row, status="need_now", person_name="", start_date="")
            elif operation == "mark_filled":
                projected[assignment_id] = replace(
                    row,
                    status="filled",
                    start_date=str(payload.get("actual_start_date") or row.start_date),
                )
            elif operation == "update_start_date":
                selected_start_date = str(payload.get("start_date") or row.start_date)
                selected_today = date.fromisoformat(str(payload.get("today")))
                projected[assignment_id] = replace(
                    row,
                    status=(
                        "coming"
                        if row.status == "filled" and date.fromisoformat(selected_start_date) > selected_today
                        else row.status
                    ),
                    start_date=selected_start_date,
                )
            elif operation == "mark_replacing":
                projected[assignment_id] = replace(row, status="replace")
            elif operation == "clear_replacement":
                projected[assignment_id] = replace(row, status="need_now", person_name="", start_date="")
            elif operation == "mark_not_needed":
                projected[assignment_id] = replace(row, status="dont_need_now", person_name="", start_date="")
            elif operation == "delete_position":
                projected.pop(assignment_id, None)
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
        self._emit(event_type, staffing_notification_payload(assignment, self._notification_person(assignment)), key)

    def _emit_notice_event(self, assignment: StaffingAssignment) -> None:
        if assignment.person_id is None or not assignment.person_name:
            return
        payload = staffing_notification_payload(assignment, self._notification_person(assignment))
        key = f"staffing:{assignment.id}:notice:{assignment.person_id}:{payload.get('notice_given', '')}"
        self._emit("employment.notice.given", payload, key)

    def _emit_person_event(self, person_id: int, assignment: StaffingAssignment) -> None:
        event_type = STAFFING_NOTIFICATION_EVENTS["update_permit_status"]
        key = f"staffing:person:{person_id}:{event_type}:{assignment.updated_at}"
        self._emit(event_type, staffing_notification_payload(assignment, self._notification_person(assignment)), key)

    def _notification_person(self, assignment: StaffingAssignment) -> StaffingPerson | None:
        if not assignment.person_id:
            return None
        try:
            with self.store.connect() as conn:
                return self.store.person_context(conn, assignment.person_id)
        except (ValueError, StaffingEditLock):
            return None

    def _emit(self, event_type: str, payload: dict[str, str], idempotency_key: str) -> None:
        if self.notification_service is None:
            return
        try:
            self.notification_service.emit_event(event_type, payload, idempotency_key)
        except Exception:
            return


def staffing_notification_payload(
    assignment: StaffingAssignment,
    person: StaffingPerson | None = None,
) -> dict[str, str]:
    payload = {
        "school": assignment.school,
        "classroom": assignment.classroom,
        "program": assignment.classroom_program,
        "position_name": assignment.position_name,
        "position": assignment.position_name,
        "position_type": assignment.position_type,
        "position_title": assignment.position_name,
        "slot_group": assignment.slot_group,
        "assignment_status": assignment.status,
        "person_name": assignment.person_name,
        "candidate_name": assignment.person_name,
        "start_date": assignment.start_date,
        "shift_start": assignment.shift_start,
        "shift_end": assignment.shift_end,
        "notice_given": assignment.notice_given,
        "notice_date": assignment.notice_given,
        "date_notice_given": assignment.notice_given,
        "final_working_day": assignment.final_working_day,
        "final_day": assignment.final_working_day,
        "last_working_day": assignment.final_working_day,
        "permit_status": assignment.permit_status,
        "classroom_capacity": str(assignment.classroom_capacity or ""),
        "ratio_group": assignment.ratio_group,
        "notes": assignment.notes,
    }
    if person is not None:
        units = "" if person.units is None else f"{person.units:g}"
        payload.update(
            {
                "person_name": person.name or payload["person_name"],
                "candidate_name": person.name or payload["candidate_name"],
                "permit_status": person.permit_status or payload["permit_status"],
                "permit_effective_date": person.permit_effective_date,
                "permit_documentation_received": "Yes" if person.permit_documentation_received else "No",
                "permit_notes": person.permit_notes,
                "notice_given": person.notice_given or payload["notice_given"],
                "notice_date": person.notice_given or payload["notice_date"],
                "date_notice_given": person.notice_given or payload["date_notice_given"],
                "final_working_day": person.final_working_day or payload["final_working_day"],
                "final_day": person.final_working_day or payload["final_day"],
                "last_working_day": person.final_working_day or payload["last_working_day"],
                "ece_units": units,
                "ece_units_completed": units,
            }
        )
    return {key: str(value) for key, value in payload.items()}


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


def _optional_rating(value: float | int | str | None, label: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return _required_rating(value, label)


def _required_rating(value: float | int | str, label: str) -> float:
    try:
        rating = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number from 1 to 10.") from exc
    if rating < 1 or rating > 10:
        raise ValueError(f"{label} must be from 1 to 10.")
    return rating


def _valid_shift_time(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required for hire decisions.")
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = datetime.strptime(text.upper(), fmt)
        except ValueError:
            continue
        return parsed.strftime("%I:%M %p").lstrip("0")
    raise ValueError(f"{label} must be a time like 8:00 AM.")


def _days_between(start: str, end: str) -> int:
    return max(0, (_parse_timestamp(end).date() - _parse_timestamp(start).date()).days)


def _parse_timestamp(value: str) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
