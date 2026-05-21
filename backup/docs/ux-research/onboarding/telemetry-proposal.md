# Telemetry Proposal: Onboarding Friction to `ux_metrics`

Canonical spec reference: [`docs/UX_EVENT_NAMING.md`](../../UX_EVENT_NAMING.md).

This proposal links observed usability friction points to measurable `ux_metrics` events.

## Event Principles

- Emit at decision points and error states across onboarding/reminder workflows.
- Prefer categorical and timing fields over free text.
- Exclude PII from payloads and follow canonical payload restrictions.

## Friction-to-Event Mapping

| Observed friction point | Canonical event (`ux_metrics`) | Trigger | Properties |
| --- | --- | --- | --- |
| Cannot find add employee action | `ux.onboarding.add_employee_form.view` | User opens create form | `entry_point`, `time_from_screen_open_ms` |
| Repeated validation errors when saving employee | `ux.onboarding.add_employee_form.validation_error` | Save blocked by validation | `error_type`, `required_fields_missing_count` |
| Urgent filter not discovered quickly | `ux.onboarding.urgent_filter.click` | Urgent filter toggled | `time_to_filter_ms`, `result_count` |
| Uncertainty about dry-run safety | `ux.onboarding.reminder_mode.click` | Dry-run/live mode set | `mode`, `time_to_mode_select_ms`, `changed_from_default` |
| Dry-run results hard to interpret | `ux.onboarding.reminder_run.completion` | Dry-run completes | `mode`, `recipient_count`, `skipped_count`, `warning_count` |
| Live send fails or partially succeeds | `ux.onboarding.reminder_run.completion` | Live run completes | `mode`, `sent_count`, `failed_count`, `blocked_count` |
| Invalid sender email blocks progress | `ux.onboarding.sender_email.validation_error` | Email setting rejected | `error_reason`, `attempt_count` |
| Recovery after fixing sender email | `ux.onboarding.sender_email.completion` | Valid sender saved | `attempts_before_success`, `domain_type` |

## Minimal Analysis Queries

- Median time to complete each task from first relevant event to success event.
- Error rate per task (`*.validation_error` events per session).
- Recovery rate after validation error (error event followed by successful completion in same session).
- Dry-run misuse rate (live run invoked without prior dry-run in early onboarding cohorts).

## Suggested Implementation Touchpoints

- `src/ux_metrics.py` for event definitions.
- `src/onboarding_app.pyw` for add employee interactions.
- `src/onboarding_task_filters.py` for urgent filter events.
- `src/onboarding_reminder_runner.py` and `src/onboarding_notifier.py` for dry/live run events.
- `src/onboarding_send_guardrails.py` and `src/email_security.py` for validation and recovery events.

## Migration Note (Legacy Names)

| Legacy Name | Canonical Name |
| --- | --- |
| `onboarding_add_employee_opened` | `ux.onboarding.add_employee_form.view` |
| `onboarding_employee_save_error` | `ux.onboarding.add_employee_form.validation_error` |
| `onboarding_urgent_filter_applied` | `ux.onboarding.urgent_filter.click` |
| `onboarding_reminder_mode_selected` | `ux.onboarding.reminder_mode.click` |
| `onboarding_reminder_dry_run_completed` | `ux.onboarding.reminder_run.completion` |
| `onboarding_reminder_live_run_completed` | `ux.onboarding.reminder_run.completion` |
| `onboarding_sender_email_validation_error` | `ux.onboarding.sender_email.validation_error` |
| `onboarding_sender_email_updated` | `ux.onboarding.sender_email.completion` |

## Implementation Notes for Analytics Handoff

All onboarding instrumentation should emit canonical names only:

- `ux.onboarding.add_employee_form.view`
- `ux.onboarding.add_employee_form.validation_error`
- `ux.onboarding.urgent_filter.click`
- `ux.onboarding.reminder_mode.click`
- `ux.onboarding.reminder_run.completion`
- `ux.onboarding.sender_email.validation_error`
- `ux.onboarding.sender_email.completion`

During migration, legacy names may still be ingested but should be translated in the telemetry logger to canonical names before persistence.

### Example Queries

```sql
-- completion time to find urgent filter in ms
SELECT
  percentile_cont(0.5) WITHIN GROUP (ORDER BY time_to_filter_ms) AS p50_time_to_filter_ms
FROM ux_events
WHERE event_type = 'ux.onboarding.urgent_filter.click';
```

```sql
-- add employee validation error rate
WITH opened AS (
  SELECT session_id, COUNT(*) AS opened_count
  FROM ux_events
  WHERE event_type = 'ux.onboarding.add_employee_form.view'
  GROUP BY session_id
),
errors AS (
  SELECT session_id, COUNT(*) AS error_count
  FROM ux_events
  WHERE event_type = 'ux.onboarding.add_employee_form.validation_error'
  GROUP BY session_id
)
SELECT
  SUM(COALESCE(errors.error_count, 0))::float / NULLIF(SUM(opened.opened_count), 0) AS validation_error_rate
FROM opened
LEFT JOIN errors USING (session_id);
```

```sql
-- sender email recovery rate (had validation error, then completion)
WITH error_sessions AS (
  SELECT DISTINCT session_id
  FROM ux_events
  WHERE event_type = 'ux.onboarding.sender_email.validation_error'
),
recovered_sessions AS (
  SELECT DISTINCT session_id
  FROM ux_events
  WHERE event_type = 'ux.onboarding.sender_email.completion'
)
SELECT
  COUNT(*) FILTER (WHERE recovered_sessions.session_id IS NOT NULL)::float / NULLIF(COUNT(*), 0) AS recovery_rate
FROM error_sessions
LEFT JOIN recovered_sessions USING (session_id);
```

```sql
-- dry-run misuse rate: live completion without prior dry-run mode click in session
WITH live_runs AS (
  SELECT session_id, MIN(timestamp_ms) AS live_ts
  FROM ux_events
  WHERE event_type = 'ux.onboarding.reminder_run.completion' AND mode = 'live'
  GROUP BY session_id
),
dry_run_clicks AS (
  SELECT session_id, MIN(timestamp_ms) AS dry_ts
  FROM ux_events
  WHERE event_type = 'ux.onboarding.reminder_mode.click' AND mode = 'dry_run'
  GROUP BY session_id
)
SELECT
  COUNT(*) FILTER (WHERE dry_run_clicks.dry_ts IS NULL OR dry_run_clicks.dry_ts > live_runs.live_ts)::float
    / NULLIF(COUNT(*), 0) AS dry_run_misuse_rate
FROM live_runs
LEFT JOIN dry_run_clicks USING (session_id);
```
