# PySide Mockup-Driven Redesign Reference Spec

Use this document before coding any future PySide UI redesign. It defines the app-wide visual and workflow standard for translating supplied mockups into native PySide screens. Screen-specific specs, such as `docs/staffing_v2_style_spec.md`, may add details, but they must not weaken this reference.

## Core Rule

Do not start coding a redesigned screen from generic Qt widgets alone. First create or update a screen reference spec from the mockup:

- identify real product elements versus mockup-only artifacts;
- map shell, navigation, content regions, cards, tables, drawers, dialogs, and forms;
- extract colors, typography, spacing, control sizes, chip styles, row heights, and icon usage;
- identify data shown by the mockup and the service/store source for each value;
- define object names for testable widgets;
- define which actions are placeholders, disabled, or service-backed;
- define manual visual smoke points for the exact mockup state.

## Product Identity

Mockups may contain generated names, logos, or brands that are not part of this app. Treat those as artifacts unless user explicitly says otherwise.

For current app:

- keep actual product identity: Interview Assistant / Launch Pad Learning as applicable;
- do not copy generated `Admin Studio` branding;
- copy layout pattern, density, visual system, navigation structure, cards, chips, and workflows.

## Fidelity Target

PySide cannot guarantee perfect pixel parity across Windows DPI, native font rendering, and platform widget metrics. Target:

- structural parity: same regions, order, hierarchy, and action placement;
- visual parity: same colors, radii, spacing, typography scale, row heights, chips, and button hierarchy within DPI variance;
- behavior parity: same visible controls and state transitions;
- data parity: values come from existing DB/services, not hardcoded mockup rows;
- safety parity: mutations go through existing service layer, never table-cell edits or UI-owned SQL.

If mockup match is poor, create a dedicated shell or component layer instead of forcing the old app shell to carry the new visual design.

## App-Wide Visual Tokens

Use shared tokens instead of per-screen ad hoc QSS.

Colors:

- app background: `#f8fafc`
- surface/card: `#ffffff`
- subtle surface: `#f1f5f9`
- border: `#e2e8f0`
- strong border: `#cbd5e1`
- text strong/navy: `#0f172a`
- text body: `#334155`
- text secondary: `#475569`
- text muted: `#64748b`
- primary blue: `#2563eb`
- primary blue hover: `#1d4ed8`
- active nav/row bg: `#eaf2ff` / `#eff6ff`
- danger red text: `#dc2626`
- danger bg: `#fee2e2`
- warning orange text: `#ea580c`
- warning bg: `#ffedd5`
- coming yellow text: `#b45309`
- coming yellow bg: `#fef3c7`
- success green text: `#15803d`
- success green bg: `#dcfce7`
- neutral text: `#475569`
- neutral bg: `#f1f5f9`
- info bg: `#dbeafe`

Typography:

- family: Segoe UI, Inter, Arial;
- page title: 24-26 px, weight 800, navy;
- page subtitle: 13-14 px, regular, secondary;
- section title: 17-18 px, weight 800;
- card label: 12-13 px, weight 500-600, secondary;
- metric value: 20-24 px, weight 800;
- table header: 12-13 px, weight 700;
- body text: 13 px;
- helper text: 11-12 px, muted.

Geometry:

- left shell width: 240-260 px when mockup has app/sidebar nav;
- right drawer width: 420-620 px depending screen;
- main content outer margins: 24 px left/right, 18-24 px top;
- region gap: 14-16 px;
- control height: 40 px;
- compact chip height: 26-34 px;
- card radius: 8 px for repeated cards, 10-12 px for large panels;
- dialog radius: 12-14 px;
- table header height: 42-46 px;
- dense table row: 48-58 px;
- dashboard table row: 58-70 px.

## Shell Rules

When mockups use a modern admin shell:

- build a matching in-page or app-wide shell instead of using old generic app navigation;
- sidebar background is white with subtle right border;
- active nav row uses pale blue fill, blue text, optional blue left rail;
- nav items use icon + text, not plain text-only rows;
- bottom cards may show environment and user context if available;
- hide or bypass old shell only for the new v2 surface until final cutover.

Do not add horizontal tab bars where mockup uses left sidebar navigation.

## Component Rules

Cards:

- use `QFrame` with object names and QSS, not default `QGroupBox`;
- fixed or bounded min heights to avoid layout drift;
- no nested decorative cards unless mockup explicitly shows a nested panel.

Buttons:

- primary action: blue filled, 40 px high, 8 px radius;
- secondary action: white, border, navy text;
- destructive action: red text/border or red filled only for final confirmation;
- icon buttons must have stable square dimensions.

Inputs:

- 40 px high;
- label above control when mockup shows labels;
- search fields include search icon or icon reserve;
- combo boxes use consistent padding and border.

Chips:

- chips are widgets or styled labels, not raw colored table cells;
- status chips include icon when mockup has icon;
- text color must contrast with chip bg.

Tables:

- hide native rough edges where possible;
- set fixed row/header heights;
- use custom cell widgets for chips and row actions;
- table cells are read-only for workflow state;
- action column opens dialogs/drawers or calls service-backed handlers.

Lists:

- selected item must keep readable navy text;
- use status dot, title, metadata row, chevron when mockup does;
- avoid visible raw native scrollbars inside card unless necessary.

Drawers:

- right side, fixed width, own header and footer;
- main canvas may dim if mockup shows overlay;
- footer actions remain stable at bottom.

Dialogs:

- modal body matches mockup sections;
- close X at top right;
- warning/info banners styled;
- final submit calls service only;
- Save Draft buttons may be disabled/placeholders until draft behavior exists, but must be visibly safe.

Icons:

- use one shared icon helper;
- nav icons around 18 px;
- metric icons 18-20 px;
- chip/action icons 14-16 px;
- temporary text fallbacks are allowed only before visual QA.

## Data and Mutation Rules

- Existing SQLite DBs/services remain source of truth.
- UI never owns staffing/interview/onboarding state transitions.
- No raw status edits in table cells.
- No hardcoded mockup PII.
- Blank DB fields render as `-`.
- Existing local privacy assumptions remain: no new network behavior, logging, or telemetry.
- Any mutation button must route through service/store API already used by current workflows or a new tested service API.

## Mockup Analysis Template

Create this section in each screen-specific spec before implementation:

```text
Screen:
Mockup file:
Real product artifacts to keep:
Generated mockup artifacts to ignore:

Shell:
Header:
Filters/actions:
Metric cards/chips:
Primary content:
Tables/lists:
Drawer/dialog states:
Footer/status areas:

Data sources:
Actions:
Disabled/placeholders:
Object names:
Visual smoke checklist:
```

## TDD Workflow

For each screen or modal:

1. Write one focused PySide test for visible structure and DB-backed values.
2. Implement the smallest vertical slice.
3. Run focused test.
4. Add next behavior test for dialog/drawer/action.
5. Implement.
6. Run focused tests again.
7. Update contracts for public classes/functions/modules.
8. Regenerate contract matrix if contracts changed.
9. Run contract review.
10. Ask for manual smoke or next mockup refresh once the slice is visually ready.

Docs-only spec changes do not require tests.

## Visual Smoke Checklist

Before handoff or next mockup:

- screen matches mockup shell and major proportions;
- header/filter/action row placement matches;
- cards/chips use correct colors, sizes, and spacing;
- table/list rows have mockup density;
- selected state text is readable;
- drawers/dialogs match section order and footer actions;
- no impossible seeded values appear in metrics;
- no old generic shell widgets leak into the v2 design unless intentionally retained;
- mutation actions are confirmation-based and service-backed.

## Common Failure Modes

- Old shell remains visible beside new shell.
- Native `QListWidget` selected text becomes white on pale background.
- Metric cards are too large where mockup uses compact pills.
- Empty table stretches and dominates screen.
- Icons missing, replaced with text-only controls.
- Mockup generated brand copied as real product identity.
- Mockup values hardcoded instead of read from DB/service.
- Dialog Save/Submit mutates directly from UI code.
- Contracts not updated after adding public modules/classes/functions.

## Current Reference Screens

Use `docs/staffing_v2_style_spec.md` as first concrete application of this reference. It contains extracted requirements for:

- Staffing Dashboard;
- Position Detail Drawer;
- Mark Coming;
- Mark Filled;
- Manage Filled Position;
- Update Permit;
- Mark Need Now;
- People / Employee Management;
- Assignment History;
- Classroom Management;
- Staffing Validation;
- Filters Side Panel;
- Add Position.
