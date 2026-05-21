from onboarding_reminder_health import (
    DEFAULT_EXPECTED_INTERVAL_HOURS,
    evaluate_onboarding_reminder_health,
)


def test_missing_last_reminder_run_at_reports_never_run_with_no_lateness():
    status = evaluate_onboarding_reminder_health(
        None,
        {"scheduler_settings": {"expected_interval_hours": 6}},
        now="2026-01-02T00:00:00Z",
    )

    assert status.severity == "warning"
    assert status.message == "Reminder scheduler has never run (or timestamp is invalid)."
    assert status.hours_or_days_late is None


def test_invalid_timestamp_is_treated_as_never_run():
    status = evaluate_onboarding_reminder_health(
        "not-a-timestamp",
        {"scheduler_settings": {"expected_interval_hours": 6}},
        now="2026-01-02T00:00:00Z",
    )

    assert status.severity == "warning"
    assert status.message == "Reminder scheduler has never run (or timestamp is invalid)."
    assert status.hours_or_days_late is None


def test_exactly_on_expected_boundary_is_healthy():
    status = evaluate_onboarding_reminder_health(
        "2026-01-01T00:00:00Z",
        {"scheduler_settings": {"expected_interval_hours": 24}},
        now="2026-01-02T00:00:00Z",
    )

    assert status.severity == "healthy"
    assert status.message == "Reminder scheduler is healthy and running on schedule."
    assert status.hours_or_days_late == 0


def test_just_over_expected_boundary_is_overdue_with_computed_lateness():
    status = evaluate_onboarding_reminder_health(
        "2026-01-01T00:00:00Z",
        {"scheduler_settings": {"expected_interval_hours": 24}},
        now="2026-01-02T01:00:00Z",
    )

    assert status.severity == "overdue"
    assert status.message == "Reminder scheduler is overdue by 1 hours."
    assert status.hours_or_days_late == 1


def test_missing_expected_interval_uses_default_interval():
    status = evaluate_onboarding_reminder_health(
        "2026-01-01T00:00:00Z",
        {"scheduler_settings": {}},
        now="2026-01-02T01:00:00Z",
    )

    assert DEFAULT_EXPECTED_INTERVAL_HOURS == 24
    assert status.severity == "overdue"
    assert status.hours_or_days_late == 1


def test_non_numeric_or_negative_expected_interval_is_sanitized_to_default():
    now = "2026-01-02T01:00:00Z"
    last_run = "2026-01-01T00:00:00Z"

    invalid_status = evaluate_onboarding_reminder_health(
        last_run,
        {"scheduler_settings": {"expected_interval_hours": "abc"}},
        now=now,
    )
    negative_status = evaluate_onboarding_reminder_health(
        last_run,
        {"scheduler_settings": {"expected_interval_hours": -12}},
        now=now,
    )

    assert invalid_status.severity == "overdue"
    assert invalid_status.hours_or_days_late == 1
    assert negative_status.severity == "overdue"
    assert negative_status.hours_or_days_late == 1


def test_timezone_aware_timestamp_is_handled_consistently():
    aware_status = evaluate_onboarding_reminder_health(
        "2026-01-01T10:00:00+02:00",
        {"scheduler_settings": {"expected_interval_hours": 24}},
        now="2026-01-02T08:00:00Z",
    )
    utc_status = evaluate_onboarding_reminder_health(
        "2026-01-01T08:00:00Z",
        {"scheduler_settings": {"expected_interval_hours": 24}},
        now="2026-01-02T08:00:00Z",
    )

    assert aware_status.severity == "healthy"
    assert aware_status.hours_or_days_late == 0
    assert aware_status.severity == utc_status.severity
    assert aware_status.hours_or_days_late == utc_status.hours_or_days_late
