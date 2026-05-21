# Release Notes

## 2026-03 Settings UX Documentation Refresh

### Highlights
- Updated manual QA guidance for the Settings window tab refactor to use the current tab set: `General`, `Templates`, `Notifications`, `Storage`, and `Security`.
- Documented keyboard traversal expectations and validation behavior for tab-level and field-level error guidance.
- Added role-based visibility checks for sensitive/high-risk settings during manual QA.
- Added refreshed Settings window visual artifacts to support release communication and QA handoff.

### Documentation assets
- `docs/manual_qa_ux_b006.md`
- `docs/assets/settings_window_tabs_overview.svg`
- `docs/assets/settings_window_validation_states.svg`

### QA expectation snapshot
- Save validation must focus the first invalid field and select the first invalid tab.
- Sensitive settings must be role-gated where applicable.
