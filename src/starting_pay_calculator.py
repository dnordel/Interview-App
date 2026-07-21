from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any


SUPPORTED_POSITION_IDS = frozenset({"lead_teacher", "teacher", "teacher_floater"})
POSITION_LABELS = {
    "lead_teacher": "Lead Teacher",
    "teacher": "Teacher",
    "teacher_floater": "Teacher/Floater",
}
CAREER_LATTICE_LEVELS = frozenset({3, 4, 5, 6, 7})
DEFAULT_STARTING_PAY_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "config" / "starting_pay.json"


class DegreeLevel(str, Enum):
    NONE = "none"
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTORATE = "doctorate"


@dataclass(frozen=True)
class QualificationInput:
    position_id: str
    highest_degree: DegreeLevel
    associate_degree_in_ece_cd: bool
    ece_cd_units: Decimal | None
    total_college_units: Decimal | None
    completed_experience_years: int


@dataclass(frozen=True)
class PayLevel:
    permit_level: str
    base_hourly_rate: Decimal


@dataclass(frozen=True)
class StartingPaySettings:
    calculation_version: str
    experience_increase_rate: Decimal
    rounding_increment: Decimal
    starting_pay_cap: Decimal
    pay_levels: dict[int, PayLevel]


@dataclass(frozen=True)
class PayCalculationResult:
    status: str
    position_id: str
    career_lattice_level: int | None
    permit_level: str | None
    highest_degree: DegreeLevel
    ece_cd_units: Decimal | None
    total_college_units: Decimal | None
    base_hourly_rate: Decimal | None
    completed_experience_years: int
    experience_increase_rate: Decimal
    experience_adjustment: Decimal | None
    starting_hourly_pay: Decimal | None
    qualification_explanation: str
    manual_review_required: bool
    calculation_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "position_id": self.position_id,
            "career_lattice_level": self.career_lattice_level,
            "permit_level": self.permit_level,
            "highest_degree": self.highest_degree.value,
            "ece_cd_units": _decimal_text(self.ece_cd_units),
            "total_college_units": _decimal_text(self.total_college_units),
            "base_hourly_rate": _decimal_text(self.base_hourly_rate),
            "completed_experience_years": self.completed_experience_years,
            "experience_increase_rate": _decimal_text(self.experience_increase_rate),
            "experience_adjustment": _decimal_text(self.experience_adjustment),
            "starting_hourly_pay": _decimal_text(self.starting_hourly_pay),
            "qualification_explanation": self.qualification_explanation,
            "manual_review_required": self.manual_review_required,
            "calculation_version": self.calculation_version,
        }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def validate_qualification_input(data: QualificationInput) -> None:
    if not isinstance(data.position_id, str) or not data.position_id.strip():
        raise ValueError("Position ID is required.")
    if not isinstance(data.highest_degree, DegreeLevel):
        raise ValueError("Highest degree is invalid.")
    if not isinstance(data.associate_degree_in_ece_cd, bool):
        raise ValueError("Associate degree in ECE/CD must be true or false.")
    ece_units = data.ece_cd_units
    ece_optional = (
        data.highest_degree in {DegreeLevel.BACHELOR, DegreeLevel.MASTER, DegreeLevel.DOCTORATE}
        or (
            data.highest_degree is DegreeLevel.ASSOCIATE
            and data.associate_degree_in_ece_cd
        )
    )
    if ece_units is None:
        if not ece_optional:
            raise ValueError("ECE/CD units are required unless a qualifying ECE/CD degree is reported.")
    elif not isinstance(ece_units, Decimal) or not ece_units.is_finite() or ece_units < 0:
        raise ValueError("ECE/CD units must be a nonnegative decimal.")
    elif ece_units.as_tuple().exponent < -2:
        raise ValueError("ECE/CD units cannot have more than two decimal places.")
    total_units = data.total_college_units
    if total_units is None:
        if data.highest_degree is DegreeLevel.NONE:
            raise ValueError("Total college units are required when no degree is reported.")
    elif not isinstance(total_units, Decimal) or not total_units.is_finite() or total_units < 0:
        raise ValueError("Total college units must be a nonnegative decimal.")
    elif total_units.as_tuple().exponent < -2:
        raise ValueError("Total college units cannot have more than two decimal places.")
    if total_units is not None and ece_units is not None and ece_units > total_units:
        raise ValueError("ECE/CD units cannot exceed total college units.")
    years = data.completed_experience_years
    if isinstance(years, bool) or not isinstance(years, int) or years < 0:
        raise ValueError("Experience years must be a nonnegative whole number.")


def qualification_input_from_mapping(
    position_id: str,
    qualification: dict[str, Any],
) -> QualificationInput:
    degree_types = {
        "AA": DegreeLevel.ASSOCIATE,
        "AS": DegreeLevel.ASSOCIATE,
        "BA": DegreeLevel.BACHELOR,
        "BS": DegreeLevel.BACHELOR,
        "MA": DegreeLevel.MASTER,
        "MS": DegreeLevel.MASTER,
        "MBA": DegreeLevel.MASTER,
        "PHD": DegreeLevel.DOCTORATE,
        "EDD": DegreeLevel.DOCTORATE,
    }
    has_degree = qualification.get("has_degree")
    if not isinstance(has_degree, bool):
        raise ValueError("Degree completion must be confirmed.")
    degree_code = str(qualification.get("degree_type") or "").strip().upper()
    if has_degree:
        highest_degree = degree_types.get(degree_code)
        if highest_degree is None:
            raise ValueError("Degree type is required and must be supported.")
    else:
        if degree_code:
            raise ValueError("Degree type cannot be set when no degree is reported.")
        highest_degree = DegreeLevel.NONE
    ece_units_raw = qualification.get("ece_units_completed")
    total_units_raw = qualification.get("total_units_completed")
    try:
        ece_units = (
            None
            if ece_units_raw is None or not str(ece_units_raw).strip()
            else Decimal(str(ece_units_raw).strip())
        )
        total_units = (
            None
            if total_units_raw is None or not str(total_units_raw).strip()
            else Decimal(str(total_units_raw).strip())
        )
    except ArithmeticError as exc:
        raise ValueError("ECE/CD and total college units must be valid decimals.") from exc
    years_raw = qualification.get("years_experience")
    if isinstance(years_raw, str) and years_raw.isdigit():
        years: Any = int(years_raw)
    else:
        years = years_raw
    result = QualificationInput(
        position_id=position_id,
        highest_degree=highest_degree,
        associate_degree_in_ece_cd=(
            highest_degree is DegreeLevel.ASSOCIATE
            and qualification.get("degree_in_ece") is True
        ),
        ece_cd_units=ece_units,
        total_college_units=total_units,
        completed_experience_years=years,
    )
    validate_qualification_input(result)
    return result


def default_starting_pay_settings() -> StartingPaySettings:
    return StartingPaySettings(
        calculation_version="2026.1",
        experience_increase_rate=Decimal("0.02"),
        rounding_increment=Decimal("0.25"),
        starting_pay_cap=Decimal("24.00"),
        pay_levels={
            3: PayLevel("Aide", Decimal("16.50")),
            4: PayLevel("Associate Teacher", Decimal("17.50")),
            5: PayLevel("Teacher", Decimal("18.00")),
            6: PayLevel("Master Teacher", Decimal("19.00")),
            7: PayLevel("Site Supervisor", Decimal("20.00")),
        },
    )


def load_starting_pay_settings(
    path: Path = DEFAULT_STARTING_PAY_SETTINGS_PATH,
) -> StartingPaySettings:
    return StartingPaySettingsStore(path).load()


class StartingPaySettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> StartingPaySettings:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        levels = {
            int(level): PayLevel(
                permit_level=str(values["permit_level"]),
                base_hourly_rate=Decimal(str(values["base_hourly_rate"])),
            )
            for level, values in payload["pay_levels"].items()
        }
        settings = StartingPaySettings(
            calculation_version=str(payload["calculation_version"]),
            experience_increase_rate=Decimal(str(payload["experience_increase_rate"])),
            rounding_increment=Decimal(str(payload["rounding_increment"])),
            starting_pay_cap=Decimal(str(payload["starting_pay_cap"])),
            pay_levels=levels,
        )
        validate_starting_pay_settings(settings)
        return settings

    def save(self, settings: StartingPaySettings) -> None:
        validate_starting_pay_settings(settings)
        if self.path.exists():
            current = self.load()
            if current != settings and current.calculation_version == settings.calculation_version:
                raise ValueError("A new calculation version is required when pay policy changes.")
        payload: dict[str, Any] = {
            "calculation_version": settings.calculation_version,
            "experience_increase_rate": str(settings.experience_increase_rate),
            "rounding_increment": str(settings.rounding_increment),
            "starting_pay_cap": str(settings.starting_pay_cap),
            "pay_levels": {
                str(level): {
                    "permit_level": values.permit_level,
                    "base_hourly_rate": str(values.base_hourly_rate),
                }
                for level, values in sorted(settings.pay_levels.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()


def validate_starting_pay_settings(settings: StartingPaySettings) -> None:
    version = settings.calculation_version
    if not isinstance(version, str) or not version.strip() or len(version) > 32:
        raise ValueError("Calculation version is required and cannot exceed 32 characters.")
    for label, value, allow_zero in (
        ("Experience increase rate", settings.experience_increase_rate, True),
        ("Rounding increment", settings.rounding_increment, False),
        ("Starting pay cap", settings.starting_pay_cap, False),
    ):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{label} must be a finite decimal.")
        if value < 0 or (not allow_zero and value == 0):
            raise ValueError(f"{label} must be {'nonnegative' if allow_zero else 'positive'}.")
    if set(settings.pay_levels) != CAREER_LATTICE_LEVELS:
        raise ValueError("Starting-pay settings must define Career Lattice Levels 3 through 7.")
    for level, pay_level in settings.pay_levels.items():
        if not isinstance(pay_level, PayLevel) or not pay_level.permit_level.strip():
            raise ValueError(f"Career Lattice Level {level} requires a permit/pay label.")
        base_rate = pay_level.base_hourly_rate
        if not isinstance(base_rate, Decimal) or not base_rate.is_finite() or base_rate <= 0:
            raise ValueError(f"Career Lattice Level {level} requires a positive base rate.")
        if base_rate > settings.starting_pay_cap:
            raise ValueError(f"Career Lattice Level {level} base rate cannot exceed starting pay cap.")


def determine_highest_level(data: QualificationInput) -> int | None:
    if data.highest_degree in {
        DegreeLevel.BACHELOR,
        DegreeLevel.MASTER,
        DegreeLevel.DOCTORATE,
    }:
        return 7
    has_associate_degree = data.highest_degree is DegreeLevel.ASSOCIATE
    ece_units = data.ece_cd_units or Decimal("0")
    if has_associate_degree and data.associate_degree_in_ece_cd:
        return 6
    if has_associate_degree and ece_units >= Decimal("24"):
        return 6
    if (data.total_college_units or Decimal("0")) >= Decimal("60") and ece_units >= Decimal("24"):
        return 6
    if ece_units >= Decimal("24") and (data.total_college_units or Decimal("0")) >= Decimal("40"):
        return 5
    if ece_units >= Decimal("12"):
        return 4
    if ece_units >= Decimal("6"):
        return 3
    return None


def calculate_offer_pay(
    data: QualificationInput,
    settings: StartingPaySettings,
) -> PayCalculationResult:
    validate_qualification_input(data)
    validate_starting_pay_settings(settings)
    if data.position_id not in SUPPORTED_POSITION_IDS:
        return PayCalculationResult(
            status="unsupported_position",
            position_id=data.position_id,
            career_lattice_level=None,
            permit_level=None,
            highest_degree=data.highest_degree,
            ece_cd_units=data.ece_cd_units,
            total_college_units=data.total_college_units,
            base_hourly_rate=None,
            completed_experience_years=data.completed_experience_years,
            experience_increase_rate=settings.experience_increase_rate,
            experience_adjustment=None,
            starting_hourly_pay=None,
            qualification_explanation="Position is not supported by the starting-pay calculator.",
            manual_review_required=False,
            calculation_version=settings.calculation_version,
        )
    level = determine_highest_level(data)
    if level is None:
        return PayCalculationResult(
            status="ineligible",
            position_id=data.position_id,
            career_lattice_level=None,
            permit_level=None,
            highest_degree=data.highest_degree,
            ece_cd_units=data.ece_cd_units,
            total_college_units=data.total_college_units,
            base_hourly_rate=None,
            completed_experience_years=data.completed_experience_years,
            experience_increase_rate=settings.experience_increase_rate,
            experience_adjustment=None,
            starting_hourly_pay=None,
            qualification_explanation="Fewer than 6 verified ECE/CD units.",
            manual_review_required=False,
            calculation_version=settings.calculation_version,
        )
    pay_level = settings.pay_levels[level]
    unrounded = pay_level.base_hourly_rate * (
        Decimal("1")
        + Decimal(data.completed_experience_years) * settings.experience_increase_rate
    )
    rounded = (
        unrounded / settings.rounding_increment
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * settings.rounding_increment
    starting_pay = min(rounded, settings.starting_pay_cap)
    return PayCalculationResult(
        status="calculated",
        position_id=data.position_id,
        career_lattice_level=level,
        permit_level=pay_level.permit_level,
        highest_degree=data.highest_degree,
        ece_cd_units=data.ece_cd_units,
        total_college_units=data.total_college_units,
        base_hourly_rate=pay_level.base_hourly_rate,
        completed_experience_years=data.completed_experience_years,
        experience_increase_rate=settings.experience_increase_rate,
        experience_adjustment=starting_pay - pay_level.base_hourly_rate,
        starting_hourly_pay=starting_pay,
        qualification_explanation=f"Qualified for Career Lattice Level {level}.",
        manual_review_required=False,
        calculation_version=settings.calculation_version,
    )
