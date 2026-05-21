# Interview Findings Overlay (Legacy Filename Preserved)

> **Deprecation notice:** This file is no longer a standalone template. It is a domain overlay on top of the shared findings template to preserve existing links.

## Inherited from Shared Template
Use all common sections and fields from:
- [`docs/ux-research/shared/findings-record-template.md`](../shared/findings-record-template.md)

## Interview-Specific Additions
- **Finding ID prefix:** `INT-F###`
- **Traceability IDs:**
  - Decision ID: `INT-D###`
  - Linked issue ID: `INT-I###`
  - Linked backlog ID: `INT-B###`
- **Implementation tracking:**
  - Implementation status: Proposed / Planned / In progress / Shipped / Blocked
  - Target release: `YYYY.MM` (or sprint identifier)
- **Verification artifacts:**
  - Test notes reference
  - Telemetry dashboard/query reference
  - Pull request reference
- **Review checkpoint:**
  - Review date
  - Reviewer
- **Closure criteria:**
  - Define closure condition explicitly against one or more success metrics from [`success-metrics.md`](./success-metrics.md)
- **Participant segment values:** New interviewer / Intermediate interviewer
- **Interview workflow location tags:**
  - Session start
  - Competency scoring
  - Draft finalization
- **Interview task references:**
  - Start interview session
  - Find competency rubric guidance
  - Score competency #1
  - Score competency #2
  - Finalize draft summary
- **Telemetry linkage scope:** `ux.interview.*` events only

## Interview Example (Abbreviated)
- **Finding ID:** INT-F014
- **Decision/Issue/Backlog IDs:** INT-D004 / INT-I022 / INT-B011
- **Severity:** High
- **Evidence:** 4/5 new interviewers could not locate rubric detail without moderator prompt; median delay 48s.
- **Recommendation:** Add inline rubric summary card beside competency scoring controls.
- **Implementation status + target release:** In progress, `2026.02`
- **Verification artifacts:** TN-INT-018, `ux.interview.rubric_discoverability` dashboard query, PR #142
- **Review checkpoint:** 2026-01-18, Research lead
- **Closure criteria:** Median rubric-locate time ≤15s and completion-without-prompt ≥90% in next interview round.
- **Affected workflow location:** Competency scoring panel (`score_competency` step)
