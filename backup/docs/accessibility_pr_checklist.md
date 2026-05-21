# Accessibility PR Checklist (Mandatory for UX Changes)

Use this checklist for every pull request that changes a user-facing flow, screen, dialog, or interaction.

## Required checks

Mark each item pass/fail and include evidence notes in your PR description.

- [ ] **Tab order is logical and complete**
  - Confirm focus moves in an expected sequence across actionable controls.
  - Confirm no interactive element is skipped.
- [ ] **Focus visibility is clear**
  - Confirm the currently focused element has an obvious visible indicator.
  - Confirm visibility in default and high-contrast themes (where supported).
- [ ] **Color contrast is sufficient**
  - Confirm text and interactive controls meet project contrast standards.
  - Confirm status badges/labels are readable against their backgrounds.
- [ ] **Status cues are not color-only**
  - Confirm all status states also use text, iconography, or shape (not color alone).
- [ ] **Keyboard-only completion works end-to-end**
  - Confirm primary task paths can be completed without a mouse.
  - Confirm submit/save/finalize actions are reachable and operable by keyboard.

## PR description requirements

Each UX PR must include:

1. A completed copy of the **Required checks** section above.
2. A brief summary of any known accessibility gaps accepted for follow-up.
3. A link to the manual QA screen verification artifact (`docs/manual_qa_screen_template.md`).

## Telemetry recommendation (keyboard-path outcomes)

To track real-world accessibility outcomes, UX changes should include telemetry planning for keyboard behavior:

- Recommended event: `ux.keyboard_path_completed`
- Suggested properties:
  - `screen_id`
  - `flow_id`
  - `completed_via_keyboard` (boolean)
  - `keyboard_step_count`
  - `abandoned` (boolean)

Use aggregated reporting to evaluate where keyboard-only users complete flows successfully vs. abandon.
