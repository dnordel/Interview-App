from __future__ import annotations

from dataclasses import dataclass


ASSIGNMENT_STATUSES = ("dont_need_now", "need_now", "coming", "filled", "replace")
PERMIT_STATUSES = (
    "unknown",
    "no_permit_or_application",
    "permit_in_process",
    "teacher_permit_approved",
    "no_units_needed",
)


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
    permit_status: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingPerson:
    id: int
    name: str
    permit_status: str
    updated_at: str = ""


@dataclass(frozen=True)
class StaffingTransitionResult:
    assignment_id: int
    status: str
    person_id: int | None = None
    updated_at: str = ""
