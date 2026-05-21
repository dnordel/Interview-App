from __future__ import annotations

from datetime import date

from onboarding_models import parse_date

SPECIFIC_DATE_REFERENCE_PREFIX = "date:"


def build_specific_date_reference(value: date) -> str:
    return f"{SPECIFIC_DATE_REFERENCE_PREFIX}{value.isoformat()}"


def parse_specific_date_reference(reference: str) -> date | None:
    normalized = str(reference or "").strip()
    if not normalized.startswith(SPECIFIC_DATE_REFERENCE_PREFIX):
        return None

    date_part = normalized.removeprefix(SPECIFIC_DATE_REFERENCE_PREFIX).strip()
    if not date_part:
        return None

    try:
        return parse_date(date_part)
    except ValueError:
        return None

