# Telemetry Mapping for UX Findings (`ux_metrics`)

Canonical spec reference: [`docs/UX_EVENT_NAMING.md`](../../UX_EVENT_NAMING.md).

This mapping links observed usability findings to candidate instrumentation events for follow-up implementation PRs.

## Event Design Principles

- Emit events at key workflow transitions and high-risk interaction points.
- Keep payloads behavior-focused; exclude personal or candidate-identifying data.
- Include `participant_segment` only for research/test contexts where allowed.

## Proposed Event Map

| Workflow Step | Canonical Event Name (`ux_metrics`) | Trigger | Key Properties |
| --- | --- | --- | --- |
| Start interview session | `ux.interview.session_start.click` | User initiates interview | `entry_point`, `has_template`, `timestamp_ms` |
| Session initialized | `ux.interview.session_start.completion` | Session load succeeds | `load_ms`, `resume_state` |
| Rubric viewed | `ux.interview.rubric_guidance.view` | Rubric help/panel opened | `competency_id`, `time_from_task_start_ms` |
| Competency score submitted | `ux.interview.competency_scoring.completion` | Score saved for competency | `competency_id`, `score_value`, `edit_count`, `time_on_task_ms` |
| Scoring validation error | `ux.interview.competency_scoring.validation_error` | Blocking validation or save error | `competency_id`, `error_type`, `is_critical` |
| Draft finalize clicked | `ux.interview.finalize.click` | Finalize action initiated | `completion_state`, `missing_fields_count` |
| Draft finalized | `ux.interview.finalize.completion` | Finalization succeeds | `time_on_task_ms`, `critical_errors_before_finalize` |
| Finalize error | `ux.interview.finalize.validation_error` | Finalization fails or blocked | `error_type`, `is_critical` |

## Finding-to-Event Traceability

| Finding Type | Metric Impact | Events to Analyze |
| --- | --- | --- |
| Cannot find how to start interview | Completion rate, time-on-task | `ux.interview.session_start.click`, `ux.interview.session_start.completion` |
| Rubric unclear or undiscoverable | Time-on-task, error count | `ux.interview.rubric_guidance.view`, `ux.interview.competency_scoring.validation_error` |
| Difficulty assigning competency scores | Completion rate, critical errors | `ux.interview.competency_scoring.completion`, `ux.interview.competency_scoring.validation_error` |
| Finalization confusion/failure | Completion rate, critical errors | `ux.interview.finalize.click`, `ux.interview.finalize.completion`, `ux.interview.finalize.validation_error` |

## Implementation Notes for Follow-up PRs

- Add event emission at UI action boundaries in scoring and finalization flows.
- Add aggregation queries to compute completion rate, median time-on-task, and critical error count.
- Validate payload schema against [`docs/UX_EVENT_NAMING.md`](../../UX_EVENT_NAMING.md) and the privacy protocol before release.

## Migration Note (Legacy Names)

| Legacy Name | Canonical Name |
| --- | --- |
| `interview_start_clicked` | `ux.interview.session_start.click` |
| `interview_session_started` | `ux.interview.session_start.completion` |
| `rubric_guidance_viewed` | `ux.interview.rubric_guidance.view` |
| `competency_scored` | `ux.interview.competency_scoring.completion` |
| `competency_score_error` | `ux.interview.competency_scoring.validation_error` |
| `draft_finalize_clicked` | `ux.interview.finalize.click` |
| `draft_finalized` | `ux.interview.finalize.completion` |
| `draft_finalize_error` | `ux.interview.finalize.validation_error` |
