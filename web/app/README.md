# Preschool Interview Web App

Static, dependency-free web migration shell for the full desktop workflow.

Run from repository root:

```powershell
python -m http.server 8765
```

Open:

```text
http://localhost:8765/web/app/
```

Current migration coverage:

- Start screen
- Candidate setup
- Interview flow for custom and scored questions
- Onboarding overview
- Settings overview
- Question editor preview
- Interview history
- Review score preview
- Per-question browser audio capture and backend audio file save

Safety and rollout notes:

- PySide is the supported desktop entry point.
- No launch scripts are changed by this web migration shell.
- Candidate/interview data stays in browser memory until explicitly exported.
- The web app reads local JSON config when served statically; interview history is loaded from the optional local backend using the SQLite history store.
- The optional local backend can save drafts, save explicit per-question browser audio recordings, edit question/offer settings, update offer status, calculate a score preview, generate a DOCX interview report, write a JSON integration export, and build a director referral packet preview.
- Reminder sends, director referral posting/email delivery, and audio transcription remain PySide desktop-only.
