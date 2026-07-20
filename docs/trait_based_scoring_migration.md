# Trait-Based Scoring Migration

## Source of Truth

The scoring source of truth is `Trait-Based Scoring/trait_based_scoring_contract.yaml`.

Finalize and export paths must use the trait-based adapter/runtime flow instead of legacy finalize scoring helpers.

## Why This Migration Exists

Older interview state relied on `raw_score` as the active finalize input. The current finalize flow is contract-driven and uses selected signal references plus trait metadata so runtime behavior stays aligned with the machine-readable scoring bundle.

## State Shape Changes

### Previous compatibility-oriented shape

```yaml
trait_inputs:
  some_trait:
    raw_score: 4
    verbatim_notes: Candidate described a concrete example.
    skipped: false
```

### Current finalize-oriented shape

```yaml
trait_inputs:
  some_trait:
    raw_score: 4  # compatibility only; not source of truth
    selected_signal_ids:
      - T10.S1
      - T10.S4
    verbatim_notes: Candidate described a concrete example.
    skipped: false
```

Supported migrated selection fields are:

- `selected_signal_ids`
- `selected_signals`
- `signal_selections`

## Migration Guidance

1. Preserve `raw_score` only for temporary compatibility with legacy views/tests.
2. Write selected signal references whenever a trait is scored.
3. Treat selected refs/signals as the finalize/export authority.
4. Keep disqualifier notes populated when `absolute_disqualifier` is true.
5. Do not add new finalize code paths that call legacy scoring directly.

## Finalize Guardrails

- Finalize paths fail closed if a non-skipped, non-disqualified trait has no selected signal/reference state.
- The app-level legacy finalize fallback is disabled and raises a validation error if called.
- `scoring_reporting.invoke_scoring_engine` fails closed with explicit `ReportingValidationError` messages when finalized trait inputs do not overlap the runtime bundle or when rubric/runtime trait definitions drift.
- `scoring_reporting.ScoringEngine.evaluate` remains outside finalize and must not be reintroduced into finalize entrypoints or adapter runtime routing.
