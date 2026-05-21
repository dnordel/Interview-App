# Shared Findings Record Template

Use one entry per finding across UX research domains.

## Required Core Fields

- **Finding ID:** Domain-specific prefix + sequence (for example, `INT-F001`, `ONB-F001`)
- **Date observed:**
- **Research round:**
- **Participant segment(s):**
- **Related task(s):**
- **Severity:** Critical / High / Medium / Low
- **Severity rationale:**
  - User impact:
  - Frequency:
  - Business/compliance risk:

- **Evidence:**
  - Observed behavior:
  - Participant quote(s):
  - Task metric impact (completion/time/error):

- **Recommendation:**
  - Proposed change:
  - Expected impact on session metrics:

- **Affected workflow location:**
  - Screen/step:
  - UI element(s):

## Optional Fields

- **Frequency count:** Number of participants impacted
- **Likely root cause:** Terminology / hierarchy / feedback / control discoverability
- **Dependencies:** Engineering/design/content dependencies
- **Telemetry linkage:** Event(s) to instrument or analyze
- **Owner:**
- **Target milestone:**

## Security Considerations

- Do not include PII, secrets, or production data in findings evidence.
- Use pseudonymous participant IDs only.
- If accidental sensitive data appears, redact immediately and log redaction action.
