---
status: active
owner: Engineering (Contracts)
last-reviewed: 2026-03-07
scope: Repo-wide contract drift detection and remediation planning
superseded-by: n/a
---

# Contract Review Task Stubs (Repo Sections)

## 30-second triage for maintainers
- **Should I act on this file now?** Yes, when contract drift exists or before major refactors.
- **Document role:** Execution-ready task stubs for a single developer to review modules and align code/contracts.
- **Primary outputs:** Updated module contracts, updated `contracts/system.contract.yaml`, updated `contracts/architecture.contract.yaml`, and unit test coverage tasks.

## Security considerations (must be included in every section review)
1. Identify trust boundaries for each module input (external vs internal caller).
2. Record input validation requirements (type/shape/range/nullability).
3. Prevent sensitive data leakage in exceptions, logs, and contract examples.
4. Document side effects (`filesystem`, `network`, `email`, `process`) per function/method.
5. Verify authorization expectations for workflow-changing modules.
6. Confirm idempotency/retry behavior for state-changing operations.
7. Verify dependency integrity when adding internal module references.
8. Ensure tests include security-relevant validation behavior.

## Working mode and sequencing (single-developer manageable)
- Execute **one section at a time**.
- Keep each section review to a focused PR-sized unit.
- Within each section, apply the same 6-step workflow:
  1. Inventory modules.
  2. Compare code signatures vs contract signatures.
  3. Add missing module contracts immediately.
  4. Validate/record locked interface compliance.
  5. Update system and architecture contracts for dependency and layer/service changes.
  6. Add unit tests per function and class method from contracts.

---

## Section A — `src/interview_app/` package modules

### Objective
Align package-level interview workflow modules with module contracts and dependency graph.

### Task stub
- [ ] Build a file inventory for all Python modules in `src/interview_app/`.
- [ ] For each module, map functions/classes/methods with input/output signatures.
- [ ] Create missing `contracts/interview_app_<module>.contract.yaml` files using schema-compliant fields.
- [ ] Reconcile drift in existing `interview_app_*` contracts (inputs, returns, methods, dependencies, version).
- [ ] Validate `locked: true` interfaces and log any violations without changing locked signatures.
- [ ] Update `contracts/system.contract.yaml` dependency edges for package-internal imports.
- [ ] Update `contracts/architecture.contract.yaml` service/module assignments if boundaries changed.
- [ ] Add or extend tests so each contracted function/method has at least one unit test.

### Definition of done
- Every module under `src/interview_app/` has a contract.
- Contract signatures match code signatures.
- System and architecture contracts reflect current structure.
- Unit tests exist for all contracted functions/methods in this section.

---

## Section B — Interview workflow root modules in `src/`

### Objective
Cover non-package interview modules that power interview state, UI flow, and transcript/finalization support.

### Task stub
- [ ] Inventory interview-focused modules in `src/` (for example: `question_screens.py`, `ui_windows.py`, `interview_state.py`, `transcript_accumulator.py`, `interview_audio_recorder.py`, `runtime_wrapper.py`).
- [ ] Create missing module contracts for each inventory module.
- [ ] Update existing contracts where function/method signatures drift from code.
- [ ] Validate all locked interfaces remain unchanged.
- [ ] Update system contract dependencies for new or corrected imports.
- [ ] Reconcile architecture contract service mapping for interview-layer modules.
- [ ] Add/expand tests to achieve one unit test per contracted function/method.

### Definition of done
- Full contract coverage for interview root modules in `src/`.
- No unresolved drift for covered modules.
- Locked interfaces respected.

---

## Section C — Onboarding workflow modules in `src/`

### Objective
Ensure onboarding scheduling, reminders, notifications, and dashboard modules are contract-synchronized.

### Task stub
- [ ] Inventory onboarding modules in `src/` (scheduler, notifier, reminders, onboarding UI/actions/models/storage).
- [ ] Generate missing module contracts immediately for uncovered onboarding modules.
- [ ] Compare and update function/class signatures, return types, exceptions, and side effects.
- [ ] Validate lock status for existing interfaces before editing any signatures.
- [ ] Update `contracts/system.contract.yaml` for onboarding dependency links.
- [ ] Update `contracts/architecture.contract.yaml` for onboarding service/layer definitions.
- [ ] Add unit tests per contracted function/method, including validation and return type checks.

### Definition of done
- All onboarding modules have module contracts.
- Contract drift is resolved and versioned.
- Dependency and architecture contracts are current.

---

## Section D — Shared infrastructure and utilities in `src/`

### Objective
Contract-align cross-cutting modules used by both interview and onboarding flows.

### Task stub
- [ ] Inventory shared modules (for example: storage, logging, reporting, template placeholders, diagnostics, email security, export).
- [ ] Add missing module contract files for every shared module.
- [ ] Reconcile inputs/outputs and side effects for each function/method.
- [ ] Verify locked interfaces are unchanged; create additive interfaces if needed.
- [ ] Update system contract dependencies where shared modules are imported.
- [ ] Reflect any shared service boundaries in architecture contract.
- [ ] Add unit tests per contracted function/method with security-sensitive cases.

### Definition of done
- Shared modules are fully contract-defined.
- Side effects and dependencies are explicitly tracked.
- Test coverage aligns with contract-defined interfaces.

---

## Section E — Configuration and schema assets

### Objective
Track machine-readable configuration/schema contracts that influence module I/O assumptions.

### Task stub
- [ ] Inventory `config/*.json` files and map owning modules.
- [ ] For modules consuming config/schema data, ensure contract input types and validation expectations reflect schema fields.
- [ ] If new parser/adapter modules are introduced, add module contracts immediately.
- [ ] Update system contract dependencies between modules and configuration adapters.
- [ ] Add tests for configuration parsing/validation behaviors declared in contracts.

### Definition of done
- Module contracts explicitly capture config-driven input/output behavior.
- Config validation pathways are unit tested.

---

## Section F — Contract governance files (`contracts/`)

### Objective
Keep global contracts coherent, complete, and machine-readable.

### Task stub
- [ ] Verify every module in `src/` has exactly one corresponding module contract.
- [ ] Normalize module contract naming, path fields, and semantic versions.
- [ ] Ensure each module contract has complete schema sections (`functions`, `classes`, `dependencies`, etc.).
- [ ] Refresh `contracts/system.contract.yaml` module list and dependency graph.
- [ ] Refresh `contracts/architecture.contract.yaml` layers/services/datastores/external APIs.
- [ ] Validate locked interfaces are represented and unchanged.

### Definition of done
- Contract directory is complete and internally consistent.
- No orphan modules and no orphan contracts.

---

## Section G — Test suite alignment (`tests/`)

### Objective
Guarantee contract-driven test obligations are represented across the suite.

### Task stub
- [ ] Build a coverage matrix: contracted function/method → unit test file/case.
- [ ] Add missing tests so each contracted function/method has at least one test.
- [ ] Ensure tests validate behavior, parameter validation, and return type correctness.
- [ ] Add targeted tests for locked-interface guardrails and drift detection helpers.
- [ ] Keep tests modular by feature domain to remain manageable for one developer.

### Definition of done
- 1:1 minimum mapping exists between contract interfaces and unit tests.
- Coverage matrix can be regenerated as part of review cadence.

---

## Section H — Tooling and process support (`scripts/`, `tools/`, docs)

### Objective
Create repeatable review mechanics so contract synchronization is sustainable.

### Task stub
- [ ] Add/refresh lightweight scripts or checklists for contract drift detection per section.
- [ ] Document the review cadence and section sequencing in docs.
- [ ] Ensure task templates are concise and reusable for future passes.
- [ ] Add CI or local check guidance for contract schema validity and coverage matrix freshness.

### Definition of done
- Review process is documented and executable by a single developer.
- Contract maintenance work is routinized instead of ad hoc.

---

## Suggested execution order
1. Section F (governance baseline)
2. Section A (interview package)
3. Section B (interview root modules)
4. Section C (onboarding modules)
5. Section D (shared modules)
6. Section E (config/schema alignment)
7. Section G (test coverage completion)
8. Section H (tooling/process hardening)

## Backlog task title template
Use this naming pattern for issue tracking:
- `contracts:<section>:<module-or-scope>:drift-review`

Examples:
- `contracts:interview_app:flow_controller:drift-review`
- `contracts:onboarding:onboarding_scheduler:drift-review`
- `contracts:shared:storage_utils:drift-review`
