# Contract Test Coverage Matrix

This repository tracks a machine-readable mapping from every contract symbol to at least one unit test.

## Regenerate

Run:

```bash
python tools/regenerate_contract_test_matrix.py
```

The command refreshes:

- `docs/contract_test_coverage_matrix.yaml`

## Review cadence checks

During review, run:

```bash
pytest tests/test_contract_coverage_matrix.py tests/test_ui_contract_interfaces.py
```

These checks enforce:

- 1:1 minimum mapping between contract symbols and test paths.
- Security mapping coverage for validation, auth expectations, side-effect controls, and leakage prevention.
- Locked-interface guardrail and drift-detection helper artifacts remain present.
