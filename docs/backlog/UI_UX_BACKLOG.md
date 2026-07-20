# UI/UX Backlog (Standardized)

Source summary: `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md`

## Prioritization Model

- **Priority score = Severity + Frequency + Reach + Risk**
- Severity/Frequency/Reach/Risk values must follow `docs/ux-research/onboarding/prioritized-backlog-format.md` consistently for every row.

## Backlog Table

| Backlog ID | Finding ID | Priority | User impact summary | Repro steps ref | Proposed change | Code location(s) | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| UX-B001 | UX-F001 | 15 | Interviewers experience high cognitive load and miss fields while scoring | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 1 | Keep score/notes visible, collapse descriptors/sample answers by default | `src/question_screens.py` | Eng + UX | Done |
| UX-B002 | UX-F002 | 13 | Admin users misconfigure settings due to mixed-priority controls in one view | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 2 | Split settings into tabs and add section-level validation + advanced defaults reset | `src/pyside_interview_app.py` | Eng + UX | Done |
| UX-B003 | UX-F003 | 14 | Onboarding operators hesitate due to flat action hierarchy | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 3 | Group actions by intent and visually prioritize primary CTA | `src/onboarding_app.pyw` | Eng + UX | Done |
| UX-B004 | UX-F004 | 14 | Dashboard awareness does not quickly convert into action | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 4 | Add clickable KPI chips and recommended next action banner | `src/onboarding_operations.py` | Eng + UX | Done |
| UX-B005 | UX-F005 | 12 | Inconsistent error patterns increase interruption and recovery confusion | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 5 | Standardize inline recoverable validation and modal-only blocking failures | `src/question_screens.py`, `src/onboarding_app.pyw`, `src/ui_feedback.py` | Eng + UX | Done |
| UX-B006 | UX-F006 | 11 | Keyboard and focus clarity inconsistencies reduce accessibility baseline | `docs/UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md` section 6 | Tab-order audit, focus-visible standard, key contrast/readability checks | `src/question_screens.py`, `src/onboarding_app.pyw`, `src/ui_windows.py` | Eng + UX | Done |

## Initial Execution Order

1. UX-B003
2. UX-B004
3. UX-B001
4. UX-B005
5. UX-B002
6. UX-B006

## Notes

- UX-B001 follow-up refinement rationale: preserving always-visible score/notes reduces working-memory load during evaluation, while collapsible guidance/history keeps contextual support available on demand without reintroducing dense default layouts.
- Keep one problem statement per backlog row.
- Update `Status` as items move from `Todo` to active delivery states.
- Active implementation detail belongs in this file, not in summary-only recommendation docs.
