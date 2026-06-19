# Flattening Baseline

## Status

The codebase is being flattened into five deep modules:

- `platform_services`
- `ui_composition`
- `interview_runtime`
- `scoring_reporting`
- `onboarding_operations`

The target modules are the preferred public import surface. Legacy modules remain importable during the migration so launch scripts, tests, and older agent work do not break.

## Current Ownership

Use each target module's `module_ownership()` function as the machine-readable ownership map. Use `available_modules()` for the legacy modules still exposed by that target module. Use `public_symbols()` to inspect which names a target module exposes through its compatibility interface.

## Compatibility Policy

Legacy modules should become thin wrappers as implementation moves behind the target modules. New production imports should prefer the five target modules. Wrapper modules must not change validation, export, email, reminder, transcription, scoring, or file-writing behavior.

## Implemented Checkpoint

- The five target modules expose ownership maps, public symbol inventories, wrapper policy text, and editor/agent discovery through `__dir__`.
- Production imports for moved legacy modules now prefer the five target modules. Retired platform and scoring/reporting wrappers have been removed; remaining legacy import paths stay available only where wrappers still exist.
- Migration guard tests verify target-module ownership, legacy import compatibility, and representative public symbols.
- Target module contracts, wrapper contracts, and the system dependency graph reflect the flattened import surface.
- `platform_services` owns shared app content, logging/crash-reporting, artifact cleanup, config parsing/validation, runtime wrapper helpers, UX metrics, storage helpers, path validation, and document compatibility helpers.
- `ui_composition` owns shared UI helpers, validation feedback, keyboard telemetry, question settings/runtime/window helpers, question screens, history grid, routed shell/router/view protocols, and reusable window composition.
- `interview_runtime` owns interview state/session storage, session path policy, audio runtime/device helpers, Whisper runtime policy, transcription queues/executors/processors/writers, transcript accumulation/diagnostics/summary seam, flow/dashboard/history controllers, finalize context/gateways/pipeline, and recording-session types.
- `scoring_reporting` owns candidate/profile/title helpers, scoring/reporting, trait loaders/adapters/state/schema, integration export, referral packets, director email drafts, email safety, offer letters, and template placeholders.
- `onboarding_operations` owns onboarding models/storage/migrations, scheduling, reminders, notifier, send guardrails, dashboard/actions, launch context, task filters, template reference, scroll helpers, and UI helpers.

## Remaining Physical Moves

No additional physical move is required for the current flattening checkpoint. Final cleanup has begun with retired wrappers that had no production imports. Future cleanup should continue in small batches, update contracts in the same change, and rerun the full contract and pytest gates.

## Validation Status

- Contract coverage matrix regenerated after the latest contract-visible changes.
- `python tools\check_contract_review.py` passed baseline, locked, schema, and coverage-matrix checks.
- Full `python -m pytest` passed with `1250 passed`, `103 skipped`, `18 warnings`, and `8 subtests passed`.
- Noninteractive entrypoint smokes passed for interview app import, onboarding app import, onboarding reminder CLI parsing, and runtime wrapper argument parsing.
- Manual Windows microphone/system-audio recording smoke was tested and verified by the user on target hardware after automated validation.
- Manual live Tk onboarding UI/reminder workflow smoke was tested and verified by the user after automated validation.
- Manual live GUI DOCX/referral/export workflow smoke was tested and verified by the user after automated validation.

## Security Notes

Flattening must preserve:

- path validation and directory traversal protections;
- candidate/interview/onboarding data privacy;
- redaction of emails, credentials, paths, and candidate identifiers in logs and diagnostics;
- fail-closed validation for finalize, export, referral, offer, and reminder-send flows;
- bounded retries and non-secret error reporting for onboarding reminders.
