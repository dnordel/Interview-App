from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from onboarding_models import parse_iso8601_datetime

DEFAULT_EXPECTED_INTERVAL_HOURS = 24


@dataclass(slots=True)
class ReminderHealthStatus:
    severity: str
    message: str
    hours_or_days_late: int | None


def evaluate_onboarding_reminder_health(
    last_reminder_run_at: Any,
    scheduler_or_settings_payload: dict[str, Any] | None,
    now: datetime | str | None = None,
) -> ReminderHealthStatus:
    expected_interval_hours = _expected_interval_hours(scheduler_or_settings_payload)
    current_time = _coerce_now(now)
    last_run = _coerce_datetime(last_reminder_run_at)

    if not last_run:
        return ReminderHealthStatus(
            severity="warning",
            message="Reminder scheduler has never run (or timestamp is invalid).",
            hours_or_days_late=None,
        )

    elapsed_hours = max(0, int((current_time - last_run).total_seconds() // 3600))
    if elapsed_hours > expected_interval_hours:
        late_hours = elapsed_hours - expected_interval_hours
        amount, unit = _format_lateness(late_hours)
        return ReminderHealthStatus(
            severity="overdue",
            message=f"Reminder scheduler is overdue by {amount} {unit}.",
            hours_or_days_late=amount,
        )

    return ReminderHealthStatus(
        severity="healthy",
        message="Reminder scheduler is healthy and running on schedule.",
        hours_or_days_late=0,
    )


def _expected_interval_hours(payload: dict[str, Any] | None) -> int:
    scheduler_settings = _extract_scheduler_settings(payload)
    if "expected_interval_hours" in scheduler_settings:
        return _coerce_positive_int(scheduler_settings.get("expected_interval_hours"), DEFAULT_EXPECTED_INTERVAL_HOURS)

    if "run_interval_minutes" in scheduler_settings:
        minutes = _coerce_positive_int(scheduler_settings.get("run_interval_minutes"), DEFAULT_EXPECTED_INTERVAL_HOURS * 60)
        return max(1, minutes // 60)

    return DEFAULT_EXPECTED_INTERVAL_HOURS


def _extract_scheduler_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    nested = payload.get("scheduler_settings")
    if isinstance(nested, dict):
        return nested
    return payload


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_now(now: datetime | str | None) -> datetime:
    parsed = _coerce_datetime(now)
    if parsed:
        return parsed
    return _normalize_datetime(datetime.now(timezone.utc))


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)

    parsed = parse_iso8601_datetime(value)
    if not parsed:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _format_lateness(late_hours: int) -> tuple[int, str]:
    if late_hours >= 48:
        return late_hours // 24, "days"
    return late_hours, "hours"


__all__ = ["evaluate_onboarding_reminder_health"]
