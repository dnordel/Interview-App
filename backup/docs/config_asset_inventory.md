# Config Asset Inventory

| Asset | Owning module/service | Consuming modules | Contracted expectations |
|---|---|---|---|
| `config/rubric.json` | `interview_runtime_service` | `data_store`, `question_settings_service`, `interview_app` | Required object with `metadata`, `scoring`, `tracks`, non-empty `traits`, `absolute_disqualifiers`; each trait requires id/name/priority/weight/question/descriptors/samples/tracks and weight in `(0,10]`. |
| `config/disqualifier_signals.json` | `interview_runtime_service` | `data_store`, `interview_app` | Optional object with `questions[]`; each question must contain a non-empty `trait_id`; malformed files fall back to safe empty defaults. |
| `config/question_overrides.json` | `interview_runtime_service` | `data_store`, `interview_app`, `question_settings_window` | Optional object with `track_trait_order`, `trait_question_overrides`, `custom_questions`, `track_question_flow`; values are normalized and invalid files are archived/reset to defaults. |
| `config/interview_output.schema.json` | `interview_runtime_service` | `integration_export`, `reporting`, `interview_session_store` | Draft 2020-12 JSON schema describing interview output payload shape and constraints. |
| `config/cues.json` | `interview_runtime_service` | _(none currently wired)_ | Scenario/cue catalog object with scoring scales, behavior flags, and case expectations. |
| `config/sample_draft.json` | `interview_runtime_service` | _(none currently wired)_ | Sample payload aligned with interview draft/output structure. |

## Security considerations applied

- Treat every config JSON file as untrusted input.
- Bound config file size before parse (`MAX_CONFIG_BYTES`) to mitigate oversized payload abuse.
- Validate required keys/types/ranges and nullability before modules consume data.
- Use safe defaults on optional config corruption (`disqualifier_signals`, `question_overrides`) to preserve runtime stability.
- Archive corrupted override files instead of repeatedly re-reading malformed content.
- Emit non-leaking validation errors that report field names only (no raw payload echo).
