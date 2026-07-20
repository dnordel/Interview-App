from __future__ import annotations

from dataclasses import dataclass

from staffing_store import StaffingStore


@dataclass(frozen=True)
class DirectorIdentity:
    person_id: str
    name: str
    school: str


class StaffingDirectorResolver:
    """Resolve current Director from one unique active filled Staffing position."""

    def __init__(self, store: StaffingStore) -> None:
        self.store = store

    def __call__(self, school: str) -> DirectorIdentity:
        clean_school = str(school or "").strip()
        if not clean_school:
            raise ValueError("School is required for Director resolution.")
        matches = [
            assignment
            for assignment in self.store.list_assignments()
            if assignment.school.casefold() == clean_school.casefold()
            and assignment.status == "filled"
            and assignment.person_id is not None
            and assignment.person_name.strip()
            and (
                assignment.position_type.casefold() == "director"
                or assignment.position_name.casefold() == "director"
            )
        ]
        if not matches:
            raise ValueError(f"Current Director is not assigned for {clean_school}.")
        if len(matches) > 1:
            raise ValueError(f"Staffing has multiple active Directors for {clean_school}.")
        assignment = matches[0]
        return DirectorIdentity(
            person_id=str(assignment.person_id),
            name=assignment.person_name.strip(),
            school=assignment.school,
        )
