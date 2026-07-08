# AGENTS.md

Repository guidance for AI coding agents working in `Preschool-Teacher-Interview-Guide`.

## Purpose
- Keep changes safe, reviewable, and aligned with the repository's contract-driven workflow.
- Treat machine-readable contracts as the source of truth for code interfaces and architecture.
- Prefer small, focused changes that preserve existing behavior unless the task explicitly requires otherwise.

## Working Style
- Ask concise clarifying questions when requirements are ambiguous.
- Keep new logic readable; avoid control-flow nesting deeper than 3 levels.
- When adding substantial new functionality, consider extracting shared logic into small modules/libraries instead of expanding large files.
- Do not add `try`/`catch`-style wrappers around imports unless a task explicitly requires it.
- Follow the closest applicable `AGENTS.md` if a deeper directory adds more specific rules.

## Security Expectations
Before making changes, explicitly consider:
- input validation and type/shape safety;
- file path safety and directory traversal risks;
- secret/token exposure in code, logs, docs, and tests;
- unsafe shell execution or privilege escalation patterns;
- data privacy for candidate/interview/onboarding records;
- fail-closed behavior for validation and export flows;
- redaction needs for logs, telemetry, and generated artifacts.

## Contract-Driven Development Rules
When modifying code:
1. Read the relevant existing contract file(s) under `contracts/` first.
2. Compare code and contract for drift:
   - functions and signatures;
   - classes and methods;
   - inputs, return types, and exceptions;
   - module dependencies.
3. Update contracts so they match the code exactly.
4. Preserve locked interfaces. If `locked: true`, do not change that interface; add a new function/class instead.
5. Update architectural relationships when modules or dependencies change.

## Required Contract Files
- Module contracts: `contracts/{module_name}.contract.yaml`
- System contract: `contracts/system.contract.yaml`
- Architecture contract: `contracts/architecture.contract.yaml`

## Contract Versioning
Use semantic versioning in module contracts:
- `MAJOR`: breaking interface changes
- `MINOR`: new functions or methods
- `PATCH`: internal-only or non-breaking updates

## Tests
- For every new or changed function/class method, add or update at least one unit test under `tests/`.
- Verify behavior, validation, and return types where applicable.
- If a change is documentation-only, tests are not required.
- Use the managed AppData Python environment and run pytest in parallel by default:
  `%LOCALAPPDATA%\LPL_InterviewTool\py311\.venv\Scripts\python.exe -m pytest -n auto`.
  Use serial pytest only when debugging one focused failure or when a test is known to be order/concurrency sensitive.

## Documentation and Output
- Keep contracts machine-readable and complete.
- Output or summarize only the files changed by the task.
- Preserve existing descriptions where possible when updating contracts.
- Cite files and commands clearly in final summaries when requested by the runtime instructions.

## Repo-Specific Notes
- Start at `README.md` for product context and setup.
- This repository already maintains extensive machine-readable contracts in `contracts/`.
- Treat interview, onboarding, scoring, storage, and reporting flows as potentially privacy-sensitive.
- Prefer `rg`/`rg --files` for discovery instead of recursive `grep` or `ls`.

## Non-Negotiables
Never:
- change code without updating the relevant contracts;
- modify a locked interface incompatibly;
- leave new interfaces undocumented in contracts;
- add secrets to the repository;
- bypass validation or safety checks without explicit approval from the task.
