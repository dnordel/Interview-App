# Onboarding Privacy/Data-Handling Overlay (Legacy Filename Preserved)

> **Deprecation notice:** This file is no longer a standalone guardrail document. It is a domain overlay on top of the shared privacy checklist to preserve existing links.

## Inherited from Shared Template
Use all checklist items and non-negotiable rules from:
- [`docs/ux-research/shared/privacy-data-handling-checklist.md`](../shared/privacy-data-handling-checklist.md)

## Onboarding-Specific Requirements
- Use synthetic employee identities and reserved fake domains (for example `example.com`) only.
- Never connect reminder tests to production SMTP or live HRIS systems.
- Include sender-email validation scenarios without exposing real mailboxes.
- Keep redaction log entries for accidental sensitive content with timestamp and owner initials.
- Ensure `ux.onboarding.*` payloads exclude names, email addresses, free text, and secrets.
