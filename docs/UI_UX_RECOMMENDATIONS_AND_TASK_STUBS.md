---
status: active
owner: Product + UX (Interview & Onboarding)
last-reviewed: 2026-03-03
scope: Cross-application UI/UX recommendations for Interview and Onboarding desktop workflows
superseded-by: n/a
---

# UI/UX Improvement Review and Task Stubs

## 30-second triage for maintainers
- **Should I act on this file now?** **Summary only**.
- **Document role:** **Findings and recommendations summary artifact** (not execution planning).
- **Where are actionable items?** `docs/backlog/UI_UX_BACKLOG.md`
- **What should I do next?** Triage stale `Todo` items into active planning or closure during the review checkpoint cadence.

## Security considerations to include before any UI/UX implementation
1. **PII minimization in the interface**: avoid exposing sensitive candidate data in titles, logs, and debug popups.
2. **Safe defaults for outbound communication**: force explicit confirmation and recipient review for email/reminder sends.
3. **Template token allowlisting**: preserve strict placeholder validation for all editable message templates.
4. **Local storage hardening**: communicate where files are stored and provide safer path selection defaults.
5. **Error-message redaction**: do not surface stack traces or raw exception payloads with private details to end users.
6. **Role-appropriate visibility**: distinguish what operational users can see/edit in settings vs. interviewer-only views.
7. **Auditability**: preserve action history for high-risk actions (finalize, reminder send, packet export).

## Purpose
This file is the high-level summary of UI/UX findings and recommendation themes. It is **not** the source of truth for execution-ready work items. The canonical actionable backlog now lives in:

- `docs/backlog/UI_UX_BACKLOG.md`

## Summary of findings and recommendations

### 1) Interview app: high cognitive load on scored-question pages
- `TraitScreenUI.render()` currently presents many dense sections together (`src/question_screens.py`).
- Recommendation: apply progressive disclosure while keeping score controls and notes always visible.
- Implemented model note: score selectors and interviewer notes remain persistently visible, while guidance content (descriptors, sample-answer cues, and response history) is available through collapsible sections to reduce scan burden without removing context.

### 2) Interview settings: dense mixed-priority controls
- `SettingsWindow` includes broad concerns in one long surface (`src/ui_windows.py`).
- Recommendation: split into tabs and add section-level validation clarity.

### 3) Onboarding app: command-heavy left panel with weak hierarchy
- `_build_layout()` presents many equal-weight actions (`src/onboarding_app.pyw`).
- Recommendation: group by intent and establish a clearly primary daily action.

### 4) Dashboard summaries are text-first, not action-first
- Dashboard uses text summary with limited direct drilldowns (`src/onboarding_app.pyw`).
- Recommendation: add clickable KPI chips/cards and one recommended next action.

### 5) Validation feedback style inconsistency
- Current behavior mixes modal errors and inline error handling (`src/question_screens.py`, `src/onboarding_app.pyw`).
- Recommendation: standardize inline validation for recoverable issues and reserve modals for blocking failures.

### 6) Accessibility baseline gaps
- Keyboard flow and focus visibility can be improved across key workflows.
- Recommendation: complete tab-order audit and focus/contrast consistency pass.

## Historical note
The detailed per-item task stubs previously embedded in this document were migrated into a standardized backlog table format for active tracking and prioritization.


## Review checkpoint for stale `Todo` items
- Run a lightweight checkpoint at least weekly.
- For each stale `Todo`, either move it to active planning in `docs/backlog/UI_UX_BACKLOG.md` with owner + status, or close it with rationale.
- Keep this document recommendation-focused; avoid duplicating implementation task breakdowns captured in the backlog.


## Shared validation standard

Adopt `info/warning/error/blocking` severity levels consistently across interview and onboarding surfaces.
Recoverable issues should render inline near their related field/section, while modal dialogs are reserved for blocking failures and irreversible confirmations.
All user-facing copy should follow issue + next-step guidance and avoid raw technical details.
