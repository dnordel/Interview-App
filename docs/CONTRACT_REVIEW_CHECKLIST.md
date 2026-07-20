# Contract Review Checklist
Owner: Platform Engineering
Audience: PR authors and contract reviewers
Last Reviewed: 2026-03-07
Next Review: 2026-06-07
Status: Active
Canonical Parent: [Documentation Hub](./README.md)

## Purpose
Provide a lightweight, section-by-section checklist that can be run locally by a single developer before requesting review.

## When to Use
Use this checklist when any PR changes interfaces, contracts, or contract governance docs.

## How to Fill This Out
- Mark each section as complete only after the command passes.
- If a section fails, capture remediation notes in the PR.
- Keep scope to one mergeable unit.

## Checklist
- [ ] Baseline: `python tools/check_contract_review.py --section baseline`
- [ ] Locked interfaces: `python tools/check_contract_review.py --section locked`
- [ ] Contract schema: `python tools/check_contract_review.py --section schema`
- [ ] Coverage matrix freshness: `python tools/check_contract_review.py --section coverage-matrix`
- [ ] Section batch run:
  - `python tools/check_contract_review.py --section baseline`
  - `python tools/check_contract_review.py --section locked`
  - `python tools/check_contract_review.py --section schema`
  - `python tools/check_contract_review.py --section coverage-matrix`
- [ ] Contract interface tests:
  - `pytest tests/test_onboarding_contract_interfaces.py`
  - `pytest tests/test_interview_runtime_contract_interfaces.py`
  - `pytest tests/test_interview_root_contracts.py`

## Security Review Gates
- [ ] Boundary checks verified for UI/filesystem/network inputs.
- [ ] Validation rules match contract input requirements.
- [ ] Side effects documented and test-covered.
- [ ] Auth/idempotency behavior verified for send/finalize operations.
- [ ] Dependency integrity reviewed in `contracts/system.contract.yaml`.

## Example
A complete single-developer unit includes:
1. Run baseline + schema checks.
2. Update one contract module and linked tests.
3. Re-run section checks.
4. Mark gates complete in the PR description.

## Related Documentation
- [Contract Review Workflow](./CONTRACT_REVIEW_WORKFLOW.md)
- [Documentation Contribution Standards](./CONTRIBUTING_DOCS.md)
