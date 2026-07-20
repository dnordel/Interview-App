import pytest

from onboarding_staffing_bridge import StaffingDirectorResolver
from staffing_store import StaffingStore
from onboarding_service import OnboardingAccess, OnboardingService
from onboarding_store import OnboardingStore


def test_current_director_resolves_unique_active_filled_staffing_position(tmp_path):
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    store.seed_assignment(
        school="Palmdale",
        classroom="Office",
        position_name="Director",
        position_type="Director",
        status="filled",
        person_name="Current Director",
    )
    resolver = StaffingDirectorResolver(store)

    identity = resolver("Palmdale")

    assert identity.name == "Current Director"
    assert identity.person_id
    with pytest.raises(ValueError, match="not assigned"):
        resolver("Hawthorne")


def test_duplicate_active_directors_fail_instead_of_guessing(tmp_path):
    store = StaffingStore(tmp_path / "staffing.sqlite3")
    store.initialize()
    for name in ("Director One", "Director Two"):
        store.seed_assignment(
            school="Palmdale",
            classroom="Office",
            position_name="Director",
            position_type="Director",
            status="filled",
            person_name=name,
        )

    with pytest.raises(ValueError, match="multiple"):
        StaffingDirectorResolver(store)("Palmdale")


def test_service_uses_current_director_dynamically_for_departure_snapshot(tmp_path):
    staffing = StaffingStore(tmp_path / "staffing.sqlite3")
    staffing.initialize()
    staffing.seed_assignment(
        school="Palmdale",
        classroom="Office",
        position_name="Director",
        position_type="Director",
        status="filled",
        person_name="Current Director",
    )
    service = OnboardingService(
        OnboardingStore(tmp_path / "onboarding.sqlite3"),
        OnboardingAccess(role="admin", actor="owner"),
        director_resolver=StaffingDirectorResolver(staffing),
    )
    employee = service.create_employee(
        legal_name="Jordan Lee",
        school="Palmdale",
        role="Teacher",
        acceptance_date="2020-01-01",
        start_date="2020-01-15",
    )

    ended = service.mark_employment_ended(
        employee.id,
        last_working_day="2026-07-19",
        departure_category="voluntary_resignation",
    )

    assert ended.departure_director_name == "Current Director"
