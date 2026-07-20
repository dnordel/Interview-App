# Contract Review Workflow
Owner: Platform Engineering
Audience: Maintainers, reviewers, and release engineers
Last Reviewed: 2026-03-07
Next Review: 2026-06-07
Status: Active
Canonical Parent: [Documentation Hub](./README.md)

## Purpose
Define a repeatable, section-by-section contract drift review workflow with explicit ownership, security gates, and acceptance criteria.

## Audience
- Engineers making contract or interface changes.
- Reviewers validating contract integrity and test coverage mapping.
- Release owners running pre-merge quality gates.

## Prerequisites
- Python 3.11+
- `pip install -r requirements-dev.txt`
- Repository checked out with local test files available

## Trigger
Run this workflow for any pull request that changes:
- `contracts/**`
- `src/**` interfaces (function/class signatures)
- `docs/contract_*.yaml` or contract workflow docs
- CI workflows related to contract validation

## Inputs
- Baseline inventory: `docs/contract_baseline_checklist.yaml`
- Locked interface report: `docs/contract_locked_validation.yaml`
- Coverage matrix: `docs/contract_test_coverage_matrix.yaml`
- Contract files: `contracts/*.contract.yaml`, `contracts/system.contract.yaml`, `contracts/architecture.contract.yaml`

## Step-by-Step Procedure
1. **Baseline pass (owner: PR author)**
   - Run `python tools/check_contract_review.py --section baseline`.
   - Confirm module inventory exists and structural keys are present.
2. **Locked interface pass (owner: PR author + reviewer)**
   - Run `python tools/check_contract_review.py --section locked`.
   - If violations appear, stop and open a lock-exception issue before proceeding.
3. **Schema pass (owner: PR author)**
   - Run `python tools/check_contract_review.py --section schema`.
   - Ensure every module contract keeps required machine-readable sections.
4. **Coverage matrix freshness pass (owner: reviewer)**
   - Run `python tools/check_contract_review.py --section coverage-matrix`.
   - Confirm `last_updated` is fresh and referenced tests still exist.
5. **Contract interface tests (owner: PR author)**
   - Run `pytest tests/test_onboarding_contract_interfaces.py tests/test_interview_runtime_contract_interfaces.py tests/test_interview_root_contracts.py`.

## Security Gates
Each pass must explicitly verify:
- **Boundary checks**: contract inputs crossing UI/filesystem/network boundaries are represented and validated.
- **Validation rules**: required-field and type constraints are preserved in contract definitions and tests.
- **Side effects**: mutation, I/O, or send/finalize actions are identified and covered by tests.
- **Auth/idempotency**: high-risk actions require explicit authorization/confirmation and dedupe-safe behavior.
- **Dependency integrity**: module dependency links in `contracts/system.contract.yaml` map to real internal modules.

## Exit Criteria
A change is merge-ready only when all items are true:
- Baseline, locked, schema, and coverage-matrix checks pass.
- Contract interface tests pass.
- Coverage matrix references current tests for each contract-review section.
- Security gate checklist has no unresolved items.

## Small, Single-Developer Execution Units
| Unit | Scope | Acceptance Criteria |
|---|---|---|
| U1 | Baseline + locked checks | Both checks pass with zero missing keys and zero lock violations. |
| U2 | Schema validity | `schema` check passes for all module contract files. |
| U3 | Coverage matrix freshness | Matrix date is within freshness window and all test paths exist. |
| U4 | Security gate review | Boundary/validation/side-effect/auth/idempotency/dependency gates are each marked complete in PR notes. |

## Related Documentation
- [Documentation Contribution Standards](./CONTRIBUTING_DOCS.md)
- [Contract Baseline Checklist](./contract_baseline_checklist.yaml)
- [Contract Locked Validation](./contract_locked_validation.yaml)
