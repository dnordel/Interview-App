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
    permit_notes: str = ""
    notice_given: str = ""
    final_working_day: str = ""
    assignment_school: str = ""
    assignment_classroom: str = ""
    assignment_position: str = ""
    current_assignment: str = ""
    updated_at: str = ""


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
