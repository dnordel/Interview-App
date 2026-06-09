# ROADMAP

## 2026-03-25
- [x] Harden transcript summarization runtime fallback so interview note exports do not expose raw `Unknown task summarization` transformer diagnostics.
- [x] Add a secondary `text2text-generation` summarizer pipeline fallback for environments where `summarization` task aliases are unavailable.
- [x] Add regression tests for fallback pipeline selection and normalized runtime error messaging in exported summary paths.
- [ ] Evaluate configurable summarization model selection so deployments can pin approved local models without code edits.
- [x] Reverted merge commit `446ae301167f460c2565ee38fbf10822da2b11b4` on isolated rollback branch and restored pre-`bb1200b` launcher/dependency baseline (requirements + pip flow, no `uv`).
- [x] Resolved revert conflicts by selecting `bb1200b^` behavior for dependency-management and launcher-adjacent files, then validated README/workflow/setup references for non-`uv` assumptions.
- [ ] Re-run full test suite in a network-enabled environment after restoring dev dependencies (e.g., `python-docx`) blocked by proxy in this runtime.
- [x] Updated Windows recording startup to allow microphone-only or system-only FFmpeg capture as long as at least one device is configured.
- [x] Adjusted interview history normalization to preserve plain score-only entries while still backfilling offer-date fields for onboarding-aware rows.
- [x] Reformatted inline-parameter PowerShell function declarations so contract signature parsing matches `setup_and_run.ps1`.
- [x] Added explicit typed PowerShell parameters for config-carrying setup helpers so contract signature extraction captures `cfg` inputs consistently.
- [x] Regenerated `docs/contract_test_coverage_matrix.yaml` against current module contracts to eliminate symbol drift.

## 2026-05-21
- [x] Add local Hugging Face executive and per-answer interview-note summaries in DOCX export with safe "Summary unavailable" fallback behavior.

## 2026-05-21 (reliability follow-up)
- [x] Harden local interview summarizer pipeline initialization failures so DOCX export continues with explicit fallback text when model/backend loading fails.
- [x] Confirmed `SPEC_TEMPLATE.md` is absent in this repository; no spec-template update was applied.
- [x] Updated finalize UX to return to the start screen immediately while finalize/export continues in background.
- [x] Added deferred-summary export mode that writes `Summary pending/failed` placeholders first, then retries summary generation in a background pass.
- [x] Added locked-file save fallback for interview notes exports by writing suffixed `(updated N)` filenames when the original DOCX is open in Word.

## 2026-06-09
- [x] Implemented trait-based scoring runtime bundle loader with bundle-contained path validation, normalized scoring config keys, fail-closed trait/signal validation, and real-engine regression coverage.
- [x] Confirmed `SPEC_TEMPLATE.md` is absent in this repository; no spec-template update was applied.
