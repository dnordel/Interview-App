# Documentation Hub

This is the canonical entrypoint for repository documentation.

## Purpose

This index helps maintainers find, update, and govern all docs in one place before routing content to contributors, researchers, or reviewers.

## Audience

Maintainers-first, with secondary audiences including:
- UX researchers and facilitators.
- Engineers implementing UX and telemetry changes.
- Reviewers running accessibility and manual QA checks.

## Docs Taxonomy

- **Reference**: Stable definitions, schemas, and naming conventions used during implementation and review.
- **Process**: Repeatable workflows, checklists, and governance steps used in delivery.
- **Templates**: Reusable doc structures for consistent planning, synthesis, and QA capture.
- **Research Artifacts**: Session guides, task plans, privacy protocols, and synthesis artifacts tied to usability work.

## Navigation Tree

```text
docs/
├── README.md
├── accessibility_pr_checklist.md
├── CONTRIBUTING_DOCS.md
├── CONTRACT_TEST_MATRIX.md
├── flattening_baseline.md
├── flattening_migration_map.md
├── manual_qa_screen_template.md
├── manual_qa_ux_b006.md
├── RUBRIC_SCHEMA.md
├── VALIDATION_FEEDBACK_POLICY.md
├── UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md
├── UX_EVENT_NAMING.md
└── ux-research/
    ├── interview/
    │   ├── README.md
    │   ├── discussion-guide.md
    │   ├── findings-template.md
    │   ├── participant-matrix-and-tasks.md
    │   ├── privacy-protocol.md
    │   ├── success-metrics.md
    │   └── telemetry-mapping.md
    └── onboarding/
        ├── README.md
        ├── findings-template.md
        ├── moderator-script-and-rubric.md
        ├── privacy-guardrails.md
        ├── prioritized-backlog-format.md
        ├── telemetry-proposal.md
        └── test-tasks.md
```

## Use This When…

### `ux-research/interview`
Use this when planning or synthesizing interview-workflow usability studies (start flow, scoring behavior, finalization confidence), or when preparing interview-specific privacy and telemetry instrumentation decisions.

### `ux-research/onboarding`
Use this when running onboarding-workflow research (task scheduling, reminder behavior, escalation flow), or when validating onboarding privacy controls and telemetry proposals.

### PR checklists
Use `accessibility_pr_checklist.md` during pull request review to verify accessibility expectations before merge.

### Documentation governance
Use `CONTRIBUTING_DOCS.md` when creating or updating docs to apply metadata, structure, and cross-linking standards.

### Contract review workflow
Use `CONTRACT_REVIEW_WORKFLOW.md` for baseline + section drift checks, sequencing, ownership, security gates, and exit criteria.

### Contract review checklist
Use `CONTRACT_REVIEW_CHECKLIST.md` for a quick single-developer local pass.

### Flattened architecture
Use `flattening_baseline.md` and `flattening_migration_map.md` to understand the five public modules, legacy wrapper policy, production import expectations, current validation status, and remaining manual smoke checks.

### Telemetry conventions
Use `UX_EVENT_NAMING.md` before creating, naming, or reviewing UX events to keep event taxonomy and naming consistent.

### Rubric schema
Use `RUBRIC_SCHEMA.md` when editing or validating rubric structure and related scoring configuration expectations.

## Quick Links by Doc Group

### Reference
- [RUBRIC_SCHEMA.md](./RUBRIC_SCHEMA.md)
- [UX_EVENT_NAMING.md](./UX_EVENT_NAMING.md)
- [VALIDATION_FEEDBACK_POLICY.md](./VALIDATION_FEEDBACK_POLICY.md)

### Process
- [accessibility_pr_checklist.md](./accessibility_pr_checklist.md)
- [CONTRACT_REVIEW_CHECKLIST.md](./CONTRACT_REVIEW_CHECKLIST.md)
- [CONTRACT_REVIEW_WORKFLOW.md](./CONTRACT_REVIEW_WORKFLOW.md)
- [CONTRIBUTING_DOCS.md](./CONTRIBUTING_DOCS.md)
- [CONTRACT_TEST_MATRIX.md](./CONTRACT_TEST_MATRIX.md)
- [UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md](./UI_UX_RECOMMENDATIONS_AND_TASK_STUBS.md)
- [flattening_baseline.md](./flattening_baseline.md)
- [flattening_migration_map.md](./flattening_migration_map.md)
- [manual_qa_ux_b006.md](./manual_qa_ux_b006.md)

### Templates
- [manual_qa_screen_template.md](./manual_qa_screen_template.md)
- [ux-research/interview/findings-template.md](./ux-research/interview/findings-template.md)
- [ux-research/onboarding/findings-template.md](./ux-research/onboarding/findings-template.md)

### Research Artifacts
- [ux-research/interview/README.md](./ux-research/interview/README.md)
- [ux-research/onboarding/README.md](./ux-research/onboarding/README.md)

### Contract Governance Artifacts
- [contract_baseline_checklist.yaml](./contract_baseline_checklist.yaml)
- [contract_locked_validation.yaml](./contract_locked_validation.yaml)
- [contract_test_coverage_matrix.yaml](./contract_test_coverage_matrix.yaml)
