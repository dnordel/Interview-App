from dataclasses import replace
from decimal import Decimal

from starting_pay_calculator import (
    DegreeLevel,
    PayLevel,
    QualificationInput,
    StartingPaySettingsStore,
    calculate_offer_pay,
    default_starting_pay_settings,
    load_starting_pay_settings,
    qualification_input_from_mapping,
)
import pytest


def test_level_four_experience_pay_rounds_to_nearest_quarter() -> None:
    result = calculate_offer_pay(
        QualificationInput(
            position_id="teacher",
            highest_degree=DegreeLevel.NONE,
            associate_degree_in_ece_cd=False,
            ece_cd_units=Decimal("12"),
            total_college_units=Decimal("12"),
            completed_experience_years=7,
        ),
        default_starting_pay_settings(),
    )

    assert result.status == "calculated"
    assert result.career_lattice_level == 4
    assert result.permit_level == "Associate Teacher"
    assert result.starting_hourly_pay == Decimal("20.00")


@pytest.mark.parametrize(
    ("degree", "associate_in_ece", "ece_units", "total_units", "expected_level"),
    [
        (DegreeLevel.NONE, False, "6", "6", 3),
        (DegreeLevel.NONE, False, "24", "40", 5),
        (DegreeLevel.NONE, False, "24", "60", 6),
        (DegreeLevel.ASSOCIATE, True, "24", "60", 6),
        (DegreeLevel.BACHELOR, False, "0", "120", 7),
        (DegreeLevel.MASTER, False, "0", "120", 7),
        (DegreeLevel.DOCTORATE, False, "0", "120", 7),
    ],
)
def test_calculator_selects_highest_qualifying_level(
    degree: DegreeLevel,
    associate_in_ece: bool,
    ece_units: str,
    total_units: str,
    expected_level: int,
) -> None:
    result = calculate_offer_pay(
        QualificationInput(
            position_id="teacher",
            highest_degree=degree,
            associate_degree_in_ece_cd=associate_in_ece,
            ece_cd_units=Decimal(ece_units),
            total_college_units=Decimal(total_units),
            completed_experience_years=0,
        ),
        default_starting_pay_settings(),
    )

    assert result.career_lattice_level == expected_level


def test_calculated_result_reports_capped_auditable_pay_components() -> None:
    result = calculate_offer_pay(
        QualificationInput(
            position_id="teacher_floater",
            highest_degree=DegreeLevel.BACHELOR,
            associate_degree_in_ece_cd=False,
            ece_cd_units=Decimal("0"),
            total_college_units=Decimal("120"),
            completed_experience_years=10,
        ),
        default_starting_pay_settings(),
    )

    assert result.base_hourly_rate == Decimal("20.00")
    assert result.completed_experience_years == 10
    assert result.experience_increase_rate == Decimal("0.02")
    assert result.experience_adjustment == Decimal("4.00")
    assert result.starting_hourly_pay == Decimal("24.00")
    assert result.calculation_version == "2026.1"


@pytest.mark.parametrize(
    "changes",
    [
        {"ece_cd_units": Decimal("-0.01")},
        {"total_college_units": Decimal("-0.01")},
        {"ece_cd_units": Decimal("13"), "total_college_units": Decimal("12")},
        {"completed_experience_years": 7.5},
        {"completed_experience_years": True},
        {"position_id": ""},
    ],
)
def test_calculator_rejects_invalid_qualification_input(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "position_id": "teacher",
        "highest_degree": DegreeLevel.NONE,
        "associate_degree_in_ece_cd": False,
        "ece_cd_units": Decimal("12"),
        "total_college_units": Decimal("12"),
        "completed_experience_years": 0,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        calculate_offer_pay(QualificationInput(**values), default_starting_pay_settings())  # type: ignore[arg-type]


@pytest.mark.parametrize("position_id", ["director", "executive_director", "assistant_director", "teacher_helper"])
def test_calculator_rejects_unsupported_position_ids_without_substring_matching(position_id: str) -> None:
    result = calculate_offer_pay(
        QualificationInput(
            position_id=position_id,
            highest_degree=DegreeLevel.BACHELOR,
            associate_degree_in_ece_cd=False,
            ece_cd_units=Decimal("0"),
            total_college_units=Decimal("120"),
            completed_experience_years=0,
        ),
        default_starting_pay_settings(),
    )

    assert result.status == "unsupported_position"
    assert result.starting_hourly_pay is None
    assert result.manual_review_required is False


def test_starting_pay_settings_round_trip_preserves_decimal_policy(tmp_path) -> None:
    path = tmp_path / "starting_pay.json"
    store = StartingPaySettingsStore(path)
    expected = default_starting_pay_settings()

    store.save(expected)

    assert store.load() == expected
    assert '"starting_pay_cap": "24.00"' in path.read_text(encoding="utf-8")


def test_settings_policy_change_requires_new_calculation_version(tmp_path) -> None:
    store = StartingPaySettingsStore(tmp_path / "starting_pay.json")
    original = default_starting_pay_settings()
    store.save(original)

    with pytest.raises(ValueError, match="calculation version"):
        store.save(replace(original, starting_pay_cap=Decimal("25.00")))

    changed = replace(
        original,
        calculation_version="2026.2",
        starting_pay_cap=Decimal("25.00"),
    )
    store.save(changed)
    assert store.load() == changed


@pytest.mark.parametrize(
    "settings",
    [
        replace(default_starting_pay_settings(), calculation_version=""),
        replace(default_starting_pay_settings(), experience_increase_rate=Decimal("-0.01")),
        replace(default_starting_pay_settings(), rounding_increment=Decimal("0")),
        replace(default_starting_pay_settings(), starting_pay_cap=Decimal("0")),
        replace(default_starting_pay_settings(), pay_levels={3: PayLevel("Aide", Decimal("16.50"))}),
        replace(
            default_starting_pay_settings(),
            pay_levels={
                **default_starting_pay_settings().pay_levels,
                7: PayLevel("Site Supervisor", Decimal("24.25")),
            },
        ),
    ],
)
def test_settings_store_rejects_invalid_policy(settings, tmp_path) -> None:
    with pytest.raises(ValueError):
        StartingPaySettingsStore(tmp_path / "starting_pay.json").save(settings)


def test_repository_starting_pay_config_loads_as_valid_policy() -> None:
    assert load_starting_pay_settings() == default_starting_pay_settings()


@pytest.mark.parametrize(
    ("degree_type", "expected_degree", "expected_associate_in_ece"),
    [
        ("", DegreeLevel.NONE, False),
        ("AA", DegreeLevel.ASSOCIATE, True),
        ("AS", DegreeLevel.ASSOCIATE, True),
        ("BA", DegreeLevel.BACHELOR, False),
        ("BS", DegreeLevel.BACHELOR, False),
        ("MA", DegreeLevel.MASTER, False),
        ("MBA", DegreeLevel.MASTER, False),
        ("PhD", DegreeLevel.DOCTORATE, False),
        ("EdD", DegreeLevel.DOCTORATE, False),
    ],
)
def test_saved_qualification_mapping_uses_degree_abbreviations(
    degree_type: str,
    expected_degree: DegreeLevel,
    expected_associate_in_ece: bool,
) -> None:
    result = qualification_input_from_mapping(
        "teacher",
        {
            "has_degree": bool(degree_type),
            "degree_type": degree_type,
            "degree_in_ece": True,
            "ece_units_completed": "24.50",
            "total_units_completed": "120",
            "years_experience": 4,
        },
    )

    assert result.highest_degree is expected_degree
    assert result.associate_degree_in_ece_cd is expected_associate_in_ece
    assert result.ece_cd_units == Decimal("24.50")


def test_saved_associate_degree_does_not_require_reentered_total_units() -> None:
    result = qualification_input_from_mapping(
        "teacher",
        {
            "has_degree": True,
            "degree_type": "AA",
            "degree_in_ece": True,
            "ece_units_completed": None,
            "total_units_completed": None,
            "years_experience": 0,
        },
    )

    assert result.highest_degree is DegreeLevel.ASSOCIATE
    assert result.total_college_units is None
    assert calculate_offer_pay(result, default_starting_pay_settings()).career_lattice_level == 6
