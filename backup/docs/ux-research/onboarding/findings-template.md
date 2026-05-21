# Onboarding Findings Overlay (Legacy Filename Preserved)

> **Deprecation notice:** This file is no longer a standalone template. It is a domain overlay on top of the shared findings template to preserve existing links.

## Inherited from Shared Template

Use all common sections and fields from:

- [`docs/ux-research/shared/findings-record-template.md`](../shared/findings-record-template.md)

## Onboarding-Specific Additions

- **Finding ID prefix:** `ONB-F###`
- **Traceability IDs:**
  - Decision ID: `ONB-D###`
  - Linked issue ID: `ONB-I###`
  - Linked backlog ID: `ONB-B###`
- **Implementation tracking:**
  - Implementation status: Proposed / Planned / In progress / Shipped / Blocked
  - Target release: `YYYY.MM` (or sprint identifier)
- **Verification artifacts:**
  - Test notes reference
  - Telemetry dashboard/query reference
  - Pull request reference
- **Review checkpoint:**
  - Review date
  - Reviewer
- **Closure criteria:**
  - Define closure condition explicitly against one or more onboarding success metrics and guardrail metrics.
- **Onboarding task references:**
  - Add employee
  - Filter urgent
  - Dry-run reminders
  - Live-run reminders
  - Fix invalid email setting
- **Failure mode detail:** Include specific validation or reminder-send failure state.
- **Reproducibility detail:** Include concise step sequence for setup -> trigger -> observed result.
- **Telemetry linkage scope:** `ux.onboarding.*` events only

## Intake-to-Backlog Conversion Requirement

- After validation, convert each finding into exactly one backlog row using the table schema in [`prioritized-backlog-format.md`](./prioritized-backlog-format.md).
- Required row fields for conversion: Finding ID, reproducible steps reference, proposed change, module-level code location(s), owner, and implementation status.
- Use the Severity/Frequency/Reach/Risk scoring model from `prioritized-backlog-format.md` when assigning priority.

## Onboarding Example (Abbreviated)

- **Finding ID:** ONB-F009
- **Decision/Issue/Backlog IDs:** ONB-D003 / ONB-I019 / ONB-B007
- **Severity:** Critical
- **Evidence:** Participant triggered live mode during dry-run task after unclear mode affordance.
- **Recommendation:** Add explicit mode banner and confirmation step before live send.
- **Implementation status + target release:** Planned, `2026.01`
- **Verification artifacts:** TN-ONB-011, `ux.onboarding.live_mode_confirmation` query, PR #188
- **Review checkpoint:** 2025-12-04, Onboarding UX owner
- **Closure criteria:** Live-run accidental-trigger rate <2% and dry-run task completion ≥95% in the next study cycle.
- **Affected workflow location:** Reminder mode selector + run confirmation dialog
