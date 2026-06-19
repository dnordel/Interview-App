# Codex Progress

## Goal

Continue the post-flattening roadmap in `docs/CODEX_HANDOFF.md` using controlled checkpoints, preserving existing behavior except where the feature checkpoint explicitly changes it.

## Definition of Done

The goal is complete only when all conditions are true:

1. Five public modules are the preferred production import surface:
   - `platform_services`
   - `ui_composition`
   - `interview_runtime`
   - `scoring_reporting`
   - `onboarding_operations`
2. Remaining physical moves from the handoff are either completed behind the five public modules or explicitly documented as intentionally separate.
3. Retired legacy modules that were safe to delete are removed; remaining adapters are launch-critical or still active.
4. Contracts match current code exactly:
   - module contracts under `contracts/`
   - `contracts/system.contract.yaml`
   - `contracts/architecture.contract.yaml`
5. `docs/contract_test_coverage_matrix.yaml` is regenerated after each contract-visible move.
6. Production imports are audited so new code prefers the five public modules.
7. Current full relevant automated validation has passed, including contract review and full pytest unless a blocker is recorded.
8. Manual smoke gaps are either completed or explicitly recorded as unable to run locally:
   - Windows recording smoke
   - onboarding launch/reminder/UI smoke
   - interview-notes DOCX/referral/export smoke
9. Maintainer docs reflect final architecture after migration.
10. Retired wrappers are removed only after production imports no longer rely on them and cleanup is explicitly safe.
11. Dirty/generated/private artifacts are not staged or intentionally modified.

## Relevant Files and Modules

- `docs/CODEX_HANDOFF.md` - source plan and remaining work.
- `docs/CODEX_PROGRESS.md` - active checkpoint log and validation record.
- `docs/flattening_baseline.md` - migration checkpoint and safety constraints.
- `docs/flattening_migration_map.md` - ownership map and wrapper order.
- `contracts/system.contract.yaml` - module dependency source of truth.
- `contracts/architecture.contract.yaml` - architecture relationship source of truth.
- `docs/contract_test_coverage_matrix.yaml` - generated contract/test coverage map.
- `src/platform_services.py` - target module for config, storage, logging/runtime helpers.
- `src/ui_composition.py` - target module for UI helpers, windows, question screens, views.
- `src/interview_runtime.py` - target module for interview state/session/audio/transcription/controllers/finalize.
- `src/scoring_reporting.py` - target module for scoring, reporting, referral, exports.
- `src/onboarding_operations.py` - target module for onboarding models/storage/scheduling/reminders/UI helpers.
- `src/ui_windows.py` - remaining shared UI/window implementation.
- `src/interview_app/views/*.py` - remaining interview views.
- `src/data_store.py` - remaining platform storage candidate.
- `src/app_logging.py`, `src/runtime_wrapper.py` - remaining platform logging/runtime candidates.
- `src/interview_app/session_context.py`, `src/interview_app/state.py`, `src/interview_app/types.py`, `src/interview_app/bootstrap.py` - remaining interview runtime review candidates.
- `tests/test_flattened_module_facades.py` - migration guard tests.
- `tests/test_ui_contract_interfaces.py` - UI contract-visible checks.
- `tests/test_interview_app_contract_interfaces.py` - interview contract-visible checks.
- `tests/test_shared_module_contract_interfaces.py` - shared module contract-visible checks.
- `tools/regenerate_contract_test_matrix.py` - coverage matrix generator.
- `tools/check_contract_review.py` - contract review gate.

## Current Active Work: Weighted Trait Scoring Source and DeepSeek Signal Scoring

- Status: Done.
- Updated: 2026-06-18.
- User request:
  - Replace `Trait-Based Scoring/shared_signal_dictionary.json` and individual `Trait-Based Scoring/T*.json` trait files with the attached weighted scoring source `preschool_teacher_interview_signals_weighted.json`.
  - Keep question/trait numbering aligned with `config/rubric.json`.
  - Account for the track split between Infant/Toddler and Preschool; source file has two Preschool trait 11 variants, with the recommended Structure & Flexibility version mapped to rubric `trait_11`.
  - Remove manual trait-observation checkboxes from the interview UI.
  - Use trait-based signal scoring for DeepSeek/model scoring rather than human checkbox selection.
- Security notes:
  - No secrets added.
  - Weighted scoring source is local JSON config.
  - Candidate/interview data remains privacy-sensitive; no generated interview records or logs should be staged.
  - Loader changes should fail closed on missing/malformed scoring source.
- Final code state:
  - `Trait-Based Scoring/preschool_teacher_interview_signals_weighted.json` copied from the user-provided file.
  - Old `Trait-Based Scoring/shared_signal_dictionary.json` and old `Trait-Based Scoring/T*.json` files were removed.
  - `Trait-Based Scoring/trait_based_scoring_contract.yaml` now points to `paths.weighted_signals`.
  - `Trait-Based Scoring/trait_based_scoring_engine.py` can derive runtime traits and an in-memory signal dictionary from the weighted source.
  - Runtime output exposes rubric-aligned trait ids `trait_1` through `trait_11`; `trait_11_recommended_version` maps to `trait_11`, and duplicate `trait_11_json_version` is not emitted as a runtime trait.
  - `src/scoring_reporting.py` now prefers bundled runtime traits before scanning legacy `T*.json`, supports flat/weighted extended signals, groups by `signal_category`, and lets DeepSeek `model_signal_suggestions` drive signal refs before falling back to legacy selected-signal ids.
  - `src/interview_runtime.py` now loads trait context from the runtime bundle instead of directly scanning `T*.json`; DeepSeek prompt wording was changed from checkbox suggestions to trait-based scoring observations.
  - `src/ui_composition.py` no longer renders trait-observation signal checkboxes in `TraitScreenUI`; persist/skip clears manual selected-signal ids while preserving model suggestions.
  - Runtime signal editing now accepts negative weighted signal values and still rejects nonnumeric weights.
- Validation performed:
  - Before the latest checkbox-removal request, focused suite passed: `python -m pytest tests/test_trait_based_scoring_engine_regression.py tests/test_trait_scoring_adapter.py tests/test_trait_signal_state.py tests/test_trait_signal_schema.py tests/test_question_runtime_definition_service.py tests/test_deepseek_summary.py` -> `92 passed`.
  - Focused weighted/checkbox/model suite: `105 passed`.
  - UI/shared/coverage contract suite: `392 passed`.
  - Focused negative-weight drift suite: `10 passed`.
  - `python tools/check_contract_review.py`: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full `python -m pytest`: `1437 passed`.
- Remaining:
  - Manual/live GUI smoke for weighted source, model suggestion quality, and no trait-observation checkboxes was not run in this automated session.
  - Worktree remains dirty with generated/private artifacts and pycache noise; do not stage broadly.

## Task Breakdown

### Checkpoint 10: Remove Separate Transcript Document Generation

- Description: Make the interview notes DOCX the sole generated document artifact while preserving transcript content inside interview notes and downstream referral/export payloads.
- Files likely to change: `src/interview_runtime.py`, `src/scoring_reporting.py`, `src/interview_app.pyw`, relevant interview app adapters, contracts for finalize/reporting/referral/integration/history fields, DOCX/finalize/referral/export tests, coverage matrix.
- Subtasks:
  - Audit transcript DOCX creation/opening, finalize payload fields, referral packet fields, integration export fields, and history persistence. Status: Done.
  - Stop normal finalize flow from creating/opening standalone transcript DOCX. Status: Done.
  - Preserve transcript text in merged interview notes and structured payloads. Status: Done.
  - Normalize legacy `transcript_path` consumers so old data does not crash while canonical flow uses `interview_notes_document_path`. Status: Done.
  - Update contracts and regenerate coverage matrix. Status: Done.
  - Update focused tests for finalize, referral, export, history, and DOCX content. Status: Done.
- Validation method: focused finalize/referral/export/history/DOCX tests, `python tools\regenerate_contract_test_matrix.py`, `python tools\check_contract_review.py`, full `python -m pytest`.
- Status: Done.
- Security notes:
  - No secrets added.
  - Candidate transcript text remains candidate data and stays in structured payloads/interview notes; standalone transcript DOCX path is now empty for new finalize outputs.
  - Legacy `transcript_path` field remains present as an empty compatibility field so downstream readers do not crash.
- Files changed:
  - `src/interview_app.pyw`
  - `src/interview_runtime.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/interview_app.contract.yaml`
  - `contracts/interview_app_transcript_writer.contract.yaml`
  - `contracts/interview_app_finalize_context.contract.yaml`
  - `contracts/interview_app_types.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_live_transcript_recording_flow.py`
  - `tests/test_transcription_recording_verification.py`
  - `tests/test_finalize_pipeline_gateways.py`
- Validation performed:
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_live_transcript_recording_flow.py tests\test_transcription_recording_verification.py tests\test_finalize_pipeline_gateways.py tests\test_finalize_history_persistence.py tests\test_reporting_export.py tests\test_referral_packet.py tests\test_integration_export.py`
  - `python -m pytest`
- Result:
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused Checkpoint 10 suite: `43 passed`, `12 warnings`.
  - Full pytest: `1252 passed`, `103 skipped`, `18 warnings`.
  - Warnings are existing `datetime.utcnow()` deprecations in `src/interview_runtime.py`; not part of this checkpoint.
- Remaining issues:
  - Manual live GUI smoke for the new no-standalone-transcript behavior was not run in this automated session.
  - Next recommended checkpoint is Checkpoint 11: editable questions and editable traits.

### Checkpoint 11: Editable Questions and Editable Traits

- Description: Allow modify/add/delete for any interview question and traits sought in answers, with persisted and validated runtime definitions.
- Files likely to change: question settings UI/service/storage, rubric/runtime definition helpers, `ui_composition`, `data_store`, contracts, question settings/runtime/UI tests, coverage matrix.
- Subtasks:
  - Write/update contracts first for question/trait storage, service, UI, and runtime definition interfaces. Status: Done.
  - Audit current question settings, rubric storage, trait definition service, runtime definition normalization, and editor contracts. Status: Done.
  - Define migration-safe persisted schema for edited questions/traits. Status: Done; scored trait edits persist in `rubric.json`, runtime trait/signal definitions persist in `Trait-Based Scoring/T*.json`, and custom question edits remain in `question_overrides.json`.
  - Implement add/edit/delete for scored traits and custom questions without breaking drafts. Status: Done. Scored trait add/save/delete validates canonical ids and syncs runtime definition files. Custom question CRUD remains in `QuestionEditorWindow`/`QuestionOverridesStore`.
  - Validate IDs, text, weights, descriptors, sample answers, trait signals, and track membership. Status: Done. Canonical scored trait id, name, primary question, weight, descriptors, sample answers, track membership, and runtime signal validation are covered.
  - Update UI validation/accessibility feedback. Status: Done for reachable trait/signal controls; signal-definition controls are now rendered in `QuestionSettingsWindow`.
  - Update contracts and tests. Status: Done.
- Validation method: question settings/runtime/service/UI tests, contract review, regenerated coverage matrix.
- Status: Done.
- Contract-first work completed:
  - Updated `contracts/ui_composition.contract.yaml` for canonical `trait_<number>` scored trait CRUD and runtime definition sync/create/delete side effects.
  - Updated `contracts/ui_windows.contract.yaml` for custom question editor CRUD, override, and apply behavior.
  - Updated `contracts/data_store.contract.yaml` for custom question store CRUD semantics.
- Code/test work completed:
  - `QuestionSettingsService` now rejects non-canonical scored trait ids for add/update/delete.
  - `QuestionSettingsService` validates descriptors, sample answers, applicable tracks, name, primary question, and weight before returning rubric edits.
  - `QuestionSettingsWindow.save_trait` syncs runtime definition question/name before rubric write.
  - `QuestionSettingsWindow.add_trait` defaults to next numeric `trait_<number>` id, validates weight, creates runtime definition, then writes rubric.
  - `QuestionSettingsWindow.delete_trait` deletes runtime definition before writing rubric changes.
  - `QuestionSettingsWindow` now renders core signal, extended group, and group signal controls for runtime trait-definition CRUD.
  - Existing custom question CRUD remains in `QuestionEditorWindow`; no move was needed for Checkpoint 11.
- Files changed:
  - `src/ui_composition.py`
  - `contracts/ui_composition.contract.yaml`
  - `contracts/ui_windows.contract.yaml`
  - `contracts/data_store.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_question_settings_service.py`
  - `tests/test_question_settings_window_runtime_behavior.py`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `python -m pytest tests\test_question_settings_service.py tests\test_question_runtime_definition_service.py tests\test_question_settings_window_runtime_behavior.py tests\test_question_overrides_store.py tests\test_storage_persistence.py`
  - `python -m pytest tests\test_question_settings_service.py tests\test_question_runtime_definition_service.py tests\test_question_settings_window_runtime_behavior.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_ui_contract_interfaces.py tests\test_ui_windows_settings_tabs.py tests\test_ui_windows_validation_messages.py tests\test_interview_app_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python -m pytest tests\test_question_settings_service.py tests\test_question_runtime_definition_service.py tests\test_question_settings_window_runtime_behavior.py tests\test_question_overrides_store.py tests\test_storage_persistence.py tests\test_ui_contract_interfaces.py tests\test_ui_windows_settings_tabs.py tests\test_ui_windows_validation_messages.py tests\test_interview_app_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python -m pytest`
- Result:
  - Focused question/storage suite: `30 passed`.
  - Descriptor/track/signal UI focused suite: `13 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full Checkpoint 11 focused suite after signal UI wiring: `375 passed`.
  - Full pytest: `1261 passed`, `103 skipped`, `18 warnings`.
  - Warnings are existing `datetime.utcnow()` deprecations in `src/interview_runtime.py`; not part of this checkpoint.
- Remaining issues:
  - Manual live GUI smoke for Question Settings signal controls was not run in this automated session.

### Checkpoint 12: Manual Trait Observation Checkboxes

- Description: Add human-selectable trait observation checkboxes during interview/scoring; persist selections separately from raw score/disqualifier state.
- Files likely to change: `src/ui_composition.py`, `src/interview_runtime.py`, `src/scoring_reporting.py`, trait signal/state contracts/tests, finalize/report/export tests, coverage matrix.
- Subtasks:
  - Write/update contracts first for manual observation state, GUI controller interfaces, persistence snapshots, and finalize/report payloads. Status: Done.
  - Audit trait signal schema/state/scoring adapter and GUI trait screen state. Status: Done.
  - Add manual observation selection state. Status: Done; canonical state uses `selected_signal_ids`.
  - Render trait observation checkboxes in interview GUI. Status: Done; `TraitScreenUI` renders runtime-backed core and extended signal checkboxes.
  - Persist selections in drafts/session snapshots/finalize payloads. Status: Done; `persist_state` writes canonical selected ids and snapshots use existing trait state payloads.
  - Include selections in reporting/export without accidental scoring-semantic changes. Status: Done; existing scoring/report/export pipeline consumes canonical selected ids.
  - Update contracts and tests. Status: Done.
- Validation method: GUI/controller/persistence/scoring/report tests, contract review, regenerated coverage matrix.
- Status: Done.
- Contract-first work completed:
  - Updated `contracts/ui_composition.contract.yaml` for `TraitScreenUI.signal_selection_vars`, `_render_trait_signal_checkboxes`, `_selected_signal_ids`, and selected-signal persistence semantics.
- Code/test work completed:
  - `TraitScreenUI` now loads runtime signal UI definitions for the selected trait.
  - It renders core and extended signal checkboxes under "Trait observations".
  - It initializes checkbox state from existing compatibility variants via `normalize_trait_signal_selection_state`.
  - It persists canonical `selected_signal_ids` via `write_canonical_selected_signal_ids`.
  - Skipping a trait clears selected signal ids.
- Files changed:
  - `src/ui_composition.py`
  - `contracts/ui_composition.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_trait_screen_sections.py`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `python -m pytest tests\test_trait_screen_sections.py tests\test_trait_signal_state.py tests\test_trait_scoring_adapter.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_trait_screen_sections.py tests\test_trait_signal_state.py tests\test_trait_scoring_adapter.py tests\test_finalize_pipeline_gateways.py tests\test_finalize_history_persistence.py tests\test_reporting_export.py tests\test_integration_export.py tests\test_ui_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python -m pytest`
- Result:
  - Focused trait checkbox/scoring suite: `52 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Broader Checkpoint 12 focused suite: `230 passed`, `5 warnings`.
  - Full pytest: `1265 passed`, `103 skipped`, `18 warnings`.
  - Warnings are existing `datetime.utcnow()` deprecations in `src/interview_runtime.py`; not part of this checkpoint.
- Remaining issues:
  - Manual live GUI smoke for runtime-backed trait observation checkboxes was not run in this automated session.

### Checkpoint 13: Deepseek Answer Summaries and Executive Summary

- Description: Add mocked-testable Deepseek summary service; generate answer summaries plus one executive summary; place executive summary first in interview notes.
- Files likely to change: `src/interview_runtime.py`, `src/scoring_reporting.py`, config/settings surfaces as needed, contracts, summary/DOCX/privacy tests, coverage matrix.
- Subtasks:
  - Write/update contracts first for summary service API, config surface, structured finalize payloads, DOCX section ordering, and error/degraded behavior. Status: Done.
  - Add Deepseek config path using env/settings without committed secrets. Status: Done.
  - Create API boundary with timeout/error handling and redacted logs. Status: Done.
  - Generate answer-level summaries and executive summary from transcript content. Status: Done.
  - Store summaries in finalize payloads. Status: Done.
  - Place executive summary first in interview notes DOCX. Status: Done.
  - Define fail-closed or explicit-degraded behavior on missing config/API failure. Status: Done.
  - Update contracts and tests. Status: Done.
- Validation method: mocked Deepseek tests, prompt/response validation tests, privacy/logging tests, DOCX section-order tests, contract review, regenerated coverage matrix.
- Status: Done.
- Contract-first work completed:
  - Updated `contracts/interview_runtime.contract.yaml` for `DeepSeekSummaryConfig`, summary config building, summary generation, and finalize payload summary fields.
  - Updated `contracts/scoring_reporting.contract.yaml` for executive summary ordering in `DocxExporter.export`.
- Code/test work completed:
  - `build_deepseek_summary_config` reads env-like mappings and supports settings overrides through finalize context construction.
  - Deepseek summary calls require explicit opt-in via `DEEPSEEK_SUMMARY_ENABLED=true` or `settings["deepseek_summary_enabled"] = True`, using local Ollama by default.
  - Runtime summary fields are `answer_summaries`, `executive_summary`, `summary_status`, and `summary_warnings`.
  - Missing config, missing transcript, and API failures degrade explicitly without blocking finalize.
  - Failure logging records exception type only, not API key or response/body details.
  - `DocxExporter` writes `Executive Summary` before education/score sections when present and includes per-answer summary lines in interview flow.
- Files changed:
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_deepseek_summary.py`
  - `tests/test_reporting_export.py`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `python -m pytest tests\test_deepseek_summary.py tests\test_reporting_export.py tests\test_shared_module_contract_interfaces.py`
  - `python -m pytest tests\test_deepseek_summary.py tests\test_reporting_export.py tests\test_finalize_pipeline_gateways.py tests\test_shared_module_contract_interfaces.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest`
  - `python -m pytest tests\test_deepseek_summary.py tests\test_reporting_export.py`
  - `python tools\check_contract_review.py`
- Result:
  - Focused summary/export/shared-contract suite: `237 passed`.
  - Broader summary/finalize/export/shared-contract suite: `249 passed`, `3 warnings`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full pytest: `1275 passed`, `103 skipped`, `18 warnings`.
  - Final focused rerun after import cleanup: `10 passed`; contract review stayed green.
  - Warnings are existing `datetime.utcnow()` deprecations in `src/interview_runtime.py`; not part of this checkpoint.
- Remaining issues:
  - Manual live Deepseek content-quality review was not run at this checkpoint; local Ollama API smoke was completed later in Checkpoint 16.
  - Next recommended checkpoint is Checkpoint 14: model-suggested trait observation auto-marking.

### Checkpoint 14: Model-Suggested Trait Observation Auto-Marking

- Description: Use Deepseek evaluation to suggest/auto-mark trait observations while preserving human-vs-model distinction and manual override.
- Files likely to change: trait observation state, GUI surfaces, scoring/report/export payloads, Deepseek evaluation service, contracts, tests, coverage matrix.
- Subtasks:
  - Write/update contracts first for model suggestion data, confidence/rationale fields, manual override state, GUI display, and report/export payloads. Status: Done.
  - Define model suggestion, confidence/rationale, and human override data model. Status: Done.
  - Add mocked-testable Deepseek evaluation prompts. Status: Done.
  - Store model suggestions separately from manual selections. Status: Done.
  - Show suggested markings without hiding human control. Status: Done.
  - Include human/model comparison in reporting/export where useful. Status: Done.
  - Update contracts and tests. Status: Done.
- Validation method: mocked evaluation tests, manual override tests, persistence tests, scoring/report/export tests, privacy tests, contract review, regenerated coverage matrix.
- Status: Done.
- Contract-first work completed:
  - Updated `contracts/interview_runtime.contract.yaml` for `generate_deepseek_trait_signal_suggestions`.
  - Updated `contracts/scoring_reporting.contract.yaml` for model-suggestion normalization, persistence, and manual/model override comparison helpers.
  - Updated `contracts/ui_composition.contract.yaml` for model suggestion hints on `TraitScreenUI`.
- Code/test work completed:
  - Deepseek trait suggestion generation reuses explicit opt-in summary config and mocked chat-completion seam.
  - Model suggestions persist as `model_signal_suggestions` per trait and `model_signal_suggestions_by_trait` in finalize payload.
  - Manual `selected_signal_ids` remain separate and continue to drive scoring math.
  - Suggestion entries carry `signal_id`, clamped `confidence`, and `rationale`, filtered to runtime-valid signal ids.
  - `model_signal_override` compares accepted, rejected, and manual-only signal ids.
  - Trait screen checkboxes display model suggestion hints but do not pre-check or hide manual controls.
  - DOCX/report/export rows include model suggestions and manual/model comparison where present.
- Files changed:
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `src/ui_composition.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `contracts/ui_composition.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `tests/test_deepseek_summary.py`
  - `tests/test_trait_signal_state.py`
  - `tests/test_trait_screen_sections.py`
  - `tests/test_trait_scoring_adapter.py`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `python -m pytest tests\test_deepseek_summary.py tests\test_trait_signal_state.py tests\test_trait_screen_sections.py tests\test_trait_scoring_adapter.py tests\test_reporting_export.py tests\test_shared_module_contract_interfaces.py tests\test_ui_contract_interfaces.py`
  - `python -m pytest tests\test_deepseek_summary.py tests\test_trait_signal_state.py tests\test_trait_screen_sections.py tests\test_trait_scoring_adapter.py tests\test_reporting_export.py tests\test_finalize_pipeline_gateways.py tests\test_shared_module_contract_interfaces.py tests\test_ui_contract_interfaces.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest`
- Result:
  - Initial focused trait suggestion suite: `450 passed`.
  - Broader Checkpoint 14 focused suite: `462 passed`, `3 warnings`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full pytest: `1285 passed`, `103 skipped`, `18 warnings`.
  - Warnings are existing `datetime.utcnow()` deprecations in `src/interview_runtime.py`; not part of this checkpoint.
- Remaining issues:
  - Manual live Deepseek suggestion content-quality review and GUI visual smoke for model suggestion hints were not run; local Ollama API smoke was completed later in Checkpoint 16.
  - Next recommended checkpoint is Checkpoint 15: final feature regression and progress/handoff refresh.

### Checkpoint 15: Final Feature Regression and Handoff Refresh

- Description: Verify full roadmap, record manual smoke gaps, and refresh docs for next agent.
- Files likely to change: `docs/CODEX_PROGRESS.md`, `docs/CODEX_HANDOFF.md`, maintainer docs if architecture/user flow changed.
- Subtasks:
  - Run full `python -m pytest`. Status: Done.
  - Run `python tools\check_contract_review.py`. Status: Done.
  - Run import/path audits relevant to changed modules. Status: Done.
  - Record manual smoke gaps for live Windows audio, live GUI DOCX/referral/export, and Deepseek local-output review. Status: Done in progress file.
  - Update handoff/progress with exact final state and next risks. Status: Done.
- Validation method: full pytest, contract review, documented manual-smoke status.
- Status: Done.
- Final automated validation performed:
  - `python tools\check_contract_review.py`
  - `python -m pytest -rs tests\test_onboarding_contract_interfaces.py`
  - `python -m pytest tests\test_finalize_history_persistence.py tests\test_finalize_pipeline_gateways.py tests\test_interview_app_session_manager.py tests\test_interview_session_store.py tests\test_live_transcript_recording_flow.py`
  - `python -m pytest`
  - Production import audit for retired wrapper modules in `src`
- Result:
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Formerly skipped onboarding contract interface tests now run: `236 passed`.
  - Warning-focused timestamp regression suite: `32 passed`.
  - Full pytest: `1396 passed` after Checkpoint 16.
  - Production import audit found no imports of audited retired wrapper modules in `src`.
  - Prior `datetime.utcnow()` deprecation warnings are resolved by timezone-aware UTC timestamps in `src/interview_runtime.py`.
- Manual smoke status:
  - Windows recording smoke: previously user-verified after earlier automated validation; not rerun in this automated session.
  - Onboarding launch/reminder/UI smoke: previously user-verified after earlier automated validation; not rerun in this automated session.
  - Interview-notes DOCX/referral/export GUI smoke: previously user-verified after earlier automated validation; not rerun after Checkpoints 10-14 in this automated session.
  - Local Ollama DeepSeek API smoke: completed later in Checkpoint 16.
  - Model suggestion UI visual smoke: not run in this automated session.
- Remaining issues:
  - Manual live Deepseek content-quality review and model suggestion UI visual smoke remain for external validation.
  - Full goal is complete from code/documentation/automated-validation perspective.

### Checkpoint 16: Local DeepSeek Smoke and JSON Response Hardening

- Description: Finish remaining safe local DeepSeek validation from handoff/progress and fix app-level JSON parsing drift found by live local Ollama smoke.
- Status: Done.
- Security notes:
  - Used synthetic smoke-test transcript text only.
  - No candidate/private interview artifacts, secrets, hosted API keys, or logs were intentionally read.
  - Local Ollama request used `api_key="ollama"` against `127.0.0.1`.
- What changed:
  - `src/interview_runtime.py` now asks local DeepSeek for schema-only JSON without input echo and sends Ollama `format: json`.
  - DeepSeek summary and trait suggestion normalization accepts plain or fenced JSON object responses.
  - Added regression tests for fenced JSON summary and trait suggestion responses.
  - Updated `contracts/interview_runtime.contract.yaml` and regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Updated `docs/CODEX_HANDOFF.md` and this progress file with current validation state.
- Files changed:
  - `src/interview_runtime.py`
  - `contracts/interview_runtime.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_HANDOFF.md`
  - `docs/CODEX_PROGRESS.md`
  - `tests/test_deepseek_summary.py`
- Validation performed:
  - Local Ollama availability check for `http://127.0.0.1:11434/api/tags`.
  - App-level local DeepSeek summary smoke with synthetic transcript.
  - `python -m pytest tests\test_deepseek_summary.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest`
- Result:
  - Local Ollama had `deepseek-r1:1.5b` installed.
  - Initial smoke exposed `JSONDecodeError` from fenced JSON; fixed by response hardening.
  - Final local smoke returned `summary_status = generated`, one answer summary, and no summary warnings.
  - Focused DeepSeek/coverage suite: `18 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full pytest: `1396 passed`.
- Remaining issues:
  - Manual live GUI finalize smoke and human review of local DeepSeek summary/suggestion quality remain external validation.
  - Model suggestion UI visual smoke remains external validation.

## Historical Flattening Task Breakdown

### Checkpoint 0: Controlled Setup

- Description: Establish progress doc, Definition of Done, file map, checkpoint protocol, validation list, risks.
- Files likely to change: `docs/CODEX_PROGRESS.md`.
- Validation method: read created progress doc; no app tests required for doc-only setup.
- Status: Done.
- Notes: `docs/CODEX_HANDOFF.md` must not be updated unless user asks.

### Checkpoint 1: Current-State Audit

- Description: Re-read current contracts, migration docs, target modules, and status; reconcile handoff with already-completed recent work without editing handoff.
- Files likely to change: `docs/CODEX_PROGRESS.md` only.
- Validation method: `rg` and targeted file reads; record current done/remaining map.
- Status: Done.
- Notes: Must not rely on prior conversation memory when files provide evidence.

### Checkpoint 2: UI Migration Slice

- Description: Move or isolate one safe remaining UI implementation behind `ui_composition`, preserving legacy import compatibility.
- Files likely to change: selected UI source file, `src/ui_composition.py`, legacy wrapper, relevant contracts, system/architecture contracts, coverage matrix, focused tests if needed.
- Validation method: UI/interface tests plus contract review.
- Status: Done.
- Notes: Moved `question_screens.py` implementation behind `ui_composition`; kept legacy wrapper compatibility. Contracts and coverage matrix updated; focused validation passed.

### Checkpoint 3: Interview Runtime Wrapper Review

- Description: Review remaining interview runtime legacy files (`session_context`, `state`, `types`, `bootstrap`) and move/wrap only safe implementation areas.
- Files likely to change: selected interview runtime source file, `src/interview_runtime.py`, legacy wrapper, contracts, system/architecture, coverage matrix.
- Validation method: interview app interface tests, focused controller/session tests, contract review.
- Status: Done.
- Notes: Moved session path-policy, shared state, and runtime type aliases behind `interview_runtime`; kept legacy wrappers importable. Used `InterviewSessionRecordingContext` inside `interview_runtime` so legacy `interview_app.types.InterviewSessionContext` can remain a recording-session alias while `interview_runtime.InterviewSessionContext` remains the path-policy helper.

### Checkpoint 4: Platform Migration Slice

- Description: Move or document one remaining platform config/logging/runtime/storage area behind `platform_services`.
- Files likely to change: selected platform source file, `src/platform_services.py`, legacy wrapper, contracts, system/architecture, coverage matrix.
- Validation method: shared/platform tests, contract review.
- Status: Done.
- Notes: Moved `app_logging.py` implementation behind `platform_services`; kept legacy import wrapper. Preserved redaction, crash-report write behavior, and wrapper reload behavior expected by tests.

### Checkpoint 4b: Platform Migration Slice - Artifact Cleanup

- Description: Move artifact cleanup deletion helpers behind `platform_services`, preserving legacy `artifact_cleanup` import compatibility.
- Files likely to change: `src/platform_services.py`, `src/artifact_cleanup.py`, `contracts/platform_services.contract.yaml`, `contracts/artifact_cleanup.contract.yaml`, `contracts/system.contract.yaml`, `contracts/architecture.contract.yaml`, `docs/contract_test_coverage_matrix.yaml`.
- Validation method: focused artifact cleanup tests, shared facade/interface tests, contract review, import audit.
- Status: Done.
- Notes: Moved `artifact_cleanup.py` implementation behind `platform_services`; kept legacy wrapper compatibility and direct wrapper forwarding coverage.

### Checkpoint 4c: Platform Migration Slice - UX Metrics

- Description: Move UX metrics logging/summary helpers behind `platform_services`, preserving legacy `ux_metrics` import compatibility and telemetry privacy behavior.
- Files likely to change: `src/platform_services.py`, `src/ux_metrics.py`, production imports currently using `ux_metrics`, `contracts/platform_services.contract.yaml`, `contracts/ux_metrics.contract.yaml`, `contracts/system.contract.yaml`, `docs/contract_test_coverage_matrix.yaml`, focused UX metrics tests.
- Validation method: focused UX metrics tests, shared/platform facade tests, contract review, import audit.
- Status: Done.
- Notes: Moved `ux_metrics.py` implementation behind `platform_services`; kept legacy wrapper compatibility and direct wrapper forwarding coverage.

### Checkpoint 4d: Platform Migration Slice - Runtime Wrapper

- Description: Move runtime launcher/crash-wrapper helpers behind `platform_services`, preserving executable legacy `runtime_wrapper.py` behavior.
- Files likely to change: `src/platform_services.py`, `src/runtime_wrapper.py`, `contracts/platform_services.contract.yaml`, `contracts/runtime_wrapper.contract.yaml`, `contracts/system.contract.yaml`, `docs/contract_test_coverage_matrix.yaml`, runtime wrapper tests.
- Validation method: focused runtime wrapper tests, interview root contract tests, shared/platform facade tests, contract review, import/script audit.
- Status: Done.
- Notes: Moved `runtime_wrapper.py` implementation behind `platform_services`; kept legacy wrapper executable and direct wrapper forwarding coverage.

### Checkpoint 4e: Platform Migration Slice - Config Adapters

- Description: Move config JSON validation/normalization helpers behind `platform_services`, preserving legacy `config_adapters` compatibility.
- Files likely to change: `src/platform_services.py`, `src/config_adapters.py`, `src/data_store.py`, `contracts/platform_services.contract.yaml`, `contracts/config_adapters.contract.yaml`, `contracts/data_store.contract.yaml`, `contracts/system.contract.yaml`, `docs/contract_test_coverage_matrix.yaml`, config/data-store tests.
- Validation method: focused config adapter tests, data-store config security tests, shared/platform facade tests, contract review, import audit.
- Status: Done.
- Notes: `platform_services` now owns config parsing/validation helpers; `config_adapters` remains a wrapper, and `data_store` imports from `platform_services`.

### Checkpoint 5: Production Import Audit

- Description: Audit production imports so moved areas prefer five public modules; keep legacy wrappers only for compatibility.
- Files likely to change: production source imports, maybe contracts/system.
- Validation method: `rg` import audit, interface tests, import smoke.
- Status: Done.
- Notes: Production import audit for moved legacy wrappers is clean; remaining wrappers are compatibility adapters.

### Checkpoint 6: Full Automated Regression

- Description: Run full relevant automated suite after migration slices.
- Files likely to change: `docs/CODEX_PROGRESS.md` only unless failures prove in-scope drift.
- Validation method: `python -m pytest`; contract commands.
- Status: Done.
- Notes: Full pytest passes after fixing contract/test drift exposed by full run.

### Checkpoint 7: Manual Smoke / External Validation Record

- Description: Record manual smoke status for Windows recording, onboarding launch/reminders/UI, DOCX/referral/export.
- Files likely to change: `docs/CODEX_PROGRESS.md`.
- Validation method: user/manual run evidence or recorded blocker.
- Status: Done.
- Notes: Safe noninteractive import/CLI smokes passed; hardware and human GUI manual smokes were later tested and verified by the user.

### Checkpoint 8: Maintainer Docs Finalization

- Description: Update maintainer docs after migration shape is final.
- Files likely to change: `docs/README.md`, `docs/flattening_baseline.md`, `docs/flattening_migration_map.md`.
- Validation method: doc review plus contract consistency.
- Status: Done.
- Notes: Maintainer docs now reflect current five-module surface, wrapper status, validation, and manual-smoke limitations. `docs/CODEX_HANDOFF.md` was not updated.

### Checkpoint 9: Final Wrapper Cleanup Decision

- Description: Remove retired wrappers only if production imports are audited clean and cleanup is explicitly safe.
- Files likely to change: legacy wrappers, retired contracts, system/architecture, coverage matrix.
- Validation method: import audit, full tests, contract review.
- Status: Done for approved retired wrappers.
- Notes: Retired platform, scoring/reporting, UI, interview root, and onboarding wrappers/contracts were removed in validated batches. Remaining wrappers are launch-critical or still-active adapters.

## Validation Commands

Use focused commands after each slice:

```powershell
python tools\regenerate_contract_test_matrix.py
python tools\check_contract_review.py
python -m pytest tests\test_flattened_module_facades.py tests\test_ui_contract_interfaces.py tests\test_interview_app_contract_interfaces.py tests\test_shared_module_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py
python -m pytest tests\test_history_data_grid.py tests\test_interview_history_actions.py tests\test_interview_audio_recorder_start_recording.py tests\test_interview_audio_recorder_process_cleanup.py
```

Use broader commands before full completion:

```powershell
python -m pytest
```

Use audit commands as needed:

```powershell
rg -n "from (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid)" src
rg -n "from ui_components\.history_data_grid|import ui_components\.history_data_grid" src
"C:\Program Files\Git\cmd\git.exe" status --short -- src contracts tests docs tools
```

Manual validation before final completion:

- launch interview app
- run Windows recording smoke on actual audio hardware: user manually tested and verified
- run onboarding launch/reminder/UI smoke: user manually tested and verified
- verify generated interview-notes DOCX path used by referral/export flow: user manually tested and verified

## Risks and Assumptions

- Worktree is very dirty; many generated/private artifacts exist. Avoid staging broad paths.
- `git` is not on PowerShell PATH; use `C:\Program Files\Git\cmd\git.exe`.
- Contracts are source of truth; contract drift is failure, not noise.
- Manual hardware/UI smoke was completed by user action after automated validation.
- Candidate/interview/onboarding data is privacy-sensitive; avoid logging or exposing generated artifacts.
- Legacy imports must keep working until final cleanup.
- `docs/CODEX_HANDOFF.md` is updated for current approved wrapper deletion state.
- Some handoff items may now be stale because recent code changes moved `HistoryDataGrid` and retired retranscription; Checkpoint 1 must verify from current files.

## Progress Log

### Checkpoint 0: Controlled Setup

- Status: Done
- Started: 2026-06-14
- What changed: created controlled progress doc.
- Files changed: `docs/CODEX_PROGRESS.md`.
- Validation performed: re-read `docs/CODEX_PROGRESS.md`.
- Result: progress doc contains Definition of Done, relevant files/modules, checkpoint breakdown, validation commands, risks/assumptions, and protocol notes.
- Remaining issues: start Checkpoint 1 current-state audit before next code move.

### Checkpoint 1: Current-State Audit

- Status: Done
- Started: 2026-06-14
- What changed: audited current handoff gaps against current files; no app code changed.
- Files changed: `docs/CODEX_PROGRESS.md`.
- Validation performed:
  - Re-read `docs/CODEX_HANDOFF.md`.
  - Re-read `AGENTS.md`.
  - Checked current source evidence with `rg` for moved UI/history-grid symbols and remaining legacy imports.
  - Ran `python tools\check_contract_review.py`.
- Result:
  - Contract review passed: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Current files prove `HistoryDataGrid` implementation now lives in `src/ui_composition.py`.
  - Current files prove `src/ui_components/history_data_grid.py` forwards to `ui_composition`.
  - Current production imports of `ui_components.history_data_grid` were not found under `src`.
  - `docs/CODEX_HANDOFF.md` is stale for `ui_components/history_data_grid.py` and retranscription notes, but user instructed not to update it.
  - Remaining production imports still show platform/UI migration candidates:
    - `src/data_store.py` imports `config_adapters`.
    - `src/interview_runtime.py` imports `app_content` and `ux_metrics`.
    - `src/interview_app/bootstrap.py` imports `app_content`.
    - `src/onboarding_operations.py` imports `ux_metrics`.
    - `src/interview_app/views/start_screen_view.py` imports `app_content` and `ux_metrics`.
    - `src/question_screens.py` imports `app_content`.
    - `src/scoring_reporting.py` imports `app_content`.
    - `src/ui_composition.py` imports `app_content`.
    - `src/ui_windows.py` imports `app_content`.
- Remaining issues:
  - Next code checkpoint should pick one safe slice, likely `question_screens.py` into `ui_composition` or a smaller platform import/move after re-reading relevant files.
  - Full pytest and manual smokes remain unverified.

### Checkpoint 2: UI Migration Slice

- Status: Done
- Started: 2026-06-14
- Intended change: move `question_screens.py` question-screen implementation behind `ui_composition` while keeping the legacy `question_screens` import path working.
- What changed:
  - `src/ui_composition.py` now owns `render_qualification_box`, `validate_and_store_qualification`, `TraitScreenUI`, and `CustomQuestionScreenUI`.
  - `src/question_screens.py` is now a thin re-export wrapper.
  - `contracts/ui_composition.contract.yaml` now includes the question-screen functions/classes and `scoring_reporting` dependency.
  - `contracts/question_screens.contract.yaml` now describes the module as a compatibility wrapper depending only on `ui_composition`.
  - `contracts/system.contract.yaml` now points dependency direction from `question_screens` to `ui_composition`.
  - `docs/contract_test_coverage_matrix.yaml` was regenerated.
- Files changed:
  - `src/ui_composition.py`
  - `src/question_screens.py`
  - `contracts/ui_composition.contract.yaml`
  - `contracts/question_screens.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code and contract files after the move.
  - `python -m pytest tests\test_trait_screen_sections.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_trait_screen_sections.py tests\test_flattened_module_facades.py tests\test_ui_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python -m pytest tests\test_interview_app_contract_interfaces.py tests\test_shared_module_contract_interfaces.py`
  - `rg -n "from question_screens|import question_screens|from ui_components\.history_data_grid|import ui_components\.history_data_grid" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- src contracts tests docs tools`
- Result:
  - Trait-section test: `2 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused UI/facade/coverage suite: `205 passed`.
  - Broader interview/shared interface suite: `243 passed`.
  - Import audit found no production imports of `question_screens` or `ui_components.history_data_grid` under `src`.
  - Git status confirms the worktree remains very dirty with many unrelated/generated/private changes; nothing was staged.
- Remaining issues:
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
  - Next recommended checkpoint: Checkpoint 3 Interview Runtime Wrapper Review.

### Checkpoint 3: Interview Runtime Wrapper Review

- Status: Done
- Started: 2026-06-14
- Intended change: move one safe remaining interview runtime implementation area behind `interview_runtime`, preserving legacy imports and launch behavior.
- Decision:
  - Move `interview_app.session_context.InterviewSessionContext` path-policy implementation into `src/interview_runtime.py`.
  - Move `interview_app.state.AppSharedState` into `src/interview_runtime.py`.
  - Move `interview_app.types` runtime dataclasses/TypedDict aliases into `src/interview_runtime.py`.
  - Use `InterviewSessionRecordingContext` as the target-module name for the recording-session dataclass; legacy `interview_app.types.InterviewSessionContext` remains an alias for compatibility.
  - Update production imports in `interview_app/bootstrap.py`, `interview_app/history_actions.py`, and `interview_app/__init__.py` to prefer `interview_runtime`.
- Security notes:
  - Preserve existing base-dir resolution, write-probe behavior, and no new logging of candidate/interview data.
- Files changed:
  - `src/interview_runtime.py`
  - `src/interview_app/session_context.py`
  - `src/interview_app/state.py`
  - `src/interview_app/types.py`
  - `src/interview_app/bootstrap.py`
  - `src/interview_app/history_actions.py`
  - `src/interview_app/__init__.py`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/interview_app_session_context.contract.yaml`
  - `contracts/interview_app_state.contract.yaml`
  - `contracts/interview_app_types.contract.yaml`
  - `contracts/interview_app_bootstrap.contract.yaml`
  - `contracts/interview_app_history_actions.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code and contract files after edits.
  - `python -m pytest tests\test_interview_app_session_context.py tests\test_interview_app_controllers.py tests\test_flattened_module_facades.py tests\test_interview_app_contract_interfaces.py tests\test_shared_module_contract_interfaces.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n "from interview_app\.(session_context|state|types)|import interview_app\.(session_context|state|types)|from \.state|from \.types|from \.session_context" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused runtime/interface suite: `286 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Coverage-matrix tests: `7 passed`.
  - Import audit found no production imports of moved `interview_app.session_context`, `interview_app.state`, or `interview_app.types` wrappers under `src`.
  - Git status still shows broader dirty worktree/untracked target files; nothing staged.
- Remaining issues:
  - Full pytest still not run for final goal.
  - Platform migration remains incomplete; next recommended checkpoint is Checkpoint 4 Platform Migration Slice.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.

### Checkpoint 4: Platform Migration Slice

- Status: Done
- Started: 2026-06-14
- Intended change: move one safe platform implementation behind `platform_services`, preserving legacy import compatibility.
- Decision:
  - Move `app_logging` implementation into `src/platform_services.py`.
  - Convert `src/app_logging.py` to a thin wrapper.
  - Update contracts and system dependencies so `app_logging` depends on `platform_services`, not vice versa.
  - Preserve compatibility with `importlib.reload(app_logging)` by resetting moved logging globals only on wrapper reload, not first import.
- Security notes:
  - Preserve log redaction for candidate/contact fields.
  - Preserve crash report behavior; do not add candidate/interview data logging.
- Files changed:
  - `src/platform_services.py`
  - `src/app_logging.py`
  - `contracts/platform_services.contract.yaml`
  - `contracts/app_logging.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code and contract files after edits.
  - `python -m pytest tests\test_app_logging.py tests\test_app_logging_crash_report.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py tests\test_shared_module_contract_interfaces.py`
  - `rg -n "from app_logging|import app_logging" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused logging/platform/shared suite: `100 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Coverage/shared suite after regeneration: `73 passed`.
  - Import audit found no production imports of `app_logging` under `src`.
  - Git status still shows broader dirty worktree/untracked target files; nothing staged.
- Remaining issues:
  - Platform migration still has `artifact_cleanup`, `runtime_wrapper`, `ux_metrics`, `config_adapters`, `data_store`, and `app_content` review/move candidates.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.

### Checkpoint 4b: Platform Migration Slice - Artifact Cleanup

- Status: Done
- Started: 2026-06-14
- Intended change: move `artifact_cleanup` implementation into `platform_services` while keeping `artifact_cleanup` as a compatibility wrapper.
- What changed:
  - `src/platform_services.py` now owns artifact path extraction and safe cleanup/delete helpers.
  - `src/artifact_cleanup.py` is now a thin re-export wrapper.
  - Added `contracts/artifact_cleanup.contract.yaml` for the wrapper surface.
  - Updated `contracts/platform_services.contract.yaml` and `contracts/system.contract.yaml` so dependency direction is `artifact_cleanup` -> `platform_services`.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Updated `tests/test_artifact_cleanup.py` to assert wrapper functions forward to `platform_services`.
- Security notes:
  - Preserve containment checks before deletion.
  - Preserve narrow extension/token allowlist.
  - Do not add logging of candidate/interview artifact paths.
- Files changed:
  - `src/platform_services.py`
  - `src/artifact_cleanup.py`
  - `tests/test_artifact_cleanup.py`
  - `contracts/platform_services.contract.yaml`
  - `contracts/artifact_cleanup.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code, test, and contract files after edits.
  - `python -m pytest tests\test_artifact_cleanup.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_artifact_cleanup.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n "from artifact_cleanup|import artifact_cleanup" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused artifact cleanup test before wrapper-forward assertion: `2 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused platform/interface/coverage suite after test update: `106 passed`.
  - Import audit found no production imports of the legacy `artifact_cleanup` module under `src`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Platform migration still has `runtime_wrapper`, `ux_metrics`, `config_adapters`, `data_store`, and `app_content` review/move candidates.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
- Notes:
  - Re-read `AGENTS.md`, `src/platform_services.py`, `src/artifact_cleanup.py`, `tests/test_artifact_cleanup.py`, `contracts/platform_services.contract.yaml`, `contracts/system.contract.yaml`, and `contracts/architecture.contract.yaml`.
  - `contracts/artifact_cleanup.contract.yaml` was missing before this checkpoint and is now added with the wrapper contract.

### Checkpoint 4c: Platform Migration Slice - UX Metrics

- Status: Done
- Started: 2026-06-14
- Intended change: move `ux_metrics` implementation into `platform_services`, keep `ux_metrics` as a compatibility wrapper, and update production imports to prefer `platform_services`.
- What changed:
  - `src/platform_services.py` now owns UX metrics constants, `MonthlyMetricsSummary`, `UxMetricsLogger`, monthly summary helpers, and telemetry sanitizers.
  - `src/ux_metrics.py` is now a thin re-export wrapper.
  - Updated production imports in `src/interview_runtime.py`, `src/onboarding_operations.py`, and `src/interview_app/views/start_screen_view.py` to prefer `platform_services`.
  - Added `contracts/ux_metrics.contract.yaml` for the wrapper surface.
  - Updated `contracts/platform_services.contract.yaml` and `contracts/system.contract.yaml` so dependency direction is `ux_metrics` -> `platform_services`.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Updated `tests/test_ux_metrics.py` to assert wrapper functions/classes forward to `platform_services`.
- Security notes:
  - Preserve telemetry sanitization for candidate/employee names, notes/free text, email, phone, address, resume paths, and email-like values.
  - Do not add logging of raw candidate/interview/onboarding data.
- Files changed:
  - `src/platform_services.py`
  - `src/ux_metrics.py`
  - `src/interview_runtime.py`
  - `src/onboarding_operations.py`
  - `src/interview_app/views/start_screen_view.py`
  - `tests/test_ux_metrics.py`
  - `contracts/platform_services.contract.yaml`
  - `contracts/ux_metrics.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code, test, and contract files after edits.
  - `python -m pytest tests\test_ux_metrics.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_ux_metrics.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_interview_app_contract_interfaces.py tests\test_ui_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n "from ux_metrics|import ux_metrics" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused UX metrics suite before wrapper-forward assertion: `6 passed`.
  - Focused UX metrics suite after wrapper-forward assertion: `7 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused platform/UI/interview/shared/coverage suite: `453 passed`.
  - Import audit found no production imports of the legacy `ux_metrics` module under `src`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Platform migration still has `runtime_wrapper`, `config_adapters`, `data_store`, and `app_content` review/move candidates.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
- Notes:
  - Re-read `docs/CODEX_PROGRESS.md`, `docs/CODEX_HANDOFF.md`, `src/ux_metrics.py`, `src/platform_services.py`, `tests/test_ux_metrics.py`, `contracts/platform_services.contract.yaml`, and `contracts/system.contract.yaml`.
  - `contracts/ux_metrics.contract.yaml` was missing before this checkpoint and is now added with the wrapper contract.

### Checkpoint 4d: Platform Migration Slice - Runtime Wrapper

- Status: Done
- Started: 2026-06-14
- Intended change: move `runtime_wrapper` implementation into `platform_services`, keep `runtime_wrapper.py` as executable compatibility wrapper.
- What changed:
  - `src/platform_services.py` now owns runtime wrapper CLI parsing, target execution, runtime logging setup, fault/trace logging, exception hooks, crash-report writing, and traceback-origin extraction.
  - `src/runtime_wrapper.py` is now a thin executable re-export wrapper that still calls `main()` under `if __name__ == "__main__"`.
  - Updated `contracts/runtime_wrapper.contract.yaml` as a wrapper contract depending on `platform_services`.
  - Updated `contracts/platform_services.contract.yaml` and `contracts/system.contract.yaml` so dependency direction is `runtime_wrapper` -> `platform_services`.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Updated `tests/test_runtime_wrapper.py` to assert wrapper functions forward to `platform_services`.
- Security notes:
  - Preserve missing-target failure behavior.
  - Preserve crash-report path under `logs/crash-reports`.
  - Do not add logging of candidate/interview data.
- Files changed:
  - `src/platform_services.py`
  - `src/runtime_wrapper.py`
  - `tests/test_runtime_wrapper.py`
  - `contracts/platform_services.contract.yaml`
  - `contracts/runtime_wrapper.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code, test, and contract files after edits.
  - `python -m pytest tests\test_runtime_wrapper.py tests\test_interview_root_contracts.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_runtime_wrapper.py tests\test_interview_root_contracts.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n "from runtime_wrapper|import runtime_wrapper" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused runtime/root suite: `22 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused platform/root/coverage suite: `125 passed`.
  - Import audit found no production imports of the legacy `runtime_wrapper` module under `src`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Platform migration still has `config_adapters`, `data_store`, and `app_content` review/move candidates.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
- Notes:
  - Re-read `src/runtime_wrapper.py`, `tests/test_runtime_wrapper.py`, `tests/test_interview_root_contracts.py`, `contracts/runtime_wrapper.contract.yaml`, `contracts/platform_services.contract.yaml`, and `docs/CODEX_PROGRESS.md`.

### Checkpoint 4e: Platform Migration Slice - Config Adapters

- Status: Done
- Started: 2026-06-14
- Intended change: move `config_adapters` implementation into `platform_services`, keep `config_adapters.py` as compatibility wrapper, and update production import in `data_store.py`.
- What changed:
  - `src/platform_services.py` now owns config asset inventory, JSON loading, config validation, question override normalization, and `ConfigValidationError`.
  - `src/config_adapters.py` is now a thin re-export wrapper.
  - `src/data_store.py` now imports config helpers from `platform_services`.
  - Updated config adapter, data store, platform services, and system contracts for the new dependency direction.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Added a wrapper-forwarding assertion to `tests/test_config_adapters.py`.
- Security notes:
  - Preserve untrusted config shape validation and size limit.
  - Preserve sanitized error messages without raw config payload echo.
  - Preserve corrupt question override archive/reset behavior through `data_store`.
- Files changed:
  - `src/platform_services.py`
  - `src/config_adapters.py`
  - `src/data_store.py`
  - `tests/test_config_adapters.py`
  - `contracts/platform_services.contract.yaml`
  - `contracts/config_adapters.contract.yaml`
  - `contracts/data_store.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code, test, and contract files after edits.
  - `python -m pytest tests\test_config_adapters.py tests\test_data_store_config_security.py tests\test_question_overrides_store.py tests\test_storage_persistence.py tests\test_whisper_settings.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_config_adapters.py tests\test_data_store_config_security.py tests\test_question_overrides_store.py tests\test_storage_persistence.py tests\test_whisper_settings.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n "from config_adapters|import config_adapters" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused config/data store suite: `31 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Focused platform/shared/coverage suite: `134 passed`.
  - Import audit found no production imports of the legacy `config_adapters` module under `src`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Platform migration still has `data_store` and `app_content` review/move candidates.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
- Notes:
  - Re-read `src/config_adapters.py`, `src/data_store.py`, `tests/test_config_adapters.py`, `tests/test_data_store_config_security.py`, `contracts/config_adapters.contract.yaml`, `contracts/data_store.contract.yaml`, `contracts/platform_services.contract.yaml`, and `contracts/system.contract.yaml`.

### Checkpoint 5: Production Import Audit - App Content Slice

- Status: Partially done
- Started: 2026-06-16
- Intended change: audit remaining legacy production imports and move the shallow `app_content` implementation behind `platform_services`.
- Architecture decision:
  - `app_content` was shallow after prior moves: callers needed individual constants/helpers, and implementation belonged with shared platform config/content.
  - Keep `app_content.py` as a compatibility adapter so legacy imports remain valid.
  - Do not remove other wrappers during this slice.
- What changed:
  - `src/platform_services.py` now owns app content paths, UI copy constants, no-example follow-up data, date/filename helpers, and timestamp helper.
  - `src/app_content.py` is now a thin re-export wrapper.
  - Updated production imports in `src/interview_runtime.py`, `src/scoring_reporting.py`, `src/ui_composition.py`, `src/ui_windows.py`, `src/interview_app/bootstrap.py`, and `src/interview_app/views/start_screen_view.py` to prefer `platform_services`.
  - Added `contracts/app_content.contract.yaml` and updated related module/system contracts.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
  - Added focused wrapper/behavior tests in `tests/test_app_content.py`.
- Security notes:
  - Preserve filename sanitization.
  - Preserve path constants; do not add new file writes.
  - Preserve interview/onboarding privacy; no new logging or artifact output.
- Files changed:
  - `src/platform_services.py`
  - `src/app_content.py`
  - `src/interview_runtime.py`
  - `src/scoring_reporting.py`
  - `src/ui_composition.py`
  - `src/ui_windows.py`
  - `src/interview_app/bootstrap.py`
  - `src/interview_app/views/start_screen_view.py`
  - `tests/test_app_content.py`
  - `contracts/app_content.contract.yaml`
  - `contracts/platform_services.contract.yaml`
  - `contracts/interview_runtime.contract.yaml`
  - `contracts/scoring_reporting.contract.yaml`
  - `contracts/ui_composition.contract.yaml`
  - `contracts/interview_app_bootstrap.contract.yaml`
  - `contracts/interview_app_views_start_screen_view.contract.yaml`
  - `contracts/ui_windows.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code, test, and contract files after edits.
  - `python -m pytest tests\test_app_content.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_ui_contract_interfaces.py tests\test_interview_app_contract_interfaces.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_app_content.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n -- "from app_content|import app_content" src`
  - Broader wrapper import audit for platform/UI legacy wrappers.
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused app-content/interface suite: `441 passed`.
  - Contract review after regeneration: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Coverage/app-content tests: `9 passed`.
  - Import audit found no production imports of legacy `app_content` under `src`.
  - Broader wrapper import audit found remaining production imports of `storage_utils` in `src/data_store.py` and `docx_compat` in `src/scoring_reporting.py`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Checkpoint 5 remains in progress until remaining production imports of moved wrappers are handled or documented.
  - `src\data_store.py` still imports `storage_utils.atomic_write_json`.
  - `src\scoring_reporting.py` still imports `docx_compat.Document`.
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.

### Checkpoint 5: Production Import Audit - Final Wrapper Import Cleanup

- Status: Done
- Started: 2026-06-16
- Intended change: remove remaining production imports of moved legacy wrapper modules after the app-content slice.
- What changed:
  - `src/data_store.py` now imports `atomic_write_json` directly from `platform_services` instead of `storage_utils`.
  - `src/scoring_reporting.py` now imports `Document` directly from `platform_services` instead of `docx_compat`.
  - Updated `contracts/data_store.contract.yaml` and `contracts/system.contract.yaml` to remove stale wrapper dependencies.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
- Security notes:
  - Preserve atomic JSON write behavior.
  - Preserve DOCX backend selection through `platform_services.Document`.
  - No new file writes, logging, shell execution, or private-data exposure added.
- Files changed:
  - `src/data_store.py`
  - `src/scoring_reporting.py`
  - `contracts/data_store.contract.yaml`
  - `contracts/system.contract.yaml`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read modified code and contract files after edits.
  - `python -m pytest tests\test_storage_persistence.py tests\test_data_store_config_security.py tests\test_reporting_export.py tests\test_referral_packet.py tests\test_flattened_module_facades.py tests\test_shared_module_contract_interfaces.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `rg -n -- "from (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils)|import (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils)" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused storage/reporting/interface/coverage suite: `135 passed`.
  - Contract review after regeneration: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Coverage tests after regeneration: `7 passed`.
  - Production import audit found no remaining imports of moved legacy wrapper modules in `src`.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Full pytest still not run for final goal.
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
  - Maintainer docs still need finalization after full automated validation.

### Checkpoint 6: Full Automated Regression

- Status: Done
- Started: 2026-06-16
- Intended change: run full automated validation after migration/import cleanup and fix only failures related to current drift.
- What changed:
  - Restored `OnboardingTrackerApp._bind_task_widget_visibility` to bind focus events through `onboarding_operations.scroll_widget_into_view`.
  - Updated `contracts/onboarding_app.contract.yaml` for the restored method.
  - Made `tests/test_setup_and_run_contract.py` parse first modern PowerShell function definitions, inline parameters, and nested PowerShell `param(...)` attributes correctly.
  - Adjusted setup log line formatting in `setup_and_run.ps1` so contract parsing does not mistake message interpolation for a parameter.
  - Made transcription fixture verification resolve fixtures relative to the test file and exclude `_full.wav` source recordings from segment-only checks.
  - Regenerated `docs/contract_test_coverage_matrix.yaml`.
- Security notes:
  - No credential handling, shell privilege, path validation, or private-data logging behavior was weakened.
  - Onboarding focus-scroll fix only binds UI focus behavior; no storage or network behavior changed.
  - Transcription fixture change affects test selection only; fixture files were not modified.
- Files changed:
  - `src/onboarding_app.pyw`
  - `contracts/onboarding_app.contract.yaml`
  - `contracts/setup_and_run.contract.yaml`
  - `setup_and_run.ps1`
  - `tests/test_setup_and_run_contract.py`
  - `tests/test_transcription_recording_verification.py`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Initial `python -m pytest` found 4 failures: onboarding task focus-scroll method missing, setup script contract parser drift, and transcription fixture tests using the wrong fixture directory/source file set.
  - `python -m pytest tests\test_onboarding_task_scrolling.py tests\test_setup_and_run_contract.py tests\test_transcription_recording_verification.py`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest`
  - Re-read modified code, test, contract, setup script, and progress files after edits.
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- ...checkpoint files...`
- Result:
  - Focused failure suite after fixes: `9 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full pytest at that checkpoint: `1163 passed`, `103 skipped`, `18 warnings`.
  - Warnings are existing `datetime.utcnow()` deprecation warnings in `src/interview_runtime.py`; not fixed in this checkpoint.
  - Scoped git status shows expected modified/untracked checkpoint files; nothing staged.
- Remaining issues:
  - Manual Windows recording, onboarding UI/reminder, and DOCX/referral/export smokes remain unverified.
  - Maintainer docs still need finalization after manual smoke status is recorded.

### Checkpoint 7: Manual Smoke / External Validation Record

- Status: Done
- Started: 2026-06-16
- Intended change: record manual smoke status and collect any safe local smoke evidence without claiming hardware or human UI validation that was not performed.
- What changed:
  - Updated `docs/CODEX_PROGRESS.md` only.
- Files changed:
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read `docs/CODEX_PROGRESS.md`, `AGENTS.md`, and launch-related source/test references.
  - Imported `src/interview_app.pyw` via `SourceFileLoader` and verified `InterviewApp` is present.
  - Imported `src/onboarding_app.pyw` via `SourceFileLoader` and verified `OnboardingTrackerApp` and `_build_arg_parser` are present.
  - Parsed onboarding reminder CLI args for `--run-reminders --run-source smoke`.
  - Parsed runtime wrapper args for target `src/interview_app.pyw` and app root `.` without launching GUI.
  - `python -m pytest tests\test_reporting_export.py tests\test_referral_packet.py tests\test_offer_workflow_onboarding.py tests\test_interview_audio_recorder_start_recording.py tests\test_interview_audio_recorder_process_cleanup.py tests\test_onboarding_launch.py tests\test_onboarding_reminder_runner.py tests\test_onboarding_scheduler.py tests\test_onboarding_validation_feedback.py`
- Result:
  - Interview app import smoke passed.
  - Onboarding app import smoke passed.
  - Onboarding reminder CLI parser smoke passed.
  - Runtime wrapper parser smoke passed.
  - Targeted recording/reminder/DOCX/referral/export automated suite: `44 passed`.
  - A direct `runtime_wrapper.py --check-target-only` attempt failed because that option does not exist; reran parser smoke instead and did not change code for the invalid option.
- Manual status:
  - Windows recording smoke on actual microphone/system audio hardware: user manually tested and verified after automated validation.
  - Onboarding launch/reminder/UI smoke through the live Tk UI: user manually tested and verified after automated validation.
  - Interview-notes DOCX/referral/export live workflow smoke through the GUI: user manually tested and verified after automated validation.
- Remaining issues:
  - Maintainer docs still need finalization.
  - Final completion audit still required after docs update.

### Checkpoint 8: Maintainer Docs Finalization

- Status: Done
- Started: 2026-06-16
- Intended change: update maintainer docs after migration shape is final without changing `docs/CODEX_HANDOFF.md`.
- What changed:
  - Updated `docs/flattening_baseline.md` with current five-module ownership, wrapper policy, validation status, and manual smoke limitations.
  - Updated `docs/flattening_migration_map.md` with completed physical moves, production import status, and remaining external validation note.
  - Updated `docs/README.md` navigation and usage guidance to include flattened architecture docs.
- Files changed:
  - `docs/README.md`
  - `docs/flattening_baseline.md`
  - `docs/flattening_migration_map.md`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - Re-read `docs/README.md`, `docs/flattening_baseline.md`, `docs/flattening_migration_map.md`, and `contracts/system.contract.yaml`.
  - `rg -n "Remaining Physical Moves|Validation Status|Current Checkpoint|Flattened architecture|Manual" docs\README.md docs\flattening_baseline.md docs\flattening_migration_map.md`
  - `python tools\check_contract_review.py`
- Result:
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Maintainer docs now state the five target modules are preferred production import surface, legacy wrappers remain for compatibility, full pytest passed, and manual hardware/live-GUI checks were tested and verified by the user.
- Remaining issues:
  - Final wrapper cleanup decision remains.
  - Final completion audit still required.

### Checkpoint 9: Final Wrapper Cleanup Decision (Superseded)

- Status: Superseded by approved wrapper deletion audit below
- Started: 2026-06-16
- Intended change: decide whether retired compatibility wrappers should be removed.
- Decision:
  - Initial decision was to keep wrappers until explicit approval.
  - User later approved wrapper deletion, completed in validated batches below.
- Files changed:
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `rg -n -- "from (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)|import (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)" src`
  - `rg -n "Legacy import paths|Legacy wrappers|wrapper|compatibility|Remove retired wrappers|Delete legacy wrappers|final cleanup" docs\CODEX_PROGRESS.md docs\flattening_baseline.md docs\flattening_migration_map.md docs\CODEX_HANDOFF.md`
  - Re-read `tests/test_flattened_module_facades.py`.
- Result:
  - Production import audit found no remaining imports of audited legacy wrapper modules under `src`.
  - Later approved cleanup removed retired wrappers and updated facade tests.
- Remaining issues:
  - Final completion audit remains.

### Final Completion Audit (Pre-Deletion, Superseded)

- Status: Done
- Started: 2026-06-16
- Scope checked:
  - This audit predated explicit wrapper deletion approval.
  - Later audit below is current source for final wrapper deletion state.
  - Production imports now prefer the five flattened public modules for audited moved surfaces.
- Definition of Done result:
  - Five public modules are the preferred production import surface for migrated code.
  - Remaining physical moves are completed behind the five modules or documented as intentionally retained compatibility surfaces.
  - Moved legacy modules are thin compatibility wrappers.
  - Contracts and architecture/system dependency records pass contract review.
  - Coverage matrix is regenerated and passes review.
  - Production import audit found no remaining imports of audited moved legacy wrapper modules under `src`.
  - Full automated validation passes.
  - Manual hardware/live-GUI smoke gaps were closed by user manual validation.
  - Maintainer docs are updated.
  - Retired wrappers were still pending explicit deletion approval at this point.
  - Generated/private dirty artifacts were not staged or intentionally cleaned.
- Validation performed:
  - `python -m pytest`
  - `python tools\check_contract_review.py`
  - `rg -n -- "from (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)|import (app_content|config_adapters|data_store|app_logging|artifact_cleanup|runtime_wrapper|ux_metrics|question_screens|ui_windows|ui_components\.history_data_grid|docx_compat|path_validation|storage_utils|candidate_profile|candidate_title|director_email_draft|director_referral_service|email_security|integration_export|offer_letter|referral_packet|reporting|template_placeholders|trait_definition_loader|trait_scoring_adapter|trait_signal_schema|trait_signal_state|interview_state|interview_session_store|transcript_accumulator|transcription_diagnostics)" src`
  - `& "C:\Program Files\Git\cmd\git.exe" status --short -- docs/CODEX_PROGRESS.md docs/README.md docs/flattening_baseline.md docs/flattening_migration_map.md docs/contract_test_coverage_matrix.yaml src contracts tests tools setup_and_run.ps1`
- Result:
  - Full pytest at this checkpoint: `1163 passed`, `103 skipped`, `18 warnings`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Production import audit: no hits.
  - Git status remains very dirty, including many generated `__pycache__` files and pre-existing broad source/contract/test changes; nothing was staged.
- Remaining issues:
  - Existing `datetime.utcnow()` deprecation warnings remain in `src/interview_runtime.py`; not part of this flattening goal.

### Manual Validation Readback

- Status: Done
- Updated: 2026-06-16
- Evidence source: user manually tested and verified the remaining hardware/live-GUI smokes after automated validation.
- Verified:
  - Windows microphone/system-audio recording smoke on target hardware.
  - Live Tk onboarding launch/reminder/UI workflow.
  - Live GUI DOCX/referral/export workflow.
- Files changed:
  - `docs/CODEX_HANDOFF.md`
  - `docs/CODEX_PROGRESS.md`
  - `docs/flattening_baseline.md`
  - `docs/flattening_migration_map.md`

### Approved Wrapper Deletion Completion Audit

- Status: Done
- Updated: 2026-06-16
- Scope checked:
  - User explicitly approved wrapper deletion after original flattening checkpoint.
  - Retired platform, scoring/reporting, UI, interview root, and onboarding wrapper modules/contracts were removed.
  - Remaining wrappers/adapters are limited to launch-critical or still-active modules documented in `docs/CODEX_HANDOFF.md`.
  - Contracts, system graph, architecture graph, coverage matrix, tests, and maintainer docs were updated in same cleanup.
- Definition of Done result:
  - Five public modules remain preferred production import surface.
  - Retired wrappers approved for deletion are gone from `src/`, `contracts/`, and generated coverage matrix.
  - Remaining adapter modules are documented as future cleanup only after launch/runtime role is understood.
  - Contract review passes.
  - Full automated validation passes.
  - Dirty/generated/private artifacts were not staged.
- Validation performed:
  - `python -m pytest -q`
  - `python tools\check_contract_review.py`
  - targeted stale wrapper import/contract `rg` audits across `src`, `tests`, `tools`, `contracts`, and migration docs.
- Result:
  - Full pytest: `1250 passed`, `103 skipped`, `18 warnings`, `8 subtests passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Stale wrapper audits found no deleted wrapper import paths or deleted wrapper contract references; expected textual mentions remain only as historical/current migration notes or public symbol names.
- Remaining issues:
- Existing `datetime.utcnow()` deprecation warnings remain in `src/interview_runtime.py`; separate task.
- Worktree remains very dirty with generated/private artifacts; avoid broad staging.

### VB-CABLE Setup Idempotency and First-Run Routing Instructions

- Status: Done
- Updated: 2026-06-16
- Scope checked:
  - Continued from `docs/CODEX_HANDOFF.md`; flattening migration was not restarted.
  - Fixed setup idempotency by returning immediately after the modern setup UI exits so the stale legacy VB-CABLE installer tail cannot run.
  - Added one-time Windows audio-routing instructions after VB-CABLE is detected or user-confirmed installed.
  - Updated setup contract and focused parser/contract tests.
- Security notes:
  - No secrets, candidate data, onboarding records, export artifacts, or logs were intentionally read or changed.
  - No new privilege escalation, shell execution, or download path was added.
  - Manual live setup smoke still requires a Windows operator because running the setup UI may launch installers/download pages.
- Files changed:
  - `setup_and_run.ps1`
  - `contracts/setup_and_run.contract.yaml`
  - `tests/test_setup_and_run_contract.py`
  - `docs/contract_test_coverage_matrix.yaml`
  - `docs/CODEX_HANDOFF.md`
  - `docs/CODEX_PROGRESS.md`
- Validation performed:
  - `python -m pytest tests\test_setup_and_run_contract.py`
  - PowerShell AST parse of `setup_and_run.ps1`
  - `python tools\regenerate_contract_test_matrix.py`
  - `python -m pytest tests\test_setup_and_run_contract.py tests\test_contract_coverage_matrix.py tests\test_regenerate_contract_test_matrix.py`
  - `python tools\check_contract_review.py`
  - `python -m pytest`
- Result:
  - Setup contract tests: `5 passed`.
  - PowerShell parse: `PowerShell parse OK`.
  - Focused setup/coverage suite: `12 passed`.
  - Contract review: `[PASS] baseline`, `[PASS] locked`, `[PASS] schema`, `[PASS] coverage-matrix`.
  - Full pytest: `1252 passed`, `103 skipped`, `18 warnings`.
- Remaining issues:
  - Manual Windows setup smoke not run in this automated session.
  - Next recommended roadmap item is removing separate transcript document generation.
