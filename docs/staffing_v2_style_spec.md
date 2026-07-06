# Staffing v2 Mockup Style Specification

This is the visual contract for the parallel PySide Staffing v2 rewrite. Use this before coding any new Staffing v2 screen. It specializes the app-wide reference in `docs/pyside_mockup_redesign_reference_spec.md`. Existing broad Qt style guide is useful for tokens, but it is not specific enough to match the supplied mockups.

## Why A Staffing v2 Screen Can Drift From Mockup

If a rendered Staffing v2 screen differs from its mockup, check these concrete causes first:

- Content-local horizontal subnav buttons. Mockups use left sidebar nav items for Staffing Dashboard, Classrooms, People, Assignment History, Validation, etc. The `Admin Studio` text/logo in the mockups is not a product requirement; ignore that brand artifact.
- Generic component-light QSS. Mockups require exact card metrics, pill chips, icons, table button menus, filter heights, and sidebar active state.
- Native `QListWidget` and `QTableWidget` defaults leak through: visible scroll bars, selected text contrast issue, grid density, table row height, and header spacing differ.
- Current content proportions are off: left classroom panel too wide/tall, right table too sparse, status key too low, and large empty table body dominates.
- Icons are missing. Mockup uses small outline icons throughout metrics, nav, buttons, status chips, drawer actions, and table action menus.
- Data display is not normalized to mockup: `Avg Days to Fill` shows `20639.0` because seed/epoch dates flow into days-open calculations; mockup expects realistic cycle metrics such as `18.4`.
- Top-level action row differs: mockup has `Export` and `View History`, primary blue `Add Position`, labeled filters, search icon, and no content-local tab buttons.

## Fidelity Target

Exact pixel parity is not guaranteed in PySide because native font rendering, DPI scaling, and widget metrics vary by Windows display settings. Required target:

- Structural parity: same shell, regions, order, hierarchy, and action placement.
- Visual parity: same colors, spacing, typography scale, card shapes, chips, icons, row heights, and density within normal Windows DPI variance.
- Behavior parity: same controls visible, disabled/placeholder where not implemented, all mutations routed through `StaffingService`.
- Regression parity: screenshots at 1600x900 or larger must look recognizably like the matching mockup before moving to next screen.

## Global Tokens

Use one Staffing v2 style module, not per-screen ad hoc QSS.

Colors:

- App background: `#f8fafc`
- Surface/card: `#ffffff`
- Card border: `#e2e8f0`
- Strong border: `#cbd5e1`
- Text/navy: `#0f172a`
- Secondary text: `#475569`
- Muted text: `#64748b`
- Primary blue: `#2563eb`
- Primary hover: `#1d4ed8`
- Active nav background: `#eaf2ff`
- Active row background: `#eff6ff`
- Danger text: `#dc2626`
- Danger bg: `#fee2e2`
- Warning/orange text: `#ea580c`
- Warning bg: `#ffedd5`
- Coming/yellow text: `#b45309`
- Coming bg: `#fef3c7`
- Success text: `#15803d`
- Success bg: `#dcfce7`
- Neutral text: `#475569`
- Neutral bg: `#f1f5f9`
- Info bg: `#dbeafe`

Typography:

- Family: Segoe UI first, then Inter, Arial.
- Page title: 24-26 px, weight 800, `#0f172a`.
- Page subtitle: 13-14 px, weight 400, `#475569`.
- Section title: 17-18 px, weight 800.
- Card label: 12-13 px, weight 500-600, `#475569`.
- Metric value: 20-24 px, weight 800.
- Table header: 12-13 px, weight 700, `#334155`.
- Body text: 13 px, `#0f172a`.
- Small helper text: 11-12 px, `#64748b`.

Spacing and geometry:

- Sidebar width: 240-260 px.
- Main content outer margins: 24 px left/right, 18-24 px top.
- Main row gap: 14-16 px.
- Card radius: 8 px for repeated cards; 10-12 px for large panels/dialog sections.
- Control height: 40 px.
- Metric card height: 72-96 px depending screen.
- Main dashboard classroom item height: 60-66 px.
- Table header height: 42-46 px.
- Table row height: 58-70 px for dashboard tables; 48-58 px for dense management tables.
- Dialog radius: 12-14 px.
- Drawer width: 420-520 px.

Icons:

- Use an icon helper around `QLabel`/`QPushButton` text or Qt-compatible icon font/assets.
- Required icon sizes: nav 18 px, metric 18-20 px, button 16 px, status 14-16 px.
- Icons must not be replaced by text if mockup uses an icon.
- Add and person-add actions use primary-blue plus/person glyphs, not native folder/new-file glyphs.
- If no icon library is added yet, use short Unicode-free ASCII-safe fallbacks only as a temporary test-safe state, then replace before visual QA.

## Staffing v2 Shell

The staffing mockups require a staffing-focused shell inside the v2 page. Do not implement the literal `Admin Studio` logo/brand from the generated mockups. Keep the actual application identity. Copy the staffing navigation layout, density, cards, controls, and interactions from the mockups.

Sidebar:

- White background, right border `#e2e8f0`.
- Header brand: use existing app/product identity if a brand header is present; do not show `Admin Studio`.
- Nav items with icon + label:
  - Dashboard
  - Staffing Dashboard
  - Classrooms
  - People
  - Assignment History
  - Analytics
  - Notifications
  - Settings
  - Validation where applicable
  - Integrations where applicable
- Active item: `#eaf2ff` fill, blue text, optional blue left rail.
- Environment card near bottom: Production dot + version.
- User card bottom: initials, name, role, caret.

Important: Keep top-level app `Staffing v2` nav item for temporary access, but once inside v2, render the Admin Studio-style staffing shell. Do not rely on horizontal tabs for sub-dashboard navigation.

Clarification: "Admin Studio-style" means the visual layout pattern from the mockup, not the `Admin Studio` name or logo.

## Main Staffing Dashboard Mockup

Canvas:

- Sidebar left, content right, full height.
- Content header starts at x aligned after sidebar, not inside a separate tab strip.
- Header height about 86-110 px.

Header:

- Left: `Staffing Dashboard`, subtitle.
- Right/top: labeled School combo, Program combo, search field with search icon, primary `+ Add Position`.
- Secondary action row right: `Export`, `View History`.
- No visible `Staffing Dashboard / People / Assignment History` content tab buttons.

Summary chips:

- One horizontal row below header.
- `Export` and `View History` sit at the right side of this same row, not in a separate row above or below the chips.
- Compact pill/cards, not large metric tiles:
  - `Schools: 4`
  - `Open positions: 7`
  - `Avg fill time: 18.4 days`
  - `Open > 7 days: 3`
  - `Validation healthy`
- Height about 38-42 px.
- Blue outline/fill for neutral stats, red fill for stale open count, green fill for validation.

Left classroom panel:

- Width around 370-390 px at 1600 mockup.
- White panel, radius 8-10 px, border.
- Header row: `Classrooms`, small filter/settings icon button.
- Item: status dot at left, bold classroom name, second-line counts, chevron at right.
- Selected item: blue border, very light blue background, text remains navy/readable.
- Status dots:
  - Need Now/critical: red
  - Coming/warning: orange/yellow
  - Filled/healthy: green
  - Neutral: gray
- Footer: `Showing 1-10 of 10 classrooms`.

Right classroom detail:

- White panel, border, radius.
- Header: classroom name + school; right priority status chip when active.
- Overview cards: 6 cards in one row on wide desktop.
  - Program
  - Licensed Capacity
  - Total Positions
  - Filled
  - Open
  - Avg Days to Fill
- Each overview card has icon + label + value.
- Cards equal height, stable width, no layout shift.

Positions table:

- White table card with subtle grid.
- Columns: row number, Position, Person, Status, Start Date, Days Open, Permit Status, Next Action.
- Status column uses chip widget, not colored cell background.
- Permit status uses icon/chip text.
- Next Action uses button/menu. Filled row action defaults to Replace menu in mockup; v2 may route Manage Filled first until replace workflow is built, but button/menu style must match.
- Table body height should not visually dominate; add lower Add Position dashed drop-zone and status key like mockup.

Bottom:

- Dashed `+ Add Position` drop zone under table.
- Status key in bordered strip, compact chips.

## Position Detail Drawer

Match mockup panel:

- Right-side overlay/docked panel, width 520-620 px depending window.
- Background dims or main panel remains visible if drawer is overlaid.
- Header: `Position Detail`, subtitle `Classroom · School · Assignment ID #...`, close X.
- Top summary card with status chip and position name.
- Two-column detail cards:
  - Position Overview
  - Available Next Actions
  - Data Integrity / Validation
  - Lifecycle History
  - Related Person
- Footer: last updated, Cancel/Save Draft/Save Changes.
- Buttons use primary/secondary styles, not generic native buttons.

## Mark Coming Dialog

Layout:

- Modal width around 900-960 px.
- Header: title/subtitle + close X.
- Section cards numbered 1-6 exactly as mockup:
  - Position Summary
  - Candidate Selection
  - Candidate Details
  - Link existing person
  - Validation / Requirements
  - What will happen on save
- Left form column, right validation column.
- Footer: warning banner, Cancel, Save Draft, primary Mark Coming.
- Start date required.
- Filled date not set here; later Mark Filled uses coming start date as filled date.

## Mark Filled Dialog

Must communicate user decision:

- Filled date is read-only and equals coming candidate start date.
- No user-editable filled date picker.
- Shows cycle close preview and days-to-fill calculation.
- Confirmation checkbox required.
- Uses service `mark_filled`; no table cell mutation.

## Manage Filled Position Dialog

Layout:

- Modal with position summary strip.
- Two large option cards:
  - Update Permit Status, selected by default, green/blue accent.
  - Replace Employee, orange accent.
- Each card has icon, radio, explanation, checklist.
- Continue routes to selected workflow.
- No DB mutation in chooser.

## Update Permit Dialog

Layout:

- Modal width around 760-900 px.
- Position summary strip.
- Left Permit Update form:
  - employee name readonly
  - role readonly
  - current permit chip
  - new permit combo
  - effective date
  - units
  - notes
  - documentation received
  - attach file placeholder
- Right validation + what-will-happen panels.
- Footer: Cancel, Save Draft, Save Permit Update.
- Mutates People permit fields only.

## Mark Need Now Dialog

Layout:

- Compact modal, about 520-600 px wide.
- Orange warning banner.
- Position summary grid.
- What will happen checklist.
- Options section with checked `Clear assigned person and start date`.
- Info banner explaining person removal.
- Footer: Cancel, primary Confirm & Mark Need Now.
- Mutates through `clear_replacement`.

## People / Employee Management

Shell:

- Same Admin Studio sidebar, People active.
- Header title: `People / Employee Management`, subtitle, Add Person.
- Filter row: Search, Active Status, Role, Permit Status, More Filters, Clear.
- Metrics: Total People, Active, Inactive, Teachers, Aides, Avg Units.
- Table: Name with avatar/email subtext, Role, Permit Status chip, Units, Status chip, Current Assignment, Actions.
- Right detail panel:
  - initials/avatar + name/role/contact
  - Active chip
  - tabs: Overview, Assignments, History, Notes, Documents
    - Overview active by default with blue text and 2 px blue underline.
    - Inactive tabs are transparent slate text with no filled button chrome.
    - PySide implementation marks tabs with `staffingV2ActivePeopleTab` for QSS and tests.
  - Employee Information
  - Current Assignment
  - Employment Status
  - Additional Information
  - bottom buttons Deactivate Employee, Edit Person
- If DB lacks email/phone fields, show `-`; do not invent sensitive person data.

## Assignment History

Shell:

- Same Admin Studio sidebar, Assignment History active.
- Header title/subtitle, last updated, refresh icon, Export, View Validation.
- Metrics: Total Cycles, Open Cycles, Closed Cycles, Avg Days to Fill, Data Issues.
- Filters: School, Classroom, Cycle Status, Date Range, Search, More Filters, Clear.
- Table: Assignment ID, Classroom, Position, Opened Date, Filled Date, Days to Fill, Cycle Status, Employee, Data Integrity, Actions.
- Detail panel:
  - History Record Detail
  - assignment ID chip
    - Neutral slate chip, object `StaffingV2HistoryAssignmentIdChip`, text like `A-1024`.
  - lifecycle events
  - validation/integrity checklist
    - Each row uses object `StaffingV2HistoryValidationCheckRow`.
    - Row property `staffingV2ValidationCheckStatus` is `pass` or `warning`.
    - Rows include leading pass/warning icon, not plain paragraph text.
  - footer buttons View Assignment, Open Employee, Export Record
- Data integrity badges: Healthy green, Warning orange, Critical red.

## Classroom Management

Shell:

- Same sidebar, Classrooms active.
- Header: `Classroom Management` or `Classrooms` depending mockup variant.
- Top metrics:
  - Total Classrooms
  - Active
  - Avg Licensed Capacity
  - Total Positions
  - Open Positions
- Filter row: School, Program, Status, search, More Filters, Clear.
- Main table: Classroom, School, Program, Licensed Capacity, Total Positions, Filled, Open, Priority Status, Active, Actions.
- Optional right detail drawer with staffing summary and validation cards.
- Validation health strip/cards under table.

## Staffing Validation

Shell:

- Same sidebar, Validation active.
- Header: `Staffing Validation`, subtitle, last updated, Export Report.
- Overview cards: Total Issues, Critical, Warning, Info, Overall Compliance.
- Issue tabs: All Issues, Critical, Warnings, Info.
  - Active tab uses blue text and 2 px blue underline.
  - Inactive tabs are transparent, navy/slate text, no filled button chrome.
  - PySide implementation marks active tab with `staffingV2ActiveValidationTab` for QSS and tests.
- Search and Filters button.
- Issues table: Issue, Classroom, Type, Severity, Detected, Details, Action.
- Right filter/summary panel:
  - Filters
  - Compliance Summary donut or simplified bar if donut not practical in PySide
  - Quick Actions
  - About Validation
- Severity chips and icons must be visible.

## Filters Side Panel

Used by Classrooms and Validation screens:

- Right drawer overlay, width about 360 px.
- Header: Filters, Reset, close X.
- Form controls stacked with 16-24 px section spacing.
- Status checkboxes with colored dots.
- Footer fixed bottom: Cancel, primary Apply Filters with active count chip.
- Drawer must not mutate DB; only updates view filters.

## Add Position Dialog

Layout:

- Modal width around 760-840 px.
- Header: Add Position, subtitle, close X.
- Two-column top form:
  - School
  - Classroom
  - Position Type
  - Position Label / Name
  - Initial Status
  - Status Definitions info card
- Status-specific sections:
  - Need Now
  - Don’t Need Now
  - Coming
  - Filled
- Only selected status section is visually active; other sections remain lower emphasis or collapsed if needed for height.
- Footer: Cancel, primary Add Position.
- DB mutation must go through service/store API, not UI-owned SQL.

## PySide Implementation Rules

- Prefer composed `QFrame` card widgets over heavy native `QTableWidget` styling where table cells need chips/buttons.
- Use `QTableView/QAbstractTableModel` for dense data once visual shell stabilizes; `QTableWidget` acceptable only with custom cell widgets and fixed row heights.
- Every mockup screen must have object names for critical elements so tests can assert structure.
- Every visually important component must have stable min/max dimensions; no dynamic label causing row/card resize.
- Set selected item text colors explicitly; current bug: selected classroom text becomes low-contrast.
- Avoid native scrollbars inside cards where mockup has clean panels; use app-styled scrollbars.
- Do not show giant empty table bodies unless mockup does; set min heights and stretch policies to preserve proportions.
- Do not invent person PII. Blank DB fields render as `-`.
- Do not add UI-only transition logic. Dialog submit calls `StaffingService`.

## Visual Test Gates Before Coding Next Mockup

For each screen:

1. Write/adjust a focused PySide test that asserts visible structure, object names, and DB-backed values.
2. Add a screenshot smoke helper or manual smoke checklist for:
   - 1600x900 desktop
   - selected row/card state
   - modal/drawer open state if applicable
3. Compare against matching mockup:
   - shell/sidebar matches
   - header/filter/action row matches
   - cards/chips match colors and proportions
   - table/drawer/dialog density matches
   - no unreadable selected text
   - no obviously wrong metric values
4. Run:
   - focused `staffing_v2` PySide tests
   - focused staffing store/service tests
   - contract review if contracts changed

## Current Remediation Baseline

These are now baseline requirements for Staffing v2 and future app redesigns:

1. Use the dedicated v2 shell/sidebar when the `Staffing v2` app nav item is selected; hide the legacy app sidebar for that surface.
2. Keep real app identity; never copy generated `Admin Studio` branding.
3. Render screen pages through the v2 sidebar stack, not content-local horizontal tabs.
4. Use compact main-dashboard summary chips, fixed classroom/detail proportions, constrained table height, dashed Add Position drop zone, and readable selected classroom list rows.
5. Normalize staffing metrics/date display so seed epoch/default dates never show impossible values like `20639.0`.
6. Before each future mockup slice, update this spec or create a screen-specific section, then write a focused PySide test before coding.

Recommended follow-up: extract the shared QSS/style helpers from `src/staffing_dashboard_v2.py` into a reusable v2 design module when the next non-staffing app surface starts adopting this visual system.
