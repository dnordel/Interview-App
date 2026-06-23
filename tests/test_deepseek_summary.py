from __future__ import annotations

import json
import threading
import time
import urllib.error
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
    generate_deepseek_interview_summaries,
    generate_deepseek_trait_signal_suggestions,
)


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
    assert calls[0]["body"]["model"] == "deepseek-r1:8b"
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
    assert config.model == "deepseek-r1:8b"


def test_deepseek_summary_config_defaults_to_local_ollama_when_disabled() -> None:
    config = build_deepseek_summary_config({})

    assert config.enabled is False
    assert config.api_key == "ollama"
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "deepseek-r1:8b"


def test_deepseek_summary_config_enables_local_ollama_without_hosted_api_key() -> None:
    config = build_deepseek_summary_config({"DEEPSEEK_SUMMARY_ENABLED": "1"})

    assert config.enabled is True
    assert config.api_key == "ollama"
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "deepseek-r1:8b"


def test_deepseek_summary_config_rejects_hosted_base_url() -> None:
    config = build_deepseek_summary_config(
        {
            "DEEPSEEK_SUMMARY_ENABLED": "1",
            "DEEPSEEK_API_BASE_URL": "https://api.deepseek.com",
        }
    )

    assert config.enabled is True
    assert config.base_url == "http://127.0.0.1:11434/v1"


def test_generate_deepseek_interview_summaries_uses_injected_completion() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    calls = []

    def _completion(active_config, messages):
        calls.append((active_config, messages))
        if "executive summary section" in messages[0]["content"]:
            content = '{"executive_summary":"Strong classroom routines.","interview_highlights":["Uses visuals.","Keeps calm transitions."]}'
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
        "interview_highlights": ["Uses visuals.", "Keeps calm transitions."],
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
            return {"choices": [{"message": {"content": '{"executive_summary":"Custom executive.","interview_highlights":[]}'}}]}
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
            return {"choices": [{"message": {"content": '{"executive_summary":"Done.","interview_highlights":[]}'}}]}
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
            return {"choices": [{"message": {"content": '{"executive_summary":"Recommend with reservations.","interview_highlights":["Strong routines."]}'}}]}
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


def test_generate_deepseek_interview_summaries_chunks_answer_calls() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")
    answer_payloads: list[str] = []

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            return {"choices": [{"message": {"content": '{"executive_summary":"Two answers summarized.","interview_highlights":["Uses routines."]}'}}]}
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
            return {"choices": [{"message": {"content": '{"executive_summary":"Summary.","interview_highlights":[]}'}}]}
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
        [{"flow_index": 1, "question": "How?", "candidate_transcript": "I use routines."}],
        {"name": "Ada"},
        config=config,
        chat_completion=fake_completion,
        progress_callback=steps.append,
    )

    assert result["summary_status"] == "generated"
    assert steps == ["Summarizing Q1", "Generating Executive Summary"]


def test_generate_deepseek_interview_summaries_accepts_fenced_json() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            content = '```json\n{"executive_summary":"Calm transition support.","interview_highlights":["Uses picture cues."]}\n```'
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


def test_generate_deepseek_interview_summaries_generates_with_highlights_only() -> None:
    config = DeepSeekSummaryConfig(enabled=True, api_key="secret-key")

    def _completion(_config, messages):
        if "executive summary section" in messages[0]["content"]:
            content = '{"executive_summary":"","interview_highlights":["Patient family communication."]}'
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
    app = SimpleNamespace(
        settings={"base_dir": str(tmp_path), "deepseek_summary_enabled": True},
        history_store=InterviewHistoryStore(tmp_path / "history.json"),
        _rubric_with_question_overrides=lambda: {"tracks": {}},
    )
    context = SimpleNamespace(payload={"candidate": {"name": "Ada"}}, scoring={"outcome": "Hire"})

    job_path = enqueue_deepseek_finalize_job(app, context, str(tmp_path / "notes.docx"), "hist-1")

    assert job_path.exists()
    assert calls
    job_payload = job_path.read_text(encoding="utf-8")
    assert '"history_id": "hist-1"' in job_payload
    assert str(job_path) in calls[0]["args"]


def test_deepseek_finalize_worker_updates_history_status(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_deepseek_finalize_worker_passes_final_scoring_to_summary_generation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_deepseek_finalize_worker_marks_partial_when_some_deepseek_outputs_fail(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    deepseek_finalize_worker.run_job(job_path)

    row = store.load()[0]
    assert row["deepseek_processing_status"] == "partial"
    assert row["deepseek_processing_warning"] == "DeepSeek processing partially completed."


def test_deepseek_finalize_worker_marks_failed_when_no_deepseek_outputs_generate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    deepseek_finalize_worker.run_job(job_path)

    row = store.load()[0]
    assert row["deepseek_processing_status"] == "failed"
    assert row["deepseek_processing_warning"] == "DeepSeek processing failed to generate output."


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
            content = '{"executive_summary":"Settings summary.","interview_highlights":["Settings highlight."]}'
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
        if "trait-based scoring observations" in system_text:
            content = (
                '{"trait_suggestions":[{"trait_id":"trait_1","suggestions":['
                '{"signal_id":"S_MODEL","confidence":0.9,"evidence_quote":"Transcript evidence","rationale":"Transcript evidence."}]}]}'
            )
        elif "Score preschool teacher" in system_text:
            content = (
                '{"trait_scores":[{"trait_id":"trait_1","raw_score":4,'
                '"evidence_quote":"Transcript evidence","rationale":"Matches score 4 descriptor.",'
                '"risks_or_gaps":""}]}'
            )
        elif "executive summary section" in system_text:
            content = '{"executive_summary":"Summary.","interview_highlights":[]}'
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
    scoring_prompt_payload = calls[1][1]["content"]
    assert "rubric.json descriptors" in calls[1][0]["content"]
    assert '"raw_score_range": [1, 5]' in scoring_prompt_payload
    assert '"descriptors": {"5": "Best evidence", "1": "Weak evidence"}' in scoring_prompt_payload
    assert "trait_based_scoring_json" in scoring_prompt_payload


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
    assert steps == ["Analyzing Traits Q1", "Scoring Q1"]


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


def test_generate_deepseek_trait_signal_suggestions_continues_after_trait_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interview_runtime,
        "load_trait_signal_ui_definition",
        lambda _trait_id: {
            "valid_signal_ids": ["S_ONE"],
            "core_signals": [{"signal_id": "S_ONE", "label": "One"}],
            "extended_groups": [],
        },
    )

    calls: list[str] = []

    def _completion(_config, messages):
        user_text = messages[1]["content"]
        calls.append(user_text)
        if '"trait_id": "trait_1"' in user_text:
            raise urllib.error.HTTPError("http://local", 500, "too large", {}, None)
        if "Score preschool teacher" in messages[0]["content"]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"trait_scores":[{"trait_id":"trait_2","raw_score":5,'
                                '"evidence_quote":"gentle voice","rationale":"Strong descriptor match.",'
                                '"risks_or_gaps":""}]}'
                            )
                        }
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"trait_suggestions":[{"trait_id":"trait_2","suggestions":['
                            '{"signal_id":"S_ONE","confidence":0.9,"evidence_quote":"gentle voice","rationale":"Specific example."}]}]}'
                        )
                    }
                }
            ]
        }

    trait_state = {"trait_1": {}, "trait_2": {}}
    result = generate_deepseek_trait_signal_suggestions(
        [
            {"type": "trait", "id": "trait_1", "candidate_transcript": "This prompt fails."},
            {"type": "trait", "id": "trait_2", "candidate_transcript": "I use a gentle voice."},
        ],
        trait_state,
        config=DeepSeekSummaryConfig(enabled=True, api_key="secret-key"),
        chat_completion=_completion,
        rubric={
            "scoring": {"raw_score_range": [1, 5]},
            "traits": [
                {"id": "trait_1", "name": "Empathy", "descriptors": {"5": "Best evidence"}},
                {"id": "trait_2", "name": "Gentleness", "descriptors": {"5": "Best evidence"}},
            ],
        },
    )

    assert len(calls) >= 3
    assert result["model_suggestion_status"] == "partial"
    assert result["model_scoring_status"] == "partial"
    assert "trait_1" not in result["model_trait_scores_by_trait"]
    assert result["model_trait_scores_by_trait"]["trait_2"]["raw_score"] == 5
    assert trait_state["trait_2"]["deepseek_raw_score"] == 5
    assert "deepseek_raw_score" not in trait_state["trait_1"]


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
