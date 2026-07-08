# Codex Handoff

Updated: 2026-07-07

## Current Handoff: PySide-Only Desktop GUI

Current desktop direction is PySide only. The legacy Tk interview GUI, standalone Tk onboarding app, Tk UI wrappers, Tk-specific tests, and Tk contracts have been removed. Future GUI migration work should target the web shell after PySide parity, not resurrect Tk paths.

Current launch state:

- `setup_and_run.ps1` accepts and launches only `-UiMode pyside`.
- `src/pyside_interview_app.py` is the supported desktop GUI entry point.
- `src/ui_mode_switch.py` normalizes missing, invalid, or legacy values to `pyside`.
- Missing PySide6 is a hard setup failure; there is no Tk fallback.
- Staffing v2 and notification rule/test-send UI remain in PySide and shared staffing services.

Validation from this cleanup:

```powershell
python -m pytest -n auto
python tools/check_contract_review.py
```

Results:

- Full pytest: `1690 passed`.
- Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.

Historical notes below may mention removed Tk modules as past implementation history only. They are not current architecture or valid import paths.

## Latest Handoff: Weighted Trait Scoring and Checkbox Removal

Active work is implemented and automated validation is green.

User's current request:

- Replace old trait-scoring assets with the user-provided weighted JSON:
  - remove `Trait-Based Scoring/shared_signal_dictionary.json`
  - remove old `Trait-Based Scoring/T*.json`
  - use `Trait-Based Scoring/preschool_teacher_interview_signals_weighted.json`
- Keep scoring source aligned with `config/rubric.json` numbering and track behavior.
- Preserve the Infant/Toddler vs Preschool path split. The source has two Preschool trait 11 variants; current implementation maps `trait_11_recommended_version` to rubric `trait_11` and excludes duplicate `trait_11_json_version` from runtime traits.
- Remove manual trait-based scoring checkboxes from the interview UI.
- Use trait-based signal scoring for DeepSeek/model scoring, not human checkbox selection.

Current code state:

- `Trait-Based Scoring/preschool_teacher_interview_signals_weighted.json` has been copied into the repo from the user's attached file.
- Old scoring JSON assets were removed from `Trait-Based Scoring/`.
- `Trait-Based Scoring/trait_based_scoring_contract.yaml` now uses `paths.weighted_signals`.
- `Trait-Based Scoring/trait_based_scoring_engine.py` derives runtime traits and an in-memory signal dictionary from the weighted source.
- Runtime trait ids currently normalize to `trait_1` through `trait_11`; Preschool Structure & Flexibility is rubric `trait_11`.
- `src/scoring_reporting.py` prefers bundled runtime traits before legacy trait-dir scans and supports weighted/flat extended signal structures.
- `src/interview_runtime.py` reads trait scoring context from runtime bundle instead of scanning `T*.json` directly.
- `src/ui_composition.py` no longer renders `TraitScreenUI` signal checkboxes and clears manual selected-signal IDs during persist/skip.
- Runtime signal editing accepts negative weighted signal values and still rejects nonnumeric weights.

Validation run:

```powershell
python -m pytest tests/test_trait_based_scoring_engine_regression.py tests/test_trait_scoring_adapter.py tests/test_trait_signal_state.py tests/test_trait_signal_schema.py tests/test_question_runtime_definition_service.py tests/test_deepseek_summary.py
```

Result before latest checkbox-removal changes: `92 passed`.

Additional validation after checkbox removal and negative-weight fix:

```powershell
python -m pytest tests/test_trait_based_scoring_engine_regression.py tests/test_trait_scoring_adapter.py tests/test_trait_signal_state.py tests/test_trait_signal_schema.py tests/test_question_runtime_definition_service.py tests/test_deepseek_summary.py tests/test_trait_screen_sections.py tests/test_reporting_export.py
python -m pytest tests/test_ui_contract_interfaces.py tests/test_shared_module_contract_interfaces.py tests/test_contract_coverage_matrix.py tests/test_regenerate_contract_test_matrix.py
python -m pytest tests/test_question_runtime_definition_service.py tests/test_question_settings_window_runtime_behavior.py
python tools/check_contract_review.py
python -m pytest
```

Latest results:

- Focused weighted/checkbox/model suite: `105 passed`.
- UI/shared/coverage contract suite: `392 passed`.
- Focused negative-weight drift suite: `10 passed`.
- Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
- Full pytest: `1437 passed`.

Pending next steps:

- Manual/live GUI smoke for weighted source, model suggestion quality, and no trait-observation checkboxes.
- Do not stage generated/private artifacts. Worktree was already very dirty before this task.

## 1. Original Objective

Flatten the Python desktop app into five larger public modules so future AI agents can navigate and change the codebase more easily:

- `platform_services`
- `ui_composition`
- `interview_runtime`
- `scoring_reporting`
- `onboarding_operations`

After flattening, continue the post-flattening feature roadmap with controlled checkpoints:

1. Remove separate transcript document generation.
2. Add editable questions and editable scored traits.
3. Add manual trait observation checkboxes.
4. Add Deepseek answer summaries and executive summary.
5. Add Deepseek model-suggested trait observation markings while preserving human/model distinction.
6. Run final regression and refresh progress/handoff.

Non-negotiable process rule: always write or update contracts for new features before writing implementation code.

## 2. Current Outcome

Flattening, approved wrapper deletion, VB-CABLE setup idempotency, and post-flattening feature Checkpoints 10-16 are complete from automated-validation perspective.

- Five target modules are preferred production import surface for migrated code.
- Approved retired wrapper import paths were removed in validated batches.
- Remaining legacy import paths are launch-critical or still-active implementation adapters.
- Production imports for audited moved modules no longer use deleted legacy wrapper modules.
- Contract coverage matrix regenerated.
- Contract review passes.
- Full pytest passes with zero skips and no warnings.
- Local Ollama DeepSeek smoke passes against `deepseek-r1:1.5b` through the app summary API.
- Progress and handoff are now refreshed for next-agent takeover.

Manual/hardware/live-service gaps remain documented:

- Windows recording smoke was previously user-verified after earlier automated validation, but not rerun after Checkpoints 10-14.
- Onboarding launch/reminder/UI smoke was previously user-verified after earlier automated validation, but not rerun after Checkpoints 10-14.
- Interview-notes DOCX/referral/export GUI smoke was previously user-verified after earlier automated validation, but not rerun after Checkpoints 10-14.
- Local Ollama DeepSeek API smoke was run after Checkpoint 15; hosted DeepSeek credential/API smoke is not applicable to the current local-only config.
- Model suggestion UI visual smoke was not run in this automated session.

## 3. 30 Action Item Status

1. Create flattening baseline
   - Status: Done
   - Evidence: `docs/flattening_baseline.md`, `docs/flattening_migration_map.md`.

2. Add migration guard tests
   - Status: Done
   - Evidence: `tests/test_flattened_module_facades.py`.

3. Normalize target module contracts
   - Status: Done
   - Evidence: target contracts exist and `python tools\check_contract_review.py` passes.

4. Define wrapper contract pattern
   - Status: Done
   - Evidence: migration docs and wrapper contracts describe compatibility wrappers.

5. Flatten platform configuration
   - Status: Done
   - Evidence: `platform_services` owns app content, config validation/parsing, data-store support helpers, storage/path/docx helpers.

6. Flatten platform logging/runtime
   - Status: Done
   - Evidence: `platform_services` owns app logging/crash reporting, artifact cleanup, runtime wrapper helpers, UX metrics.

7. Convert platform legacy files to wrappers
   - Status: Done
   - Evidence: platform legacy modules forward to `platform_services`, except approved retired wrappers that were deleted.

8. Flatten candidate/profile scoring primitives
   - Status: Done
   - Evidence: scoring primitives live behind `scoring_reporting`.

9. Flatten reporting and DOCX export
   - Status: Done
   - Evidence: reporting/referral/export code lives behind `scoring_reporting`; docx compatibility lives behind `platform_services`.

10. Flatten trait scoring implementation
   - Status: Done
   - Evidence: trait loaders/adapters/schema/state live behind `scoring_reporting`.

11. Flatten referral and integration delivery
   - Status: Done
   - Evidence: director email/referral, integration export, offer letter live behind `scoring_reporting`.

12. Convert scoring/reporting legacy files to wrappers
   - Status: Done
   - Evidence: approved retired scoring/reporting wrappers were deleted; remaining adapters are active or launch/runtime relevant.

13. Flatten interview state and sessions
   - Status: Done
   - Evidence: interview state/session helpers live behind `interview_runtime`.

14. Flatten audio and transcription runtime
   - Status: Done
   - Evidence: audio devices/runtime, Whisper policy, transcript processing/queue/executor/writer, accumulator, diagnostics live behind `interview_runtime`.

15. Flatten interview flow/finalize controllers
   - Status: Done
   - Evidence: flow/dashboard/history/finalize controllers live behind `interview_runtime`.

16. Preserve transcript summary seam
   - Status: Done
   - Evidence: local transcript summary seam remains behind `interview_runtime`; Deepseek summary/evaluation feature is now implemented through new explicit opt-in API boundaries.

17. Convert interview runtime legacy files to wrappers
   - Status: Done
   - Evidence: interview runtime legacy modules forward to `interview_runtime`; `interview_app.pyw` remains launch entrypoint.

18. Flatten shared UI helpers
   - Status: Done
   - Evidence: shared UI helpers live behind `ui_composition`.

19. Flatten question UI
   - Status: Done
   - Evidence: question screen implementation moved behind `ui_composition`; `question_screens.py` compatibility handled by current adapter shape.

20. Flatten interview shell/views
   - Status: Done
   - Evidence: UI router/shell/view protocol/history grid/start screen surfaces live behind `ui_composition`; remaining entry/view modules stay where needed for app shape and compatibility.

21. Convert UI legacy files to wrappers
   - Status: Done
   - Evidence: moved UI legacy modules forward to `ui_composition`, except approved deleted wrappers.

22. Flatten onboarding models/storage
   - Status: Done
   - Evidence: onboarding models/storage/migrations/template/task/launch helpers live behind `onboarding_operations`.

23. Flatten onboarding scheduling/reminders
   - Status: Done
   - Evidence: scheduler/reminder/health/notifier/send guard/status/dialog code lives behind `onboarding_operations`.

24. Flatten onboarding UI actions
   - Status: Done
   - Evidence: dashboard actions, action sections, scroll helpers/modal, UI helpers live behind `onboarding_operations`.

25. Convert onboarding legacy files to wrappers
   - Status: Done
   - Evidence: onboarding legacy modules forward to `onboarding_operations`; `onboarding_app.pyw` remains launch entrypoint.

26. Update app entrypoints
   - Status: Done
   - Evidence: import smokes passed for `src/interview_app.pyw` and `src/onboarding_app.pyw`; parser smokes passed for onboarding reminder CLI and runtime wrapper args.

27. Regenerate contract coverage
   - Status: Done
   - Evidence: `docs/contract_test_coverage_matrix.yaml` regenerated; contract review passes.

28. Run full regression suite
   - Status: Done
   - Evidence: latest full pytest passed with `1396 passed`.

29. Remove retired wrappers
   - Status: Done for approved retired wrappers
   - Evidence: retired platform, scoring/reporting, UI, interview root, and onboarding wrappers/contracts were deleted with matching contract/system/architecture/matrix/test updates. Remaining wrappers/adapters are launch-critical or still active.

30. Update maintainer docs
   - Status: Done
   - Evidence: `docs/README.md`, `docs/flattening_baseline.md`, `docs/flattening_migration_map.md`, `docs/CODEX_PROGRESS.md`, and this handoff updated.

## 4. Post-Flattening Checkpoint Status

### Checkpoint 10: Remove Separate Transcript Document Generation

- Status: Done.
- Result: Interview notes DOCX is the sole normal generated document artifact. Standalone transcript DOCX creation/opening is disabled for normal finalize flow.
- Compatibility: legacy `transcript_path` remains present as an empty compatibility field for new finalize output.
- Key files:
  - `src/interview_app.pyw`
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/interview_app.contract.yaml`
  - `contracts/interview_app_transcript_writer.contract.yaml`
  - `contracts/interview_app_finalize_context.contract.yaml`
  - `contracts/interview_app_types.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `tests/test_live_transcript_recording_flow.py`
  - `tests/test_transcription_recording_verification.py`
  - `tests/test_finalize_pipeline_gateways.py`
- Validation:
  - Focused suite: `43 passed`, `12 warnings`.
  - Full pytest at checkpoint: `1252 passed`, `103 skipped`, `18 warnings`.
  - Contract review passed.
- Manual gap: live GUI smoke for no-standalone-transcript behavior was not run in this automated session.

### Checkpoint 11: Editable Questions and Editable Traits

- Status: Done.
- Result: scored trait add/save/delete validates canonical ids and syncs runtime definition files; custom question CRUD remains in `QuestionEditorWindow`/`QuestionOverridesStore`; runtime signal controls are rendered in `QuestionSettingsWindow`.
- Key files:
  - `src/ui_composition.py`
  - `contracts/ui_composition.contract.yaml`
  - `contracts/ui_windows.contract.yaml`
  - `contracts/data_store.contract.yaml`
  - `tests/test_question_settings_service.py`
  - `tests/test_question_settings_window_runtime_behavior.py`
- Validation:
  - Focused storage/question suite: `30 passed`.
  - Full focused checkpoint suite: `375 passed`.
  - Full pytest at checkpoint: `1261 passed`, `103 skipped`, `18 warnings`.
  - Contract review passed.
- Manual gap: live GUI smoke for Question Settings signal controls was not run in this automated session.

### Checkpoint 12: Manual Trait Observation Checkboxes

- Status: Done.
- Result: `TraitScreenUI` renders runtime-backed core and extended signal checkboxes and persists canonical `selected_signal_ids`.
- Key files:
  - `src/ui_composition.py`
  - `contracts/ui_composition.contract.yaml`
  - `tests/test_trait_screen_sections.py`
- Validation:
  - Focused trait checkbox/scoring suite: `52 passed`.
  - Broader focused suite: `230 passed`, `5 warnings`.
  - Full pytest at checkpoint: `1265 passed`, `103 skipped`, `18 warnings`.
  - Contract review passed.
- Manual gap: live GUI smoke for runtime-backed trait observation checkboxes was not run in this automated session.

### Checkpoint 13: Deepseek Answer Summaries and Executive Summary

- Status: Done.
- Result: Deepseek summary calls require explicit opt-in via `DEEPSEEK_SUMMARY_ENABLED=true` or `settings["deepseek_summary_enabled"] = True`, using local Ollama by default. Finalize payload includes `answer_summaries`, `executive_summary`, `summary_status`, and `summary_warnings`. DOCX writes `Executive Summary` before education/score sections when present.
- Secret/privacy behavior: local Ollama config uses no hosted API key; failure logging records exception type only, not key/body/response detail.
- Key files:
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `tests/test_deepseek_summary.py`
  - `tests/test_reporting_export.py`
- Validation:
  - Focused summary/export/shared-contract suite: `237 passed`.
  - Broader summary/finalize/export/shared-contract suite: `249 passed`, `3 warnings`.
  - Full pytest at checkpoint: `1275 passed`, `103 skipped`, `18 warnings`.
  - Contract review passed.
- Manual gap: live Deepseek content-quality review was not run at this checkpoint; local Ollama API smoke was completed later in Checkpoint 16.

### Checkpoint 14: Model-Suggested Trait Observation Auto-Marking

- Status: Done.
- Result: Deepseek can suggest trait observation signals through mocked-testable API boundary. Model suggestions persist as `model_signal_suggestions` per trait and `model_signal_suggestions_by_trait` in finalize payload. Manual `selected_signal_ids` remain separate and continue to drive scoring math. `model_signal_override` compares accepted/rejected/manual-only signal ids. UI shows suggestion hints but does not pre-check manual boxes.
- Key files:
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `src/ui_composition.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `contracts/ui_composition.contract.yaml`
  - `tests/test_deepseek_summary.py`
  - `tests/test_trait_signal_state.py`
  - `tests/test_trait_screen_sections.py`
  - `tests/test_trait_scoring_adapter.py`
- Validation:
  - Initial focused trait suggestion suite: `450 passed`.
  - Broader focused suite: `462 passed`, `3 warnings`.
  - Full pytest at checkpoint: `1285 passed`, `103 skipped`, `18 warnings`.
  - Contract review passed.
- Manual gap: live Deepseek suggestion content-quality review and GUI visual smoke for model suggestion hints were not run; local Ollama API smoke was completed later in Checkpoint 16.

### Checkpoint 15: Final Feature Regression and Handoff Refresh

- Status: Done.
- Result: final automated validation, formerly skipped onboarding contract checks, warning cleanup, and import audit completed; progress and handoff refreshed.
- Validation:
  - `python tools\check_contract_review.py`: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - `python -m pytest -rs tests\test_onboarding_contract_interfaces.py`: `236 passed`.
  - `python -m pytest`: `1396 passed`.
  - Production import audit found no imports of audited retired wrapper modules in `src`.
- Manual smoke status:
  - Windows recording smoke: previously user-verified after earlier automated validation; not rerun after Checkpoints 10-14.
  - Onboarding launch/reminder/UI smoke: previously user-verified after earlier automated validation; not rerun after Checkpoints 10-14.
  - Interview-notes DOCX/referral/export GUI smoke: previously user-verified after earlier automated validation; not rerun after Checkpoints 10-14.
  - Local Ollama DeepSeek API smoke: passed on 2026-06-17 with `deepseek-r1:1.5b`; no hosted API key required.
  - Model suggestion UI visual smoke: not run.

### Checkpoint 16: Local DeepSeek Smoke and JSON Response Hardening

- Status: Done.
- Result: local Ollama at `http://127.0.0.1:11434` was reachable with `deepseek-r1:1.5b`; app-level summary smoke returned `summary_status = generated`. DeepSeek response parsing now accepts plain or fenced JSON objects, and prompts request schema-only output without input echo.
- Key files:
  - `src/interview_runtime.py`
  - `contracts/interview_runtime.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_deepseek_summary.py`
- Validation:
  - `python tools\check_contract_review.py`: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - `python -m pytest tests\test_deepseek_summary.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`: `18 passed`.
  - `python -m pytest`: `1396 passed`.
  - Local Ollama smoke: `generated`, one answer summary, no summary warnings.
- Manual gap: summary/suggestion content quality still needs human review in a real interview finalize smoke.

## 5. Current Repository State

- Current branch: `main`.
- Normal `git` may not be on PowerShell PATH. Use `C:\Program Files\Git\cmd\git.exe`.
- Worktree is very dirty.
- Relevant checkpoint changes span `src`, `contracts`, `tests`, `docs`, and setup/runtime files from earlier work.
- Dirty tree also contains generated `__pycache__`/`.pyc` files and local/generated/private artifacts. Do not stage broadly.
- `src/ux_metrics.jsonl`, generated interview diagnostics/exports/sessions, logs, backups, and pycache files may contain local or private data. Avoid including them in summaries, commits, or patches unless user explicitly asks.
- Scoped current checkpoint files include:
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `src/ui_composition.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `contracts/ui_composition.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
  - `docs/CODEX_HANDOFF.md`
  - `tests/test_deepseek_summary.py`
  - `tests/test_reporting_export.py`
  - `tests/test_trait_signal_state.py`
  - `tests/test_trait_screen_sections.py`
  - `tests/test_trait_scoring_adapter.py`

## 6. Important Architecture Decisions

- Five target modules are public import surface for migrated code.
- Retired legacy import paths are removed after approval; remaining legacy paths are kept only when launch-critical or still active.
- Wrappers should contain no business logic once moved.
- Contracts and architecture/system dependency records must change with code-visible moves.
- No import fallback wrappers should be added around imports unless task explicitly requires it.
- Retranscription remains unavailable at UI/action level; retired retranscription progress module/contract were removed.
- Windows recording startup tolerates default microphone-only availability; user manually verified target hardware recording smoke after earlier automated validation.
- Interview notes DOCX is now the sole normal generated document artifact; transcript text remains inside notes and structured payloads.
- Deepseek summary/suggestion features are explicit opt-in, run through local Ollama by default, and must not log secrets.
- Manual trait observation selections and model suggestions are intentionally separate. Manual selections drive scoring math.
- Privacy-sensitive flows: candidate data, interview artifacts, onboarding records, generated DOCX/referral/export files, email/reminder credentials, logs, transcript summaries, and model suggestions.

## 7. Files and Modules Next Agent Should Read First

- `AGENTS.md`: project rules, including contract-driven workflow.
- `docs/CODEX_HANDOFF.md`: this file.
- `docs/CODEX_PROGRESS.md`: checkpoint log and validation evidence.
- `docs/flattening_baseline.md`: current architecture checkpoint, validation status, manual smoke notes.
- `docs/flattening_migration_map.md`: ownership map and wrapper policy.
- `docs/README.md`: maintainer doc index and flattened architecture note.
- `contracts/system.contract.yaml`: module dependency source of truth.
- `contracts/architecture.contract.yaml`: architecture relationship source of truth.
- `docs/contract_test_coverage_matrix.yaml`: generated contract/test coverage map.
- `src/platform_services.py`
- `src/ui_composition.py`
- `src/interview_runtime.py`
- `src/scoring_reporting.py`
- `src/onboarding_operations.py`
- `src/interview_app.pyw`
- `tests/test_deepseek_summary.py`
- `tests/test_trait_signal_state.py`
- `tests/test_trait_screen_sections.py`
- `tests/test_trait_scoring_adapter.py`
- `tests/test_reporting_export.py`
- `tests/test_finalize_pipeline_gateways.py`
- `tools/check_contract_review.py`
- `tools/regenerate_contract_test_matrix.py`

## 8. Validation Evidence

Latest confirmed commands:

```powershell
python tools\check_contract_review.py
python -m pytest
rg -n -- "from (app_content|config_adapters|app_logging|artifact_cleanup|ux_metrics|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)|import (app_content|config_adapters|app_logging|artifact_cleanup|ux_metrics|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)" src
```

Latest confirmed results:

- Formerly skipped onboarding contract interface tests: `236 passed`.
- Full pytest: `1396 passed`.
- Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
- Production import audit: no hits for audited retired wrapper modules in `src`.

## 9. Remaining Risks and Follow-Up

Remaining manual/external validation:

- Run live Windows recording smoke after current transcript/reporting changes if release confidence requires it.
- Run live GUI interview finalize smoke for no-standalone-transcript behavior, interview notes DOCX, referral/export, executive summary section, absence of trait-observation checkboxes, and model suggestion hints.
- Review local DeepSeek summary/suggestion output quality during a real GUI finalize smoke.
- Verify redaction/no-secret behavior in any live Deepseek error logs.

Potential future cleanup:

- Review remaining launch-critical wrappers/adapters separately before any deletion.
- Consider adding a small live-config diagnostic command for local DeepSeek/Ollama availability without printing secrets.

## 10. Recommended Next Goal

Recommended next work:

1. Perform manual/live smoke validation for the completed feature roadmap.
   - Windows recording smoke.
   - Live GUI interview finalize with interview notes DOCX/referral/export.
   - Model suggestion UI visual smoke.
   - Human review of local DeepSeek summary/suggestion quality.

2. Fix only issues found by those smokes.
   - Write/update contracts first for any code-visible change.
   - Add or update focused tests before changing behavior.
   - Regenerate `docs/contract_test_coverage_matrix.yaml` after contract-visible changes.
   - Run `python tools\check_contract_review.py`.
   - Run focused tests and full `python -m pytest` before completion.

Use this exact next goal prompt for the next implementation thread:

```text
Continue from docs/CODEX_HANDOFF.md and docs/CODEX_PROGRESS.md. Do not restart flattening. Checkpoints 10-16 are implemented and automated validation is green: contract review passes, full pytest passes with 1396 passed, production import audit found no retired wrapper imports in src, and local Ollama DeepSeek summary smoke passes with `deepseek-r1:1.5b`. Next, perform or coordinate manual/live smoke validation for Windows recording, live GUI interview finalize/DOCX/referral/export, model suggestion UI hints, and human review of local DeepSeek summary/suggestion quality. If any issue is found, write/update contracts before code changes, make minimal targeted fixes, regenerate the contract matrix, run contract review, focused tests, and full pytest. Do not commit secrets or stage generated/private artifacts.
```

## 11. Constraints for Future Agents

- Read `AGENTS.md` first; it requires caveman skill and contract-driven workflow.
- Do not rely on previous conversation memory when repo files can be inspected.
- Re-read current files before editing.
- Make minimal targeted changes.
- Preserve behavior unless next goal explicitly changes it.
- Always write or update contracts for new features before writing implementation code.
- Update contracts with code/interface/dependency changes.
- Regenerate coverage matrix after contract-visible changes.
- Run focused tests after each change and full pytest before completion.
- Stop and report blocker instead of guessing.
- Do not stage or commit generated/private artifacts.
- Do not delete remaining wrappers unless their launch/runtime role is understood and validated.
