# Preschool Teacher Interview Guide

A structured, offline desktop application for running preschool teacher interviews with consistent scoring, evidence capture, and report generation.

This repository includes:

- The primary interview workflow desktop app.
- Optional audio recording/transcription support.
- A companion onboarding task tracker desktop app.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Audience](#audience)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Application Workflows](#application-workflows)
  - [Interview App Workflow](#interview-app-workflow)
  - [Onboarding App Workflow](#onboarding-app-workflow)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Settings Window Flow](#settings-window-flow)
- [Configuration and Data Files](#configuration-and-data-files)
- [Output Locations](#output-locations)
- [Security and Privacy Considerations](#security-and-privacy-considerations)
- [Troubleshooting FAQ](#troubleshooting-faq)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [Documentation Hub](#documentation-hub)
- [Release Notes](#release-notes)
- [Compatibility Notes](#compatibility-notes)
- [Developer Validation Checklist](#developer-validation-checklist)
- [Accessibility Governance for UX Pull Requests](#accessibility-governance-for-ux-pull-requests)
- [Release Readiness Security Checklist](#release-readiness-security-checklist)

---

## Overview

The Preschool Teacher Interview Guide is designed to standardize interviewing and documentation. It helps interviewers:

- Follow a consistent, track-based interview sequence.
- Capture candidate evidence and scores per competency.
- Apply weighted scoring and recommendation thresholds.
- Save draft interviews and finalize to Word reports.

The tool is optimized for local/offline usage and can run without optional audio dependencies.

## Key Features

### Interview App

- Guided interview flow from candidate setup to final recommendation.
- Support for scored competencies and custom questions.
- Draft save/load for interview pause and resume.
- Weighted scoring and final outcome calculation.
- DOCX export for interview records.
- Optional audio recording and transcription support when dependencies are installed.

### Onboarding App

- Template-driven onboarding checklist management.
- Employee start/acceptance date-based task scheduling.
- Per-task reminder cadence options.
- Reminder and escalation email settings.
- JSON-based storage suitable for local or synced folders.

## Audience

This project is intended for:

- **Interviewers and school operations staff** who need a reliable, repeatable interview workflow.
- **Technical maintainers** who support setup, updates, and data management.

## Requirements

### Core

- Python 3.11+
- `python-docx`

### Optional (for recording + transcription)

- `soundfile`
- `faster-whisper`
- `ffmpeg` available on system `PATH`

If optional audio dependencies are unavailable, the interview app remains fully usable for manual note-taking and scoring.

### Local DeepSeek summaries

- Windows setup automatically installs Ollama when missing.
- Setup pulls the smallest local DeepSeek model used by the app: `deepseek-r1:1.5b`.
- DeepSeek summaries use Ollama at `http://127.0.0.1:11434/v1`; no DeepSeek API key is required.
- To use a larger local Ollama model for better summary/signal quality, set `DEEPSEEK_SUMMARY_MODEL`
  before launch; setup will pull/use that local model instead of overwriting it with the fallback.
- First setup on a new machine needs Internet access for Ollama and model download. After that, inference runs locally.

## Installation

From the repository root:

```bash
pip install -r requirements.txt
```

For the full Windows bootstrap, including local DeepSeek setup:

```powershell
.\setup_and_run.ps1
```

For developer/test environments, use `python-docx` and **do not** install the unrelated `docx` package (it can shadow imports and break report generation).

Optional standardized dev setup:

```bash
pip install -r requirements-dev.txt
```

## Quick Start

### Launch Interview App

**Windows (recommended for non-technical users):**

- Double-click `Start Preschool Teacher Interview Guide.bat` in the repository root.
- This starts the same setup/launch flow without needing to open PowerShell manually.
- For debugger logging/console mode, run `.\setup_and_run.ps1 -DebugMode`.

**Direct Python launch (advanced):**

```bash
pythonw src/interview_app.pyw
```

### Launch Onboarding App

```bash
pythonw src/onboarding_app.pyw
```

On first use, the left-side **Actions** area is organized by intent so daily work is front-loaded:

1. **Daily workflow** (primary: **Run Reminders Now**)
2. **Candidate management**
3. **Communications**
4. **Admin & advanced**

Each action button remains keyboard reachable in tab order and includes an inline helper description for faster recognition.

### First Interview (Generic Flow)

1. Open the app and select **New Interview**.
2. Complete candidate information (name, date, school, role track).
3. Proceed through scored competencies and custom questions.
4. Save draft at any point if needed.
5. Finalize interview to generate a DOCX report.

## Application Workflows

### Interview App Workflow

1. **Start Screen**
   - Actions: New Interview, Open Draft, Edit Questions, Settings, Exit.

2. **Candidate Information**
   - Required fields include candidate name, interview date (`YYYY-MM-DD`), school, and role track.
   - Validation blocks progression when required values are missing or malformed.

3. **Question Flow**
   - Scored competencies collect raw score and evidence notes.
   - Custom questions collect free-text responses.
   - Progress indicators help maintain interview pacing.

4. **Finalize**
   - Runs final validation.
   - Computes weighted results.
   - Generates DOCX output in the configured folder.

#### Interview Terms

- **Scored competency**: rubric-driven section used in scoring.
- **Raw score**: interviewer rating (1-5) before weighting.
- **Scoring descriptors**: definitions that anchor score quality.
- **Absolute disqualifier**: evidence-based trigger for automatic no-hire recommendation.

### Onboarding App Workflow

1. Launch onboarding tracker and start in **Daily workflow** actions.
2. Use **Run Reminders Now** for production sends, or **Run Reminders (Dry Run)** for safe verification.
3. Add employee details (including acceptance and start dates).
4. Apply default or custom onboarding task templates.
5. Mark task completion status.
6. Configure reminder cadence and recipient settings from **Communications**.
7. Use **Admin & advanced** actions only for infrequent operational changes (for example storage location).

## Keyboard Shortcuts

Interview app shortcuts:

- `Ctrl+N` or `Ctrl+Right`: Next
- `Ctrl+B` or `Ctrl+Left`: Back
- `Ctrl+S`: Save Draft
- `Ctrl+Shift+F`: Finalize (or Continue on non-final steps)
- `Ctrl+,`: Open Settings
- `Ctrl+E`: Open Question Editor
- `F1`: Shortcut help

## Settings Window Flow

The interview app **Settings** window is organized into task-oriented tabs in this traversal order:

1. `General`
2. `Templates`
3. `Notifications`
4. `Storage`
5. `Security`

Validation behavior expectations:

- Invalid template tokens appear in **Templates** with field-level remediation guidance.
- Invalid referral endpoints appear in **Notifications** and require `http://` or `https://`.
- Invalid Whisper/transcription values appear in **Security** with explicit correction guidance.
- On save failures, the first invalid tab is selected and focus moves to the first invalid field.

Role-sensitive visibility expectations for manual QA:

- Restricted roles should not be able to modify high-risk delivery or security controls.
- Admin roles should retain access to those controls and receive confirmation prompts for high-risk toggles.

## Configuration and Data Files

- `config/rubric.json`: role tracks, traits, weights, thresholds.
- `config/disqualifier_signals.json`: disqualifier cues for evaluation.
- `user_artifacts/interviews/onboarding_data.json`: onboarding records (created by onboarding app).
- `user_artifacts/interviews/onboarding_settings.json`: onboarding email/reminder settings (created by onboarding app).
- `user_artifacts/interview_history.json`: finalized interview history rows (created by interview app).
- `user_artifacts/school_offer_settings.json`: local offer-template settings (created by interview app).
- `user_artifacts/interview_app_settings.json`: local app settings, including output folder preference.

## Output Locations

Default user-artifact directory: `./user_artifacts/` (auto-created and ignored by Git).
Default output base directory: `./user_artifacts/interviews/` (auto-created).

| Output Type | Example File | Default Location |
| --- | --- | --- |
| Interview draft (JSON) | `draft-20260206-154500-Jane_Doe.json` | `./user_artifacts/interviews/drafts/` |
| Final interview report (DOCX) | `2026-02-06 - Brooklyn_Center - Jane_Doe - Interview.docx` | `./user_artifacts/interviews/Indeed Interview Notes/` |
| Optional live transcript notes (DOCX) | `Candidate_Jane_Doe_2026-02-06_live_transcript.docx` | `./user_artifacts/interviews/` |
| Optional recording/transcription artifacts | tool-generated timestamped files | `./user_artifacts/interviews/` |

For existing installs, move or copy prior generated files from `./interviews/`,
`./interview_history.json`, `./school_offer_settings.json`,
`./school_email_template_settings.json`, and `./interview_app_settings.json`
into matching paths under `./user_artifacts/` before pulling branch updates that
stop tracking generated artifacts.

## Security and Privacy Considerations

When operating this tool in production environments:

1. **Protect candidate data**
   - Interview notes, scores, and transcripts may contain sensitive personal information.
   - Store output in access-controlled directories.

2. **Use secure credential handling**
   - Do not hardcode SMTP/IMAP/POP credentials in source files.
   - Keep operational credentials outside version control.
   - `onboarding_settings.json` may contain `smtp_password`; for production deployments prefer environment variables (`ONBOARDING_SMTP_PASSWORD`, fallback `SMTP_PASSWORD`) so plaintext file storage can be avoided.

3. **Enforce approved email placeholders**
   - Email templates are rendered using context-specific placeholder allowlists (director, offer, welcome, onboarding reminders, escalation).
   - Unknown placeholders should be removed or rejected before send/draft actions so unrecognized tokens are not emitted to recipients.

4. **Limit data exposure**
   - Share reports only with authorized stakeholders.
   - Minimize copying files into unsecured cloud/shared locations.

5. **Maintain dependency hygiene**
   - Install packages from trusted sources.
   - Keep Python and dependencies up to date.

6. **Treat launch scripts as trusted entry points**
   - Run only launcher scripts that come from this repository.
   - If distributing to staff, share from a controlled location and avoid editing launcher files locally.

7. **Validate operational configuration**
   - Confirm production reminder recipient lists before sending automated emails.
   - Verify date formats and track configuration to avoid workflow errors.

8. **Implement retention and deletion policies**
   - Define how long interview drafts/reports are retained.
   - Remove outdated files according to organizational policy.

If you discover a security issue, report it privately to project maintainers instead of posting sensitive details publicly.

## Troubleshooting FAQ

### 1) "Interview Date must be valid YYYY-MM-DD."

Use strict date format such as `2026-02-06`.

### 2) App does not launch

- Confirm Python 3.11+ installation.
- Reinstall dependencies:

```bash
pip install -r requirements.txt
```

- Relaunch:

```bash
pythonw src/interview_app.pyw
```

### 3) Audio/transcription features unavailable

Install optional packages and ensure `ffmpeg` is on `PATH`:

```bash
pip install soundfile faster-whisper
```

The interview flow remains usable without these features.

### 4) "No questions configured for this track."

Open **Edit Questions** and ensure the selected role track has at least one active scored competency or custom question.

## Project Structure

- `src/interview_app.pyw`: canonical interview app entry point.
- `src/Initial Teacher Interview Guide.pyw`: legacy wrapper forwarding to `src/interview_app.pyw`.
- `src/app_content.py`: shared constants and helper content.
- `src/data_store.py`: rubric/question persistence.
- `src/reporting.py`: scoring, draft management, DOCX export.
- `src/interview_audio_recorder.py`: optional recording/transcription integration.
- `src/onboarding_app.pyw`: onboarding tracker desktop app.
- `config/rubric.json`: interview rubric configuration.
- `config/disqualifier_signals.json`: disqualifier cue definitions.

## Contributing

1. Create a feature branch.
2. Keep changes focused and documented.
3. Validate behavior locally before submitting.
4. For UX or user-facing changes, complete `docs/accessibility_pr_checklist.md` and attach manual verification from `docs/manual_qa_screen_template.md` (one entry per changed screen).
5. Follow the shared validation UX contract in `docs/VALIDATION_FEEDBACK_POLICY.md` for inline and modal error handling.
6. Submit a pull request with:
   - Problem statement
   - Scope of changes
   - Testing notes
   - Accessibility checklist results (required for UX PRs)

## Documentation Hub

The canonical entrypoint for repository documentation is [`docs/README.md`](./docs/README.md). Use it to navigate reference docs, process/checklist docs, templates, and UX research artifacts.
Documentation contribution standards are defined in [`docs/CONTRIBUTING_DOCS.md`](./docs/CONTRIBUTING_DOCS.md).

## Release Notes

- Current release communication: [`docs/release-notes.md`](./docs/release-notes.md).

## Compatibility Notes

Legacy launch name remains available for one release cycle:

- `src/Initial Teacher Interview Guide.pyw` -> forwards to `src/interview_app.pyw`

This legacy name is scheduled for removal in the following release cycle.

## Developer Validation Checklist

Before opening a pull request, run these checks from the repository root:

```bash
python tools/check_docx_environment.py
```

The check prints the resolved `docx.__file__` path and fails if it appears to come from an incompatible `docx` package instead of `python-docx`.

```bash
python -m compileall src/interview_app.pyw src/onboarding_app.pyw src/app_content.py src/data_store.py src/reporting.py
```

```bash
python -m json.tool config/rubric.json > /dev/null
python -m json.tool config/disqualifier_signals.json > /dev/null
```

```bash
python -m pip check
```

These checks confirm the Python modules compile, core JSON configuration remains valid, and dependency metadata is consistent.

## Accessibility Governance for UX Pull Requests

For pull requests that change UX, interaction behavior, or visual components:

1. Complete all mandatory checks in `docs/accessibility_pr_checklist.md`.
2. Include checklist results directly in the PR description.
3. Add manual QA coverage using `docs/manual_qa_screen_template.md` for each changed screen.
4. Include telemetry planning for keyboard-path outcomes (`ux.keyboard_path_completed`) to monitor real-world accessibility success and abandonment trends.

## Release Readiness Security Checklist

Use this checklist before distributing builds or running the tools with real candidate/employee data:

- [ ] Confirm interview and onboarding output directories use least-privilege access controls.
- [ ] Verify no SMTP/IMAP/POP or other secrets are committed to source control.
- [ ] Confirm report-sharing workflows are restricted to approved recipients only.
- [ ] Ensure Python, `python-docx`, and optional audio packages are patched to current supported versions.
- [ ] Validate reminder/escalation email targets in onboarding settings before sending automated messages.
- [ ] Define and enforce retention/deletion schedules for drafts, reports, and transcripts.
- [ ] Review machine-level protections (disk encryption, endpoint protection, OS patching) on interview workstations.
- [ ] Confirm backup and restore processes for local JSON/DOCX records are tested and access-controlled.
