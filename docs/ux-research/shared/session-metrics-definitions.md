# Shared Session Metrics Definitions

These metric definitions are the common baseline across research domains.

## Primary Metrics

### Task Completion Rate

- **Definition:** Percentage of participants who complete a task without moderator intervention.
- **Formula:** `completed_without_help / total_participants`
- **Required cuts:** Per task, per participant segment, and overall.

### Time-on-Task

- **Definition:** Elapsed time from task start prompt to successful completion.
- **Required reporting:** Median and p75 per task.
- **Interpretation:** Elevated values indicate friction, low discoverability, or high cognitive load.

### Critical Error Count

- **Definition:** Count of errors that block progress, create incorrect outcomes, or require moderator rescue.
- **Required reporting:** Mean and distribution per task and per session.

## Secondary Metrics

- **Task confidence rating:** 1-5 self-reported confidence after each task.
- **Recovery success rate:** Proportion of sessions where participant recovers after first error.
- **Backtrack count:** Number of navigation reversals or repeated attempts.

## Exit Criteria Template

- **Completion threshold:** At least 90% on core tasks.
- **Critical error threshold:** At most 1 critical error per participant across core tasks.
- **Efficiency threshold:** Round-over-round reduction in median time-on-task for target tasks.

## Measurement Integrity Rules

- Use standardized task boundaries from the domain task script.
- Keep timing capture method consistent across rounds.
- Do not change metric formulas inside domain overlays; only add domain-specific targets.
