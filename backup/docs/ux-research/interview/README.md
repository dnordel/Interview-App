# Interview UX Research Artifacts

This folder contains interview-domain overlays and task artifacts for moderated usability sessions focused on interview start, competency scoring, and draft finalization workflows.

## Local Index

- Canonical docs index: [`/docs/README.md`](../../README.md)
- Shared template layer: [`/docs/ux-research/shared/`](../shared/)

### Ownership and Update Expectations

- Primary owner: maintainers responsible for interview UX research operations.
- Update this README when files are added, removed, renamed, or repurposed.
- Keep interview-study documents aligned with telemetry, privacy, and synthesis conventions in the canonical docs hub.

## Inheritance Map (Shared vs Domain-Specific)

### Inherited Common Sections

- Findings record core fields are inherited from [`shared/findings-record-template.md`](../shared/findings-record-template.md).
- Privacy/data-handling baseline checklist is inherited from [`shared/privacy-data-handling-checklist.md`](../shared/privacy-data-handling-checklist.md).
- Session metrics definitions and formulas are inherited from [`shared/session-metrics-definitions.md`](../shared/session-metrics-definitions.md).

### Interview-Specific Overlays

- `findings-template.md`: interview-only ID prefix, workflow tags, and task mapping.
- `privacy-protocol.md`: interview-only data constraints and telemetry scoping.
- `success-metrics.md`: interview-only task scope and critical-error qualifiers.
- `discussion-guide.md`, `participant-matrix-and-tasks.md`, `telemetry-mapping.md`: fully interview-domain content.

## Traceability Workflow

1. Capture each finding in `findings-template.md` with full linked IDs (`INT-F###`, `INT-D###`, `INT-I###`, `INT-B###`).
2. Convert validated findings into backlog items and set implementation status + target release.
3. Attach verification artifacts for each change: test notes, telemetry dashboard/query, and PR link.
4. Run a review checkpoint (date + reviewer) before marking work as shipped or closed.
5. Mark items closed only when closure criteria are met against interview success metrics in `success-metrics.md`.

## Change Rule

- **Rule:** Update common sections in shared templates first.
- Then sync or adjust interview overlays to reflect domain-specific additions only.
- Do not duplicate shared baseline text into interview overlays except brief references.

## Contents

- `discussion-guide.md`
- `participant-matrix-and-tasks.md`
- `findings-template.md` (overlay; legacy filename retained)
- `success-metrics.md` (overlay; legacy filename retained)
- `privacy-protocol.md` (overlay; legacy filename retained)
- `telemetry-mapping.md`
