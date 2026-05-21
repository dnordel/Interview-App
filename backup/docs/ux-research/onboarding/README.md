# Onboarding UX Research Artifacts

This folder contains onboarding-domain overlays and task artifacts for moderated usability testing of the onboarding workflow.

## Local Index
- Canonical docs index: [`/docs/README.md`](../../README.md)
- Shared template layer: [`/docs/ux-research/shared/`](../shared/)

### Ownership and Update Expectations
- Primary owner: maintainers responsible for onboarding UX research operations.
- Update this README when files are added, removed, renamed, or repurposed.
- Keep onboarding-study documents aligned with privacy and telemetry conventions documented in the canonical docs hub.

## Inheritance Map (Shared vs Domain-Specific)

### Inherited Common Sections
- Findings record core fields are inherited from [`shared/findings-record-template.md`](../shared/findings-record-template.md).
- Privacy/data-handling baseline checklist is inherited from [`shared/privacy-data-handling-checklist.md`](../shared/privacy-data-handling-checklist.md).
- Session metrics definitions and formulas are inherited from [`shared/session-metrics-definitions.md`](../shared/session-metrics-definitions.md).

### Onboarding-Specific Overlays
- `findings-template.md`: onboarding-only task mapping and failure-mode details.
- `privacy-guardrails.md`: onboarding-only data-source constraints and telemetry scoping.

### Onboarding Domain Artifacts (Not Shared Overlays)
- `test-tasks.md`
- `moderator-script-and-rubric.md`
- `prioritized-backlog-format.md`
- `telemetry-proposal.md`

## Security Considerations (Review Before Any Session)
1. Use synthetic employee names, emails, and onboarding records only.
2. Do not connect reminder tests to production SMTP or live HRIS systems.
3. Redact accidental PII immediately from notes and recordings.
4. Restrict access to notes, recordings, and exports to approved research contributors.
5. Log and time-box retention for raw notes; keep only anonymized summaries long-term.
6. Ensure telemetry payloads exclude names, email addresses, phone numbers, and free-text notes.


## Traceability Workflow
1. Capture each finding in `findings-template.md` with full linked IDs (`ONB-F###`, `ONB-D###`, `ONB-I###`, `ONB-B###`).
2. For each validated finding, convert it into one row using the required schema in `prioritized-backlog-format.md`.
3. Populate every converted row with: Finding ID, reproducible steps reference, proposed change, module-level code location(s), owner, and implementation status.
4. Attach verification artifacts for each change: test notes, telemetry dashboard/query, and PR link.
5. Run a review checkpoint (date + reviewer) before marking work as shipped or closed.
6. Mark items closed only when closure criteria are met against defined onboarding success and guardrail metrics.

## Docs Workflow Checkpoint (Stale Todo Triage)
- Run a lightweight backlog/doc review checkpoint during normal grooming cadence (recommended: weekly).
- Triage stale `Todo` items into one of three outcomes:
  1. Move to active planning in `docs/backlog/UI_UX_BACKLOG.md` with owner + target release.
  2. Keep as `Todo` with refreshed rationale and next review date.
  3. Close as no longer relevant with a brief reason.
- Do not duplicate execution-level task planning in summary-only docs.

## Change Rule
- **Rule:** Update common sections in shared templates first.
- Then sync or adjust onboarding overlays to reflect domain-specific additions only.
- Do not duplicate shared baseline text into onboarding overlays except brief references.

## Contents
- `test-tasks.md`
- `moderator-script-and-rubric.md`
- `findings-template.md` (overlay; legacy filename retained)
- `prioritized-backlog-format.md`
- `privacy-guardrails.md` (overlay; legacy filename retained)
- `telemetry-proposal.md`
