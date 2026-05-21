from __future__ import annotations

from typing import Any

from onboarding_models import ReminderRunSummary


SCHEDULER_RESULT_VALUES = {"success", "failed", "dry_run", "no_due_items"}


def build_scheduler_status(summary: ReminderRunSummary, scheduler_enabled: bool, run_source: str = "manual") -> dict[str, Any]:
    status: dict[str, Any] = {
        "enabled": bool(scheduler_enabled),
        "last_scheduler_run_at": summary.ran_at,
        "last_scheduler_result": _derive_result(summary),
    }
    last_error = _derive_last_error(summary)
    if last_error:
        status["last_error"] = last_error
    status["last_run_source"] = "scheduler" if run_source == "scheduler" else "manual"
    return status


def normalize_run_source(value: str | None) -> str:
    if value == "scheduler":
        return "scheduler"
    return "manual"


def _derive_result(summary: ReminderRunSummary) -> str:
    due_count = summary.counts.get("due_reminders", 0)
    if summary.dry_run:
        return "dry_run"
    if due_count == 0:
        return "no_due_items"

    has_outcomes = bool(summary.outcomes)
    has_failure = any(not outcome.success for outcome in summary.outcomes)
    has_success = any(outcome.success for outcome in summary.outcomes)

    if has_failure:
        return "failed"
    if has_outcomes and has_success:
        return "success"
    return "failed"


def _derive_last_error(summary: ReminderRunSummary) -> str:
    for outcome in summary.outcomes:
        message = outcome.error.strip()
        if message:
            return message
    return ""
