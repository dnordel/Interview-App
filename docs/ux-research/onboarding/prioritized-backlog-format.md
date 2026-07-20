# Prioritized Backlog Output Format

Use this format after each research round to convert findings into implementation-ready backlog items.

## Prioritization Model

- **Priority score = Severity weight + Frequency weight + Reach weight + Risk weight**
- Use this scoring model consistently for every row in intake and planning docs.

| Dimension | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| Severity | Cosmetic annoyance | Minor friction | Moderate task slowdown | Major task failure risk | Critical failure / unsafe outcome |
| Frequency | Rare edge case | Occasional | Common | Very common | Near-universal |
| Reach | Niche role/path | Small subset | Meaningful segment | Most users in target flow | Broad/cross-workflow impact |
| Risk | Low downside | Contained rework | Repeated support burden | Compliance/reliability concern | Security, trust, or high-cost operational risk |

- **Priority score example:** Severity 4 + Frequency 3 + Reach 4 + Risk 2 = **13**.

## Backlog Table Template

| Backlog ID | Finding ID | Decision ID | Issue ID | Priority | User impact summary | Repro steps ref | Proposed change | Code location(s) | Owner | Implementation status | Target release | Verification artifacts (test notes / telemetry query / PR) | Review checkpoint (date + reviewer) | Closure criteria tied to success metric(s) |
| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ONB-B001 | ONB-F001 | ONB-D001 | ONB-I004 | 14 | Users cannot locate dry-run toggle and fear accidental sends | ONB-F001 steps | Add explicit mode selector with persistent state badge | `src/onboarding_operations.py` | Eng | Planned | 2026.01 | TN-ONB-003; `ux.onboarding.dry_run_toggle_usage`; PR #145 | 2025-12-08, UX + Eng reviewer | Dry-run toggle discovery ≥90% and accidental live-send attempts ≤2% |

## Required Traceability Fields

- Every backlog item must include linked **Decision ID**, **Issue ID**, and **Backlog ID**.
- Each validated finding must be converted to a backlog row using this exact table schema.
- Every converted row must include: **Finding ID**, **repro steps reference**, **proposed change**, **module-level code location(s)**, **owner**, and **implementation status**.
- Track both **Implementation status** and **Target release** for delivery planning.
- Record **Verification artifacts** as a triad: test notes reference, telemetry dashboard/query reference, and PR link/number.
- Capture a **Review checkpoint** with date and named reviewer.
- Define **Closure criteria** mapped directly to one or more documented success metrics.

## Code Location Mapping Guide

| Finding Type | Primary Modules |
| --- | --- |
| Add employee flow confusion | `src/onboarding_operations.py` |
| Urgent filter discoverability/accuracy | `src/onboarding_operations.py` |
| Dry-run reminder clarity | `src/onboarding_operations.py` |
| Live-run reminder completion/errors | `src/onboarding_operations.py` |
| Invalid sender email recovery | `src/email_security.py`, `src/onboarding_operations.py` |

## Backlog Entry Notes

- Always reference finding, decision, and issue IDs with reproducible steps.
- Keep one problem statement per backlog item.
- Keep implementation status, target release, and review checkpoint current at every grooming cycle.
- Include closure criteria tied to defined success metrics and verify with linked artifacts before marking closed.
- Keep active implementation execution detail in `docs/backlog/UI_UX_BACKLOG.md`; keep summary docs recommendation-focused.
