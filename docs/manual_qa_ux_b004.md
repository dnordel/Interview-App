# Manual QA — UX-B004 (Dashboard KPI actions + recommended next action)

- **Screen name / ID:** Onboarding app Today Dashboard (`_build_today_dashboard`)
- **Related PR change:** KPI chips map to deterministic filters; one recommendation banner with actionable CTA
- **Tester:** Codex agent
- **Date:** 2026-03-03

## Functional smoke checks

- [x] Dashboard loads with KPI chips for Overdue, Due Today, Urgent, and Pending.
- [x] Each KPI click navigates to an employee and applies the expected task filter.
- [x] KPI chips are disabled when no matching tasks are available.
- [x] Recommended next action banner text updates from current task-state heuristics.
- [x] Recommended action button executes an explicit navigation or Start Interview fallback.

## Accessibility checks

- [x] KPI chips are keyboard-focusable.
- [x] KPI chips activate via Enter and Space.
- [x] Recommendation action button activates via Enter and Space.
- [x] Focus-visible styling appears on keyboard focus for KPI chips and recommendation CTA.

## Acceptance summary against backlog intent (`UX-B004`)

- [x] Added clickable KPI chips/cards that trigger concrete task filter actions.
- [x] Added one recommendation banner that proposes and executes a next best action.
- [x] KPI and banner actions are operable with keyboard and include focus-visible treatment.

## Notes

- **Observed issues:** None in this pass.
- **Follow-up tickets:** Consider UX-B006 follow-up for global focus style consistency beyond dashboard controls.
- **Evidence:** Code-level verification + local automated tests.
