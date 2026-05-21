# Manual QA: UX-B005 Validation Feedback Standardization

## Scope
- `src/ui_feedback.py`
- `src/question_screens.py`
- `src/onboarding_app.pyw`

## Checks
1. **Recoverable field validation appears inline with focus transfer**
   - Add Employee dialog: leave Name empty, click Save.
   - Verify inline message uses cause + corrective action format and keyboard focus moves to Name.
2. **Date formatting errors are anchored to specific controls**
   - Add Employee dialog: enter malformed Acceptance date.
   - Verify inline message and focus move to Acceptance date field.
3. **Custom template integer validation is inline**
   - Add Custom Task Template dialog: enter non-integer offset.
   - Verify inline message and focus move to Offset field.
4. **Email settings validation is inline**
   - Enter non-integer SMTP port and save.
   - Verify inline message appears in settings dialog and no modal error appears.
5. **System failures remain modal and hide raw exception payloads**
   - Simulate a recording transition failure on interview question screens.
   - Verify a blocking dialog appears with user-safe copy and optional technical details affordance.

## Expected outcome
- Recoverable user-input issues are inline and anchored to the related field.
- Blocking/system failures use modal dialogs.
- User-facing error copy is normalized to short cause + corrective action format.
