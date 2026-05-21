# Shared Privacy and Data-Handling Checklist

Use this checklist before, during, and after any UX research session.

## Pre-Session Checklist

- [ ] Test data is fully synthetic (no real names, emails, phone numbers, IDs, or notes).
- [ ] Consent language includes recording, retention, and anonymization details.
- [ ] Moderator materials use participant IDs (for example `P01`) rather than names.
- [ ] Telemetry/event payloads are checked against allowed schema fields.
- [ ] Storage location for notes/recordings is approved and access-restricted.

## In-Session Checklist

- [ ] Confirm consent before recording starts.
- [ ] Capture only minimally necessary data for research goals.
- [ ] Avoid copying free text that could contain sensitive or identifying details.
- [ ] Pause and redact immediately if accidental sensitive data appears.

## Post-Session Checklist

- [ ] Verify notes and exports contain only pseudonymous participant identifiers.
- [ ] Restrict raw artifacts to approved contributors only.
- [ ] Log redactions and data-handling incidents with timestamp and owner.
- [ ] Apply retention/deletion schedule for raw recordings and notes.
- [ ] Publish only anonymized summaries for long-term reference.

## Non-Negotiable Rules

- Never send PII, secrets, or free-form sensitive text in telemetry.
- Never use production account data for usability testing.
- Prefer aggregate reporting over participant-level detail whenever possible.
