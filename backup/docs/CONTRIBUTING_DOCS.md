# Documentation Contribution Standards

> Canonical parent index: [Documentation Hub](./README.md)

## Security Considerations for Documentation Changes

Before drafting or updating repository documentation, confirm the content:

- Does not expose secrets (passwords, API keys, tokens, private endpoints).
- Does not include unnecessary personal data (candidate, employee, or customer PII).
- Uses sanitized examples for logs, screenshots, and sample payloads.
- Clearly calls out data classification and access expectations for sensitive workflows.
- Includes retention/deletion guidance when documents involve data handling processes.

## Validation UX Documentation Standard

When documenting UX behavior that includes validation or failure feedback, reference and align with [`VALIDATION_FEEDBACK_POLICY.md`](./VALIDATION_FEEDBACK_POLICY.md):

- classify each state as `info`, `warning`, `error`, or `blocking`
- use inline placement for recoverable issues
- reserve modal dialogs for blocking failures or irreversible actions
- avoid exposing technical internals in user-facing copy

## 1) Writing Standards

### Voice and Tone

- Use clear, direct language with an instructional, maintainer-focused tone.
- Prefer active voice and action-oriented phrasing (for example: run the checklist instead of phrasing it passively).
- Define uncommon acronyms on first use.
- Keep paragraphs concise; use bullets and tables for scannable guidance.

### Heading Structure

All new docs should follow this heading hierarchy:

1. `# Title`
2. `## Purpose`
3. `## Audience`
4. `## Prerequisites` (if applicable)
5. `## Procedure` or `## Guidance`
6. `## Validation` or `## Review Checklist`
7. `## Related Documentation`

Use `###` subheadings only when needed to break up complex procedures.

### Required Sections for Templates and Process Docs

In addition to the standard structure above:

- **Template docs** must include:
  - `## When to Use`
  - `## How to Fill This Out`
  - `## Example`
- **Process docs** must include:
  - `## Trigger` (when to run the process)
  - `## Inputs`
  - `## Step-by-Step Procedure`
  - `## Exit Criteria`

## 2) Required Metadata Block

Every document in `docs/` must start with a metadata block immediately below the title.

```md
Owner: <team-or-role>
Audience: <primary readers>
Last Reviewed: YYYY-MM-DD
Next Review: YYYY-MM-DD
Status: Draft | Active | Deprecated
Canonical Parent: [Documentation Hub](./README.md)
```

Rules:

- `Last Reviewed` and `Next Review` must use ISO date format (`YYYY-MM-DD`).
- `Next Review` should generally be within 3 to 6 months of `Last Reviewed`.
- Use `Draft` for in-progress docs, `Active` for maintained docs, and `Deprecated` for docs replaced by newer guidance.

## 3) Policy: Create New Docs vs Extend Existing Docs

Create a **new document** when:

- The content introduces a distinct workflow, owner group, or lifecycle.
- The update would make an existing document significantly harder to navigate.
- The topic needs independent review cadence, status, or deprecation tracking.

Extend an **existing document** when:

- The change clarifies or expands an established workflow.
- The new content belongs to the same audience and maintenance owner.
- The existing document can absorb the update without losing readability.

Decision rule:

- If readers would expect to find the content under an existing page title, extend.
- If readers would search by a new topic name, create a new page and link both directions.

## 4) Cross-Linking Requirements

For every doc in `docs/`:

- Include a link to the canonical parent index (`docs/README.md`) near the top.
- Add a `## Related Documentation` section with at least one inbound or sibling link.
- When creating a new doc, update `docs/README.md` to include it in the correct taxonomy group.

Recommended reusable line:

```md
Canonical Parent: [Documentation Hub](./README.md)
```

## 5) Docs Definition of Done Checklist

A documentation update is complete only when all checks pass:

- [ ] **Clarity**: wording is direct, unambiguous, and audience-appropriate.
- [ ] **Completeness**: required sections and metadata block are present.
- [ ] **Privacy/PII compliance**: no unnecessary personal data or secrets are exposed.
- [ ] **Link validity**: all links resolve and canonical parent index link is present.
- [ ] **Index coverage**: new docs are listed in `docs/README.md`.
- [ ] **Maintenance readiness**: owner, status, and review dates are current.

## Related Documentation

- [Documentation Hub](./README.md)
- [Repository README](../README.md)

## 6) Lightweight Review Checkpoint for Stale `Todo` Items

When docs mention backlog `Todo` items, run this checkpoint during normal docs review:

1. Identify stale `Todo` items that have not moved since the last review date.
2. Triage each stale item to one of:
   - Move to active planning in `docs/backlog/UI_UX_BACKLOG.md` with owner + status.
   - Keep as `Todo` with refreshed rationale and next review date.
   - Close with a brief reason if no longer relevant.
3. Keep summary docs recommendation-focused; do not duplicate execution-level implementation detail outside the active backlog file.

## 7) Local Documentation QA Commands

Run these checks locally before opening a pull request that changes docs:

1. **Markdown linting (blocking in CI)**

   ```bash
   npx --yes markdownlint-cli2 '**/*.md' '#node_modules'
   ```

2. **Markdown link checks (blocking in CI, internal links only)**

   ```bash
   lychee --config .lychee.toml --no-progress '**/*.md'
   ```

3. **Optional prose/style checks for maintainer-facing docs (non-blocking in CI)**

   ```bash
   proselint check docs/CONTRIBUTING_DOCS.md docs/README.md docs/accessibility_pr_checklist.md
   ```

If one of these tools is not installed, install it with your package manager of choice:

- `npm` for `markdownlint-cli2`
- `cargo` or standalone binary for `lychee`
- `pip` for `proselint`


## 7) Contract Review Cadence and Sequencing

For contract-driven updates, apply the repository workflow in [`CONTRACT_REVIEW_WORKFLOW.md`](./CONTRACT_REVIEW_WORKFLOW.md):

- **Cadence**: run baseline + section drift checks on each contract-affecting PR, with biweekly matrix freshness review.
- **Ownership**: PR author executes baseline/schema/tests; reviewer validates locked interfaces + security gates.
- **Sequencing**: baseline -> locked -> schema -> coverage matrix -> contract interface tests.

Use [`CONTRACT_REVIEW_CHECKLIST.md`](./CONTRACT_REVIEW_CHECKLIST.md) as the local pre-review checklist and keep issue/task execution scoped to single-developer units with explicit acceptance criteria.
