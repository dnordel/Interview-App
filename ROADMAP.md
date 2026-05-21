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
