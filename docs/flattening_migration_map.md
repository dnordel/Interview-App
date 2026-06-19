# Flattening Migration Map

This repository is being flattened in staged batches. The five new domain modules
are compatibility facades first. Retired wrappers are removed during final
cleanup after production imports move to the five public modules.

## Target Modules

| Target module | Legacy modules initially exposed |
|---|---|
| `platform_services` | `app_logging`, `data_store`, `docx_compat`, `path_validation`, `runtime_wrapper`, `storage_utils` |
| `ui_composition` | `ui_windows`, interview router/shell/view modules |
| `interview_runtime` | interview state/session modules, audio recorder, transcription diagnostics/queue/executor/processor/writer/summary, interview controllers, history actions/controllers, finalize context/gateways/pipeline |
| `scoring_reporting` | `trait_scoring_adapter` |
| `onboarding_operations` | onboarding models/storage/migrations, scheduling, reminders, notifier, guardrails, dashboard/actions, launch, task filters, template reference, scroll helpers, UI helpers |

## Wrapper Order

1. Add flat facade modules and contracts.
2. Move internal imports to the flat modules one domain at a time.
3. Keep legacy modules as compatibility wrappers while tests and contracts are migrated.
4. Retire old per-module contracts only after no production import references remain.
5. Delete legacy wrappers in the final cleanup batch.

## Current Checkpoint

- The five target modules expose `module_ownership()`, `wrapper_policy()`, `public_symbols()`, and `__dir__()` for agent and editor discovery.
- Production imports for moved legacy modules prefer the five target modules.
- Retired platform wrappers for `app_content`, `artifact_cleanup`, `config_adapters`, and `ux_metrics` have been removed.
- Retired scoring/reporting wrappers for candidate/profile/title, reporting/export/referral/email/offer/template, trait definition loader, and trait signal schema/state have been removed.
- Retired UI wrappers for keyboard telemetry, question runtime/settings/screens, UI feedback, and history grid have been removed.
- Retired interview root wrappers for interview state/session store, transcript accumulation, and transcription diagnostics have been removed.
- Retired onboarding wrappers for dashboard today, onboarding models, scheduler status, task filters, and template reference have been removed.
- Retired onboarding wrappers for action sections, dashboard actions, migrations, reminder health, and send guardrails have been removed.
- Remaining onboarding compatibility wrappers have been removed; `onboarding_operations` is the sole public onboarding module.
- Remaining legacy import paths stay valid where wrappers still exist.
- Physical moves completed:
  - `platform_services`: `app_content`, `app_logging`, `artifact_cleanup`, `config_adapters`, `data_store` config dependencies, `docx_compat`, `path_validation`, `runtime_wrapper`, `storage_utils`, `ux_metrics`
  - `ui_composition`: `interview_app.ui_router`, `interview_app.ui_shell`, `interview_app.view_protocols`, `ui_windows` shared imports
  - `interview_runtime`: `interview_app.session_context`, `interview_app.session_manager`, `interview_app.state`, `interview_app.types`, `interview_app.audio_devices`, `interview_app.audio_runtime`, `interview_app.whisper_runtime_policy`, `interview_app.transcript_processor`, `interview_app.transcript_summary`, `interview_app.transcription_queue`, `interview_app.transcription_executor`, `interview_app.transcript_writer`, `interview_app.flow_controller`, `interview_app.dashboard_controller`, `interview_app.history_controller`, `interview_app.finalize_context`, `interview_app.finalize_gateways`, `interview_app.finalize_pipeline`
  - `scoring_reporting`: `trait_scoring_adapter` remains pending final cleanup
  - `onboarding_operations`: all listed onboarding helpers now live directly in `onboarding_operations`; no legacy onboarding wrappers remain
- Contract review and full pytest passed after the production import audit.
- Manual hardware/live-GUI smoke checks were tested and verified by the user; see `docs/flattening_baseline.md`.

## Safety Notes

- No validation, export, referral, email, transcription, scoring, onboarding, or file-writing behavior should change during the facade stage.
- Privacy-sensitive behavior remains owned by the existing implementations until each function is physically moved.
- Contract changes must be made in the same task as any interface move.
