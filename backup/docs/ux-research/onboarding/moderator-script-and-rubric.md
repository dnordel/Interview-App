# Moderator Script and Success/Failure Rubric

## Moderator Script (35 Minutes)

### 0:00-4:00 — Welcome and Consent

- Thank participant and explain this is a workflow test, not a performance evaluation.
- Confirm consent for notes/recording.
- Reiterate synthetic dataset policy and no real employee details.

### 4:00-7:00 — Warm-up

- Ask: "How do you currently track onboarding follow-ups?"
- Ask: "What is usually most error-prone when sending reminders?"

### 7:00-27:00 — Task Execution

- Run the five tasks in order.
- Use neutral prompts only:
  - "What would you do next?"
  - "What are you expecting to happen?"
  - "How would you know this worked?"
- Allow one hint after 90 seconds of no progress.

### 27:00-32:00 — Reflection

- Ask for top confusion point.
- Ask for confidence rating (1-5) for performing onboarding reminders independently.
- Ask what UI feedback or guardrail felt most/least helpful.

### 32:00-35:00 — Close

- Confirm final comments.
- Explain anonymization and retention handling.
- Thank participant.

## Success/Failure Rubric

| Task | Success | Partial Success | Failure | Critical Failure Trigger |
| --- | --- | --- | --- | --- |
| Add employee | Completes without hints; record visible and valid | Completes with 1 hint or minor field correction | Cannot complete or creates unusable record | Saves record with wrong person data and does not detect issue |
| Filter urgent | Applies correct filter and validates results | Applies filter but misinterprets one item | Cannot find or apply filter | Uses wrong list state and takes irreversible action on non-urgent item |
| Dry-run reminders | Executes dry-run and explicitly confirms "no send" | Executes dry-run but uncertain about send safety | Performs wrong action or aborts due to confusion | Accidentally sends live reminders when asked for dry-run |
| Live-run reminders | Switches to live mode and confirms send outcome | Sends but cannot interpret mixed success/errors | Cannot complete send or stops after non-critical error | Sends without recognizing high-risk warning block |
| Fix invalid email setting | Finds setting, fixes format, reruns successfully | Fixes with moderator hint or multiple retries | Cannot find setting or cannot clear validation | Disables guardrail or bypasses validation unsafely |

## Session Scoring

- **Task completion score:** Success = 2, Partial = 1, Failure = 0.
- **Escalation:** Any critical failure is logged as Severity: Critical regardless of completion score.
- **Confidence capture:** Collect self-reported confidence after each task and at session end.
