# Staffing Dashboard Implementation Plan

## Purpose

Build a local desktop staffing dashboard inside this repository from the prior Power Apps / SharePoint specification. The new module should track classroom staffing needs, position lifecycle state, hiring pipeline status, employee permit status, and time-to-fill history.

This is a handoff guide for a follow-up agent. Do not build Power Apps, SharePoint lists, or Power BI in this phase. Translate those concepts into this app's existing local, contract-driven Python + PySide6 workflow.

## Source Inputs

- User-supplied Power Apps / SharePoint developer spec for the Staffing Dashboard App.
- Screenshot of current Excel tracker with classroom rows, teacher cells, capacity totals, color-coded position states, permit colors, and school sections.
- Existing repo guidance in `AGENTS.md`: contract-first changes, small reviewable modules, tests for changed functions/classes, privacy-sensitive data handling.

## Product Scope

Build now:

- Staffing dashboard module in the desktop app.
- PySide6 GUI surface for school tabs, classroom rows, expanded classroom details, teacher/position list, and status transitions.
- Position lifecycle state machine.
- Employee / people records.
- Assignment history for each open-to-filled staffing cycle.
- Metrics for open positions, days open, average days to fill, and positions open more than 7 days.
- Seed/import path for current school/classroom/position data.
- Generic checkpoint email notifications for staffing, offer, interview, and onboarding events using editable Admin Studio rules.

Do not build now:

- Power BI.
- Predictive staffing.
- Ratio compliance alerts.
- Multi-school executive rollup beyond basic school tabs and global KPIs.
- Live SharePoint or Microsoft Graph sync.

Network exception: configured checkpoint notifications may send email through the existing onboarding SMTP settings after a matching active notification rule fires. Do not add separate staffing SMTP credentials.

## Recommended Architecture

Use SQLite for this module, not flat JSON, because staffing needs indexed relational joins, unique active history constraints, and deterministic multi-row state transitions. Store the DB under `user_artifacts/staffing_dashboard.sqlite3`.

Add these modules:

| Module | Purpose |
| --- | --- |
| `src/staffing_models.py` | Dataclasses/enums for school, classroom, assignment, person, history, metrics, and transition commands. |
| `src/staffing_store.py` | SQLite schema, migrations, indexes, CRUD, transaction helpers, and query methods. |
| `src/staffing_service.py` | Business state machine, validation, duplicate-history prevention, and metrics calculations. |
| PySide staffing view in `src/pyside_interview_app.py` | PySide6 widgets/model classes for school tabs, classroom/position tables, action dialogs, and color rendering. A separate `src/staffing_dashboard_view.py` module is no longer required for this implementation unless the PySide file needs later extraction. |
| `src/notification_models.py` | Shared notification dataclasses for rules, recipients, events, and send results. |
| `src/notification_store.py` | SQLite-backed notification rules, recipients, inactive defaults, and sanitized send audit. |
| `src/notification_service.py` | Exact-match event emitter, template rendering, idempotency, validation, and SMTP delivery via onboarding settings. |

Wire into:

- `src/pyside_interview_app.py`: add a Staffing Dashboard entry point/tab/action in the main PySide shell.
- `contracts/architecture.contract.yaml`: add staffing service/module ownership.
- `contracts/system.contract.yaml`: add module dependency entries.
- `docs/contract_test_coverage_matrix.yaml`: regenerate after contracts/tests land.

Add contracts:

- `contracts/staffing_models.contract.yaml`
- `contracts/staffing_store.contract.yaml`
- `contracts/staffing_service.contract.yaml`
- PySide staffing view contract coverage in `contracts/pyside_interview_app.contract.yaml`
- `contracts/notification_models.contract.yaml`
- `contracts/notification_store.contract.yaml`
- `contracts/notification_service.contract.yaml`

## Checkpoint Email Notifications

Use the generic notification subsystem for all staffing, offer, interview, and onboarding checkpoints.

Rules:

- Store rules in `user_artifacts/notification_rules.sqlite3`.
- Manage rules in Admin Studio, not source-controlled JSON.
- Each rule has one exact `event_type`, active flag, subject template, body template, and multiple recipients.
- Use existing onboarding SMTP settings for delivery; do not duplicate SMTP secrets in staffing storage.
- Default example rules are inactive until recipients/templates are configured:
  - `staffing.assignment.need_now` for hiring manager notification.
  - `offer.accepted` for executive director, director, and office manager notification.
- Deduplicate sends by `(rule_id, idempotency_key)` so retries and UI refreshes cannot send duplicates.
- Write sanitized audit rows only; never log SMTP credentials or full sensitive payload dumps.

Initial supported staffing events:

- `staffing.assignment.need_now`
- `staffing.assignment.coming`
- `staffing.assignment.filled`
- `staffing.assignment.replace`
- `staffing.assignment.not_needed`
- `staffing.permit.updated`

Offer/onboarding events already exposed for integration:

- `offer.generated`
- `offer.approved`
- `offer.accepted`
- `offer.welcome_email_sent`
- `onboarding.task.completed`

## Data Model

### `schools`

| Column | Type | Rules |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Stable internal ID. |
| `name` | TEXT NOT NULL UNIQUE | Example: `Hawthorne`. |
| `display_order` | INTEGER NOT NULL DEFAULT 0 | Controls tab/order. |
| `active` | INTEGER NOT NULL DEFAULT 1 | Soft hide only. |

### `classrooms`

| Column | Type | Rules |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Stable internal ID. |
| `school_id` | INTEGER NOT NULL | FK to `schools.id`. |
| `name` | TEXT NOT NULL | Example: `Tranquility`. |
| `program` | TEXT NOT NULL DEFAULT '' | Example: `Infant`, `Toddler`, `Preschool`, `Support`. |
| `licensed_capacity` | INTEGER | Nullable for non-classroom roles like Chef/Office. |
| `display_order` | INTEGER NOT NULL DEFAULT 0 | Spreadsheet-like ordering. |
| `active` | INTEGER NOT NULL DEFAULT 1 | Soft hide only. |

Unique: `(school_id, name)`.

### `people`

| Column | Type | Rules |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Stable internal ID. |
| `name` | TEXT NOT NULL | Employee/candidate name. |
| `role` | TEXT NOT NULL | `Teacher`, `Aide`, `Floater`, `Chef`, `Office`, `Custodian`, etc. |
| `permit_status` | TEXT NOT NULL DEFAULT `unknown` | Enum below. |
| `units` | REAL | Optional. |
| `notice_given` | TEXT | ISO date. |
| `final_working_day` | TEXT | ISO date. |
| `active` | INTEGER NOT NULL DEFAULT 1 | 1 active, 0 inactive. |
| `created_at` | TEXT NOT NULL | UTC ISO timestamp. |
| `updated_at` | TEXT NOT NULL | UTC ISO timestamp. |

Permit status enum:

- `unknown`
- `no_permit_or_application`
- `permit_in_process`
- `teacher_permit_approved`
- `no_units_needed`

### `assignments`

Represents one staffing position, not one person.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Stable internal ID. |
| `classroom_id` | INTEGER NOT NULL | FK to `classrooms.id`; no text joins. |
| `person_id` | INTEGER | FK to `people.id`; null when open or not needed. |
| `position_name` | TEXT NOT NULL | Human label, e.g. `Teacher 1`, `Aide`, `Floater`. |
| `position_type` | TEXT NOT NULL | `Teacher`, `Aide`, `Floater`, `Chef`, `Office`, `Custodian`, etc. |
| `status` | TEXT NOT NULL | Enum below. |
| `current_opened_date` | TEXT | UTC ISO timestamp; set when opening cycle starts. |
| `current_filled_date` | TEXT | UTC ISO timestamp; set when filled. |
| `start_date` | TEXT | ISO date for coming hires. |
| `display_order` | INTEGER NOT NULL DEFAULT 0 | Position ordering within classroom. |
| `active` | INTEGER NOT NULL DEFAULT 1 | Soft hide only. |
| `created_at` | TEXT NOT NULL | UTC ISO timestamp. |
| `updated_at` | TEXT NOT NULL | UTC ISO timestamp. |

Status enum:

- `dont_need_now`
- `need_now`
- `coming`
- `filled`
- `replace`

Indexes:

- `idx_assignments_classroom_id`
- `idx_assignments_person_id`
- `idx_assignments_status`
- `idx_assignments_opened_date`

### `assignment_history`

Tracks each open-to-fill cycle.

| Column | Type | Rules |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY | Stable internal ID. |
| `assignment_id` | INTEGER NOT NULL | FK to `assignments.id`. |
| `classroom_id` | INTEGER NOT NULL | Snapshot FK for easier historical queries. |
| `position_name` | TEXT NOT NULL | Snapshot label. |
| `opened_date` | TEXT NOT NULL | UTC ISO timestamp. |
| `filled_date` | TEXT | UTC ISO timestamp. |
| `days_to_fill` | INTEGER | Set when filled. |
| `closed_reason` | TEXT | `filled`, `cancelled`, or null while active. |
| `created_at` | TEXT NOT NULL | UTC ISO timestamp. |
| `updated_at` | TEXT NOT NULL | UTC ISO timestamp. |

Indexes:

- `idx_history_assignment_id`
- `idx_history_classroom_id`
- `idx_history_opened_date`
- Unique partial index: one active history row per assignment where `filled_date IS NULL AND closed_reason IS NULL`.

## State Machine

All transitions must run through `staffing_service.py` inside one DB transaction. UI must not update tables directly.

### Allowed Transitions

| From | To | Command | Required input |
| --- | --- | --- | --- |
| `dont_need_now` | `need_now` | `open_position` | assignment ID |
| `need_now` | `coming` | `mark_coming` | assignment ID, teacher name, start date |
| `coming` | `need_now` | `revert_coming` | assignment ID |
| `coming` | `filled` | `mark_filled` | assignment ID |
| `filled` | `filled` | `update_permit_status` | person ID, permit status |
| `filled` | `replace` | `mark_replacing` | assignment ID, notice given, final working day |
| `replace` | `need_now` | `clear_replacement` | assignment ID |
| any status | `dont_need_now` | `mark_not_needed` | assignment ID, explicit confirmation flag |

### Transition Effects

#### `open_position`

- Validate current status is `dont_need_now` or `replace`.
- Set assignment:
  - `status = need_now`
  - `person_id = NULL`
  - `current_opened_date = now`
  - `current_filled_date = NULL`
  - `start_date = NULL`
- Create one active `assignment_history` row.
- Fail closed if active history already exists for this assignment.

#### `mark_coming`

- Validate current status is `need_now`.
- Validate non-empty teacher name and valid future/present start date.
- Create person if no exact active person exists with same normalized name; otherwise reuse existing active person only after UI confirmation.
- Set assignment:
  - `status = coming`
  - `person_id = person.id`
  - `start_date = selected date`
- Do not close history yet.

#### `revert_coming`

- Validate current status is `coming`.
- Clear assignment `person_id` and `start_date`.
- Set `status = need_now`.
- Do not delete active history.
- Leave person record active but unassigned. Future agent can add cleanup UI if needed.

#### `mark_filled`

- Validate current status is `coming`.
- Validate assignment has `person_id`.
- Set assignment:
  - `status = filled`
  - `current_filled_date = now`
- Find latest active history for assignment.
- Set history:
  - `filled_date = now`
  - `days_to_fill = calendar day difference between opened_date and filled_date`
  - `closed_reason = filled`
- Fail closed if no active history exists or more than one is found.

#### `update_permit_status`

- Validate person exists and active.
- Set `people.permit_status`.
- Keep assignment status unchanged.

#### `mark_replacing`

- Validate current status is `filled`.
- Validate notice and final working day dates.
- Set current person:
  - `notice_given`
  - `final_working_day`
  - `active = 0`
- Set assignment:
  - `status = replace`
  - `current_opened_date = now`
  - `current_filled_date = NULL`
- Create new active history row.
- Keep assignment `person_id` until `clear_replacement` or new person selected so UI can show `replacing [name]`. If this complicates service logic, add `replacing_person_id` instead of overloading `person_id`.

#### `clear_replacement`

- Validate current status is `replace`.
- Set assignment:
  - `status = need_now`
  - `person_id = NULL`
  - `start_date = NULL`
- Keep active history open.

#### `mark_not_needed`

- Require explicit UI confirmation if current status is `coming`, `filled`, or `replace`.
- Set assignment:
  - `status = dont_need_now`
  - `person_id = NULL` unless status was `filled` and user chooses to preserve person separately.
  - `start_date = NULL`
  - `current_opened_date = NULL`
  - `current_filled_date = NULL`
- Close any active history with `closed_reason = cancelled`.

## Visual System

Power Apps spec colors and spreadsheet colors conflict. Implement canonical enum colors, then allow later tuning from config if user wants exact spreadsheet parity.

Recommended colors:

| Semantic status | UI color | Screenshot note |
| --- | --- | --- |
| `replace` | Red | Spreadsheet uses red for replace. |
| `need_now` | Yellow | Spreadsheet uses yellow for job opening. |
| `coming` | Purple | Spreadsheet uses purple for offer accepted. |
| `filled` | Dark green | Spreadsheet uses dark green for approved/filled staff. |
| `dont_need_now` | Blue-gray | Spreadsheet uses blue/cyan for not needed. |
| `no_permit_or_application` | Light green | From screenshot key. |
| `permit_in_process` | Medium green | From screenshot key. |
| `teacher_permit_approved` | Dark green | From screenshot key. |
| `no_units_needed` | Pale pink | From screenshot key. |

Priority for classroom summary color:

1. `replace`
2. `need_now`
3. `coming`
4. `dont_need_now`
5. `filled`

## GUI Requirements

Primary surface is PySide6.

### Navigation

- Add `Staffing` entry to existing PySide main window.
- Show one tab per active school.
- Within each school tab, show a workbook-style board:
  - compact KPI summary;
  - explicit school selector above the tabs for directors to switch schools even when tab styling is subtle;
  - color-code key matching the Excel tracker;
  - ratio band labels, classroom rows, teacher/aide/support slots, capacity, notes, and action controls.

### Classroom Rows

Workbook row:

- Classroom name.
- Program and licensed capacity.
- Ratio group such as `3 to 1 (infant units needed)`, `4 to 1`, or `8 to 1`.
- Teacher, aide, and support slots ordered like the workbook.
- `OPEN POSITION` for visible workbook `?` cells.
- Row/cell colors from staffing status or permit status.

### Position Rows

Each position row shows:

- Position name/type.
- Teacher name or `OPEN POSITION`.
- Status.
- Start date for `coming`.
- Days open for `need_now` or `replace`.
- Permit status when person exists.
- Context action button/menu.

Actions:

- `Open Position`
- `Mark Coming`
- `Revert Coming`
- `Mark Filled`
- `Update Permit`
- `Replace Employee`
- `Clear Replacement`
- `Mark Not Needed`

Use modal dialogs for required dates/names. Validate before applying service command. Show exact failure message from service if transition is blocked.

## Metrics

Compute in `staffing_service.py`, using `staffing_store.py` queries.

Required metrics:

- Assignment days open: `today - current_opened_date` for `need_now` and `replace`.
- Classroom total positions.
- Classroom filled count.
- Classroom open count: `need_now + replace`.
- Classroom coming count.
- Classroom average days to fill from closed history.
- Global total open positions.
- Global average fill time.
- Global positions open more than 7 days.

Do not calculate metrics by scraping GUI table text.

## Seed Data / Import Plan

Add deterministic seed/import helper in `staffing_store.py` or a small script under `tools/`.

Recommended first seed format: `config/staffing_seed.json`.

Current seed is generated from the school workbooks:

- `HAW Staffing Needs.xlsx` -> `Hawthorne`
- `NLB Staffing Needs.xlsx` -> `North Long Beach`
- `PMD Staffing Needs.xlsx` -> `Palmdale`

The committed seed includes every non-empty teacher, aide, and support-staff cell parsed from those workbooks.

Shape:

```json
{
  "schools": [
    {
      "name": "Hawthorne",
      "display_order": 1,
      "classrooms": [
        {
          "name": "Tranquility",
          "program": "Infant",
          "ratio_group": "3 to 1 (infant units needed)",
          "licensed_capacity": 12,
          "display_order": 1,
          "slots": [
            {"slot_group": "teacher", "position_name": "Teacher 1", "position_type": "Teacher", "status": "filled", "person": {"name": "Angie", "permit_status": "teacher_permit_approved"}},
            {"slot_group": "teacher", "position_name": "Teacher 2", "position_type": "Teacher", "status": "need_now", "notes": "?"}
          ]
        }
      ],
      "support_rows": [
        {
          "name": "Infant Floater",
          "slots": [
            {"slot_group": "support", "position_name": "Infant Floater", "position_type": "Support", "status": "filled", "person": {"name": "Amy"}, "notes": "Full time"}
          ]
        }
      ]
}
```

Import requirements:

- Idempotent by school/classroom/position name.
- Supports legacy `positions` and workbook-style `slots`.
- Supports `support_rows` as school-scoped non-classroom staffing rows.
- Never duplicate active history.
- Refuse unknown statuses/permit statuses.
- Normalize blank names to `NULL`, not empty strings.
- Log only counts and non-sensitive validation errors. Do not dump employee data into logs.

## Contract Work Required

Before code changes, read relevant existing contracts:

- `contracts/pyside_interview_app.contract.yaml`
- `contracts/architecture.contract.yaml`
- `contracts/system.contract.yaml`
- `contracts/data_store.contract.yaml`
- Any contract for a module imported by new staffing code.

Then:

- Add contract YAML for every new staffing module.
- Update system and architecture contracts.
- Mark no new interface `locked: true` until behavior is stable.
- Regenerate `docs/contract_test_coverage_matrix.yaml` with `tools/regenerate_contract_test_matrix.py`.
- Run `tools/check_contract_review.py` relevant sections before handoff.

## Test Plan

Add focused tests before or alongside implementation.

### Model / Store Tests

- Creates DB schema from empty file.
- Adds schools/classrooms/assignments/people.
- Enforces no duplicate school/classroom names within same school.
- Enforces no duplicate active history per assignment.
- Uses IDs/FKs for joins, not classroom text.

### Service State Machine Tests

- `dont_need_now -> need_now` opens history and clears person.
- `need_now -> coming` creates/reuses person and keeps history open.
- `coming -> need_now` clears person/start date, keeps history open.
- `coming -> filled` closes history and calculates days to fill.
- `filled -> replace` marks person inactive and opens new history.
- `replace -> need_now` clears assignment person and keeps replacement history open.
- Invalid transitions fail closed and leave DB unchanged.
- Marking filled with missing or duplicate active history fails closed.

### Metrics Tests

- Days open from `current_opened_date`.
- Average days to fill excludes active/unfilled history.
- Positions open over 7 days counts `need_now` and `replace`.
- Classroom counts match mixed statuses.

### PySide Tests

- Staffing entry point exists in PySide shell.
- School tabs are generated from seed/store data, not hardcoded.
- Workbook board renders actual visible names, support rows, capacity, ratio bands, and color key.
- Status colors map by enum, not free text.
- Action dialogs call service commands, not direct store writes.
- Service errors surface visibly and do not mutate UI state optimistically.

## Validation Commands

Use explicit working Python interpreter if local `python` points at wrong runtime. Known good path on this machine has often been:

```powershell
C:\Users\Dnord\AppData\Local\LPL_InterviewTool\py311\.venv\Scripts\python.exe -m pytest tests/test_staffing_models.py tests/test_staffing_store.py tests/test_staffing_service.py tests/test_staffing_dashboard_view.py
```

Then run:

```powershell
C:\Users\Dnord\AppData\Local\LPL_InterviewTool\py311\.venv\Scripts\python.exe tools\regenerate_contract_test_matrix.py
C:\Users\Dnord\AppData\Local\LPL_InterviewTool\py311\.venv\Scripts\python.exe tools\check_contract_review.py --section baseline --section locked --section schema --section coverage-matrix
C:\Users\Dnord\AppData\Local\LPL_InterviewTool\py311\.venv\Scripts\python.exe -m pytest
```

If that interpreter is missing, inspect repo setup docs and use the active project venv. Do not trust failures from LibreOffice Python or other unrelated Python installs.

## Security / Privacy Requirements

- Treat names, notice dates, final working days, and staffing records as sensitive operational data.
- Keep DB under ignored `user_artifacts/`; do not commit live staffing data.
- Do not log full names, full DB rows, or generated seed files from real school data.
- Validate all dates and enum values at service boundary.
- Use DB parameters for all SQL.
- Keep import paths constrained to repo/config or explicit operator-selected files.
- Fail closed on invalid transitions, duplicate active history, missing history on fill, or unknown IDs.
- No network calls for staffing dashboard MVP except configured checkpoint email sends through existing onboarding SMTP settings.

## Handoff Checklist

1. Read this guide, `AGENTS.md`, `README.md`, `docs/README.md`, and relevant contracts.
2. Add tests for models/store/service first.
3. Implement SQLite schema/migrations and contracts.
4. Implement service state machine with transactional commands.
5. Implement metrics.
6. Add seed/import helper and test idempotency.
7. Build PySide dashboard view and wire into `pyside_interview_app.py`.
8. Add PySide tests for expansion/action wiring.
9. Update architecture/system/module contracts.
10. Regenerate contract matrix.
11. Run targeted tests, contract review, then full test suite.
12. Manually smoke PySide dashboard with seed data:
    - open Staffing;
    - switch school tabs;
    - expand/collapse classroom;
    - open position;
    - mark coming;
    - mark filled;
    - replace employee;
    - confirm history/metrics changed.

## Open Decisions For User

Resolve before or during implementation:

- Exact schools to seed first.
- Exact classroom list and capacities.
- Whether non-classroom roles like Chef, Office, Custodian live in pseudo-classrooms or a separate support-staff group.
- Whether screenshot color palette should override recommended canonical colors.
- Whether replacing employee should keep old person visible on assignment until replacement starts.
- Whether `mark_not_needed` should cancel active history with a closed reason or be blocked when active history exists.
