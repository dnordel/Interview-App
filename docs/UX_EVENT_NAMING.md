# UX Telemetry Canonical Specification (Interview + Onboarding)

This file is the **single source of truth** for telemetry naming and payload schema restrictions.
All UX telemetry docs must follow this specification.

## 1) Authoritative Event Naming Pattern

Use this canonical event format:

- `ux.<app>.<surface>.<event_kind>`
- `app`: `interview` or `onboarding`
- `surface`: stable snake_case UI area (for example: `session_start`, `competency_scoring`, `add_employee_form`, `reminder_run`)
- `event_kind`: `view`, `click`, `validation_error`, or `completion`

### Allowed Exceptions

Exceptions are allowed only when needed for compatibility:

1. `ux.<app>.session.start` may be emitted in addition to canonical names during migration from legacy analytics dashboards.
2. Existing dashboards may temporarily read legacy names, but all new instrumentation MUST emit canonical names.
3. Any additional exception requires explicit documentation in a migration note and removal target date.

### Canonical Examples

- `ux.interview.session_start.click`
- `ux.interview.competency_scoring.validation_error`
- `ux.interview.finalize.completion`
- `ux.onboarding.add_employee_form.view`
- `ux.onboarding.reminder_run.completion`

## 2) Payload Schema Requirements and PII Guardrails

### Required Common Fields (all events)

- `event_version` (integer)
- `app` (`interview` | `onboarding`)
- `surface` (snake_case string)
- `event_kind` (`view` | `click` | `validation_error` | `completion`)
- `timestamp_ms` (integer epoch milliseconds)

### Required Fields by Event Kind

- `view`: `target`
- `click`: `target`
- `validation_error`: `error_type`
- `completion`: `outcome`

### Common Optional Fields

- `target`, `surface_step`, `field_name`, `error_count`, `mode`, `source`, `stage`, `kpi`, `count`, `input_method`, `duration_ms`

### Prohibited Fields (PII and Sensitive Data)

Never include:

- Direct identifiers: `name`, `full_name`, `email`, `phone`, `address`, `resume_path`, `employee_id`, `candidate_id`.
- Free text or long-form user input: `notes`, `free_text`, `comment`, `*_notes`, `raw_input`.
- Secrets or auth material: `password`, `token`, `api_key`, `session_cookie`.

### Security Considerations Checklist

- Use synthetic/test identities only in research and staging telemetry.
- Prefer enums, booleans, and bounded integers over arbitrary strings.
- Cap string field length and whitelist allowed values where possible.
- Validate payload keys against an allowlist before emit.
- Reject or redact prohibited fields at instrumentation boundaries.

## 3) Accessibility Gate

Where keyboard activation is supported, emit:

- `completion` with `outcome="keyboard_only_success"` and `input_method="keyboard"`

This enables keyboard-only success KPI tracking per surface.

## 4) Migration Note (Legacy to Canonical)

Maintainers should migrate legacy names to canonical names as follows:

| Legacy Event Name | Canonical Event Name |
|---|---|
| `interview_start_clicked` | `ux.interview.session_start.click` |
| `interview_session_started` | `ux.interview.session_start.completion` |
| `rubric_guidance_viewed` | `ux.interview.rubric_guidance.view` |
| `competency_scored` | `ux.interview.competency_scoring.completion` |
| `competency_score_error` | `ux.interview.competency_scoring.validation_error` |
| `draft_finalize_clicked` | `ux.interview.finalize.click` |
| `draft_finalized` | `ux.interview.finalize.completion` |
| `draft_finalize_error` | `ux.interview.finalize.validation_error` |
| `onboarding_add_employee_opened` | `ux.onboarding.add_employee_form.view` |
| `onboarding_employee_save_error` | `ux.onboarding.add_employee_form.validation_error` |
| `onboarding_urgent_filter_applied` | `ux.onboarding.urgent_filter.click` |
| `onboarding_reminder_mode_selected` | `ux.onboarding.reminder_mode.click` |
| `onboarding_reminder_dry_run_completed` | `ux.onboarding.reminder_run.completion` |
| `onboarding_reminder_live_run_completed` | `ux.onboarding.reminder_run.completion` |
| `onboarding_sender_email_validation_error` | `ux.onboarding.sender_email.validation_error` |
| `onboarding_sender_email_updated` | `ux.onboarding.sender_email.completion` |
