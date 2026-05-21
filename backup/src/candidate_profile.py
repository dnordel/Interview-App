from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANONICAL_DEGREE_TYPES: tuple[str, ...] = (
    "AA",
    "AS",
    "BA",
    "BS",
    "MA",
    "MS",
    "MBA",
    "PhD",
    "EdD",
)


@dataclass
class CandidateQualification:
    has_degree: bool | None = None
    degree_type: str = ""
    degree_in_ece: bool = False
    ece_units_completed: int | None = None
    infant_toddler_class_completed: bool = False
    total_units_completed: int | None = None
    years_experience: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_degree": self.has_degree,
            "degree_type": self.degree_type,
            "degree_in_ece": self.degree_in_ece,
            "ece_units_completed": self.ece_units_completed,
            "infant_toddler_class_completed": self.infant_toddler_class_completed,
            "total_units_completed": self.total_units_completed,
            "years_experience": self.years_experience,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CandidateQualification":
        has_degree_raw = payload.get("has_degree", None)
        has_degree = has_degree_raw if isinstance(has_degree_raw, bool) else None
        degree_type = normalize_degree_type(payload.get("degree_type", ""))
        degree_in_ece = bool(payload.get("degree_in_ece", False))
        ece_units_completed = coerce_non_negative_int(payload.get("ece_units_completed"))
        infant_toddler_class_completed = bool(payload.get("infant_toddler_class_completed", False))
        total_units_completed = coerce_non_negative_int(payload.get("total_units_completed"))
        years_experience = coerce_non_negative_int(payload.get("years_experience"))
        return cls(
            has_degree=has_degree,
            degree_type=degree_type,
            degree_in_ece=degree_in_ece,
            ece_units_completed=ece_units_completed,
            infant_toddler_class_completed=infant_toddler_class_completed,
            total_units_completed=total_units_completed,
            years_experience=years_experience,
        )


def normalize_degree_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in CANONICAL_DEGREE_TYPES:
        return text
    return ""


def coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def validate_candidate_qualification(
    has_degree_raw: str,
    degree_type_raw: str,
    degree_in_ece: bool,
    ece_units_raw: str,
    total_units_raw: str,
    infant_toddler_class_completed: bool,
    years_experience_raw: str,
) -> tuple[bool, str, CandidateQualification]:
    has_degree = parse_yes_no(has_degree_raw)
    if has_degree is None:
        return False, "Please confirm whether the candidate has a degree.", CandidateQualification()

    ece_units_completed = coerce_non_negative_int(ece_units_raw)
    if not degree_in_ece and ece_units_completed is None:
        return False, "ECE units completed is required and must be a non-negative whole number unless the degree is in ECE.", CandidateQualification()

    degree_type = ""
    total_units_completed: int | None = None

    if has_degree:
        degree_type = normalize_degree_type(degree_type_raw)
        if not degree_type:
            allowed = ", ".join(CANONICAL_DEGREE_TYPES)
            return False, f"Degree type is required and must be one of: {allowed}.", CandidateQualification()
    else:
        total_units_completed = coerce_non_negative_int(total_units_raw)
        if total_units_completed is None:
            return False, "Total units completed is required when no degree is reported.", CandidateQualification()

    years_experience = coerce_non_negative_int(years_experience_raw)
    if years_experience is None:
        return False, "Years of experience is required and must be a non-negative whole number.", CandidateQualification()

    qualification = CandidateQualification(
        has_degree=has_degree,
        degree_type=degree_type,
        degree_in_ece=degree_in_ece,
        ece_units_completed=ece_units_completed,
        infant_toddler_class_completed=infant_toddler_class_completed,
        total_units_completed=total_units_completed,
        years_experience=years_experience,
    )
    return True, "", qualification


def parse_yes_no(value: str) -> bool | None:
    text = str(value or "").strip().lower()
    if text == "yes":
        return True
    if text == "no":
        return False
    return None
