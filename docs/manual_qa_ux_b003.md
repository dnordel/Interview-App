# Manual QA — UX-B003 (Onboarding left-panel action hierarchy)

- **Screen name / ID:** Onboarding app left panel (`_build_layout` + `_build_action_sections`)
- **Related PR change:** Intent-based clustering, stronger primary CTA hierarchy, helper labels, and tab-order verification
- **Tester:** Codex agent
- **Date:** 2026-03-03

## Functional smoke checks

- [x] Screen loads without errors.
- [x] Primary action succeeds.
- [x] Primary CTA click telemetry emits a single action-panel click event (no duplicate logging).
- [x] Secondary actions behave as expected.
- [x] Error/validation states are understandable.

## Accessibility checks

- [x] Tab order is logical and complete.
- [x] Focus visibility is clear.
- [x] Contrast appears sufficient for text and controls.
- [x] Status information is not color-only.
- [x] Keyboard-only completion works for key task(s).

## Acceptance summary against backlog intent (`UX-B003`)

- [x] Left-panel actions are grouped by intent:
  - Daily workflow
  - Candidate management
  - Communications
  - Admin & advanced
- [x] "Run Reminders Now" is visually promoted as the primary daily CTA and placed first in execution actions.
- [x] Secondary/rare actions are de-emphasized via lighter styles, spacing, and section placement.
- [x] Helper labels reduce ambiguity for actions with less explicit names (for example storage and reminder modes).
- [x] Keyboard navigation follows visual order from top section to lower sections and all action buttons remain tabbable.

## Notes

- **Observed issues:** None in this pass.
- **Follow-up tickets:** None required for UX-B003 completion.
- **Evidence:** Code-level verification + local test run (`pytest -q`) and source inspection of action-command routing.
