# Onboarding Usability Test Tasks

## Session Conditions

- Run in a non-production environment with seeded synthetic records.
- Ask participants to think aloud and narrate confidence after each task.
- Capture completion status, time-on-task, and moderator interventions.

## Task 1 — Add Employee
**Goal:** Verify users can create a new onboarding employee record without assistance.

**Prompt:**
> "Add a new employee named `Alex Rivera` with a start date next Monday and save the record."

**Success signals:**

- Participant reaches employee creation flow quickly.
- Required fields are completed and saved without blocking errors.
- Participant can confirm the new record appears in the onboarding list.

---

## Task 2 — Filter Urgent
**Goal:** Verify users can isolate urgent onboarding tasks.

**Prompt:**
> "Show only urgent onboarding items that need action today."

**Success signals:**

- Participant finds urgency filter without moderator guidance.
- Filtered list excludes non-urgent items.
- Participant can explain why remaining items are urgent.

---

## Task 3 — Dry-Run Reminders

**Goal:** Verify users can run a safe reminder preview/dry-run.

**Prompt:**
> "Run reminders in preview mode so nothing is actually sent, then tell me what you would review before sending."

**Success signals:**

- Participant identifies and uses dry-run mode.
- Participant verifies dry-run output (recipient count, warnings, skipped items).
- Participant states that no actual emails were sent.

---

## Task 4 — Live-Run Reminders

**Goal:** Verify users can execute live reminder sending when ready.

**Prompt:**
> "Now send reminders for real using the same dataset."

**Success signals:**

- Participant intentionally switches from dry-run to live mode.
- Participant confirms send completion and interprets post-send status.
- Participant can identify failed/skipped recipients if any.

---

## Task 5 — Fix Invalid Email Setting

**Goal:** Verify users can resolve a blocked send caused by invalid email configuration.

**Prompt:**
> "A reminder run is blocked due to an invalid sender email setting. Fix it and retry successfully."

**Success signals:**

- Participant locates relevant setting quickly.
- Participant corrects invalid email format and saves.
- Participant reruns flow and clears validation block.
