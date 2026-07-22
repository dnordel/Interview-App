from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


ASSIGNMENT_STATUSES = ("dont_need_now", "need_now", "offer_pending", "coming", "filled", "replace")
PERMIT_STATUSES = (
    "unknown",
    "no_permit_or_application",
    "permit_in_process",
    "teacher_permit_approved",
    "no_units_needed",
)
DIRECTOR_REFERRAL_OUTCOMES = ("hire", "borderline")
DIRECTOR_INTERVIEW_DECISIONS = ("hire", "no_hire")
TEACHER_OFFER_POSITION_IDS = ("lead_teacher", "teacher", "teacher_floater")
TEACHER_OFFER_POSITION_LABELS = {
    "lead_teacher": "Lead Teacher",
    "teacher": "Teacher",
    "teacher_floater": "Teacher/Floater",
}


@dataclass(frozen=True)
class StaffingAssignment:
    id: int
    school: str
    classroom: str
    position_name: str
    position_type: str
    status: str
    person_id: int | None = None
    person_name: str = ""
    start_date: str = ""
    shift_start: str = ""
    shift_end: str = ""
    notice_given: str = ""
    final_working_day: str = ""
    permit_status: str = ""
    updated_at: str = ""
    current_opened_date: str = ""
    current_filled_date: str = ""
    classroom_capacity: int | None = None
    classroom_program: str = ""
    ratio_group: str = ""
    slot_group: str = ""
    notes: str = ""
    display_order: int = 0
    offer_history_id: str = ""


@dataclass(frozen=True)
class StaffingPerson:
    id: int
    name: str
    permit_status: str
    role: str = ""
    active: bool = True
    permit_effective_date: str = ""
    units: float | None = None
    permit_documentation_received: bool = False
    permit_document_path: str = ""
    permit_notes: str = ""
    notice_given: str = ""
    final_working_day: str = ""
    assignment_school: str = ""
    assignment_classroom: str = ""
    assignment_position: str = ""
    current_assignment: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingClassroom:
    id: int
    school: str
    name: str
    program: str = ""
    ratio_group: str = ""
    licensed_capacity: int | None = None
    active: bool = True
    display_order: int = 0


@dataclass(frozen=True)
class StaffingHistoryRecord:
    id: int
    assignment_id: int
    school: str
    classroom: str
    position_name: str
    opened_date: str
    filled_date: str = ""
    days_to_fill: int | None = None
    cycle_status: str = ""
    employee: str = ""
    data_integrity: str = ""
    closed_reason: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingTransitionResult:
    assignment_id: int
    status: str
    person_id: int | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingMetricRow:
    assignment_id: int
    school: str
    classroom: str
    position_name: str
    position_type: str
    status: str
    person_name: str = ""
    permit_status: str = ""
    start_date: str = ""
    shift_start: str = ""
    shift_end: str = ""
    days_open: int | None = None
    classroom_capacity: int | None = None
    classroom_program: str = ""
    ratio_group: str = ""
    slot_group: str = ""
    notes: str = ""
    display_order: int = 0


@dataclass(frozen=True)
class StaffingMetrics:
    open_count: int
    avg_days_to_fill: float
    open_over_7_days: int
    rows: list[StaffingMetricRow]


@dataclass(frozen=True)
class StaffingDirectorCandidate:
    id: int
    history_id: str
    candidate_name: str
    school: str
    position: str
    interviewer_rating: float | None
    interviewer_outcome: str
    interview_date: str
    candidate_email: str = ""
    candidate_phone: str = ""
    referral_date: str = ""
    director_interview_completed_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingDirectorInterview:
    id: int
    referral_id: int
    candidate_name: str
    school: str
    position: str
    interviewer_rating: float | None
    interviewer_outcome: str
    director_name: str
    completed_date: str
    rating: float
    decision: str
    decision_notes: str
    history_id: str = ""
    proposed_shift_start: str = ""
    proposed_shift_end: str = ""
    proposed_classroom: str = ""
    offer_position_id: str = ""
    follow_up_needed: bool = False
    owner_approval_status: str = "pending_owner_approval"
    state: str = "finalized"
    row_version: int = 1
    version_number: int = 1
    reopen_reason: str = ""
    interviewer_rating_at_completion: float | None = None
    interviewer_outcome_at_completion: str = ""
    initial_report_amended: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingDirectorInterviewDifference:
    field_name: str
    saved_value: object
    current_value: object
    local_value: object


@dataclass(frozen=True)
class StaffingDirectorReferralRemovalAudit:
    id: int
    history_id: str
    candidate_name: str
    school: str
    removed_by: str
    removal_source: str
    removed_at: str = ""


def director_interview_position_options(
    rows: Sequence[StaffingAssignment | StaffingMetricRow],
    school: str,
) -> tuple[tuple[str, str], ...]:
    school_key = str(school or "").strip().casefold()
    labels: set[str] = set()
    for row in rows:
        if row.school.strip().casefold() != school_key:
            continue
        slot_group = row.slot_group.strip().casefold()
        position_type = row.position_type.strip()
        if slot_group == "support" or position_type.casefold() == "support":
            label = row.position_name.strip() or row.classroom.strip()
        elif position_type.casefold() not in {"teacher", "lead teacher", "lead"}:
            label = position_type
        else:
            label = ""
        if label:
            labels.add(label)
    options = list(TEACHER_OFFER_POSITION_LABELS.items())
    for label in sorted(labels, key=str.casefold):
        position_id = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if position_id and position_id not in TEACHER_OFFER_POSITION_IDS:
            options.append((position_id, label))
    return tuple(options)


def director_interview_classroom_options(
    rows: Sequence[StaffingAssignment | StaffingMetricRow],
    school: str,
) -> tuple[str, ...]:
    school_key = str(school or "").strip().casefold()
    teacher_types = {"teacher", "lead teacher", "lead"}
    return tuple(
        sorted(
            {
                row.classroom
                for row in rows
                if row.school.strip().casefold() == school_key
                and row.classroom
                and (
                    row.slot_group.strip().casefold() == "teacher"
                    or row.position_type.strip().casefold() in teacher_types
                )
            }
        )
    )
