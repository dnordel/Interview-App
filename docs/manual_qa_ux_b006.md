# Manual QA: UX-B006 Keyboard and Focus Accessibility

## Scope
- `src/question_screens.py`
- `src/onboarding_app.pyw`
- `src/ui_windows.py`

## Settings window tab model (post-refactor)
- **Tab labels:** `General`, `Templates`, `Notifications`, `Storage`, `Security`.
- **Keyboard traversal order:** `General` -> `Templates` -> `Notifications` -> `Storage` -> `Security` -> `Save` -> `Cancel`.
- **Validation expectations:**
  - Template placeholder violations are surfaced on **Templates** with field-level guidance (for example, "Open Placeholders picker...").
  - Director endpoint format issues are surfaced on **Notifications** with URL guidance (for example, "Use <https://...>").
  - Whisper/transcription value issues are surfaced on **Security** (beam size and temperature constraints).
  - Validation summaries appear both at tab level and field level, and focus moves to the first invalid field after save.

## Accessibility checklist evidence
- [x] **Disclosure controls are keyboard reachable**
  - Collapsible guidance/history toggles on interview screens are reachable via Tab and operable with Enter/Space.
- [x] **Focus-visible state is present on disclosure toggles**
  - Guidance/history toggle controls render a visible focus indicator that meets existing focus-ring contrast expectations.
- [x] **Disclosure panel state retention works within session**
  - Expanded/collapsed state persists while navigating between interview questions during the same app session.
- [x] **Tab sequence prioritizes score and notes before optional guidance**
  - Keyboard navigation reaches score selectors and interviewer notes before disclosure toggles for guidance/history panels.
- [x] **Tab order is logical and complete**
  - Interview question screens: Start at qualification/question notes, then move through scoring/disqualifier controls and footer actions.
  - Onboarding dashboard: Search -> employee list -> action panel -> task filters -> task rows.
  - Settings window: `General` -> `Templates` -> `Notifications` -> `Storage` -> `Security` -> Save/Cancel.
- [x] **Focus visibility is clear**
  - Focus rings are visible for interactive custom controls (KPI chips, recommended CTA, employee list, task checkboxes).
  - Settings tabs retain visible focus for keyboard-traversed widgets.
- [x] **Color contrast is sufficient**
  - Existing contrast-preserving badge palettes retained for status badges.
- [x] **Status cues are not color-only**
  - Task status badges include both icon + text labels.
  - Guidance text references explicit statuses (`Overdue`, `Due today`) instead of color names.
- [x] **Keyboard-only completion works end-to-end**
  - Interview flow supports keyboard-only edit/save/finalize.
  - Onboarding reminder send flow supports keyboard-only send/confirm in pre-send dialog.
  - Settings flow supports keyboard-only save/cancel with telemetry capture.
- [x] **Role-based visibility checks cover sensitive sections**
  - Non-admin/interviewer role: cannot view or toggle high-risk settings in **Notifications** (auto-send) and cannot modify **Security** transcription controls.
  - Admin role: can access high-risk delivery toggle and advanced **Security** controls, with confirmation for high-risk state changes.

## Telemetry checks
- Event emitted: `ux.keyboard_path_completed`
- Required properties validated in output:
  - `screen_id`
  - `flow_id`
  - `completed_via_keyboard`
  - `keyboard_step_count`
  - `abandoned`
- PII check: payload excludes names, emails, notes, and other free text.

## Known follow-up gaps
- Keyboard-step counting is currently event-based and approximate rather than semantic per-form-step.
- Follow-up opportunity: consolidate keyboard-step tracking into shared app shell hooks for broader screen coverage.

## Verification artifact reference
- Template: `docs/manual_qa_screen_template.md`
- Settings tab structure screenshot: `docs/assets/settings_window_tabs_overview.svg`
- Settings validation-state screenshot: `docs/assets/settings_window_validation_states.svg`
