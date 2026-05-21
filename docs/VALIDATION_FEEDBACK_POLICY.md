# Shared Validation Feedback Policy

Owner: Engineering + UX
Audience: Engineers, maintainers, UX reviewers
Last Reviewed: 2026-03-07
Next Review: 2026-07-07
Status: Active
Canonical Parent: [Documentation Hub](./README.md)

## Purpose

Define a shared validation model across interview and onboarding flows so users get consistent, actionable guidance with minimal interruption.

## Audience

- Engineers implementing validation behavior.
- UX reviewers and QA contributors validating interaction patterns.

## Guidance

### Severity model

- `info`: contextual, non-blocking guidance; no action required.
- `warning`: recoverable issue that allows progress after adjustment.
- `error`: recoverable validation failure that blocks current action until corrected.
- `blocking`: hard failure (system/runtime) or irreversible action confirmation that requires modal intervention.

### Display rules

1. Prefer inline feedback adjacent to the relevant field or section for `info`, `warning`, and `error` states.
2. Use modal dialogs only for `blocking` failures and irreversible confirmations.
3. Always provide two-part guidance:
   - issue statement (what happened)
   - next step (what to do now)
4. Move keyboard focus to the first actionable control for recoverable issues.

### Error message style

- Keep tone calm, specific, and action oriented.
- Use short declarative language (for example, “Sender email is invalid.”).
- Provide one explicit corrective action (for example, “Enter a valid sender email address, then save again.”).

### Privacy and technical detail handling

- Never show stack traces, file paths, exception class names, or raw technical payloads in inline validation messages.
- Persist full technical detail to logs for debugging.
- User-facing failure copy should be sanitized and operational (what to retry and where to escalate).

## Review Checklist

- [ ] Recoverable validation appears inline near the affected control/section.
- [ ] Modal dialogs are limited to blocking failures or irreversible actions.
- [ ] Validation copy follows issue + next-step structure.
- [ ] User-facing errors redact technical internals.
- [ ] Debug logs retain sufficient diagnostics for support.

## Related Documentation

- [Documentation Hub](./README.md)
- [Repository README](../README.md)
- [UI/UX Recommendations and Task Stubs](./UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md)
- [manual_qa_ux_b005.md](./manual_qa_ux_b005.md)
