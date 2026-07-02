from __future__ import annotations

import json
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

import deepseek_finalize_worker
import interview_runtime
from data_store import InterviewHistoryStore
from interview_app.bootstrap import build_default_settings
from interview_runtime import (
    DeepSeekSummaryConfig,
    build_finalize_context,
    build_deepseek_summary_config,
    enqueue_deepseek_finalize_job,
    format_deepseek_question_prompt_overrides,
    generate_deepseek_interview_summaries,
    generate_deepseek_trait_signal_suggestions,
    parse_deepseek_question_prompt_overrides,
    regenerate_interview_notes_job,
    retry_deepseek_finalize_job,
    save_deepseek_prompt_templates,
)


def _disable_local_deepseek_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deepseek_finalize_worker, "_ensure_local_deepseek_runtime", lambda *_args, **_kwargs: None)


def test_request_deepseek_chat_completion_uses_native_ollama_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"response":"{\\"ok\\":true}"}'

    def _urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return _Response()

    monkeypatch.setattr(interview_runtime.urllib.request, "urlopen", _urlopen)

    response = interview_runtime._request_deepseek_chat_completion(
        DeepSeekSummaryConfig(enabled=True, api_key="ollama", base_url="http://127.0.0.1:11434/v1", timeout_seconds=7),
        [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "Return ok."}],
    )

    assert calls[0]["url"] == "http://127.0.0.1:11434/api/generate"
    assert calls[0]["body"]["format"] == "json"
    assert calls[0]["body"]["stream"] is False
    assert calls[0]["body"]["model"] == "deepseek-r1:14b"
    assert calls[0]["body"]["options"]["num_predict"] == 4096
    assert calls[0]["timeout"] == 7
    assert response["choices"][0]["message"]["content"] == '{"ok":true}'


def test_default_tk_settings_enable_local_deepseek_summary() -> None:
    settings = build_default_settings()

    config = build_deepseek_summary_config(
        {
            "DEEPSEEK_SUMMARY_ENABLED": str(settings["deepseek_summary_enabled"]),
            "DEEPSEEK_API_KEY": str(settings["deepseek_api_key"]),
            "DEEPSEEK_API_BASE_URL": str(settings["deepseek_api_base_url"]),
            "DEEPSEEK_SUMMARY_MODEL": str(settings["deepseek_summary_model"]),
            "DEEPSEEK_SUMMARY_TIMEOUT_SECONDS": str(settings["deepseek_summary_timeout_seconds"]),
        }
    )

    assert config.enabled is True
    assert config.api_key == "ollama"
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "deepseek-r1:14b"


def test_deepseek_summary_config_defaults_to_local_ollama_when_disabled() -> None:
    config = build_deepseek_summary_config({})

    assert config.enabled is False
    assert config.api_key == "ollama"
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "deepseek-r1:14b"


def test_deepseek_summary_config_enables_local_ollama_without_hosted_api_key() -> None:
    config = build_deepseek_summary_config({"DEEPSEEK_SUMMARY_ENABLED": "1"})

    assert config.enabled is True
    assert config.api_key == "ollama"
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "deepseek-r1:14b"


def test_deepseek_summary_config_allows_long_local_timeout() -> None:
    config = build_deepseek_summary_config(
        {
            "DEEPSEEK_SUMMARY_ENABLED": "1",
            "DEEPSEEK_SUMMARY_TIMEOUT_SECONDS": "900",
        }
    )

    assert config.timeout_seconds == 900


def test_deepseek_summary_config_loads_prompt_templates_from_config_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt_path = tmp_path / "deepseek_prompts.json"
    save_deepseek_prompt_templates(
        {
            "answer_summary_user": "CONFIG ANSWER {payload_json}",
            "trait_scoring_user_by_question": {"trait_1": "CONFIG SCORE {payload_json}"},
        },
        prompt_path,
    )
    monkeypatch.setattr(interview_runtime, "DEEPSEEK_PROMPTS_CONFIG_PATH", prompt_path)

    config = build_deepseek_summary_config({"DEEPSEEK_SUMMARY_ENABLED": "1"})

    assert config.prompt_templates["answer_summary_user"] == "CONFIG ANSWER {payload_json}"
    assert config.prompt_templates["trait_scoring_user_by_question"] == {
        "trait_1": "CONFIG SCORE {payload_json}"
    }


def test_deepseek_question_prompt_layout_roundtrips_human_blocks() -> None:
    layout = (
        "Question: trait_1\n"
        "Prompt:\n"
        "Score empathy using only transcript {payload_json}\n\n"
        "---\n\n"
        "Question: custom_why_lpl\n"
        "Prompt:\n"
        "Summarize mission fit {payload_json}"
    )

    parsed = parse_deepseek_question_prompt_overrides(layout)
    rendered = format_deepseek_question_prompt_overrides(parsed)

    assert parsed == {
        "trait_1": "Score empathy using only transcript {payload_json}",
        "custom_why_lpl": "Summarize mission fit {payload_json}",
    }
    assert "Question: trait_1" in rendered
    assert '"trait_1"' not in rendered


def test_deepseek_summary_config_rejects_hosted_base_url() -> None:
    config = build_deepseek_summary_config(
        {
            "DEEPSEEK_SUMMARY_ENABLED": "1",
            "DEEPSEEK_API_BASE_URL": "https://api.deepseek.com",
        }
    )

    assert config.enabled is True
    assert config.base_url == "http://127.0.0.1:11434/v1"


def test_deepseek_generated_sections_do_not_truncate_long_text() -> None:
    long_summary = " ".join(["Specific safety, warmth, and classroom evidence"] * 260)
    long_evidence = " ".join(["candidate evidence quote"] * 120)
    executive_payload = json.dumps(
        {
            "executive_summary_sections": {"overall_fit": long_summary},
            "executive_summary": long_summary,
            "interview_highlights": ["Uses safety routines."],
        }
    )
    answer_payload = json.dumps(
        {
            "answer_summaries": [
                {
                    "flow_index": 1,
                    "summary": long_summary,
                    "evidence_quotes": [long_evidence],
                    "rubric_alignment": long_summary,
                    "risks_or_gaps": long_summary,
                }
            ]
        }
    )

    executive = interview_runtime._normalize_deepseek_executive_summary_payload(executive_payload)
    answers = interview_runtime._normalize_deepseek_answer_summary_payload(answer_payload, {1})

    assert executive["executive_summary"] == long_summary
    assert executive["executive_summary"].endswith("evidence")
    assert answers[0]["summary"] == long_summary
    assert answers[0]["evidence_quotes"][0] == long_evidence
    assert answers[0]["rubric_alignment"] == long_summary
    assert answers[0]["risks_or_gaps"] == long_summary


def test_generate_deepseek_interview_summaries_uses_injected_completion() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    calls = []

    def _completion(active_config, messages):
        calls.append((active_config, messages))
        if "executive summary section" in messages[0]["content"]:
            content = '{"executive_summary_sections":{"overall_fit":"Strong classroom routines."},"executive_summary":"Strong classroom routines.","interview_highlights":["Uses visuals.","Keeps calm transitions."]}'
        else:
            content = (
                '{"answer_summaries":[{"flow_index":2,"summary":"Uses visual schedules.",'
                '"evidence_quotes":["visual schedules"],"rubric_alignment":"Uses classroom routine support.",'
                '"risks_or_gaps":""}]}'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 2, "question": "How?", "candidate_transcript": "I use visual schedules."}],
        {"name": "Ada", "track": "lead"},
        config=config,
        chat_completion=_completion,
    )

    assert calls
    assert calls[0][0].api_key == "secret-key"
    assert result == {
        "answer_summaries": [
            {
                "flow_index": 2,
                "summary": "Uses visual schedules.",
                "evidence_quotes": ["visual schedules"],
                "rubric_alignment": "Uses classroom routine support.",
                "risks_or_gaps": "",
            }
        ],
        "executive_summary": "Strong classroom routines.",
        "executive_summary_sections": {"overall_fit": "Strong classroom routines."},
        "interview_highlights": ["Uses visuals.", "Keeps calm transitions."],
        "confidence": None,
        "summary_status": "generated",
        "summary_warnings": [],
    }
    assert len(calls) == 2
    assert "individual preschool teacher interview answers" in calls[0][1][0]["content"]
    assert "executive summary section" in calls[1][1][0]["content"]


def test_generate_deepseek_interview_summaries_uses_configured_prompt_templates() -> None:
    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="secret-key",
        prompt_templates={
            "answer_summary_system": "CUSTOM ANSWER SYSTEM",
            "answer_summary_user": "CUSTOM ANSWER USER {payload_json}",
            "executive_summary_system": "CUSTOM EXEC SYSTEM",
            "executive_summary_user": "CUSTOM EXEC USER {answer_summaries_json} {transcript_text}",
        },
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        if messages[0]["content"] == "CUSTOM EXEC SYSTEM":
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Custom executive."},"executive_summary":"Custom executive.","interview_highlights":[]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_summaries":[{"flow_index":2,"summary":"Uses routines.",'
                            '"evidence_quotes":["routines"],"rubric_alignment":"Routine support.",'
                            '"risks_or_gaps":""}]}'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 2, "question": "How?", "candidate_transcript": "I use routines."}],
        {"name": "Ada", "track": "lead"},
        config=config,
        chat_completion=_completion,
    )

    assert result["executive_summary"] == "Custom executive."
    assert calls[0][0]["content"] == "CUSTOM ANSWER SYSTEM"
    assert calls[0][1]["content"].startswith("CUSTOM ANSWER USER ")
    assert '"candidate_transcript": "I use routines."' in calls[0][1]["content"]
    assert calls[1][0]["content"] == "CUSTOM EXEC SYSTEM"
    assert "CUSTOM EXEC USER" in calls[1][1]["content"]
    assert "Uses routines." in calls[1][1]["content"]


def test_default_deepseek_executive_prompt_requests_concise_renderable_sections() -> None:
    messages = interview_runtime._deepseek_executive_summary_messages(
        [{"flow_index": 1, "summary": "Uses routines."}],
        {"name": "Ada", "track": "lead"},
        scoring={"outcome": "Hire", "rows": []},
        transcript_items=[{"flow_index": 1, "question": "How?", "candidate_transcript": "I use routines."}],
    )
    prompt = messages[1]["content"]

    assert "Use these exact sections" in prompt
    assert '"executive_summary_sections"' in prompt
    assert '"role_specific_match"' in prompt
    assert '"score_pattern"' in prompt
    assert '"suggested_follow_up_questions": [string, string, string, string]' in prompt
    assert prompt.index("Overall Fit: 3 to 5 sentences") < prompt.index("Role-Specific Match: 2 to 4 sentences")
    assert prompt.index("Role-Specific Match: 2 to 4 sentences") < prompt.index("Score Pattern: 2 to 4 sentences")
    assert prompt.index("Score Pattern: 2 to 4 sentences") < prompt.index("Key Strengths: exactly 3 bullets")
    assert "Overall Fit: 3 to 5 sentences" in prompt
    assert "Key Strengths: exactly 3 bullets" in prompt
    assert "Key Concerns or Risks: exactly 3 bullets" in prompt
    assert "Suggested Follow-Up Questions: exactly 4 numbered questions" in prompt
    assert "Final Hiring Notes: 1 to 2 practical closing sentences" in prompt
    assert "markdown-formatted" not in prompt
    assert "Do not use markdown" not in messages[0]["content"]


def test_default_deepseek_user_prompts_include_json_output_templates() -> None:
    prompts = interview_runtime.DEFAULT_DEEPSEEK_PROMPT_TEMPLATES

    for key in ("answer_summary_user", "executive_summary_user", "trait_suggestion_user", "trait_scoring_user"):
        prompt = prompts[key]
        assert "JSON output template:" in prompt
        assert "Return exactly this JSON shape" in prompt

    answer_prompt = prompts["answer_summary_user"]
    assert "question_label" in answer_prompt
    assert "never a generic label like Non-scored question" in answer_prompt
    assert "must be empty for non-scored answers" in answer_prompt


def test_deepseek_answer_summary_normalizer_preserves_question_label() -> None:
    payload = json.dumps(
        {
            "answer_summaries": [
                {
                    "flow_index": 1,
                    "question_id": "custom_start",
                    "question_label": "When could you start?",
                    "summary": "Candidate can start next week.",
                    "evidence_quotes": ["next week"],
                    "rubric_alignment": "",
                    "risks_or_gaps": "",
                }
            ]
        }
    )

    result = interview_runtime._normalize_deepseek_answer_summary_payload(
        payload,
        {1},
        "I can start next week.",
    )

    assert result[0]["question_id"] == "custom_start"
    assert result[0]["question_label"] == "When could you start?"


def test_configured_executive_prompt_uses_only_matching_role_context() -> None:
    config = build_deepseek_summary_config({"DEEPSEEK_SUMMARY_ENABLED": "1"})
    messages = interview_runtime._deepseek_executive_summary_messages(
        [{"flow_index": 1, "summary": "Mentors assistants."}],
        {"name": "Ada", "track": "Lead Teacher"},
        scoring={"outcome": "Hire", "rows": []},
        transcript_items=[{"flow_index": 1, "question": "Leadership?", "candidate_transcript": "I mentor assistants."}],
        prompt_templates=config.prompt_templates,
    )
    prompt = messages[1]["content"]

    assert "mentoring assistants" in prompt
    assert "safe sleep awareness" not in prompt
    assert "avoiding diagnosis" not in prompt
    assert "licensing/compliance" not in prompt
    assert "Role-tailoring guide" not in prompt


def test_deepseek_executive_summary_normalizer_accepts_structured_sections() -> None:
    payload = json.dumps(
        {
            "executive_summary_sections": {
                "recommendation": "Recommend with reservations.",
                "overall_fit": "Calm and practical.",
                "role_specific_match": "Matches toddler routines.",
                "score_pattern": "High empathy, lower specificity.",
                "key_strengths": ["Uses visual routines.", "Communicates early.", "Stays calm."],
                "key_concerns_or_risks": ["Needs safety detail.", "Verify reliability.", "Probe coachability."],
                "suggested_follow_up_questions": ["Q1?", "Q2?", "Q3?", "Q4?"],
                "final_hiring_notes": "Verify safety judgment.",
            },
            "interview_highlights": ["Uses visual routines."],
        }
    )

    result = interview_runtime._normalize_deepseek_executive_summary_payload(payload)

    assert result["executive_summary_sections"]["recommendation"] == "Recommend with reservations."
    assert result["executive_summary_sections"]["suggested_follow_up_questions"] == ["Q1?", "Q2?", "Q3?", "Q4?"]
    assert "Role-Specific Match: Matches toddler routines." in result["executive_summary"]
    assert "4. Q4?" in result["executive_summary"]
    assert result["interview_highlights"] == ["Uses visual routines."]


def test_generate_deepseek_interview_summaries_uses_question_specific_answer_prompt() -> None:
    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="secret-key",
        prompt_templates={
            "answer_summary_system_by_question": {
                "custom_why_lpl": "CUSTOM Q SYSTEM",
            },
            "answer_summary_user_by_question": {
                "custom_why_lpl": "CUSTOM Q PROMPT {payload_json}",
            }
        },
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        if "executive summary section" in messages[0]["content"]:
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Done."},"executive_summary":"Done.","interview_highlights":[]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_summaries":[{"flow_index":2,"summary":"LPL mission fit.",'
                            '"evidence_quotes":["mission"],"rubric_alignment":"Mission alignment.",'
                            '"risks_or_gaps":""}]}'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"id": "custom_why_lpl", "flow_index": 2, "question": "Why LPL?", "candidate_transcript": "I like the mission."}],
        {"name": "Ada", "track": "lead"},
        config=config,
        chat_completion=_completion,
    )

    assert result["summary_status"] == "generated"
    assert calls[0][0]["content"] == "CUSTOM Q SYSTEM"
    assert calls[0][1]["content"].startswith("CUSTOM Q PROMPT ")
    assert '"id": "custom_why_lpl"' in calls[0][1]["content"]


def test_generate_deepseek_interview_summaries_feeds_scoring_into_executive_prompt() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    executive_payloads: list[str] = []

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            executive_payloads.append(messages[1]["content"])
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Recommend with reservations."},"executive_summary":"Recommend with reservations.","interview_highlights":["Strong routines."]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_summaries":[{"flow_index":2,"summary":"Uses visual schedules.",'
                            '"evidence_quotes":["visual schedules"],"rubric_alignment":"Routine support.",'
                            '"risks_or_gaps":"Needs follow-up on safety."}]}'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 2, "question": "How?", "candidate_transcript": "I use visual schedules."}],
        {"name": "Ada", "track": "Lead Teacher"},
        scoring={
            "outcome": "Proceed with Caution",
            "percent_of_max": 72,
            "rows": [
                {
                    "name": "Safety",
                    "raw_score": 3,
                    "deepseek_raw_score": 2,
                    "deepseek_calculated_score": 4,
                    "model_trait_score": {"raw_score": 2, "rationale": "Limited safety example."},
                }
            ],
        },
        config=config,
        chat_completion=_completion,
    )

    assert result["executive_summary"] == "Recommend with reservations."
    assert executive_payloads
    assert "QUESTION SCORES / RATINGS" in executive_payloads[0]
    assert "Uses visual schedules." in executive_payloads[0]
    assert "Proceed with Caution" in executive_payloads[0]
    assert "Limited safety example." in executive_payloads[0]


def test_generate_deepseek_interview_summaries_executive_prompt_uses_scored_rows_and_ai_analysis() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    executive_payloads: list[str] = []

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            executive_payloads.append(messages[1]["content"])
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Scored rows only."},"executive_summary":"Scored rows only.","interview_highlights":[]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_summaries":[{"flow_index":1,"summary":"Uses calm routines.",'
                            '"evidence_quotes":["calm routines"],"rubric_alignment":"Routine support.",'
                            '"risks_or_gaps":""}]}'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I use calm routines."}],
        {"name": "Ada", "track": "Preschool Teacher"},
        scoring={
            "outcome": "Hire",
            "rows": [
                {
                    "trait_id": "trait_1",
                    "name": "Warmth",
                    "raw_score": 5,
                    "model_signal_analysis_summary": "Signal review found warm child language.",
                    "model_trait_score": {
                        "raw_score": 5,
                        "rationale": "Strong descriptor match.",
                        "analysis_summary": "Advisory score found strong routine evidence.",
                    },
                },
                {
                    "trait_id": "trait_2",
                    "name": "Skipped Safety",
                    "raw_score": None,
                    "skipped": True,
                    "model_trait_score": {
                        "raw_score": 2,
                        "rationale": "Missing safety detail.",
                        "analysis_summary": "Should not influence strengths or risks.",
                    },
                },
            ],
        },
        config=config,
        chat_completion=_completion,
    )

    assert result["executive_summary"] == "Scored rows only."
    assert executive_payloads
    payload = executive_payloads[0]
    assert "Signal review found warm child language." in payload
    assert "Advisory score found strong routine evidence." in payload
    assert "Skipped Safety" in payload
    assert "Should not influence strengths or risks." not in payload
    assert "Use only scored_questions" in payload


def test_generate_deepseek_interview_summaries_chunks_answer_calls() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    answer_payloads: list[str] = []

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Two answers summarized."},"executive_summary":"Two answers summarized.","interview_highlights":["Uses routines."]}'}}]}
        answer_payloads.append(messages[1]["content"])
        if "visual timers" in messages[1]["content"]:
            content = (
                '{"answer_summaries":[{"flow_index":1,"summary":"Uses visual timers.",'
                '"evidence_quotes":["visual timers"],"rubric_alignment":"Transition support.",'
                '"risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"answer_summaries":[{"flow_index":2,"summary":"Calls families early.",'
                '"evidence_quotes":["call families"],"rubric_alignment":"Family communication.",'
                '"risks_or_gaps":""}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    result = generate_deepseek_interview_summaries(
        [
            {"flow_index": 1, "question": "How?", "candidate_transcript": "I use visual timers."},
            {"flow_index": 2, "question": "Families?", "candidate_transcript": "I call families early."},
        ],
        {"name": "Ada"},
        config=config,
        chat_completion=_completion,
    )

    assert len(answer_payloads) == 2
    assert "visual timers" in answer_payloads[0]
    assert "call families" in answer_payloads[1]
    assert result["summary_status"] == "generated"
    assert [item["flow_index"] for item in result["answer_summaries"]] == [1, 2]
    assert result["executive_summary"] == "Two answers summarized."


def test_generate_deepseek_interview_summaries_reports_step_progress() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    steps: list[str] = []

    def fake_completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            return {"choices": [{"message": {"content": '{"executive_summary_sections":{"overall_fit":"Summary."},"executive_summary":"Summary.","interview_highlights":[]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"answer_summaries":[{"flow_index":1,"summary":"Uses routines.",'
                        '"evidence_quotes":["routines"],"rubric_alignment":"Routines","risks_or_gaps":""}]}'
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [
            {
                "flow_index": 1,
                "title": "Classroom routines",
                "question": "How?",
                "candidate_transcript": "I use routines.",
            }
        ],
        {"name": "Ada"},
        config=config,
        chat_completion=fake_completion,
        progress_callback=steps.append,
    )

    assert result["summary_status"] == "generated"
    assert steps == ["Summarizing Q1: Classroom routines", "Generating Executive Summary"]


def test_generate_deepseek_interview_summaries_accepts_fenced_json() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            content = '```json\n{"executive_summary_sections":{"overall_fit":"Calm transition support."},"executive_summary":"Calm transition support.","interview_highlights":["Uses picture cues."]}\n```'
        else:
            content = (
                '```json\n{"answer_summaries":[{"flow_index":1,"summary":"Uses picture schedules.",'
                '"evidence_quotes":["picture schedules"],"rubric_alignment":"Uses visual classroom supports.",'
                '"risks_or_gaps":""}]}\n```'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I use picture schedules."}],
        {"name": "Ada"},
        config=config,
        chat_completion=_completion,
    )

    assert result["summary_status"] == "generated"
    assert result["executive_summary"] == "Calm transition support."
    assert result["interview_highlights"] == ["Uses picture cues."]
    assert result["answer_summaries"][0]["summary"] == "Uses picture schedules."
    assert result["answer_summaries"][0]["evidence_quotes"] == ["picture schedules"]


def test_generate_deepseek_interview_summaries_repairs_validates_logs_and_keeps_exact_quotes(tmp_path: Path) -> None:
    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="secret-key",
        debug_log_dir=tmp_path / "deepseek-debug",
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        system_text = messages[0]["content"]
        if "executive summary section" in system_text:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "notes before "
                                '{"executive_summary_sections":{"recommendation":{"rating":"Recommend","rationale":"Supported."}},'
                                '"executive_summary":"Recommendation: Recommend","interview_highlights":[],"confidence":0.55}'
                                " notes after"
                            )
                        }
                    }
                ]
            }
        if len(calls) == 1:
            return {"choices": [{"message": {"content": '{"wrong_key":[]}'}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            'prefix {"answer_summaries":[{"flow_index":1,"summary":"Uses routines.",'
                            '"evidence_quotes":["visual routines","not in transcript"],"rubric_alignment":"Routine support.",'
                            '"risks_or_gaps":"","confidence":0.82}]} suffix'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I use visual routines."}],
        {"name": "Ada", "track": "Lead Teacher"},
        config=config,
        chat_completion=_completion,
    )

    assert len(calls) == 3
    assert "Return only valid JSON matching the required schema" in calls[1][-1]["content"]
    assert result["summary_status"] == "generated"
    assert result["answer_summaries"][0]["evidence_quotes"] == ["visual routines"]
    assert result["answer_summaries"][0]["confidence"] == 0.82
    assert result["confidence"] == 0.55
    log_files = list((tmp_path / "deepseek-debug").glob("*.jsonl"))
    assert log_files
    log_text = log_files[0].read_text(encoding="utf-8")
    assert '"prompt_name": "answer_summary"' in log_text
    assert '"parse_success": false' in log_text
    assert '"candidate_name": "Ada"' in log_text
    assert '"job_title": "Lead Teacher"' in log_text


def test_deepseek_json_parser_skips_invalid_brace_text_before_valid_object() -> None:
    parsed = interview_runtime._load_deepseek_json_object('noise {not json} then {"answer_summaries":[]}')

    assert parsed == {"answer_summaries": []}


def test_generate_deepseek_interview_summaries_stops_invalid_executive_retry() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    calls = 0

    def _completion(_config, messages):
        nonlocal calls
        calls += 1
        if "executive summary section" in messages[0]["content"]:
            return {"choices": [{"message": {"content": "not json"}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"answer_summaries":[{"flow_index":1,"summary":"Uses routines.",'
                            '"evidence_quotes":["routines"],"rubric_alignment":"Routine support.",'
                            '"risks_or_gaps":""}]}'
                        )
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I use routines."}],
        {"name": "Ada", "track": "lead"},
        config=config,
        chat_completion=_completion,
    )

    assert calls == 3
    assert result["summary_status"] == "generated"
    assert result["answer_summaries"][0]["summary"] == "Uses routines."
    assert result["executive_summary"] == "Executive summary could not be generated automatically. Please review transcript and scores manually."
    assert result["summary_warnings"] == ["DeepSeek executive summary failed: ValueError"]


def test_generate_deepseek_interview_summaries_generates_with_highlights_only() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            content = '{"executive_summary_sections":{},"executive_summary":"","interview_highlights":["Patient family communication."]}'
        else:
            content = (
                '{"answer_summaries":[{"flow_index":1,"summary":"Communicates patiently with families.",'
                '"evidence_quotes":["communicate patiently"],"rubric_alignment":"Family communication.",'
                '"risks_or_gaps":""}]}'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I communicate patiently with families."}],
        {"name": "Ada"},
        config=config,
        chat_completion=_completion,
    )

    assert result["summary_status"] == "generated"
    assert result["executive_summary"] == ""
    assert result["interview_highlights"] == ["Patient family communication."]
    assert result["answer_summaries"][0]["summary"] == "Communicates patiently with families."


def test_generate_deepseek_interview_summaries_redacts_failure_detail(caplog: pytest.LogCaptureFixture) -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")

    def _completion(_config, _messages):
        raise RuntimeError("secret-key leaked")

    result = generate_deepseek_interview_summaries(
        [{"flow_index": 1, "candidate_transcript": "Candidate answer."}],
        {},
        config=config,
        chat_completion=_completion,
    )

    assert result["summary_status"] == "failed"
    assert result["summary_warnings"] == ["DeepSeek summary failed: RuntimeError"]
    assert result["interview_highlights"] == []
    assert "secret-key" not in caplog.text


def test_build_finalize_context_adds_disabled_summary_payload() -> None:
    app = SimpleNamespace()
    app.state = SimpleNamespace(
        flow_recordings={1: {"base_name": "x"}},
        referral_packet={"transcript_path": "", "interview_notes_path": ""},
        to_dict=lambda: {"candidate": {"name": "Ada", "track": "general"}},
    )
    app._serialize_flow_audio_recordings = lambda: [{"flow_index": 1}]
    app._ordered_custom_answers = lambda: []
    app._build_flow_transcript = lambda: [{"flow_index": 1, "candidate_transcript": "Candidate answer."}]
    app._apply_candidate_transcripts_to_flow = lambda _flow_tx: None
    app._rewrite_live_transcript_docx_from_flow = lambda _flow_tx: None

    context = build_finalize_context(
        app,
        scoring={"outcome": "Hire"},
        warnings=[],
        transcript_metadata={"transcript_complete": True, "remaining_question_indices": []},
    )

    assert context.payload["summary_status"] == "disabled"
    assert context.payload["answer_summaries"] == []
    assert context.payload["executive_summary"] == ""
    assert context.payload["interview_highlights"] == []


def test_build_finalize_context_can_defer_deepseek_work() -> None:
    app = SimpleNamespace()
    app.state = SimpleNamespace(
        flow_recordings={1: {"base_name": "x"}},
        referral_packet={"transcript_path": "", "interview_notes_path": ""},
        trait_inputs={},
        to_dict=lambda: {"candidate": {"name": "Ada", "track": "general"}},
    )
    app._serialize_flow_audio_recordings = lambda: [{"flow_index": 1}]
    app._ordered_custom_answers = lambda: []
    app._build_flow_transcript = lambda: [{"flow_index": 1, "candidate_transcript": "Candidate answer."}]
    app._apply_candidate_transcripts_to_flow = lambda _flow_tx: None
    app._rewrite_live_transcript_docx_from_flow = lambda _flow_tx: None

    context = build_finalize_context(
        app,
        scoring={"outcome": "Hire"},
        warnings=[],
        transcript_metadata={"transcript_complete": True, "remaining_question_indices": []},
        run_deepseek=False,
    )

    assert context.payload["summary_status"] == "processing"
    assert context.payload["model_suggestion_status"] == "processing"
    assert context.payload["model_scoring_status"] == "processing"


def test_enqueue_deepseek_finalize_job_writes_job_and_launches_worker(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _Popen:
        def __init__(self, args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(interview_runtime.subprocess, "Popen", _Popen)
    history_store = InterviewHistoryStore(tmp_path / "history.json")
    history_store.append({"history_id": "hist-1", "candidate_name": "Ada"})
    app = SimpleNamespace(
        settings={"base_dir": str(tmp_path), "deepseek_summary_enabled": True},
        history_store=history_store,
        _rubric_with_question_overrides=lambda: {"tracks": {}},
    )
    context = SimpleNamespace(payload={"candidate": {"name": "Ada"}}, scoring={"outcome": "Hire"})

    job_path = enqueue_deepseek_finalize_job(app, context, str(tmp_path / "notes.docx"), "hist-1")

    assert job_path.exists()
    assert calls
    job_payload = job_path.read_text(encoding="utf-8")
    assert '"history_id": "hist-1"' in job_payload
    assert str(job_path) in calls[0]["args"]
    progress_path = job_path.with_suffix(".progress.json")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["step"] == "Launching local DeepSeek worker"
    assert progress["status"] == "processing"
    row = history_store.load()[0]
    assert row["deepseek_job_path"] == str(job_path)
    assert row["deepseek_progress_path"] == str(progress_path)


def test_retry_deepseek_finalize_job_marks_history_processing_and_relaunches(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist-1",
                    "candidate_name": "Ada",
                    "deepseek_processing_status": "failed",
                    "deepseek_processing_warning": "DeepSeek processing failed.",
                }
            ]
        ),
        encoding="utf-8",
    )
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "progress_path": str(job_path.with_suffix(".progress.json")),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(interview_runtime, "_start_deepseek_finalize_worker", lambda path: calls.append(Path(path)))

    progress_path = interview_runtime.retry_deepseek_finalize_job(job_path)

    assert progress_path == job_path.with_suffix(".progress.json")
    assert calls == [job_path]
    row = json.loads(history_path.read_text(encoding="utf-8"))[0]
    assert row["deepseek_processing_status"] == "processing"
    assert row["deepseek_processing_warning"] == ""
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["step"] == "Retrying local DeepSeek worker"


def test_retry_deepseek_finalize_job_recovers_failed_same_job_lock(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    progress_path = job_path.with_suffix(".progress.json")
    lock_path = job_path.parent / "deepseek-finalize.lock"
    job_path.write_text(
        json.dumps({"history_id": "hist-1", "progress_path": str(progress_path)}),
        encoding="utf-8",
    )
    progress_path.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    lock_path.write_text(json.dumps({"job": job_path.stem, "pid": 999999}), encoding="utf-8")
    monkeypatch.setattr(interview_runtime, "_start_deepseek_finalize_worker", lambda path: calls.append(Path(path)))

    retry_deepseek_finalize_job(job_path)

    assert calls == [job_path]
    assert not lock_path.exists()


def test_regenerate_interview_notes_job_document_only_marks_mode_and_relaunches(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps([{"history_id": "hist-1", "deepseek_processing_status": "complete"}]), encoding="utf-8")
    job_path = tmp_path / "deepseek-finalize-hist-1.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "progress_path": str(job_path.with_suffix(".progress.json")),
                "payload": {"summary_status": "generated"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(interview_runtime, "_start_deepseek_finalize_worker", lambda path: calls.append(Path(path)))

    progress_path = regenerate_interview_notes_job(job_path, mode="document_only")

    assert calls == [job_path]
    assert progress_path == job_path.with_suffix(".progress.json")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["rerun_mode"] == "document_only"
    row = json.loads(history_path.read_text(encoding="utf-8"))[0]
    assert row["deepseek_processing_status"] == "processing"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["step"] == "Regenerating interview notes document"


def test_regenerate_interview_notes_job_full_mode_resets_deepseek_checkpoints(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    job_path = tmp_path / "deepseek-finalize-hist-1.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "progress_path": str(job_path.with_suffix(".progress.json")),
                "payload": {
                    "answer_summaries": [{"flow_index": 1, "summary": "Old"}],
                    "executive_summary": "Old summary",
                    "summary_status": "generated",
                    "summary_warnings": [],
                    "model_signal_suggestions_by_trait": {"t1": []},
                    "model_suggestion_status": "generated",
                    "model_suggestion_warnings": [],
                    "model_trait_scores_by_trait": {"t1": {"raw_score": 4}},
                    "model_scoring_status": "generated",
                    "model_scoring_warnings": [],
                },
                "deepseek_settings": {
                    "DEEPSEEK_SUMMARY_ENABLED": "",
                    "DEEPSEEK_API_KEY": "",
                    "DEEPSEEK_API_BASE_URL": "",
                    "DEEPSEEK_SUMMARY_MODEL": "",
                    "DEEPSEEK_SUMMARY_TIMEOUT_SECONDS": "120",
                    "DEEPSEEK_PROMPT_TEMPLATES": {"executive_summary_user": "old prompt"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(interview_runtime, "_start_deepseek_finalize_worker", lambda path: calls.append(Path(path)))
    monkeypatch.setattr(interview_runtime, "load_deepseek_prompt_templates", lambda: {"executive_summary_user": "new prompt"})

    regenerate_interview_notes_job(job_path, mode="full")

    assert calls == [job_path]
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["rerun_mode"] == "full"
    payload = job["payload"]
    assert payload["summary_status"] == "processing"
    assert payload["model_suggestion_status"] == "processing"
    assert payload["model_scoring_status"] == "processing"
    assert payload["answer_summaries"] == []
    assert payload["model_signal_suggestions_by_trait"] == {}
    assert job["deepseek_settings"]["DEEPSEEK_SUMMARY_ENABLED"] == "1"
    assert job["deepseek_settings"]["DEEPSEEK_API_KEY"] == "ollama"
    assert job["deepseek_settings"]["DEEPSEEK_API_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert job["deepseek_settings"]["DEEPSEEK_SUMMARY_MODEL"] == "deepseek-r1:14b"
    assert job["deepseek_settings"]["DEEPSEEK_SUMMARY_TIMEOUT_SECONDS"] == "600"
    assert job["deepseek_settings"]["DEEPSEEK_PROMPT_TEMPLATES"] == {"executive_summary_user": "new prompt"}


def test_deepseek_finalize_worker_resumes_from_checkpointed_trait_output(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "history_id": "hist-1",
                    "deepseek_processing_status": "failed",
                }
            ]
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "notes.docx"
    progress_path = tmp_path / "deepseek.progress.json"
    job_path = tmp_path / "deepseek_jobs" / "deepseek-finalize-hist-1.json"
    job_path.parent.mkdir()
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "base_dir": str(tmp_path),
                "report_path": str(report_path),
                "rubric": {"tracks": {"preschool": {}}},
                "payload": {
                    "candidate": {"track": "preschool"},
                    "flow_transcript": [{"flow_index": 1, "candidate_transcript": "Candidate answer."}],
                    "trait_inputs": {"trait_1": {"raw_score": 4}},
                    "model_suggestion_status": "generated",
                    "model_scoring_status": "generated",
                    "model_signal_suggestions_by_trait": {"trait_1": []},
                    "model_trait_scores_by_trait": {"trait_1": {"raw_score": 4}},
                },
                "scoring": {"percent_of_max": 80, "outcome": "Hire"},
                "deepseek_settings": {},
                "progress_path": str(progress_path),
            }
        ),
        encoding="utf-8",
    )

    def _unexpected_trait_generation(*_args, **_kwargs):
        raise AssertionError("trait generation should be skipped when checkpointed")

    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_trait_signal_suggestions", _unexpected_trait_generation)
    monkeypatch.setattr(deepseek_finalize_worker, "_ensure_local_deepseek_runtime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_interview_summaries",
        lambda *_args, **_kwargs: {"summary_status": "generated", "answer_summaries": []},
    )
    monkeypatch.setattr(deepseek_finalize_worker.ScoringEngine, "evaluate", staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}))

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def export(self, *_args):
            out = self.output_dir / "notes.docx"
            out.write_text("notes", encoding="utf-8")
            return out

    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)

    deepseek_finalize_worker.run_job(job_path)

    row = json.loads(history_path.read_text(encoding="utf-8"))[0]
    assert row["deepseek_processing_status"] == "complete"


def test_deepseek_finalize_worker_starts_local_ollama_and_reports_specific_steps(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    progress_path = tmp_path / "deepseek.progress.json"
    progress_steps: list[str] = []
    readiness_checks = iter([False, True])
    popen_calls: list[list[str]] = []

    monkeypatch.setattr(deepseek_finalize_worker, "_local_ollama_api_ready", lambda _config: next(readiness_checks))
    monkeypatch.setattr(deepseek_finalize_worker, "_resolve_ollama_executable", lambda: "C:\\Ollama\\ollama.exe")

    class _Popen:
        def __init__(self, args, **_kwargs):
            popen_calls.append(list(args))

    monkeypatch.setattr(deepseek_finalize_worker.subprocess, "Popen", _Popen)

    original_write_progress = deepseek_finalize_worker._write_progress

    def _capture_progress(job, step, status="processing"):
        progress_steps.append(step)
        original_write_progress(job, step, status)

    monkeypatch.setattr(deepseek_finalize_worker, "_write_progress", _capture_progress)

    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="deepseek-r1:14b",
        timeout_seconds=3,
        prompt_templates={},
    )
    deepseek_finalize_worker._ensure_local_deepseek_runtime({"progress_path": str(progress_path)}, config)

    assert progress_steps == [
        "Checking local Ollama service",
        "Starting local Ollama service",
        "Local Ollama service ready",
    ]
    assert popen_calls == [["C:\\Ollama\\ollama.exe", "serve"]]


def test_deepseek_finalize_worker_updates_history_status(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        def export(self, _rubric, _payload, _scoring):
            out_path = tmp_path / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_interview_summaries", lambda *_args, **_kwargs: {"summary_status": "generated"})
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "generated", "model_scoring_status": "generated"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(deepseek_finalize_worker.ScoringEngine, "evaluate", staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}))
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    row = store.load()[0]
    assert row["deepseek_processing_status"] == "complete"
    assert row["interview_score"] == 88
    assert row["interview_notes_path"] == str(tmp_path / "updated.docx")


def test_deepseek_finalize_worker_uses_regenerated_notes_path_when_report_is_locked(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )
    original_report = tmp_path / "2026-02-20 - Palmdale - Ada - Interview.docx"
    original_report.write_text("open in word", encoding="utf-8")
    export_dirs: list[Path] = []

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def export(self, _rubric, _payload, _scoring):
            export_dirs.append(self.output_dir)
            if self.output_dir == tmp_path:
                raise PermissionError("report is locked")
            out_path = self.output_dir / original_report.name
            out_path.write_text("regenerated docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(deepseek_finalize_worker, "_utc_timestamp", lambda: "2026-02-20T01:02:03Z")
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_interview_summaries",
        lambda *_args, **_kwargs: {"summary_status": "generated"},
    )
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "generated", "model_scoring_status": "generated"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(
        deepseek_finalize_worker.ScoringEngine,
        "evaluate",
        staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}),
    )
    job_path = tmp_path / "deepseek-finalize-hist-1.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(original_report),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    regenerated_report = tmp_path / "2026-02-20 - Palmdale - Ada - Interview - regenerated 20260220-010203.docx"
    row = store.load()[0]
    assert regenerated_report.read_text(encoding="utf-8") == "regenerated docx placeholder"
    assert row["deepseek_processing_status"] == "complete"
    assert row["interview_notes_path"] == str(regenerated_report)
    assert export_dirs[0] == tmp_path
    assert export_dirs[1] != tmp_path


def test_deepseek_finalize_worker_document_only_rerun_skips_deepseek_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "deepseek_processing_status": "processing",
        }
    )
    calls: list[str] = []

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def export(self, _rubric, payload, scoring):
            calls.append(f"export:{payload['summary_status']}:{scoring['outcome']}")
            out_path = self.output_dir / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(deepseek_finalize_worker, "_ensure_local_deepseek_runtime", lambda *_args: calls.append("ollama"))
    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_interview_summaries", lambda *_args, **_kwargs: calls.append("summary"))
    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_trait_signal_suggestions", lambda *_args, **_kwargs: calls.append("traits"))
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rerun_mode": "document_only",
                "rubric": {},
                "payload": {
                    "candidate": {"track": "lead"},
                    "flow_transcript": [],
                    "summary_status": "generated",
                    "model_suggestion_status": "generated",
                    "model_scoring_status": "generated",
                },
                "scoring": {"outcome": "Hire", "percent_of_max": 88},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    assert calls == ["export:generated:Hire"]
    row = store.load()[0]
    assert row["deepseek_processing_status"] == "complete"
    assert row["interview_notes_path"] == str(tmp_path / "updated.docx")


def test_deepseek_finalize_worker_passes_final_scoring_to_summary_generation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    call_order: list[str] = []
    summary_scoring: list[dict] = []

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        def export(self, _rubric, _payload, _scoring):
            out_path = tmp_path / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    def _summaries(*_args, **kwargs):
        call_order.append("summary")
        summary_scoring.append(kwargs.get("scoring"))
        return {"summary_status": "generated", "answer_summaries": [{"flow_index": 1, "summary": "Uses routines."}]}

    def _trait_suggestions(*_args, **_kwargs):
        call_order.append("trait_scoring")
        return {"model_suggestion_status": "generated", "model_scoring_status": "generated"}

    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_interview_summaries", _summaries)
    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_trait_signal_suggestions", _trait_suggestions)
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(
        deepseek_finalize_worker.ScoringEngine,
        "evaluate",
        staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire", "rows": [{"name": "Safety"}]}),
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [{"flow_index": 1, "candidate_transcript": "Answer."}], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    assert call_order == ["trait_scoring", "summary"]
    assert summary_scoring == [{"percent_of_max": 88, "outcome": "Hire", "rows": [{"name": "Safety"}]}]


def test_deepseek_finalize_worker_fails_without_export_when_deepseek_outputs_are_incomplete(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )

    export_called = False

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        def export(self, _rubric, _payload, _scoring):
            nonlocal export_called
            export_called = True
            return tmp_path / "updated.docx"

    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_interview_summaries",
        lambda *_args, **_kwargs: {"summary_status": "generated", "answer_summaries": [{"flow_index": 1, "summary": "Summary."}]},
    )
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "failed", "model_scoring_status": "failed"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(deepseek_finalize_worker.ScoringEngine, "evaluate", staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}))
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    result = deepseek_finalize_worker.main(["deepseek_finalize_worker.py", str(job_path)])

    row = store.load()[0]
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert result == 1
    assert export_called is False
    assert row["deepseek_processing_status"] == "failed"
    assert row["deepseek_processing_warning"] == (
        "DeepSeek processing failed: DeepSeek prompts incomplete: model_suggestion_status, model_scoring_status"
    )
    assert job["payload"]["model_suggestion_status"] == "failed"
    assert job["payload"]["model_scoring_status"] == "failed"


def test_deepseek_progress_write_retries_transient_replace_permission_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress_path = tmp_path / "deepseek.progress.json"
    original_replace = Path.replace
    calls = {"count": 0}

    def _flaky_replace(self: Path, target: Path) -> Path:
        if Path(target) == progress_path and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError("sync lock")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _flaky_replace)

    deepseek_finalize_worker._write_progress({"progress_path": str(progress_path)}, "Retrying local DeepSeek worker")

    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    assert calls["count"] == 1
    assert payload["status"] == "processing"
    assert payload["step"] == "Retrying local DeepSeek worker"


def test_deepseek_progress_write_does_not_abort_on_persistent_replace_permission_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress_path = tmp_path / "deepseek.progress.json"
    original_replace = Path.replace

    def _locked_replace(self: Path, target: Path) -> Path:
        if Path(target) == progress_path:
            raise PermissionError("sync lock")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _locked_replace)

    deepseek_finalize_worker._write_progress({"progress_path": str(progress_path)}, "Retrying local DeepSeek worker")

    assert not progress_path.exists()


def test_deepseek_lock_treats_fresh_unreadable_metadata_as_active(tmp_path) -> None:
    lock_path = tmp_path / "deepseek-finalize.lock"
    lock_path.write_text("{", encoding="utf-8")

    assert deepseek_finalize_worker._lock_is_stale(lock_path, now=lock_path.stat().st_mtime + 1) is False


def test_deepseek_progress_write_persists_task_status_list(tmp_path) -> None:
    progress_path = tmp_path / "deepseek.progress.json"
    job = {"progress_path": str(progress_path)}

    deepseek_finalize_worker._write_progress(job, "Waiting for DeepSeek queue")
    deepseek_finalize_worker._write_progress(job, "Starting DeepSeek processing")

    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    tasks = payload["tasks"]

    assert {"name": "Waiting for DeepSeek queue", "status": "Finished"} in tasks
    assert {"name": "Starting DeepSeek processing", "status": "Processing"} in tasks
    assert {"name": "Updating interview notes document", "status": "Queued"} in tasks


def test_finalize_progress_tasks_mark_all_queued_finished_when_complete() -> None:
    tasks = interview_runtime.build_finalize_progress_tasks(
        "Complete",
        "complete",
        existing_tasks=[
            {"name": "Analyzing traits", "status": "Queued"},
            {"name": "Scoring traits", "status": "Queued"},
            {"name": "Complete", "status": "Queued"},
        ],
    )

    assert tasks == [
        {"name": "Analyzing traits", "status": "Finished"},
        {"name": "Scoring traits", "status": "Finished"},
        {"name": "Complete", "status": "Finished"},
    ]


def test_finalize_progress_tasks_mark_ordered_prior_queued_steps_finished() -> None:
    tasks = interview_runtime.build_finalize_progress_tasks(
        "Generating Executive Summary",
        "processing",
        existing_tasks=[
            {"name": "Starting local Ollama service", "status": "Queued"},
            {"name": "Local Ollama service ready", "status": "Queued"},
            {"name": "Analyzing traits", "status": "Queued"},
            {"name": "Scoring traits", "status": "Queued"},
            {"name": "Generating Executive Summary", "status": "Queued"},
            {"name": "Updating interview notes document", "status": "Queued"},
        ],
    )

    assert tasks == [
        {"name": "Starting local Ollama service", "status": "Finished"},
        {"name": "Local Ollama service ready", "status": "Finished"},
        {"name": "Analyzing traits", "status": "Finished"},
        {"name": "Scoring traits", "status": "Finished"},
        {"name": "Generating Executive Summary", "status": "Processing"},
        {"name": "Updating interview notes document", "status": "Queued"},
    ]


def test_deepseek_finalize_worker_marks_failed_when_no_deepseek_outputs_generate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )

    export_called = False

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = output_dir

        def export(self, _rubric, _payload, _scoring):
            nonlocal export_called
            export_called = True
            out_path = tmp_path / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(deepseek_finalize_worker, "generate_deepseek_interview_summaries", lambda *_args, **_kwargs: {"summary_status": "failed"})
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "failed", "model_scoring_status": "failed"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(deepseek_finalize_worker.ScoringEngine, "evaluate", staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}))
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    result = deepseek_finalize_worker.main(["deepseek_finalize_worker.py", str(job_path)])

    row = store.load()[0]
    assert result == 1
    assert export_called is False
    assert row["deepseek_processing_status"] == "failed"
    assert row["deepseek_processing_warning"] == (
        "DeepSeek processing failed: DeepSeek prompts incomplete: model_suggestion_status, model_scoring_status"
    )


def test_deepseek_finalize_worker_exports_partial_when_trait_advisory_has_no_transcript(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def export(self, _rubric, _payload, _scoring):
            out_path = self.output_dir / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_interview_summaries",
        lambda *_args, **_kwargs: {"summary_status": "generated", "answer_summaries": [{"flow_index": 1, "summary": "Summary."}]},
    )
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "no_transcript", "model_scoring_status": "no_transcript"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(
        deepseek_finalize_worker.ScoringEngine,
        "evaluate",
        staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}),
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    row = store.load()[0]
    assert row["deepseek_processing_status"] == "partial"
    assert row["deepseek_processing_warning"] == "DeepSeek processing partially completed."
    assert row["interview_notes_path"] == str(tmp_path / "updated.docx")


def test_deepseek_finalize_worker_exports_partial_when_trait_suggestions_are_partial(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_local_deepseek_runtime(monkeypatch)
    history_path = tmp_path / "history.json"
    store = InterviewHistoryStore(history_path)
    store.append(
        {
            "history_id": "hist-1",
            "candidate_name": "Ada",
            "interview_date": "2026-02-20",
            "saved_at": "2026-02-20T00:00:00Z",
            "deepseek_processing_status": "processing",
        }
    )

    class _Exporter:
        def __init__(self, output_dir):
            self.output_dir = Path(output_dir)

        def export(self, _rubric, _payload, _scoring):
            out_path = self.output_dir / "updated.docx"
            out_path.write_text("docx placeholder", encoding="utf-8")
            return out_path

    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_interview_summaries",
        lambda *_args, **_kwargs: {"summary_status": "generated", "answer_summaries": [{"flow_index": 1, "summary": "Summary."}]},
    )
    monkeypatch.setattr(
        deepseek_finalize_worker,
        "generate_deepseek_trait_signal_suggestions",
        lambda *_args, **_kwargs: {"model_suggestion_status": "partial", "model_scoring_status": "generated"},
    )
    monkeypatch.setattr(deepseek_finalize_worker, "DocxExporter", _Exporter)
    monkeypatch.setattr(
        deepseek_finalize_worker.ScoringEngine,
        "evaluate",
        staticmethod(lambda *_args, **_kwargs: {"percent_of_max": 88, "outcome": "Hire"}),
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(
        json.dumps(
            {
                "history_id": "hist-1",
                "history_path": str(history_path),
                "report_path": str(tmp_path / "original.docx"),
                "rubric": {},
                "payload": {"candidate": {"track": "lead"}, "flow_transcript": [], "trait_inputs": {}},
                "scoring": {},
                "deepseek_settings": {},
            }
        ),
        encoding="utf-8",
    )

    deepseek_finalize_worker.run_job(job_path)

    row = store.load()[0]
    assert row["deepseek_processing_status"] == "partial"
    assert row["deepseek_processing_warning"] == "DeepSeek processing partially completed."
    assert row["interview_notes_path"] == str(tmp_path / "updated.docx")


def test_deepseek_finalize_worker_lock_serializes_jobs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deepseek_finalize_worker, "_LOCK_POLL_SECONDS", 0.01)
    active_count = 0
    max_active_count = 0
    completed_jobs: list[str] = []
    lock = threading.Lock()

    def _worker(job_path):
        nonlocal active_count, max_active_count
        with deepseek_finalize_worker._deepseek_worker_lock(job_path):
            with lock:
                active_count += 1
                max_active_count = max(max_active_count, active_count)
            time.sleep(0.05)
            with lock:
                completed_jobs.append(job_path.stem)
                active_count -= 1

    job_one = tmp_path / "deepseek-finalize-one.json"
    job_two = tmp_path / "deepseek-finalize-two.json"
    first = threading.Thread(target=_worker, args=(job_one,))
    second = threading.Thread(target=_worker, args=(job_two,))

    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert max_active_count == 1
    assert sorted(completed_jobs) == [job_one.stem, job_two.stem]
    assert not deepseek_finalize_worker._lock_path_for_job(job_one).exists()


def test_deepseek_finalize_worker_lock_recovers_stale_owner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deepseek_finalize_worker, "_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(deepseek_finalize_worker, "_process_is_alive", lambda _pid: False)
    job_path = tmp_path / "deepseek-finalize-next.json"
    lock_path = deepseek_finalize_worker._lock_path_for_job(job_path)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999999,
                "created_at": "2026-01-01T00:00:00Z",
                "created_at_epoch": time.time(),
                "job": "deepseek-finalize-old",
            }
        ),
        encoding="utf-8",
    )

    with deepseek_finalize_worker._deepseek_worker_lock(job_path):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["job"] == job_path.stem
        assert metadata["pid"] == deepseek_finalize_worker.os.getpid()

    assert not lock_path.exists()


def test_deepseek_finalize_worker_main_rejects_missing_job_arg() -> None:
    assert deepseek_finalize_worker.main(["deepseek_finalize_worker.py"]) == 2


def test_build_finalize_context_uses_settings_summary_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def _completion(config, messages):
        assert config.api_key == "settings-key"
        if "executive summary section" in messages[0]["content"]:
            content = '{"executive_summary_sections":{"overall_fit":"Settings summary."},"executive_summary":"Settings summary.","interview_highlights":["Settings highlight."]}'
        else:
            content = (
                '{"answer_summaries":[{"flow_index":1,"summary":"Settings answer.",'
                '"evidence_quotes":["Candidate answer"],"rubric_alignment":"Evidence captured.",'
                '"risks_or_gaps":""}]}'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    monkeypatch.setattr(interview_runtime, "_request_deepseek_chat_completion", _completion)
    app = SimpleNamespace()
    app.settings = {
        "deepseek_summary_enabled": True,
        "deepseek_api_key": "settings-key",
        "deepseek_summary_timeout_seconds": 3,
    }
    app.state = SimpleNamespace(
        flow_recordings={1: {"base_name": "x"}},
        referral_packet={"transcript_path": "", "interview_notes_path": ""},
        to_dict=lambda: {"candidate": {"name": "Ada", "track": "general"}},
    )
    app._serialize_flow_audio_recordings = lambda: [{"flow_index": 1}]
    app._ordered_custom_answers = lambda: []
    app._build_flow_transcript = lambda: [{"flow_index": 1, "candidate_transcript": "Candidate answer."}]
    app._apply_candidate_transcripts_to_flow = lambda _flow_tx: None
    app._rewrite_live_transcript_docx_from_flow = lambda _flow_tx: None

    context = build_finalize_context(
        app,
        scoring={"outcome": "Hire"},
        warnings=[],
        transcript_metadata={"transcript_complete": True, "remaining_question_indices": []},
    )

    assert context.payload["summary_status"] == "generated"
    assert context.payload["executive_summary"] == "Settings summary."
    assert context.payload["interview_highlights"] == ["Settings highlight."]
    assert context.payload["answer_summaries"][0]["summary"] == "Settings answer."


def test_build_finalize_context_stores_model_suggestions_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["S_MODEL"],
            "core_signals": [{"signal_id": "S_MODEL", "label": "Model signal"}],
            "extended_groups": [],
        },
    )

    def _completion(_config, messages):
        system_text = messages[0]["content"]
        if "trait_suggestions" in messages[1]["content"]:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"Transcript evidence","rationale":"Transcript evidence."}]}]}'
            )
        elif "trait_scores" in messages[1]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"Transcript evidence","rationale":"Matches score 4 descriptor.",'
                '"risks_or_gaps":""}]}'
            )
        elif "executive summary section" in system_text:
            content = '{"executive_summary_sections":{"overall_fit":"Summary."},"executive_summary":"Summary.","interview_highlights":[]}'
        else:
            content = '{"answer_summaries":[{"flow_index":1,"summary":"Summary.","evidence_quotes":[],"rubric_alignment":"","risks_or_gaps":""}]}'
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(interview_runtime, "_request_deepseek_chat_completion", _completion)
    app = SimpleNamespace()
    app.settings = {"deepseek_summary_enabled": True, "deepseek_api_key": "settings-key"}
    app.state = SimpleNamespace(
        flow_recordings={1: {"base_name": "x"}},
        referral_packet={"transcript_path": "", "interview_notes_path": ""},
        trait_inputs={"trait_1": {"selected_signal_ids": ["S_MANUAL"]}},
        to_dict=lambda: {
            "candidate": {"name": "Ada", "track": "general"},
            "trait_inputs": {"trait_1": {"selected_signal_ids": ["S_MANUAL"]}},
        },
    )
    app._serialize_flow_audio_recordings = lambda: [{"flow_index": 1}]
    app._ordered_custom_answers = lambda: []
    app._build_flow_transcript = lambda: [
        {"type": "trait", "id": "trait_1", "candidate_transcript": "Transcript evidence."}
    ]
    app._apply_candidate_transcripts_to_flow = lambda _flow_tx: None
    app._rewrite_live_transcript_docx_from_flow = lambda _flow_tx: None

    context = build_finalize_context(
        app,
        scoring={"outcome": "Hire"},
        warnings=[],
        transcript_metadata={"transcript_complete": True, "remaining_question_indices": []},
    )

    assert context.payload["model_suggestion_status"] == "generated"
    assert context.payload["model_signal_suggestions_by_trait"] == {
        "trait_1": [
            {
                "signal_id": "S_MODEL",
                "confidence": 0.9,
                "rationale": "Transcript evidence.",
                "evidence_quote": "Transcript evidence",
            }
        ]
    }
    assert context.payload["trait_inputs"]["trait_1"]["selected_signal_ids"] == ["S_MANUAL"]
    assert context.payload["trait_inputs"]["trait_1"]["model_signal_suggestions"] == [
        {"signal_id": "S_MODEL", "confidence": 0.9, "rationale": "Transcript evidence.", "evidence_quote": "Transcript evidence"}
    ]
    assert context.payload["trait_inputs"]["trait_1"]["deepseek_raw_score"] == 4


def test_generate_deepseek_trait_signal_suggestions_filters_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["S_ONE", "S_TWO"],
            "core_signals": [{"signal_id": "S_ONE", "label": "One"}],
            "extended_groups": [{"signals": [{"signal_id": "S_TWO", "label": "Two"}]}],
        },
    )

    calls = []

    def _completion(_config, messages):
        calls.append(messages)
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"clear visual routine","rationale":"Specific routine example.",'
                '"analysis_summary":"Advisory score finds solid routine evidence.",'
                '"risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_ONE","confidence":0.8,"evidence_quote":"clear visual routine","rationale":"Specific example."},'
                '{"signal_id":"INVALID","confidence":1,"evidence_quote":"Bad","rationale":"Bad id."}]}]}'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    trait_state = {"trait_1": {"selected_signal_ids": ["S_TWO"]}}
    result = generate_deepseek_trait_signal_suggestions(
        [
            {
                "type": "trait",
                "id": "trait_1",
                "question": "How?",
                "candidate_transcript": "I use a clear visual routine.",
            }
        ],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=_completion,
        rubric={
            "scoring": {"raw_score_range": [1, 5]},
            "traits": [
                {
                    "id": "trait_1",
                    "name": "Empathy",
                    "descriptors": {"5": "Best evidence", "1": "Weak evidence"},
                }
            ],
        },
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_signal_suggestions_by_trait"] == {
        "trait_1": [
            {
                "signal_id": "S_ONE",
                "confidence": 0.8,
                "rationale": "Specific example.",
                "evidence_quote": "clear visual routine",
            }
        ]
    }
    assert trait_state["trait_1"]["selected_signal_ids"] == ["S_TWO"]
    assert trait_state["trait_1"]["model_signal_suggestions"] == [
        {"signal_id": "S_ONE", "confidence": 0.8, "rationale": "Specific example.", "evidence_quote": "clear visual routine"}
    ]
    assert trait_state["trait_1"]["deepseek_raw_score"] == 4
    assert trait_state["trait_1"]["model_trait_score"]["analysis_summary"] == "Advisory score finds solid routine evidence."
    suggestion_system_prompt = calls[0][0]["content"]
    suggestion_user_prompt = calls[0][1]["content"]
    assert "Treat rubric wording as reference context, not a grading rubric" in suggestion_system_prompt
    assert "emotional intelligence" in suggestion_system_prompt
    assert "start from question and answer content, not numeric rubric descriptors" in suggestion_user_prompt
    assert "preschool role expectations" in suggestion_user_prompt
    scoring_prompt_payload = calls[1][1]["content"]
    scoring_guard_prompt = calls[1][2]["content"]
    assert "rubric.json descriptors" in calls[1][0]["content"]
    assert '"raw_score_range": [1, 5]' in scoring_prompt_payload
    assert '"descriptors": {"5": "Best evidence", "1": "Weak evidence"}' in scoring_prompt_payload
    assert '"interviewer_raw_score"' not in scoring_prompt_payload
    assert "trait_based_scoring_json" in scoring_prompt_payload
    assert "Required current trait_id: trait_1" in scoring_guard_prompt
    assert "Do not return placeholder values" in scoring_guard_prompt


def test_generate_deepseek_trait_signal_suggestions_uses_only_current_trait_engine_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["S_ONE"],
            "core_signals": [{"signal_id": "S_ONE", "label": "One"}],
            "extended_groups": [],
        },
    )
    monkeypatch.setattr(
        interview_runtime,
        "_trait_based_scoring_json_context",
        lambda: {
            "trait_1": {"trait_id": "trait_1", "engine_marker": "CURRENT_TRAIT_ONLY"},
            "trait_2": {"trait_id": "trait_2", "engine_marker": "UNRELATED_TRAIT"},
        },
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"visual routine","rationale":"Matches.",'
                '"analysis_summary":"Advisory score looked only at trait 1.",'
                '"risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1",'
                '"analysis_summary":"Signal review looked only at trait 1.",'
                '"suggestions":[{"signal_id":"S_ONE","confidence":0.8,'
                '"evidence_quote":"visual routine","rationale":"Specific example."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    trait_state: dict[str, dict[str, object]] = {"trait_1": {}}
    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "question": "How?", "candidate_transcript": "I use a visual routine."}],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=_completion,
        rubric={"traits": [{"id": "trait_1", "name": "Routines"}, {"id": "trait_2", "name": "Unrelated"}]},
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_signal_analysis_by_trait"] == {"trait_1": "Signal review looked only at trait 1."}
    assert trait_state["trait_1"]["model_signal_analysis_summary"] == "Signal review looked only at trait 1."
    assert trait_state["trait_1"]["model_trait_score"]["analysis_summary"] == "Advisory score looked only at trait 1."
    combined_prompt_text = "\n".join(message["content"] for call in calls for message in call)
    assert "CURRENT_TRAIT_ONLY" in combined_prompt_text
    assert "UNRELATED_TRAIT" not in combined_prompt_text


def test_generate_deepseek_trait_scores_preserve_risk_flag_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["S_CONCERN"],
            "core_signals": [{"signal_id": "S_CONCERN", "label": "Concern"}],
            "extended_groups": [],
        },
    )

    calls = []

    def _completion(_config, messages):
        calls.append(messages)
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":2,'
                '"evidence_quote":"I yell first","rationale":"Matches low descriptor.",'
                '"risks_or_gaps":"Unsafe first response.",'
                '"risk_flag_evidence":"Candidate says they would yell first."}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_CONCERN","confidence":0.8,"evidence_quote":"I yell first","rationale":"Direct concern."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    trait_state = {"trait_1": {}}
    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "question": "How?", "candidate_transcript": "I yell first."}],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=_completion,
        rubric={"traits": [{"id": "trait_1", "name": "Empathy", "descriptors": {"2": "Weak safety response"}}]},
    )

    scoring_prompt = calls[1][1]["content"]
    assert '"risk_flag_evidence"' in scoring_prompt
    assert result["model_trait_scores_by_trait"]["trait_1"]["risk_flag_evidence"] == "Candidate says they would yell first."
    assert trait_state["trait_1"]["model_trait_score"]["risk_flag_evidence"] == "Candidate says they would yell first."


def test_generate_deepseek_trait_signal_suggestions_reports_step_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "trait_1",
                    "flow_index": 1,
                    "title": "Empathy",
                    "question": "How?",
                    "candidate_transcript": "I use routines.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Uses routines"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"trait_1": ["S_MODEL"]},
        ),
    )
    steps: list[str] = []

    def fake_completion(_config, messages):
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"routines","rationale":"Matches.","risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"routines","rationale":"Matches."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I use routines."}],
        {},
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=fake_completion,
        progress_callback=steps.append,
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_scoring_status"] == "generated"
    assert steps == ["Analyzing Traits Q1: Empathy", "Scoring Q1: Empathy"]


def test_generate_deepseek_trait_signal_suggestions_preserves_empty_valid_suggestion_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "trait_1",
                    "flow_index": 1,
                    "title": "Empathy",
                    "question": "How?",
                    "candidate_transcript": "I use routines.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Uses routines"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"trait_1": ["S_MODEL"]},
        ),
    )

    def fake_completion(_config, messages):
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"routines","rationale":"Matches.","risks_or_gaps":""}]}'
            )
        else:
            content = '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":[]}]}'
        return {"choices": [{"message": {"content": content}}]}

    trait_state: dict[str, dict[str, object]] = {"trait_1": {}}
    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I use routines."}],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=fake_completion,
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_signal_suggestions_by_trait"] == {"trait_1": []}
    assert result["model_suggestion_warnings"] == []
    assert trait_state["trait_1"]["model_signal_suggestions"] == []


def test_generate_deepseek_trait_signal_suggestions_retries_invalid_json_until_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "trait_1",
                    "flow_index": 1,
                    "title": "Empathy",
                    "question": "How?",
                    "candidate_transcript": "I use routines.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Uses routines"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"trait_1": ["S_MODEL"]},
        ),
    )
    suggestion_attempts = 0

    def fake_completion(_config, messages):
        nonlocal suggestion_attempts
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"routines","rationale":"Matches.","risks_or_gaps":""}]}'
            )
        else:
            suggestion_attempts += 1
            content = "not json" if suggestion_attempts == 1 else (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"routines","rationale":"Matches."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I use routines."}],
        {},
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=fake_completion,
    )

    assert suggestion_attempts == 2
    assert result["model_suggestion_status"] == "generated"
    assert result["model_scoring_status"] == "generated"


def test_generate_deepseek_trait_signal_suggestions_retries_trait_score_for_wrong_trait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "bss_trait_6",
                    "flow_index": 8,
                    "title": "Non-Diagnostic Professional Boundaries",
                    "question": "What would you say?",
                    "candidate_transcript": "That is beyond my realm to answer.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Maintains boundaries"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"bss_trait_6": ["S_MODEL"]},
        ),
    )
    scoring_attempts = 0

    def fake_completion(_config, messages):
        nonlocal scoring_attempts
        if "trait_scores" in messages[1]["content"]:
            scoring_attempts += 1
            if scoring_attempts == 1:
                content = (
                    '{"trait_scores":[{"trait_id":"from input","raw_score":1,'
                    '"evidence_quote":"exact short candidate wording","rationale":"template echo",'
                    '"risks_or_gaps":""}]}'
                )
            else:
                content = (
                    '{"trait_scores":[{"trait_id":"bss_trait_6","raw_score":5,'
                    '"evidence_quote":"beyond my realm","rationale":"Maintains professional boundaries.",'
                    '"risks_or_gaps":""}]}'
                )
        else:
            content = '{"trait_suggestions":[{"trait_id":"bss_trait_6","suggestions":[]}]}'
        return {"choices": [{"message": {"content": content}}]}

    trait_state: dict[str, dict[str, object]] = {"bss_trait_6": {}}
    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "bss_trait_6", "candidate_transcript": "That is beyond my realm to answer."}],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=fake_completion,
    )

    assert scoring_attempts == 2
    assert result["model_scoring_status"] == "generated"
    assert result["model_trait_scores_by_trait"]["bss_trait_6"]["raw_score"] == 5
    assert trait_state["bss_trait_6"]["deepseek_raw_score"] == 5


def test_generate_deepseek_trait_signal_suggestions_uses_configured_prompt_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "trait_1",
                    "flow_index": 1,
                    "title": "Empathy",
                    "question": "How?",
                    "candidate_transcript": "I use routines.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Uses routines"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"trait_1": ["S_MODEL"]},
        ),
    )
    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="secret-key",
        prompt_templates={
            "trait_suggestion_system": "CUSTOM SUGGEST SYSTEM",
            "trait_suggestion_user": "CUSTOM SUGGEST USER {payload_json}",
            "trait_scoring_system": "CUSTOM SCORE SYSTEM",
            "trait_scoring_user": "CUSTOM SCORE USER {payload_json}",
        },
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        if messages[0]["content"] == "CUSTOM SCORE SYSTEM":
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"routines","rationale":"Matches.","risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"routines","rationale":"Matches."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I use routines."}],
        {},
        config=config,
        chat_completion=_completion,
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_scoring_status"] == "generated"
    assert calls[0][0]["content"] == "CUSTOM SUGGEST SYSTEM"
    assert calls[0][1]["content"].startswith("CUSTOM SUGGEST USER ")
    assert '"trait_id": "trait_1"' in calls[0][1]["content"]
    assert calls[1][0]["content"] == "CUSTOM SCORE SYSTEM"
    assert calls[1][1]["content"].startswith("CUSTOM SCORE USER ")
    assert '"scoring_policy"' in calls[1][1]["content"]


def test_generate_deepseek_trait_signal_suggestions_uses_trait_specific_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "_trait_suggestion_items",
        lambda _flow, _rubric=None: (
            [
                {
                    "trait_id": "trait_1",
                    "flow_index": 1,
                    "title": "Empathy",
                    "question": "How?",
                    "candidate_transcript": "I use routines.",
                    "valid_signals": [{"signal_id": "S_MODEL", "label": "Uses routines"}],
                    "rubric": {},
                    "trait_based_scoring_json": {},
                }
            ],
            {"trait_1": ["S_MODEL"]},
        ),
    )
    config = DeepSeekSummaryConfig(
        enabled=True,
        api_key="secret-key",
        prompt_templates={
            "trait_suggestion_system_by_question": {"trait_1": "CUSTOM TRAIT SUGGEST SYSTEM"},
            "trait_suggestion_user_by_question": {"trait_1": "CUSTOM TRAIT SUGGEST {payload_json}"},
            "trait_scoring_system_by_question": {"trait_1": "CUSTOM TRAIT SCORE SYSTEM"},
            "trait_scoring_user_by_question": {"trait_1": "CUSTOM TRAIT SCORE {payload_json}"},
        },
    )
    calls: list[list[dict[str, str]]] = []

    def _completion(_config, messages):
        calls.append(messages)
        if messages[0]["content"] == "CUSTOM TRAIT SCORE SYSTEM":
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"routines","rationale":"Matches.","risks_or_gaps":""}]}'
            )
        else:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"routines","rationale":"Matches."}]}]}'
            )
        return {"choices": [{"message": {"content": content}}]}

    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I use routines."}],
        {},
        config=config,
        chat_completion=_completion,
    )

    assert result["model_suggestion_status"] == "generated"
    assert result["model_scoring_status"] == "generated"
    assert calls[0][0]["content"] == "CUSTOM TRAIT SUGGEST SYSTEM"
    assert calls[0][1]["content"].startswith("CUSTOM TRAIT SUGGEST ")
    assert calls[1][0]["content"] == "CUSTOM TRAIT SCORE SYSTEM"
    assert calls[1][1]["content"].startswith("CUSTOM TRAIT SCORE ")


def test_generate_deepseek_trait_signal_suggestions_accepts_fenced_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["warmth"],
            "core_signals": [{"signal_id": "warmth", "label": "Warmth"}],
            "extended_groups": [],
        },
    )

    def _completion(_config, messages):
        if "Score preschool teacher" in messages[0]["content"]:
            content = (
                '```json\n{"trait_scores":[{"trait_id":"trait_1","raw_score":5,'
                '"evidence_quote":"greet each child warmly","rationale":"Strong warmth evidence.",'
                '"risks_or_gaps":""}]}\n```'
            )
        else:
            content = (
                '```json\n{"trait_suggestions":[{"trait_id":"trait_1","suggestions":'
                '[{"signal_id":"warmth","confidence":0.7,"evidence_quote":"greet each child warmly","rationale":"Warm greeting."}]}]}\n```'
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ]
        }

    trait_state: dict[str, dict[str, object]] = {}
    result = generate_deepseek_trait_signal_suggestions(
        [{"type": "trait", "id": "trait_1", "candidate_transcript": "I greet each child warmly."}],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=_completion,
        rubric={
            "scoring": {"raw_score_range": [1, 5]},
            "traits": [
                {
                    "id": "trait_1",
                    "name": "Empathy",
                    "descriptors": {"5": "Best evidence", "1": "Weak evidence"},
                }
            ],
        },
    )

    assert result["model_suggestion_status"] == "generated"
    assert trait_state["trait_1"]["model_signal_suggestions"] == [
        {"signal_id": "warmth", "confidence": 0.7, "rationale": "Warm greeting.", "evidence_quote": "greet each child warmly"}
    ]
    assert trait_state["trait_1"]["deepseek_raw_score"] == 5
