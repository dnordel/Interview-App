from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from datetime import date
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from admin_studio import AdminStudio, AdminStudioPaths
from docx import Document
from data_store import (
    InterviewHistoryStore,
    QuestionOverridesStore,
    RubricLoader,
    SchoolOfferSettingsStore,
    default_school_offer_settings,
    resolve_interview_notes_output_dir,
)
from interview_runtime import (
    DEFAULT_WINDOWS_MIC_DEVICE,
    DEEPSEEK_PROMPTS_CONFIG_PATH,
    DEFAULT_DEEPSEEK_PROGRESS_TASKS,
    FinalizeGateways,
    build_finalize_progress_tasks,
    build_finalize_context,
    build_flow_time_windows,
    enqueue_deepseek_finalize_job,
    format_finalize_progress_tasks,
    load_candidate_segments,
    map_segments_to_flow_indices,
    regenerate_interview_notes_job,
    resolve_deepseek_regeneration_job_path,
    resolve_default_windows_system_device,
)
from onboarding_operations import JsonStore, build_dashboard_today_summary, filtered_tasks, task_status
from platform_services import (
    DEFAULT_RUBRIC_PATH,
    DEFAULT_SCHOOL_OPTIONS,
    DEFAULT_BASE_DIR,
    INTERVIEW_HISTORY_PATH,
    QUESTIONS_OVERRIDE_PATH,
    SCHOOL_OFFER_SETTINGS_PATH,
    atomic_write_json,
    compose_intro_script,
)
from scoring_reporting import OfferInput, OfferLetterService, ScoringEngine, build_offer_filename
from scoring_reporting import build_integration_payload, serialize_integration_payload
from scoring_reporting import CANONICAL_DEGREE_TYPES, CandidateQualification, validate_candidate_qualification
from ui_mode_switch import switch_to_ui_mode


APP_TITLE = "Interview Assistant"
NAVIGATION = ["Interviews", "Candidates", "Offers", "Onboarding", "Admin"]
SETUP_STEPS = ["Candidate", "Interview Plan", "Ready"]
QUICK_ACTIONS = [
    "Needs follow-up",
    "Candidate gave no example",
    "Evidence captured",
    "Disqualifier observed",
]
PYSIDE_FINALIZE_PROGRESS_TASKS = (
    "Stopping recording and transcribing",
    "Building interview notes",
    "Queueing DeepSeek processing",
    *DEFAULT_DEEPSEEK_PROGRESS_TASKS,
)


@dataclass(frozen=True)
class RecentInterview:
    candidate: str
    school: str
    role: str
    score: str
    status: str
    next_action: str


@dataclass(frozen=True)
class PySideHistoryRow:
    row_key: str
    interview_date: str
    candidate: str
    school: str
    position: str
    score: str
    status: str
    offer_status: str
    offer_action: str
    notes_path: str
    report_path: str
    deepseek_processing_status: str = ""
    deepseek_processing_warning: str = ""


@dataclass(frozen=True)
class HomeModel:
    primary_action: str
    continue_action: str
    admin_visible_on_home: bool
    recent_interviews: list[RecentInterview]
    history_rows: list[PySideHistoryRow]


@dataclass(frozen=True)
class ReadinessCheck:
    label: str
    ready: bool


@dataclass(frozen=True)
class ScoreCard:
    label: str
    description: str


@dataclass(frozen=True)
class FlowQuestion:
    kind: str
    question_id: str
    title: str
    prompt: str
    progress_label: str
    followups: list[str] = field(default_factory=list)
    score_cards: list[ScoreCard] = field(default_factory=list)
    quick_actions: list[str] = field(default_factory=list)
    disqualifiers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrackFlow:
    track_key: str
    label: str
    items: list[FlowQuestion]


@dataclass(frozen=True)
class InterviewRedesignModel:
    app_title: str
    navigation: list[str]
    setup_steps: list[str]
    school_options: list[str]
    track_labels: dict[str, str]
    readiness_checks: list[ReadinessCheck]
    home: HomeModel
    flows: dict[str, TrackFlow]
    rubric: dict[str, Any]
    history_path: Path


@dataclass(frozen=True)
class PySideReviewSummary:
    percent_of_max: float
    outcome: str
    next_action: str
    missing_scores: list[str]
    strongest_evidence: list[str]
    concerns: list[str]


@dataclass(frozen=True)
class PySideOnboardingBoard:
    overdue: int
    due_today: int
    due_soon: int
    next_task: str
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class PySideCandidateBoard:
    total_candidates: int
    rows: list[dict[str, str]]
    history_rows: list[PySideHistoryRow] = field(default_factory=list)


@dataclass
class PySideInterviewSession:
    model: InterviewRedesignModel
    draft_path: Path
    candidate_name: str = ""
    school: str = ""
    track_key: str = ""
    current_index: int = 0
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    qualification: dict[str, Any] = field(default_factory=dict)
    flow_time_marks: list[dict[str, Any]] = field(default_factory=list)
    flow_candidate_transcripts: dict[int, str] = field(default_factory=dict)
    flow_recordings: dict[int, dict[str, Any]] = field(default_factory=dict)

    def start(self, *, candidate_name: str, school: str, track_key: str) -> None:
        if track_key not in self.model.flows:
            raise ValueError(f"Unknown track: {track_key}")
        self.candidate_name = candidate_name.strip()
        self.school = school.strip()
        self.track_key = track_key
        self.current_index = 0
        self.answers = {}
        self.qualification = {}
        self.flow_time_marks = []
        self.flow_candidate_transcripts = {}
        self.flow_recordings = {}
        self.save_draft()

    def _workflow_items(self) -> list[FlowQuestion]:
        flow = self.model.flows.get(self.track_key)
        if flow is None:
            return []
        total = len(flow.items) + 1
        return [
            _intro_flow_question(self.school, total),
            *[replace(item, progress_label=f"Question {index} of {total}") for index, item in enumerate(flow.items, start=2)],
        ]

    def active_question(self) -> FlowQuestion | None:
        items = self._workflow_items()
        if self.current_index < 0 or self.current_index >= len(items):
            return None
        return items[self.current_index]

    def save_answer_and_advance(
        self,
        *,
        notes: str,
        score: str = "",
        quick_actions: Sequence[str] = (),
        qualification: dict[str, Any] | None = None,
        skipped: bool = False,
    ) -> None:
        question = self.active_question()
        if question is None:
            self.save_draft()
            return
        answer = {
            "kind": question.kind,
            "title": question.title,
            "prompt": question.prompt,
            "notes": notes,
            "score": score,
            "quick_actions": [str(action) for action in quick_actions],
        }
        if skipped:
            answer["skipped"] = True
        if question.kind == "qualification" or qualification is not None:
            normalized = _normalize_qualification_payload(qualification or {})
            self.qualification = normalized
            answer["qualification"] = normalized
        self.answers[question.question_id] = answer
        self.current_index += 1
        self.save_draft()

    def go_back(self) -> None:
        if self.current_index <= 0:
            self.current_index = 0
            self.save_draft()
            return
        self.current_index -= 1
        self.save_draft()

    def skip_active_question(self, *, notes: str = "", quick_actions: Sequence[str] = ()) -> None:
        self.save_answer_and_advance(notes=notes, score="", quick_actions=quick_actions, skipped=True)

    def review_summary(self) -> PySideReviewSummary:
        flow = self.model.flows.get(self.track_key)
        trait_inputs: dict[str, dict[str, Any]] = {}
        missing_scores: list[str] = []
        strongest_evidence: list[str] = []
        concerns: list[str] = []
        if flow is None:
            return PySideReviewSummary(
                percent_of_max=0.0,
                outcome="Incomplete",
                next_action="Review",
                missing_scores=[],
                strongest_evidence=[],
                concerns=["No interview flow is available."],
            )
        for item in flow.items:
            answer = self.answers.get(item.question_id, {})
            if item.kind != "trait":
                continue
            raw_score = _coerce_session_score(answer.get("score"))
            notes = str(answer.get("notes", "") or "").strip()
            quick_actions = [str(action) for action in answer.get("quick_actions", []) or []]
            trait_inputs[item.question_id] = {
                "raw_score": raw_score,
                "question_notes": notes,
                "trait_notes": notes,
                "absolute_disqualifier": "Disqualifier observed" in quick_actions,
                "no_example_after_followups": "Candidate gave no example" in quick_actions,
            }
            if raw_score is None:
                missing_scores.append(item.title)
            if raw_score is not None and raw_score >= 4 and notes:
                strongest_evidence.append(notes)
            if raw_score is not None and raw_score <= 2 and notes:
                concerns.append(notes)
            if "Disqualifier observed" in quick_actions:
                concerns.append(f"Disqualifier observed: {item.title}")
            if "Candidate gave no example" in quick_actions:
                concerns.append(f"No concrete example: {item.title}")
        scoring = ScoringEngine.evaluate(self.model.rubric, self.track_key, trait_inputs)
        outcome = str(scoring.get("outcome", "Incomplete"))
        return PySideReviewSummary(
            percent_of_max=float(scoring.get("percent_of_max", 0.0) or 0.0),
            outcome=outcome,
            next_action=_next_action_for_outcome(outcome),
            missing_scores=missing_scores,
            strongest_evidence=strongest_evidence[:5],
            concerns=concerns[:5],
        )

    def offer_review_defaults(self) -> dict[str, str]:
        summary = self.review_summary()
        track = self.model.flows.get(self.track_key)
        return {
            "candidate": self.candidate_name,
            "school": self.school,
            "position": track.label if track is not None else self.track_key,
            "determination": summary.outcome,
            "next_action": summary.next_action,
            "employment_type": "Full-time",
            "start_date": "",
            "start_time": "08:00 AM",
            "end_time": "05:00 PM",
            "hourly_pay": "",
            "hours_week": "40",
            "template_path": "",
            "output_dir": str(DEFAULT_BASE_DIR / "offers"),
        }

    def generate_offer_document(
        self,
        *,
        template_path: Path,
        output_dir: Path,
        start_date: date,
        start_time_12h: str,
        end_time_12h: str,
        hourly_pay: float,
        hours: int,
        created_on: date,
    ) -> Path:
        first_name, last_name = _split_candidate_name(self.candidate_name)
        defaults = self.offer_review_defaults()
        output_path = Path(output_dir) / build_offer_filename(first_name, last_name, created_on)
        data = OfferInput(
            first_name=first_name,
            last_name=last_name,
            city=self.school,
            position=defaults["position"],
            start_date=start_date,
            start_time_12h=start_time_12h,
            end_time_12h=end_time_12h,
            hourly_pay=float(hourly_pay),
            hours=int(hours),
            created_on=created_on,
        )
        return OfferLetterService.render_offer(Path(template_path), output_path, data)

    def generate_interview_notes_document(self, *, output_dir: Path) -> Path:
        result = self.finalize_interview(
            base_dir=Path(output_dir),
            history_path=Path(output_dir) / "interview_history.json",
        )
        return Path(result["out_path"])

    def finalize_interview(
        self,
        *,
        base_dir: Path = DEFAULT_BASE_DIR,
        history_path: Path = INTERVIEW_HISTORY_PATH,
        gateways: FinalizeGateways | None = None,
    ) -> dict[str, Any]:
        adapter = _PySideFinalizeAdapter(self, base_dir=Path(base_dir), history_path=Path(history_path))
        warnings: list[str] = []
        scoring = ScoringEngine.evaluate(adapter._rubric_with_question_overrides(), adapter.state.track, adapter.state.trait_inputs)
        transcript_metadata = self._transcript_metadata()
        context = build_finalize_context(adapter, scoring, warnings, transcript_metadata, run_deepseek=False)
        active_gateways = gateways or FinalizeGateways()
        out_path = active_gateways.export_report(adapter, context)
        integration_path = active_gateways.export_integration(adapter, context)
        director_packet, comm_log_path = active_gateways.send_referral(adapter, context, out_path, integration_path)
        history_id = active_gateways.persist_finalize_history(adapter, context, out_path)
        deepseek_job_path = enqueue_deepseek_finalize_job(adapter, context, out_path, history_id)
        deepseek_job_available = bool(getattr(deepseek_job_path, "name", ""))
        deepseek_progress_path = deepseek_job_path.with_suffix(".progress.json") if deepseek_job_available else Path()
        return {
            "scoring": scoring,
            "out_path": str(out_path),
            "integration_path": Path(integration_path).as_posix(),
            "transcript_path": context.transcript_path,
            "director_packet": director_packet,
            "warnings": warnings,
            "communication_log_path": str(comm_log_path) if comm_log_path else None,
            "transcript_complete": transcript_metadata["transcript_complete"],
            "transcript_completeness_status": transcript_metadata["transcript_completeness_status"],
            "remaining_question_indices": transcript_metadata["remaining_question_indices"],
            "deepseek_job_path": str(deepseek_job_path) if deepseek_job_available else "",
            "deepseek_progress_path": str(deepseek_progress_path) if deepseek_job_available else "",
        }

    def _transcript_metadata(self) -> dict[str, Any]:
        total = len(self._workflow_items())
        missing: list[int] = []
        for flow_idx in range(total):
            transcript = str(self.flow_candidate_transcripts.get(flow_idx, "") or "").strip()
            recording = self.flow_recordings.get(flow_idx, {}) or {}
            if transcript or str(recording.get("candidate_transcript") or "").strip():
                continue
            missing.append(flow_idx + 1)
        complete = not missing
        return {
            "transcript_complete": complete,
            "transcript_completeness_status": "complete" if complete else "partial",
            "remaining_question_indices": missing,
        }

    def to_draft(self) -> dict[str, Any]:
        return {
            "schema": "pyside_interview_draft.v1",
            "candidate_name": self.candidate_name,
            "school": self.school,
            "track_key": self.track_key,
            "current_index": self.current_index,
            "qualification": self.qualification,
            "answers": self.answers,
            "flow_time_marks": self.flow_time_marks,
            "flow_candidate_transcripts": {str(key): value for key, value in self.flow_candidate_transcripts.items()},
            "flow_recordings": {str(key): value for key, value in self.flow_recordings.items()},
        }

    def save_draft(self) -> None:
        atomic_write_json(Path(self.draft_path), self.to_draft(), indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, *, model: InterviewRedesignModel, draft_path: Path) -> PySideInterviewSession:
        try:
            payload = json.loads(Path(draft_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid PySide interview draft") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "pyside_interview_draft.v1":
            raise ValueError("Invalid PySide interview draft")
        track_key = str(payload.get("track_key", "")).strip()
        if track_key not in model.flows:
            raise ValueError(f"Unknown track in draft: {track_key}")
        current_index = int(payload.get("current_index", 0) or 0)
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            raise ValueError("Invalid PySide interview draft answers")
        qualification = payload.get("qualification", {})
        if not isinstance(qualification, dict):
            qualification = {}
        flow_time_marks = payload.get("flow_time_marks", [])
        if not isinstance(flow_time_marks, list):
            flow_time_marks = []
        flow_candidate_transcripts = payload.get("flow_candidate_transcripts", {})
        if not isinstance(flow_candidate_transcripts, dict):
            flow_candidate_transcripts = {}
        flow_recordings = payload.get("flow_recordings", {})
        if not isinstance(flow_recordings, dict):
            flow_recordings = {}
        return cls(
            model=model,
            draft_path=Path(draft_path),
            candidate_name=str(payload.get("candidate_name", "")).strip(),
            school=str(payload.get("school", "")).strip(),
            track_key=track_key,
            current_index=max(0, current_index),
            qualification=_normalize_qualification_payload(qualification),
            answers={str(key): value for key, value in answers.items() if isinstance(value, dict)},
            flow_time_marks=[dict(item) for item in flow_time_marks if isinstance(item, dict)],
            flow_candidate_transcripts={
                int(key): str(value)
                for key, value in flow_candidate_transcripts.items()
                if str(key).lstrip("-").isdigit()
            },
            flow_recordings={
                int(key): dict(value)
                for key, value in flow_recordings.items()
                if str(key).lstrip("-").isdigit() and isinstance(value, dict)
            },
        )


class _PySideFinalizeAdapter:
    def __init__(self, session: PySideInterviewSession, *, base_dir: Path, history_path: Path) -> None:
        self.session = session
        self.settings = {
            "base_dir": str(base_dir),
            "send_director_referral_on_finalize": False,
            "director_referral_endpoint": "",
            "deepseek_summary_enabled": True,
            "deepseek_api_key": "ollama",
            "deepseek_api_base_url": "http://127.0.0.1:11434/v1",
            "deepseek_summary_model": "deepseek-r1:8b",
            "deepseek_summary_timeout_seconds": 120,
            "deepseek_prompt_templates": {},
        }
        self.school_offer_store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
        self.history_store = InterviewHistoryStore(Path(history_path))
        self.state = SimpleNamespace(
            candidate_name=session.candidate_name,
            track=session.track_key,
            trait_inputs=self._trait_inputs(),
            custom_inputs=self._custom_inputs(),
            flow_recordings=dict(session.flow_recordings),
            flow_candidate_transcripts=self._flow_candidate_transcripts(),
            referral_packet={"transcript_path": "", "interview_notes_path": ""},
            communication_log=[],
            to_dict=self._state_payload,
        )

    def _rubric_with_question_overrides(self) -> dict[str, Any]:
        return dict(self.session.model.rubric)

    def _interview_notes_output_dir(self) -> Path:
        return resolve_interview_notes_output_dir(
            Path(self.settings["base_dir"]),
            self.session.school,
            self.school_offer_store.load(),
        )

    def _safe_attr(self, _name: str) -> Any:
        return None

    def _collect_transcription_health_warnings(self) -> list[str]:
        return []

    def _hydrate_state_from_session_store(self) -> None:
        return None

    def _serialize_flow_audio_recordings(self) -> list[dict[str, Any]]:
        recordings: list[dict[str, Any]] = []
        for flow_idx in sorted(self.session.flow_recordings):
            item = dict(self.session.flow_recordings.get(flow_idx, {}) or {})
            item["flow_index"] = int(item.get("flow_index", flow_idx))
            recordings.append(item)
        return recordings

    def _ordered_custom_answers(self) -> list[dict[str, Any]]:
        custom_answers: list[dict[str, Any]] = []
        for question in self._workflow_items():
            if question.kind not in {"custom", "qualification", "intro"}:
                continue
            answer = self.session.answers.get(question.question_id, {})
            custom_answers.append(
                {
                    "id": question.question_id,
                    "question": question.prompt,
                    "answer": str(answer.get("notes", "") or ""),
                    "skipped": False,
                }
            )
        return custom_answers

    def _build_flow_transcript(self) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        for index, question in enumerate(self._workflow_items()):
            answer = self.session.answers.get(question.question_id, {})
            notes = str(answer.get("notes", "") or "")
            transcript.append(
                {
                    "flow_index": index,
                    "type": question.kind,
                    "id": question.question_id,
                    "title": question.title,
                    "prompt": question.prompt,
                    "candidate_transcript": notes,
                    "evaluator_notes": notes,
                }
            )
        return transcript

    def _apply_candidate_transcripts_to_flow(self, _flow_tx: list[dict[str, Any]]) -> None:
        by_flow_index: dict[int, str] = {}
        for key, value in self.session.flow_candidate_transcripts.items():
            text = str(value or "").strip()
            if text:
                by_flow_index[int(key)] = text
        for key, value in self.session.flow_recordings.items():
            text = str((value or {}).get("candidate_transcript") or "").strip()
            if text:
                by_flow_index[int(key)] = text
        for index, item in enumerate(_flow_tx):
            item["candidate_transcript"] = by_flow_index.get(index, str(item.get("candidate_transcript") or ""))

    def _rewrite_live_transcript_docx_from_flow(self, _flow_tx: list[dict[str, Any]]) -> None:
        return None

    def _state_payload(self) -> dict[str, Any]:
        track = self.session.model.flows.get(self.session.track_key)
        return {
            "candidate": {
                "name": self.session.candidate_name,
                "candidate_name": self.session.candidate_name,
                "interview_date": date.today().isoformat(),
                "school": self.session.school,
                "track": self.session.track_key,
                "position": track.label if track is not None else self.session.track_key,
                "qualification": dict(self.session.qualification),
            }
        }

    def _trait_inputs(self) -> dict[str, dict[str, Any]]:
        inputs: dict[str, dict[str, Any]] = {}
        for question in self._workflow_items():
            if question.kind != "trait":
                continue
            answer = self.session.answers.get(question.question_id, {})
            quick_actions = [str(action) for action in answer.get("quick_actions", []) or []]
            notes = str(answer.get("notes", "") or "")
            inputs[question.question_id] = {
                "raw_score": _coerce_session_score(answer.get("score")),
                "question_notes": notes,
                "trait_notes": notes,
                "verbatim_notes": notes,
                "absolute_disqualifier": "Disqualifier observed" in quick_actions,
                "no_example_after_followups": "Candidate gave no example" in quick_actions,
                "skipped": False,
            }
        return inputs

    def _custom_inputs(self) -> dict[str, dict[str, Any]]:
        custom: dict[str, dict[str, Any]] = {}
        for question in self._workflow_items():
            if question.kind not in {"custom", "qualification", "intro"}:
                continue
            answer = self.session.answers.get(question.question_id, {})
            custom[question.question_id] = {
                "question_text": question.prompt,
                "answer": str(answer.get("notes", "") or ""),
                "skipped": False,
            }
        return custom

    def _flow_candidate_transcripts(self) -> dict[int, str]:
        return dict(self.session.flow_candidate_transcripts)

    def _workflow_items(self) -> list[FlowQuestion]:
        return self.session._workflow_items()


def _history_text(row: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _coerce_history_percent(value: Any) -> float | None:
    text = str(value or "").strip().rstrip("%").strip()
    if not text:
        return None
    try:
        percent = float(text)
    except ValueError:
        return None
    if 0 <= percent <= 100:
        return percent
    return None


def _history_has_no_hire_override(row: dict[str, Any]) -> bool:
    override_keys = ("disqualifier_present", "critical_eq_1", "critical_lt_3")
    if any(bool(row.get(key)) for key in override_keys):
        return True
    locked_rule = str(row.get("locked_rule") or row.get("override_rationale") or "").strip().lower()
    if "automatic no-hire signal" in locked_rule or "deepseek automatic no-hire" in locked_rule:
        return False
    return any(term in locked_rule for term in ("no hire", "disqualifier", "critical"))


def _history_status_from_score(row: dict[str, Any], score: Any, status: Any) -> str:
    status_text = str(status or "").strip()
    normalized = _normalize_history_search(status_text)
    if normalized in {"incomplete", "processing"}:
        return status_text
    if normalized == "no hire" and _history_has_no_hire_override(row):
        return status_text or "No Hire"

    percent = _coerce_history_percent(score)
    if percent is None:
        return status_text
    if percent >= 80:
        return "Hire"
    if percent >= 65:
        return "Borderline"
    return "No Hire"


def _history_offer_action(offer_status: str) -> str:
    status = str(offer_status or "not_generated").strip().lower() or "not_generated"
    if status == "not_generated":
        return "Generate Offer"
    if status == "generated":
        return "Mark Approved"
    if status == "approved":
        return "Mark Accepted"
    if status == "accepted":
        return "Open Onboarding"
    return "Review Offer"


def _build_pyside_history_rows(history_path: Path) -> list[PySideHistoryRow]:
    store = InterviewHistoryStore(Path(history_path))
    rows = store.load()
    history_rows: list[PySideHistoryRow] = []
    for row in rows:
        row_key = store.build_row_key(row)
        offer_status = _history_text(row, "offer_status", default="not_generated").strip().lower() or "not_generated"
        score = _history_text(row, "score", "percent_of_max", "overall_score", "interview_score", default="")
        status = _history_status_from_score(
            row,
            score,
            _history_text(row, "status", "interview_status", "outcome", "determination", default=""),
        )
        history_rows.append(
            PySideHistoryRow(
                row_key=row_key,
                interview_date=_history_text(row, "interview_date", "date", default=""),
                candidate=_history_text(row, "candidate_name", "candidate", "name", default="Unknown candidate"),
                school=_history_text(row, "school", default=""),
                position=_history_text(row, "position", "candidate_position", "role", "track", default=""),
                score=score,
                status=status,
                offer_status=offer_status,
                offer_action=_history_offer_action(offer_status),
                notes_path=_history_text(row, "interview_notes_path", "saved_report_path", "notes_path", default=""),
                report_path=_history_text(row, "saved_report_path", "report_path", "interview_notes_path", default=""),
                deepseek_processing_status=_history_text(row, "deepseek_processing_status", default="").strip().lower(),
                deepseek_processing_warning=_history_text(row, "deepseek_processing_warning", default=""),
            )
        )
    return history_rows


def _recent_interviews_from_history_rows(history_rows: Sequence[PySideHistoryRow], *, limit: int = 6) -> list[RecentInterview]:
    recent: list[RecentInterview] = []
    for row in history_rows[:limit]:
        recent.append(
            RecentInterview(
                candidate=row.candidate,
                school=row.school,
                role=row.position,
                score=row.score,
                status=row.status,
                next_action=row.offer_action if row.offer_status == "not_generated" else "Review",
            )
        )
    return recent


def _ordered_traits(loader: RubricLoader, store: QuestionOverridesStore, track_key: str) -> list[dict[str, Any]]:
    traits = loader.get_traits_for_track(track_key)
    by_id = {str(trait.get("id")): trait for trait in traits}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for trait_id in store.get_trait_order(track_key):
        trait = by_id.get(str(trait_id))
        if trait is None:
            continue
        ordered.append(trait)
        seen.add(str(trait.get("id")))
    for trait in traits:
        trait_id = str(trait.get("id"))
        if trait_id not in seen:
            ordered.append(trait)
    return ordered


def _score_cards(trait: dict[str, Any]) -> list[ScoreCard]:
    descriptors = trait.get("descriptors", {}) or {}
    cards: list[ScoreCard] = []
    for score in ["1", "2", "3", "4", "5"]:
        description = str(descriptors.get(score, "")).strip()
        if not description:
            description = f"Score {score}"
        cards.append(ScoreCard(label=score, description=description))
    return cards


def _flow_question_for_trait(
    *,
    trait: dict[str, Any],
    store: QuestionOverridesStore,
    index: int,
    total: int,
    disqualifiers: list[str],
) -> FlowQuestion:
    trait_id = str(trait.get("id", "")).strip()
    prompt = store.get_trait_question_override(trait_id) or str(trait.get("primary_question", "")).strip()
    raw_followups = (
        trait.get("followups", [])
        or trait.get("follow_up_prompts", [])
        or trait.get("follow_up_probes", [])
        or []
    )
    followups = [str(item).strip() for item in raw_followups if str(item).strip()]
    return FlowQuestion(
        kind="trait",
        question_id=trait_id,
        title=str(trait.get("name", trait_id)).strip(),
        prompt=prompt,
        progress_label=f"Question {index + 1} of {total}",
        followups=followups,
        score_cards=_score_cards(trait),
        quick_actions=list(QUICK_ACTIONS),
        disqualifiers=disqualifiers,
    )


def _flow_question_for_custom(question: dict[str, Any], *, index: int, total: int) -> FlowQuestion:
    question_id = str(question.get("id", "")).strip()
    kind = "qualification" if question_id == "Why-ECE" else "custom"
    return FlowQuestion(
        kind=kind,
        question_id=question_id,
        title="Candidate qualification" if kind == "qualification" else "Non-scored question",
        prompt=str(question.get("text", "")).strip(),
        progress_label=f"Question {index + 1} of {total}",
        quick_actions=["Mark as important"],
    )


def _intro_flow_question(school: str, total: int) -> FlowQuestion:
    return FlowQuestion(
        kind="intro",
        question_id="intro_script",
        title="Intro Script",
        prompt=compose_intro_script(school),
        progress_label=f"Question 1 of {total}",
        quick_actions=["Mark as read"],
    )


def _normalize_qualification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return CandidateQualification.from_dict(payload).to_dict()


def _optional_int_text(value: Any) -> str:
    return "" if value is None else str(value)


def _build_track_flow(
    *,
    loader: RubricLoader,
    store: QuestionOverridesStore,
    track_key: str,
    track_label: str,
    disqualifiers: list[str],
) -> TrackFlow:
    traits = _ordered_traits(loader, store, track_key)
    custom_questions = store.list_custom_questions(track_key)
    trait_by_id = {str(trait.get("id")): trait for trait in traits}
    custom_by_id = {str(question.get("id")): question for question in custom_questions}
    flow = store.ensure_flow(
        track_key,
        [str(trait.get("id")) for trait in traits],
        [str(question.get("id")) for question in custom_questions],
    )

    items: list[FlowQuestion] = []
    total = len(flow)
    for index, flow_item in enumerate(flow):
        item_type = str(flow_item.get("type", "")).strip()
        item_id = str(flow_item.get("id", "")).strip()
        if item_type == "trait" and item_id in trait_by_id:
            items.append(
                _flow_question_for_trait(
                    trait=trait_by_id[item_id],
                    store=store,
                    index=index,
                    total=total,
                    disqualifiers=disqualifiers,
                )
            )
        if item_type == "custom" and item_id in custom_by_id:
            items.append(_flow_question_for_custom(custom_by_id[item_id], index=index, total=total))
    return TrackFlow(track_key=track_key, label=track_label, items=items)


def build_interview_redesign_model(
    *,
    rubric_path: Path = DEFAULT_RUBRIC_PATH,
    overrides_path: Path = QUESTIONS_OVERRIDE_PATH,
    history_path: Path = INTERVIEW_HISTORY_PATH,
    school_options: Sequence[str] = DEFAULT_SCHOOL_OPTIONS,
) -> InterviewRedesignModel:
    loader = RubricLoader(Path(rubric_path))
    store = QuestionOverridesStore(Path(overrides_path))
    tracks = loader.data.get("tracks", {}) or {}
    disqualifiers = [str(item).strip() for item in loader.data.get("absolute_disqualifiers", []) if str(item).strip()]

    track_labels = {str(key): str(value.get("label", key)) for key, value in tracks.items() if isinstance(value, dict)}
    flows = {
        track_key: _build_track_flow(
            loader=loader,
            store=store,
            track_key=track_key,
            track_label=label,
            disqualifiers=disqualifiers,
        )
        for track_key, label in track_labels.items()
    }

    readiness = [
        ReadinessCheck("Storage folder ready", True),
        ReadinessCheck("Question set loaded", bool(flows)),
        ReadinessCheck("Word template available", True),
    ]
    resolved_history_path = Path(history_path)
    history_rows = _build_pyside_history_rows(resolved_history_path)
    return InterviewRedesignModel(
        app_title=APP_TITLE,
        navigation=list(NAVIGATION),
        setup_steps=list(SETUP_STEPS),
        school_options=[str(option) for option in school_options],
        track_labels=track_labels,
        readiness_checks=readiness,
        home=HomeModel(
            primary_action="Start a New Interview",
            continue_action="Continue Draft",
            admin_visible_on_home=False,
            recent_interviews=_recent_interviews_from_history_rows(history_rows),
            history_rows=history_rows,
        ),
        flows=flows,
        rubric=loader.data,
        history_path=resolved_history_path,
    )


def build_pyside_onboarding_board(
    *,
    employees: list[Any],
    scheduler_settings: dict[str, Any] | None,
    today: date,
) -> PySideOnboardingBoard:
    summary = build_dashboard_today_summary(
        history_rows=[],
        employees=employees,
        scheduler_settings=scheduler_settings,
        today=today,
    )
    rows: list[dict[str, str]] = []
    due_soon = 0
    for employee in sorted(employees, key=lambda item: str(getattr(item, "name", "")).lower()):
        urgent = filtered_tasks(list(getattr(employee, "tasks", []) or []), today, "urgent")
        next_task = urgent[0] if urgent else None
        for task in getattr(employee, "tasks", []) or []:
            if task_status(task, today) == "due_soon":
                due_soon += 1
        rows.append(
            {
                "employee": str(getattr(employee, "name", "")),
                "school": str(getattr(employee, "school", "")),
                "next_task": str(getattr(next_task, "title", "")) if next_task is not None else "",
                "next_due": str(getattr(next_task, "due_date", "")) if next_task is not None else "",
                "status": task_status(next_task, today) if next_task is not None else "clear",
            }
        )
    next_critical = summary.onboarding.next_critical
    next_label = ""
    if next_critical is not None:
        next_label = f"{next_critical.employee_name}: {next_critical.title}"
    return PySideOnboardingBoard(
        overdue=summary.onboarding.overdue,
        due_today=summary.onboarding.due_today,
        due_soon=due_soon,
        next_task=next_label,
        rows=rows,
    )


def build_pyside_candidate_board(history_rows: Sequence[dict[str, Any] | PySideHistoryRow]) -> PySideCandidateBoard:
    by_candidate: dict[str, dict[str, str]] = {}
    shared_history_rows: list[PySideHistoryRow] = []
    for row in history_rows:
        if isinstance(row, PySideHistoryRow):
            candidate = row.candidate or "Unknown candidate"
            shared_history_rows.append(row)
            row_data = {
                "school": row.school,
                "role": row.position,
                "score": row.score,
                "status": row.status,
                "next_action": row.offer_action,
            }
        else:
            candidate = _history_text(row, "candidate_name", "candidate", "name", default="Unknown candidate")
            offer_status = _history_text(row, "offer_status", default="").strip().lower()
            offer_action = _history_text(row, "next_action", "recommended_next_action", default="")
            if not offer_action:
                offer_action = _history_offer_action(offer_status)
            score = _history_text(row, "score", "percent_of_max", "overall_score", "interview_score", default="")
            status = _history_status_from_score(
                row,
                score,
                _history_text(row, "status", "interview_status", "outcome", "determination", default=""),
            )
            shared_history_rows.append(
                PySideHistoryRow(
                    row_key=_history_text(row, "history_id", "row_key", default=""),
                    interview_date=_history_text(row, "interview_date", "date", default=""),
                    candidate=candidate,
                    school=_history_text(row, "school", default=""),
                    position=_history_text(row, "position", "candidate_position", "role", "track", default=""),
                    score=score,
                    status=status,
                    offer_status=offer_status or "not_generated",
                    offer_action=offer_action,
                    notes_path=_history_text(row, "interview_notes_path", "saved_report_path", "notes_path", default=""),
                    report_path=_history_text(row, "saved_report_path", "report_path", "interview_notes_path", default=""),
                    deepseek_processing_status=_history_text(row, "deepseek_processing_status", default="").strip().lower(),
                    deepseek_processing_warning=_history_text(row, "deepseek_processing_warning", default=""),
                )
            )
            row_data = {
                "school": _history_text(row, "school", default=""),
                "role": _history_text(row, "role", "track", "position", default=""),
                "score": score,
                "status": status,
                "next_action": offer_action,
            }
        if candidate in by_candidate:
            continue
        by_candidate[candidate] = {
            "candidate": candidate,
            "school": row_data["school"],
            "role": row_data["role"],
            "score": row_data["score"],
            "status": row_data["status"],
            "next_action": row_data["next_action"] or "Review",
        }
    return PySideCandidateBoard(
        total_candidates=len(by_candidate),
        rows=list(by_candidate.values()),
        history_rows=shared_history_rows,
    )


def latest_pyside_draft_path(drafts_dir: Path = DEFAULT_BASE_DIR / "pyside_drafts") -> Path | None:
    folder = Path(drafts_dir)
    try:
        candidates = [path for path in folder.glob("*.json") if path.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _coerce_session_score(value: Any) -> int | None:
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= score <= 5:
        return score
    return None


def _next_action_for_outcome(outcome: str) -> str:
    normalized = outcome.strip().lower()
    if normalized == "hire":
        return "Generate Offer"
    if normalized == "borderline":
        return "Refer to Director"
    if normalized == "no hire":
        return "Return Home"
    return "Review"


def _split_candidate_name(candidate_name: str) -> tuple[str, str]:
    parts = candidate_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "Candidate"


def _parse_iso_or_us_date(value: str) -> date:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Date must be YYYY-MM-DD or MM/DD/YYYY.")


def _normalize_history_search(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return " ".join(text.split())


def _history_token_matches(term: str, candidates: list[str]) -> bool:
    if not term:
        return True
    for candidate in candidates:
        if term in candidate or candidate in term:
            return True
        if SequenceMatcher(None, term, candidate).ratio() >= 0.78:
            return True
    return False


def _history_outcome_color(outcome: str) -> str:
    normalized = _normalize_history_search(outcome)
    if normalized in {"no hire", "reject", "rejected"}:
        return "#fee2e2"
    if normalized in {"hire", "hired", "accepted"}:
        return "#dcfce7"
    if normalized in {"needs follow up", "follow up", "followup", "pending"}:
        return "#fef3c7"
    if normalized in {"incomplete", "processing"}:
        return "#e5e7eb"
    return ""


class PySide6UnavailableError(RuntimeError):
    pass


def _import_qt() -> Any:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise PySide6UnavailableError(
            "PySide6 is not installed. Install requirements, then launch this redesign."
        ) from exc
    return QtCore, QtGui, QtWidgets


def standard_window_control_flags(QtCore: Any) -> Any:
    window_type = QtCore.Qt.WindowType
    return (
        window_type.Window
        | window_type.WindowTitleHint
        | window_type.WindowSystemMenuHint
        | window_type.WindowMinimizeButtonHint
        | window_type.WindowMaximizeButtonHint
        | window_type.WindowCloseButtonHint
    )


def _apply_styles(app: Any) -> None:
    app.setStyleSheet(
        """
        QWidget {
            font-family: "Segoe UI";
            font-size: 14px;
            color: #172033;
            background: #f6f7f9;
        }
        QLabel#Title {
            font-size: 26px;
            font-weight: 700;
        }
        QLabel#SectionTitle {
            font-size: 18px;
            font-weight: 650;
        }
        QLabel#PySideFinalizeProgressTitle {
            font-size: 18px;
            font-weight: 700;
            background: transparent;
        }
        QLabel#PySideFinalizeProgressHelp {
            color: #526071;
            background: transparent;
        }
        QLabel#PySideFinalizeProgressLabel {
            font-family: "Cascadia Mono", Consolas, monospace;
            font-size: 12px;
            background: #ffffff;
            color: #172033;
        }
        QPushButton {
            border: 1px solid #c9ced8;
            border-radius: 6px;
            padding: 8px 12px;
            background: #ffffff;
        }
        QPushButton#PrimaryButton {
            color: white;
            background: #2563eb;
            border-color: #2563eb;
            font-weight: 650;
        }
        QFrame#Surface {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 8px;
        }
        QListWidget {
            background: #111827;
            color: #e5e7eb;
            border: 0;
            padding: 8px;
        }
        QListWidget::item {
            padding: 10px;
            border-radius: 6px;
        }
        QListWidget::item:selected {
            background: #2563eb;
        }
        QLineEdit, QTextEdit, QComboBox {
            background: #ffffff;
            border: 1px solid #c9ced8;
            border-radius: 6px;
            padding: 7px;
        }
        QTableWidget {
            background: #ffffff;
            border: 1px solid #d9dee7;
            gridline-color: #eef1f5;
        }
        QScrollArea#PySideFinalizeProgressScroll {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 6px;
        }
        """
    )


class PySideInterviewWindow:
    def __init__(self, model: InterviewRedesignModel) -> None:
        QtCore, QtGui, QtWidgets = _import_qt()
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.model = model
        self.session_track_key = next(iter(model.flows), "")
        self.session_index = 0
        self.session_answers: dict[str, dict[str, Any]] = {}
        self.session: PySideInterviewSession | None = None
        self.selected_history_offer_row: PySideHistoryRow | None = None
        self.history_store = InterviewHistoryStore(model.history_path)
        self.school_offer_store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
        self.history_search_text = ""
        self.history_school_filter_text = ""
        self.history_outcome_filter_text = ""
        self.recording_session: Any | None = None
        self.recording_base_name = ""
        self.recording_started_monotonic: float | None = None
        self.recording_candidate_label = "CANDIDATE"
        self.recording_warning = ""
        self._pyside_finalize_running = False
        self._pyside_finalize_progress_step = ""
        self._pyside_finalize_progress_tasks: list[dict[str, str]] = []
        self.pyside_finalize_progress_dialog: Any | None = None
        self.pyside_finalize_progress_label: Any | None = None
        self.pyside_finalize_progress_bar: Any | None = None
        self.pyside_finalize_deepseek_progress_path: Path | None = None
        self._pyside_finalize_progress_queue: queue.Queue[str] | None = None
        self._pyside_finalize_progress_refresh_timer: Any | None = None
        self._pyside_deepseek_progress_timer: Any | None = None
        self._history_table_widgets: dict[str, Any] = {}
        self._overwrite_next_live_timestamp = False
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowFlags(standard_window_control_flags(QtCore))
        self.window.setWindowTitle(model.app_title)
        self.window.resize(1260, 820)
        self.stack = QtWidgets.QStackedWidget()
        self.sidebar = QtWidgets.QListWidget()
        for item in model.navigation:
            self.sidebar.addItem(item)
        self.sidebar.setFixedWidth(190)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

        root = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.sidebar)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addLayout(self._ui_switch_row())
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)
        self.window.setCentralWidget(root)

        self.stack.addWidget(self._interviews_page())
        self.stack.addWidget(self._candidates_page())
        self.stack.addWidget(self._offer_page())
        self.stack.addWidget(self._onboarding_page())
        self.stack.addWidget(self._admin_page())
        self.sidebar.setCurrentRow(0)

    def show(self) -> None:
        self.window.show()

    def _ui_switch_row(self) -> Any:
        row = self.QtWidgets.QHBoxLayout()
        row.setContentsMargins(16, 10, 16, 0)
        row.addStretch(1)
        row.addWidget(self._label("Current UI: PySide"))
        tk_button = self.QtWidgets.QPushButton("Switch to Tk UI")
        tk_button.clicked.connect(lambda: self._switch_to_ui_mode("tk"))
        row.addWidget(tk_button)
        return row

    def _switch_to_ui_mode(self, mode: str) -> None:
        try:
            switch_to_ui_mode(mode, app_root=Path(__file__).resolve().parent.parent)
        except Exception as exc:
            self.QtWidgets.QMessageBox.critical(self.window, "Switch UI", f"Could not switch UI: {exc}")
            return
        self.window.close()

    def _page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        return page, layout

    def _scrollable_page(self) -> tuple[Any, Any]:
        page = self.QtWidgets.QWidget()
        outer_layout = self.QtWidgets.QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = self.QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        content = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return page, layout

    def _label(self, text: str, object_name: str = "") -> Any:
        label = self.QtWidgets.QLabel(text)
        if object_name:
            label.setObjectName(object_name)
        label.setWordWrap(True)
        return label

    def _surface(self) -> tuple[Any, Any]:
        frame = self.QtWidgets.QFrame()
        frame.setObjectName("Surface")
        layout = self.QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return frame, layout

    def _primary_button(self, text: str) -> Any:
        button = self.QtWidgets.QPushButton(text)
        button.setObjectName("PrimaryButton")
        return button

    def _interviews_page(self) -> Any:
        page, layout = self._page()
        layout.addWidget(self._label(self.model.app_title, "Title"))
        layout.addWidget(self._label("Guided hiring workflow: setup, interview, scoring, notes, next action."))
        self.interview_tabs = self.QtWidgets.QTabWidget()
        self.interview_tabs.addTab(self._home_tab(), "Home")
        self.interview_tabs.addTab(self._setup_tab(), "Setup")
        self.interview_tabs.addTab(self._live_question_tab(), "Live Interview")
        self.interview_tabs.addTab(self._review_tab(), "Review")
        layout.addWidget(self.interview_tabs, 1)
        return page

    def _home_tab(self) -> Any:
        page, layout = self._page()

        setup, setup_layout = self._surface()
        setup_layout.addWidget(self._label(self.model.home.primary_action, "SectionTitle"))
        form = self.QtWidgets.QFormLayout()
        candidate = self.QtWidgets.QLineEdit()
        school = self.QtWidgets.QComboBox()
        school.addItems(self.model.school_options)
        role = self.QtWidgets.QComboBox()
        role.addItems(list(self.model.track_labels.values()))
        self.home_role_combo = role
        form.addRow("Candidate name", candidate)
        self.home_candidate_input = candidate
        form.addRow("School", school)
        self.home_school_combo = school
        form.addRow("Role", role)
        setup_layout.addLayout(form)

        latest_draft = latest_pyside_draft_path()
        self.home_draft_label = self._label(
            f"Saved draft: {latest_draft.name}" if latest_draft else "No saved draft available."
        )
        if latest_draft:
            self.home_draft_label.setToolTip(str(latest_draft))
        setup_layout.addWidget(self.home_draft_label)

        action_row = self.QtWidgets.QHBoxLayout()
        begin = self._primary_button("Begin Interview")
        begin.clicked.connect(self._begin_selected_interview)
        action_row.addWidget(begin, 2)
        continue_button = self.QtWidgets.QPushButton("Continue")
        self.home_continue_button = continue_button
        continue_button.setEnabled(latest_draft is not None)
        continue_button.clicked.connect(lambda: self._continue_latest_draft())
        action_row.addWidget(continue_button, 1)
        delete_draft = self.QtWidgets.QPushButton("Delete Saved Draft")
        self.home_delete_draft_button = delete_draft
        delete_draft.setEnabled(latest_draft is not None)
        delete_draft.clicked.connect(self._delete_latest_draft)
        action_row.addWidget(delete_draft, 1)
        setup_layout.addLayout(action_row)
        layout.addWidget(setup)

        recent, recent_layout = self._surface()
        recent_layout.addWidget(self._label("Interview History", "SectionTitle"))
        controls = self.QtWidgets.QHBoxLayout()
        search = self.QtWidgets.QLineEdit()
        search.setPlaceholderText("Search history")
        search.textChanged.connect(self._set_history_search_text)
        self.history_search_input = search
        controls.addWidget(search, 2)

        school_filter = self.QtWidgets.QComboBox()
        school_filter.addItems(["All schools", *self._history_school_options()])
        school_filter.currentTextChanged.connect(self._set_history_school_filter)
        self.history_school_filter = school_filter
        controls.addWidget(school_filter, 1)

        outcome_filter = self.QtWidgets.QComboBox()
        outcome_filter.addItems(["All outcomes", *self._history_outcome_options()])
        outcome_filter.currentTextChanged.connect(self._set_history_outcome_filter)
        self.history_outcome_filter = outcome_filter
        controls.addWidget(outcome_filter, 1)
        recent_layout.addLayout(controls)

        table = self._create_history_table("PySideHistoryGrid")
        self.history_table = table
        self._refresh_history_table()
        recent_layout.addWidget(table, 1)
        layout.addWidget(recent, 1)
        return page

    def _history_school_options(self) -> list[str]:
        values = {row.school for row in self.model.home.history_rows if row.school}
        return sorted(values, key=str.lower)

    def _history_outcome_options(self) -> list[str]:
        values = {row.status for row in self.model.home.history_rows if row.status}
        return sorted(values, key=str.lower)

    def _set_history_search_text(self, value: str) -> None:
        self.history_search_text = value.strip()
        self._refresh_history_table()

    def _set_history_school_filter(self, value: str) -> None:
        self.history_school_filter_text = "" if value == "All schools" else value.strip()
        self._refresh_history_table()

    def _set_history_outcome_filter(self, value: str) -> None:
        self.history_outcome_filter_text = "" if value == "All outcomes" else value.strip()
        self._refresh_history_table()

    def _filtered_history_rows(self) -> list[PySideHistoryRow]:
        rows = self.model.home.history_rows
        school = self.history_school_filter_text.lower()
        outcome = self.history_outcome_filter_text.lower()
        filtered: list[PySideHistoryRow] = []
        for row in rows:
            if school and row.school.lower() != school:
                continue
            if outcome and row.status.lower() != outcome:
                continue
            if not self._history_row_matches_search(row, self.history_search_text):
                continue
            filtered.append(row)
        return filtered

    def _history_row_matches_search(self, row: PySideHistoryRow, search_text: str) -> bool:
        query = _normalize_history_search(search_text)
        if not query:
            return True
        blob = _normalize_history_search(
            " ".join([row.interview_date, row.candidate, row.school, row.position, row.score, row.status])
        )
        if query in blob:
            return True
        blob_tokens = blob.split()
        return all(_history_token_matches(term, blob_tokens) for term in query.split())

    def _create_history_table(self, object_name: str) -> Any:
        table = self.QtWidgets.QTableWidget(0, 10)
        table.setObjectName(object_name)
        table.setHorizontalHeaderLabels(
            ["Date", "Candidate", "School", "Position", "Score", "Status", "Notes", "Regenerate", "Offer", "Delete"]
        )
        table.horizontalHeader().setStretchLastSection(True)
        table.setSortingEnabled(True)
        policy = table.sizePolicy()
        policy.setVerticalStretch(1)
        table.setSizePolicy(policy)
        self._history_table_widgets[object_name] = table
        return table

    def _refresh_history_table(self, table: Any | None = None, rows: Sequence[PySideHistoryRow] | None = None) -> None:
        table = table or getattr(self, "history_table", None)
        if table is None:
            return
        visible_rows = list(rows) if rows is not None else self._filtered_history_rows()
        table.setSortingEnabled(False)
        table.setRowCount(len(visible_rows))
        for row_index, row in enumerate(visible_rows):
            self._populate_history_table_row(table, row_index, row)
        self._size_history_table_columns(table)
        table.setSortingEnabled(True)

    def _refresh_all_history_tables(self) -> None:
        home_table = getattr(self, "history_table", None)
        if home_table is not None:
            self._refresh_history_table(home_table)
        candidate_table = getattr(self, "candidate_history_table", None)
        if candidate_table is not None:
            self._refresh_history_table(candidate_table, self.model.home.history_rows)

    def _populate_history_table_row(self, table: Any, row_index: int, row: PySideHistoryRow) -> None:
        values = [row.interview_date, row.candidate, row.school, row.position, row.score, row.status]
        background = self._history_outcome_brush(row.status)
        for column, value in enumerate(values):
            item = self.QtWidgets.QTableWidgetItem(value)
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, row.row_key)
            if background is not None:
                item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, background)
            table.setItem(row_index, column, item)
        notes_label, notes_enabled, notes_tooltip = self._history_notes_action_state(row)
        notes_button = self.QtWidgets.QPushButton(notes_label)
        notes_button.setMaximumWidth(95)
        notes_button.setProperty("history_row_key", row.row_key)
        notes_button.setEnabled(notes_enabled)
        if notes_tooltip:
            notes_button.setToolTip(notes_tooltip)
        notes_button.clicked.connect(lambda _checked=False, item=row: self._open_history_notes(item))
        table.setCellWidget(row_index, 6, notes_button)
        regenerate_button = self.QtWidgets.QPushButton("Regenerate")
        regenerate_button.setMaximumWidth(105)
        regenerate_button.setProperty("history_row_key", row.row_key)
        regenerate_button.setEnabled(bool(row.row_key) and row.deepseek_processing_status.strip().lower() != "processing")
        regenerate_button.setToolTip("Regenerate interview notes from saved data or rerun local DeepSeek first.")
        regenerate_button.clicked.connect(lambda _checked=False, item=row: self._retry_history_deepseek(item))
        table.setCellWidget(row_index, 7, regenerate_button)
        offer_button = self.QtWidgets.QPushButton(row.offer_action)
        offer_button.setMaximumWidth(115)
        offer_button.setProperty("history_row_key", row.row_key)
        offer_button.setEnabled(bool(row.row_key))
        offer_button.clicked.connect(lambda _checked=False, item=row: self._open_history_offer(item))
        table.setCellWidget(row_index, 8, offer_button)
        delete_button = self.QtWidgets.QPushButton("Delete")
        delete_button.setMaximumWidth(80)
        delete_button.setProperty("history_row_key", row.row_key)
        delete_button.setEnabled(bool(row.row_key))
        delete_button.clicked.connect(lambda _checked=False, item=row: self._delete_history_row(item))
        table.setCellWidget(row_index, 9, delete_button)

    def _history_notes_action_state(self, row: PySideHistoryRow) -> tuple[str, bool, str]:
        status = row.deepseek_processing_status.strip().lower()
        if status == "processing":
            return "Processing", False, "DeepSeek is still processing interview notes."
        if row.notes_path and Path(row.notes_path).exists():
            warning = row.deepseek_processing_warning.strip()
            return "Open Notes", True, warning
        if status == "failed":
            warning = row.deepseek_processing_warning.strip() or "DeepSeek processing failed."
            return "Failed/Retry", True, warning
        return "Unavailable", False, "Interview notes file was not found."

    def _size_history_table_columns(self, table: Any) -> None:
        table.resizeColumnsToContents()
        minimums = {
            0: 115,
            1: 170,
            2: 190,
            3: 210,
            4: 75,
            5: 130,
            6: 90,
            7: 105,
            8: 110,
            9: 80,
        }
        for column, minimum in minimums.items():
            table.setColumnWidth(column, max(table.columnWidth(column), table.sizeHintForColumn(column), minimum))
        table.setColumnWidth(6, min(table.columnWidth(6), 105))
        table.setColumnWidth(7, min(table.columnWidth(7), 115))
        table.setColumnWidth(8, min(table.columnWidth(8), 125))
        table.setColumnWidth(9, min(table.columnWidth(9), 95))

    def _history_outcome_brush(self, outcome: str) -> Any:
        color = _history_outcome_color(outcome)
        if not color:
            return None
        return self.QtGui.QBrush(self.QtGui.QColor(color))

    def _setup_tab(self) -> Any:
        page, layout = self._page()
        header_row = self.QtWidgets.QHBoxLayout()
        for index, step in enumerate(self.model.setup_steps, start=1):
            header_row.addWidget(self._label(f"Step {index}: {step}", "SectionTitle"))
        layout.addLayout(header_row)

        body = self.QtWidgets.QHBoxLayout()
        candidate, candidate_layout = self._surface()
        candidate_layout.addWidget(self._label("Candidate", "SectionTitle"))
        form = self.QtWidgets.QFormLayout()
        form.addRow("Name", self.QtWidgets.QLineEdit())
        school = self.QtWidgets.QComboBox()
        school.addItems(self.model.school_options)
        form.addRow("School", school)
        role = self.QtWidgets.QComboBox()
        role.addItems(list(self.model.track_labels.values()))
        form.addRow("Role Track", role)
        candidate_layout.addLayout(form)
        body.addWidget(candidate, 1)

        ready, ready_layout = self._surface()
        ready_layout.addWidget(self._label("Readiness Check", "SectionTitle"))
        for check in self.model.readiness_checks:
            checkbox = self.QtWidgets.QCheckBox(check.label)
            checkbox.setChecked(check.ready)
            checkbox.setEnabled(False)
            ready_layout.addWidget(checkbox)
        intro = self.QtWidgets.QTextEdit()
        intro.setPlainText(compose_intro_script(self.model.school_options[0] if self.model.school_options else ""))
        intro.setReadOnly(True)
        ready_layout.addWidget(self._label("Intro Script"))
        ready_layout.addWidget(intro, 1)
        ready_layout.addWidget(self._primary_button("Begin Interview"))
        body.addWidget(ready, 1)
        layout.addLayout(body, 1)
        return page

    def _first_flow_item(self, *, kind: str | None = None) -> FlowQuestion | None:
        for flow in self.model.flows.values():
            for item in flow.items:
                if kind is None or item.kind == kind:
                    return item
        return None

    def _live_question_tab(self) -> Any:
        page = self.QtWidgets.QWidget()
        outer_layout = self.QtWidgets.QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = self.QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        content = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        footer = self.QtWidgets.QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 16)
        footer.setSpacing(10)
        outer_layout.addLayout(footer)
        self.live_question_layout = layout
        self.live_footer_layout = footer
        self._render_live_question_page()
        return page

    def _review_tab(self) -> Any:
        page, layout = self._scrollable_page()
        self.review_layout = layout
        self._render_review_page()
        return page

    def _clear_layout(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            if child_layout is not None:
                self._clear_layout(child_layout)

    def _track_key_for_label(self, label: str) -> str:
        for key, value in self.model.track_labels.items():
            if value == label:
                return key
        return next(iter(self.model.flows), "")

    def _current_flow(self) -> TrackFlow | None:
        return self.model.flows.get(self.session_track_key)

    def _begin_selected_interview(self) -> None:
        label = self.home_role_combo.currentText() if hasattr(self, "home_role_combo") else ""
        self.session_track_key = self._track_key_for_label(label)
        self.session_index = 0
        self.session_answers = {}
        candidate_name = self.home_candidate_input.text().strip() if hasattr(self, "home_candidate_input") else ""
        school = self.home_school_combo.currentText().strip() if hasattr(self, "home_school_combo") else ""
        draft_path = self._default_draft_path(candidate_name or "Candidate")
        self.session = PySideInterviewSession(model=self.model, draft_path=draft_path)
        self.session.start(candidate_name=candidate_name, school=school, track_key=self.session_track_key)
        self._start_pyside_interview_recording()
        self._render_live_question_page()
        self._render_review_page()
        self._render_offer_page()
        self.interview_tabs.setCurrentIndex(2)

    def _continue_latest_draft(self) -> None:
        draft_path = latest_pyside_draft_path()
        if draft_path is None:
            return
        try:
            self.session = PySideInterviewSession.load(model=self.model, draft_path=draft_path)
        except ValueError:
            return
        self.session_track_key = self.session.track_key
        self.session_index = self.session.current_index
        self.session_answers = dict(self.session.answers)
        self._render_live_question_page()
        self._render_review_page()
        self._render_offer_page()
        self.interview_tabs.setCurrentIndex(2 if self.session.active_question() is not None else 3)

    def _refresh_home_draft_panel(self) -> None:
        latest_draft = latest_pyside_draft_path()
        if latest_draft is not None and not Path(latest_draft).exists():
            latest_draft = None
        label = getattr(self, "home_draft_label", None)
        if label is not None:
            label.setText(f"Saved draft: {latest_draft.name}" if latest_draft else "No saved draft available.")
            label.setToolTip(str(latest_draft) if latest_draft else "")
        continue_button = getattr(self, "home_continue_button", None)
        if continue_button is not None:
            continue_button.setEnabled(latest_draft is not None)
        delete_button = getattr(self, "home_delete_draft_button", None)
        if delete_button is not None:
            delete_button.setEnabled(latest_draft is not None)

    def _delete_latest_draft(self) -> None:
        draft_path = latest_pyside_draft_path()
        if draft_path is None or not Path(draft_path).exists():
            self._refresh_home_draft_panel()
            return
        result = self.QtWidgets.QMessageBox.question(
            self.window,
            "Delete Saved Draft",
            f"Delete saved draft?\n\n{draft_path}",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if result != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            Path(draft_path).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Delete Saved Draft", f"Could not delete draft: {exc}")
            return
        if self.session is not None and Path(self.session.draft_path) == Path(draft_path):
            self.session = None
            self.session_index = 0
            self.session_answers = {}
        self._refresh_home_draft_panel()

    def _safe_base_name(self) -> str:
        raw = f"{self.session.candidate_name if self.session else 'Candidate'}_{date.today().isoformat()}"
        safe = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
        return safe or "Candidate"

    def _start_pyside_interview_recording(self) -> None:
        if self.session is None:
            return
        self.session.flow_time_marks = []
        self.session.flow_candidate_transcripts = {}
        self.session.flow_recordings = {}
        self.recording_warning = ""
        self.recording_started_monotonic = time.monotonic()
        self.recording_base_name = self._safe_base_name()
        self.recording_candidate_label = "CANDIDATE"
        try:
            from interview_audio_recorder import start_recording

            self.recording_session = start_recording(
                os_name="windows" if sys.platform.startswith("win") else "linux",
                output_dir=DEFAULT_BASE_DIR,
                base_name=self.recording_base_name,
                win_mic_device=DEFAULT_WINDOWS_MIC_DEVICE,
                win_sys_device=resolve_default_windows_system_device() if sys.platform.startswith("win") else None,
            )
        except (Exception, SystemExit) as exc:
            self.recording_session = None
            self.recording_warning = f"Recording unavailable: {exc}"
        self.session.save_draft()

    def _mark_flow_timestamp(self, flow_idx: int) -> None:
        if self.session is None or self.recording_started_monotonic is None:
            return
        elapsed = max(0.0, time.monotonic() - self.recording_started_monotonic)
        overwrite = self._overwrite_next_live_timestamp
        self._overwrite_next_live_timestamp = False
        self._mark_flow_timestamp_at(flow_idx, elapsed, overwrite=overwrite)

    def _mark_flow_timestamp_at(self, flow_idx: int, elapsed: float, *, overwrite: bool = False) -> None:
        if self.session is None:
            return
        marks = self.session.flow_time_marks
        if any(int(mark.get("flow_index", -1)) == flow_idx for mark in marks):
            if not overwrite:
                return
            marks[:] = [mark for mark in marks if int(mark.get("flow_index", -1)) != flow_idx]
        if marks and int(marks[-1].get("flow_index", -1)) == flow_idx and "end_t" not in marks[-1]:
            return
        marks.append({"flow_index": flow_idx, "t": elapsed})
        self.session.save_draft()

    def _close_flow_timestamp(self, flow_idx: int) -> float | None:
        if self.session is None or self.recording_started_monotonic is None:
            return None
        elapsed = max(0.0, time.monotonic() - self.recording_started_monotonic)
        for mark in reversed(self.session.flow_time_marks):
            if int(mark.get("flow_index", -1)) == flow_idx and "end_t" not in mark:
                mark["end_t"] = elapsed
                break
        self.session.save_draft()
        return elapsed

    def _stop_pyside_interview_recording(self) -> None:
        if self.session is None or self.recording_session is None:
            return
        try:
            result = self.recording_session.stop_and_transcribe(
                output_dir=DEFAULT_BASE_DIR,
                base_name=self.recording_base_name or self._safe_base_name(),
                language="en",
            )
            payload = {
                "flow_index": -1,
                "base_name": self.recording_base_name,
                "output_dir": str(DEFAULT_BASE_DIR),
                "mic_wav": str(result.mic_wav),
                "sys_wav": str(result.sys_wav),
                "transcript_txt": str(result.transcript_txt),
                "transcript_jsonl": str(result.transcript_jsonl),
                "candidate_label": self.recording_candidate_label,
            }
            self._apply_pyside_recording_result(payload)
        except Exception as exc:
            self.recording_warning = f"Recording/transcription failed: {exc}"
        finally:
            self.recording_session = None
            self.recording_base_name = ""

    def _apply_pyside_recording_result(self, recording_result: dict[str, Any]) -> None:
        if self.session is None:
            return
        jsonl_path = Path(str(recording_result.get("transcript_jsonl") or ""))
        segments = load_candidate_segments(jsonl_path, self.recording_candidate_label)
        windows = build_flow_time_windows(self.session.flow_time_marks)
        by_flow_index = map_segments_to_flow_indices(segments, windows)
        for flow_idx, candidate_transcript in by_flow_index.items():
            payload = dict(recording_result)
            payload["flow_index"] = flow_idx
            payload["candidate_transcript"] = candidate_transcript
            self.session.flow_recordings[flow_idx] = payload
            self.session.flow_candidate_transcripts[flow_idx] = candidate_transcript
        self.session.save_draft()

    def _active_question(self) -> FlowQuestion | None:
        if self.session is not None:
            return self.session.active_question()
        flow = self._current_flow()
        if flow is None or not flow.items:
            return None
        if self.session_index >= len(flow.items):
            return None
        return flow.items[self.session_index]

    def _save_and_next(self, *, finalize: bool = False, skip: bool = False) -> None:
        item = self._active_question()
        if item is None:
            self.interview_tabs.setCurrentIndex(3)
            return
        current_index = self.session.current_index if self.session is not None else self.session_index
        qualification: dict[str, Any] | None = None
        if item.kind == "qualification":
            qualification = self._collect_qualification_from_fields()
            if qualification is None:
                return
        score = ""
        if hasattr(self, "score_group"):
            checked = self.score_group.checkedButton()
            score = checked.text().split(" ", 1)[0] if checked is not None else ""
        if item.score_cards and not score and not skip:
            self._update_live_next_enabled(item)
            return
        boundary_elapsed = self._close_flow_timestamp(current_index)
        notes = self.live_notes.toPlainText() if hasattr(self, "live_notes") else ""
        quick_actions = [
            checkbox.text()
            for checkbox in getattr(self, "quick_action_checks", [])
            if checkbox.isChecked()
        ]
        if self.session is not None:
            if skip:
                self.session.skip_active_question(notes=notes, quick_actions=quick_actions)
            else:
                self.session.save_answer_and_advance(
                    notes=notes,
                    score=score,
                    quick_actions=quick_actions,
                    qualification=qualification,
                )
            self.session_index = self.session.current_index
            self.session_answers = dict(self.session.answers)
            if self.session.active_question() is not None and boundary_elapsed is not None:
                self._mark_flow_timestamp_at(self.session.current_index, boundary_elapsed)
        else:
            answer = {
                "kind": item.kind,
                "title": item.title,
                "score": score,
                "notes": notes,
            }
            if skip:
                answer["skipped"] = True
            if qualification is not None:
                answer["qualification"] = qualification
            self.session_answers[item.question_id] = answer
            self.session_index += 1
            if self._active_question() is not None and boundary_elapsed is not None:
                self._mark_flow_timestamp_at(self.session_index, boundary_elapsed)
        if self._active_question() is None:
            self._render_review_page()
            self._render_offer_page()
            self.interview_tabs.setCurrentIndex(3)
            if finalize:
                self._generate_interview_notes_from_session()
            return
        if finalize:
            self._render_review_page()
            self._render_offer_page()
            self.interview_tabs.setCurrentIndex(3)
            self._generate_interview_notes_from_session()
            return
        self._render_live_question_page()
        self._render_offer_page()

    def _finalize_from_live_question(self) -> None:
        self._save_and_next(finalize=True)

    def _skip_live_question(self) -> None:
        self._save_and_next(skip=True)

    def _go_back_live_question(self) -> None:
        if self.session is not None:
            self.session.go_back()
            self.session_index = self.session.current_index
            self.session_answers = dict(self.session.answers)
            self._overwrite_next_live_timestamp = True
        elif self.session_index > 0:
            self.session_index -= 1
            self._overwrite_next_live_timestamp = True
        self._render_live_question_page()
        self._render_review_page()
        self._render_offer_page()

    def _exit_live_interview(self) -> None:
        if self.session is not None:
            self.session.save_draft()
        self.interview_tabs.setCurrentIndex(0)

    def _render_live_question_page(self) -> None:
        layout = getattr(self, "live_question_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        item = self._active_question() or self._first_flow_item(kind="trait") or self._first_flow_item()
        if item is None:
            layout.addWidget(self._label("No configured interview questions."))
            self._render_live_footer(None)
            return
        current_index = self.session.current_index if self.session is not None else self.session_index
        self._mark_flow_timestamp(current_index)

        layout.addWidget(self._label(item.progress_label, "SectionTitle"))
        split = self.QtWidgets.QHBoxLayout()
        left, left_layout = self._surface()
        left_layout.addWidget(self._label(item.title, "SectionTitle"))
        left_layout.addWidget(self._label(item.prompt))
        if item.kind == "qualification":
            self._render_qualification_fields(left_layout)
        if item.followups:
            followups = self.QtWidgets.QListWidget()
            followups.addItems(item.followups)
            left_layout.addWidget(self._label("Follow-up prompts"))
            left_layout.addWidget(followups)
        notes = self.QtWidgets.QTextEdit()
        notes.setPlaceholderText("Type optional notes here...")
        self.live_notes = notes
        left_layout.addWidget(self._label("Manual Notes"))
        left_layout.addWidget(notes, 1)
        split.addWidget(left, 2)

        right, right_layout = self._surface()
        right_layout.addWidget(self._label("Score", "SectionTitle"))
        self.score_group = self.QtWidgets.QButtonGroup()
        for card in item.score_cards:
            row = self.QtWidgets.QWidget()
            row_layout = self.QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            radio = self.QtWidgets.QRadioButton(card.label)
            radio.toggled.connect(lambda _checked, question=item: self._update_live_next_enabled(question))
            option_text = self._label(card.description, "ScoreOptionText")
            row_layout.addWidget(radio, 0)
            row_layout.addWidget(option_text, 1)
            self.score_group.addButton(radio)
            right_layout.addWidget(row)
        if item.score_cards:
            self.score_group.buttonClicked.connect(lambda _button: self._update_live_next_enabled(item))
        if not item.score_cards:
            right_layout.addWidget(self._label("Non-scored question"))
        right_layout.addWidget(self._label("Quick Actions", "SectionTitle"))
        self.quick_action_checks = []
        for action in item.quick_actions:
            checkbox = self.QtWidgets.QCheckBox(action)
            self.quick_action_checks.append(checkbox)
            right_layout.addWidget(checkbox)
        right_layout.addStretch(1)
        split.addWidget(right, 1)
        layout.addLayout(split, 1)
        self._render_live_footer(item)
        self._restore_live_answer(item)
        self._update_live_next_enabled(item)

    def _render_live_footer(self, item: FlowQuestion | None) -> None:
        footer = getattr(self, "live_footer_layout", None)
        if footer is None:
            return
        self._clear_layout(footer)
        back_button = self.QtWidgets.QPushButton("Back")
        back_button.setProperty("pyside_live_footer_action", "back")
        back_button.setEnabled((self.session.current_index if self.session is not None else self.session_index) > 0)
        back_button.clicked.connect(self._go_back_live_question)
        footer.addWidget(back_button)
        skip_button = self.QtWidgets.QPushButton("Skip")
        skip_button.setProperty("pyside_live_footer_action", "skip")
        skip_button.setEnabled(item is not None)
        skip_button.clicked.connect(self._skip_live_question)
        footer.addWidget(skip_button)
        footer.addStretch(1)
        if item is None:
            exit_button = self.QtWidgets.QPushButton("Exit")
            exit_button.setProperty("pyside_live_footer_action", "exit")
            exit_button.clicked.connect(self._exit_live_interview)
            footer.addWidget(exit_button)
            return
        is_last = False
        if self.session is not None:
            is_last = self.session.current_index == len(self.session._workflow_items()) - 1
        action = self._primary_button("Finalize" if is_last else "Next")
        action.setProperty("pyside_live_footer_action", "finalize" if is_last else "next")
        self.live_next_button = action
        action.clicked.connect(
            (lambda _checked=False: self._finalize_from_live_question())
            if is_last
            else (lambda _checked=False: self._save_and_next())
        )
        footer.addWidget(action)
        exit_button = self.QtWidgets.QPushButton("Exit")
        exit_button.setProperty("pyside_live_footer_action", "exit")
        exit_button.clicked.connect(self._exit_live_interview)
        footer.addWidget(exit_button)

    def _restore_live_answer(self, item: FlowQuestion) -> None:
        answer = {}
        if self.session is not None:
            answer = self.session.answers.get(item.question_id, {})
        else:
            answer = self.session_answers.get(item.question_id, {})
        if not answer:
            return
        if hasattr(self, "live_notes"):
            self.live_notes.setPlainText(str(answer.get("notes", "") or ""))
        stored_score = str(answer.get("score", "") or "").strip()
        if stored_score and hasattr(self, "score_group"):
            for button in self.score_group.buttons():
                if button.text().split(" ", 1)[0] == stored_score:
                    button.setChecked(True)
                    break
        stored_actions = {str(action) for action in answer.get("quick_actions", []) or []}
        for checkbox in getattr(self, "quick_action_checks", []):
            checkbox.setChecked(checkbox.text() in stored_actions)

    def _update_live_next_enabled(self, item: FlowQuestion | None) -> None:
        action = getattr(self, "live_next_button", None)
        if action is None or item is None:
            return
        if not item.score_cards:
            action.setEnabled(True)
            return
        checked = self.score_group.checkedButton() if hasattr(self, "score_group") else None
        action.setEnabled(checked is not None)

    def _render_qualification_fields(self, layout: Any) -> None:
        stored = self.session.qualification if self.session is not None else {}
        fields, fields_layout = self._surface()
        fields_layout.addWidget(self._label("Education & Experience", "SectionTitle"))
        form = self.QtWidgets.QFormLayout()

        self.qualification_has_degree = self.QtWidgets.QComboBox()
        self.qualification_has_degree.addItems(["", "Yes", "No"])
        has_degree = stored.get("has_degree")
        self.qualification_has_degree.setCurrentText("Yes" if has_degree is True else "No" if has_degree is False else "")
        form.addRow("Has degree", self.qualification_has_degree)

        self.qualification_degree_type = self.QtWidgets.QComboBox()
        self.qualification_degree_type.addItems(["", *list(CANONICAL_DEGREE_TYPES)])
        self.qualification_degree_type.setCurrentText(str(stored.get("degree_type", "") or ""))
        form.addRow("Degree type", self.qualification_degree_type)

        self.qualification_degree_in_ece = self.QtWidgets.QCheckBox("Degree is in ECE")
        self.qualification_degree_in_ece.setChecked(bool(stored.get("degree_in_ece", False)))
        form.addRow("", self.qualification_degree_in_ece)

        self.qualification_ece_units = self.QtWidgets.QLineEdit()
        self.qualification_ece_units.setText(_optional_int_text(stored.get("ece_units_completed")))
        form.addRow("ECE units", self.qualification_ece_units)

        self.qualification_infant_toddler = self.QtWidgets.QCheckBox("Infant/toddler class completed")
        self.qualification_infant_toddler.setChecked(bool(stored.get("infant_toddler_class_completed", False)))
        form.addRow("", self.qualification_infant_toddler)

        self.qualification_total_units = self.QtWidgets.QLineEdit()
        self.qualification_total_units.setText(_optional_int_text(stored.get("total_units_completed")))
        form.addRow("Total units if no degree", self.qualification_total_units)

        self.qualification_years = self.QtWidgets.QLineEdit()
        self.qualification_years.setText(_optional_int_text(stored.get("years_experience")))
        form.addRow("Years experience", self.qualification_years)

        self.qualification_status_label = self._label("")
        fields_layout.addLayout(form)
        fields_layout.addWidget(self.qualification_status_label)
        layout.addWidget(fields)

    def _collect_qualification_from_fields(self) -> dict[str, Any] | None:
        has_degree = self.qualification_has_degree.currentText().strip().lower()
        ok, message, qualification = validate_candidate_qualification(
            has_degree,
            self.qualification_degree_type.currentText(),
            self.qualification_degree_in_ece.isChecked(),
            self.qualification_ece_units.text(),
            self.qualification_total_units.text(),
            self.qualification_infant_toddler.isChecked(),
            self.qualification_years.text(),
        )
        if not ok:
            self.qualification_status_label.setText(message)
            return None
        self.qualification_status_label.setText("")
        return qualification.to_dict()

    def _render_review_page(self) -> None:
        layout = getattr(self, "review_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        flow = self._current_flow()
        total = len(flow.items) if flow is not None else 0
        answers = self.session.answers if self.session is not None else self.session_answers
        answered = len(answers)
        summary, summary_layout = self._surface()
        summary_layout.addWidget(self._label("Interview Complete", "Title"))
        summary_layout.addWidget(self._label(f"Captured {answered} of {total} configured interview responses."))
        review = self.session.review_summary() if self.session is not None else None
        if review is not None:
            summary_layout.addWidget(
                self._label(
                    f"Overall Score: {review.percent_of_max}%\n"
                    f"Determination: {review.outcome}\n"
                    f"Recommended Next Action: {review.next_action}",
                    "SectionTitle",
                )
            )
            if review.missing_scores:
                missing = self.QtWidgets.QListWidget()
                missing.addItems(review.missing_scores)
                summary_layout.addWidget(self._label("Missing Scores"))
                summary_layout.addWidget(missing)
            if review.strongest_evidence:
                strengths = self.QtWidgets.QListWidget()
                strengths.addItems(review.strongest_evidence)
                summary_layout.addWidget(self._label("Strongest Evidence"))
                summary_layout.addWidget(strengths)
            if review.concerns:
                concerns = self.QtWidgets.QListWidget()
                concerns.addItems(review.concerns)
                summary_layout.addWidget(self._label("Concerns"))
                summary_layout.addWidget(concerns)
        if answers:
            answer_list = self.QtWidgets.QListWidget()
            for answer in answers.values():
                score = f" | score {answer['score']}" if answer.get("score") else ""
                answer_list.addItem(f"{answer['title']}{score}")
            summary_layout.addWidget(answer_list)
        else:
            summary_layout.addWidget(self._label("Overall score, determination, missing items, flagged answers, strongest evidence, and concerns appear here after interview completion."))
        actions = self.QtWidgets.QHBoxLayout()
        notes_button = self._primary_button("Finalize Interview")
        notes_button.clicked.connect(self._generate_interview_notes_from_session)
        actions.addWidget(notes_button)
        offer_button = self._primary_button("Generate Offer")
        offer_button.clicked.connect(self._open_session_offer)
        actions.addWidget(offer_button)
        home_button = self.QtWidgets.QPushButton("Home")
        home_button.clicked.connect(lambda: self.interview_tabs.setCurrentIndex(0))
        actions.addWidget(home_button)
        summary_layout.addLayout(actions)
        self.review_status_label = self._label("")
        summary_layout.addWidget(self.review_status_label)
        layout.addWidget(summary)
        layout.addStretch(1)

    def _default_draft_path(self, candidate_name: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in candidate_name).strip("_") or "Candidate"
        return DEFAULT_BASE_DIR / "pyside_drafts" / f"{safe_name}.json"

    def _generate_interview_notes_from_session(self) -> None:
        if self.session is None:
            self.review_status_label.setText("Start an interview before generating notes.")
            return
        if self._pyside_finalize_running:
            self.review_status_label.setText("Finalizing interview. Recording and notes are still processing.")
            return
        self._pyside_finalize_running = True
        self._show_pyside_finalize_progress("Preparing finalize")
        self.review_status_label.setText("Finalizing interview. Recording and notes are processing in the background.")
        results: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        session = self.session

        def _worker() -> None:
            try:
                self._report_pyside_finalize_progress("Stopping recording and transcribing")
                self._stop_pyside_interview_recording()
                self._report_pyside_finalize_progress("Building interview notes")
                result = session.finalize_interview(base_dir=DEFAULT_BASE_DIR, history_path=INTERVIEW_HISTORY_PATH)
                self._report_pyside_finalize_progress("Queueing DeepSeek processing")
                results.put({"ok": True, "result": result, "warning": self.recording_warning})
            except Exception as exc:  # noqa: BLE001
                results.put({"ok": False, "error": exc})

        threading.Thread(target=_worker, daemon=True).start()
        timer = self.QtCore.QTimer(self.window)
        timer.timeout.connect(lambda: self._poll_pyside_finalize_worker(results, timer))
        timer.start(0)

    def _poll_pyside_finalize_worker(self, results: queue.Queue[dict[str, Any]], timer: Any) -> None:
        self._refresh_pyside_finalize_progress()
        try:
            message = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        timer.deleteLater()
        self._pyside_finalize_running = False
        if not message.get("ok"):
            self.review_status_label.setText(f"Interview notes not generated: {message.get('error')}")
            self._report_pyside_finalize_progress("Interview notes not generated")
            self._refresh_pyside_finalize_progress()
            return
        result = message.get("result", {})
        output_path = result.get("out_path", "") if isinstance(result, dict) else ""
        warning_text = str(message.get("warning") or "").strip()
        warning = f" {warning_text}" if warning_text else ""
        self.review_status_label.setText(f"Interview finalized: {output_path}{warning}")
        self._reload_history_model()
        if isinstance(result, dict) and result.get("deepseek_progress_path"):
            self._watch_pyside_deepseek_finalize_progress(result.get("deepseek_progress_path"))
        else:
            self._report_pyside_finalize_progress("Interview finalized")
            self._refresh_pyside_finalize_progress()

    def _show_pyside_finalize_progress(self, step: str) -> None:
        normalized = str(step or "").strip() or "Preparing finalize"
        self._pyside_finalize_progress_step = normalized
        self._pyside_finalize_progress_tasks = build_finalize_progress_tasks(
            normalized,
            existing_tasks=getattr(self, "_pyside_finalize_progress_tasks", []),
            queued_steps=PYSIDE_FINALIZE_PROGRESS_TASKS,
        )
        if self.pyside_finalize_progress_dialog is not None:
            self._refresh_pyside_finalize_progress()
            return
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("Finalizing Interview")
        dialog.setModal(False)
        dialog.resize(620, 380)
        dialog.setMinimumSize(520, 320)
        dialog.setMaximumHeight(460)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(self._label("Finalizing Interview", "PySideFinalizeProgressTitle"))
        layout.addWidget(self._label("Tasks run in order. Processing continues if this window is closed.", "PySideFinalizeProgressHelp"))
        scroll = self.QtWidgets.QScrollArea()
        scroll.setObjectName("PySideFinalizeProgressScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        content = self.QtWidgets.QWidget()
        content_layout = self.QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(12, 10, 12, 10)
        label = self._label(
            format_finalize_progress_tasks(self._pyside_finalize_progress_tasks, fallback=normalized)
        )
        label.setObjectName("PySideFinalizeProgressLabel")
        label.setWordWrap(False)
        content_layout.addWidget(label)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        bar = self.QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        dialog.finished.connect(lambda _result=0: self._clear_pyside_finalize_progress_dialog())
        self.pyside_finalize_progress_dialog = dialog
        self.pyside_finalize_progress_label = label
        self.pyside_finalize_progress_bar = bar
        self._pyside_finalize_progress_queue = queue.Queue()
        refresh_timer = self.QtCore.QTimer(dialog)
        refresh_timer.timeout.connect(self._refresh_pyside_finalize_progress)
        self._pyside_finalize_progress_refresh_timer = refresh_timer
        refresh_timer.start(500)
        dialog.show()

    def _clear_pyside_finalize_progress_dialog(self) -> None:
        timer = self._pyside_deepseek_progress_timer
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._pyside_deepseek_progress_timer = None
        refresh_timer = self._pyside_finalize_progress_refresh_timer
        if refresh_timer is not None:
            refresh_timer.stop()
            refresh_timer.deleteLater()
            self._pyside_finalize_progress_refresh_timer = None
        if self.pyside_finalize_progress_bar is not None:
            self.pyside_finalize_progress_bar = None
        self.pyside_finalize_progress_dialog = None
        self.pyside_finalize_progress_label = None
        self._pyside_finalize_progress_queue = None

    def _close_pyside_finalize_progress(self) -> None:
        dialog = self.pyside_finalize_progress_dialog
        if dialog is not None:
            dialog.close()
        self._clear_pyside_finalize_progress_dialog()
        self._pyside_finalize_progress_queue = None
        self.pyside_finalize_deepseek_progress_path = None

    def _report_pyside_finalize_progress(self, step: str) -> None:
        normalized = str(step or "").strip()
        if not normalized:
            return
        self._pyside_finalize_progress_tasks = build_finalize_progress_tasks(
            normalized,
            existing_tasks=getattr(self, "_pyside_finalize_progress_tasks", []),
            queued_steps=PYSIDE_FINALIZE_PROGRESS_TASKS,
        )
        progress_queue = self._pyside_finalize_progress_queue
        if progress_queue is None:
            self._pyside_finalize_progress_step = normalized
            return
        try:
            progress_queue.put_nowait(normalized)
        except queue.Full:
            self._pyside_finalize_progress_step = normalized

    def _refresh_pyside_finalize_progress(self) -> None:
        progress_queue = self._pyside_finalize_progress_queue
        if progress_queue is not None:
            while True:
                try:
                    self._pyside_finalize_progress_step = progress_queue.get_nowait()
                    self._pyside_finalize_progress_tasks = build_finalize_progress_tasks(
                        self._pyside_finalize_progress_step,
                        existing_tasks=getattr(self, "_pyside_finalize_progress_tasks", []),
                        queued_steps=PYSIDE_FINALIZE_PROGRESS_TASKS,
                    )
                except queue.Empty:
                    break
        deepseek_step, _status = self._read_pyside_deepseek_progress_step()
        if deepseek_step:
            self._pyside_finalize_progress_step = deepseek_step
            self._pyside_finalize_progress_tasks = self._read_pyside_deepseek_progress_tasks()
        label = self.pyside_finalize_progress_label
        if label is not None and self._pyside_finalize_progress_step:
            label.setText(
                format_finalize_progress_tasks(
                    getattr(self, "_pyside_finalize_progress_tasks", []),
                    fallback=self._pyside_finalize_progress_step,
                )
            )

    def _read_pyside_deepseek_progress_step(self) -> tuple[str, str]:
        path = self.pyside_finalize_deepseek_progress_path
        if path is None or not path.exists():
            return "", ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "", ""
        if not isinstance(payload, dict):
            return "", ""
        return str(payload.get("step") or "").strip(), str(payload.get("status") or "").strip().lower()

    def _read_pyside_deepseek_progress_tasks(self) -> list[dict[str, str]]:
        path = self.pyside_finalize_deepseek_progress_path
        if path is None or not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        if not isinstance(payload.get("tasks"), list):
            return build_finalize_progress_tasks(payload.get("step"), payload.get("status"))
        return build_finalize_progress_tasks(
            payload.get("step"),
            payload.get("status"),
            existing_tasks=payload.get("tasks"),
        )

    def _watch_pyside_deepseek_finalize_progress(self, progress_path: str | Path | None) -> None:
        if progress_path:
            self.pyside_finalize_deepseek_progress_path = Path(progress_path)
        step, status = self._read_pyside_deepseek_progress_step()
        if step:
            self._pyside_finalize_progress_step = step
            self._refresh_pyside_finalize_progress()
        if status in {"complete", "failed"}:
            timer = self._pyside_deepseek_progress_timer
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                self._pyside_deepseek_progress_timer = None
            self._reload_history_model()
            return
        if self.pyside_finalize_progress_dialog is None:
            return
        if self._pyside_deepseek_progress_timer is None:
            timer = self.QtCore.QTimer(self.window)
            timer.timeout.connect(lambda: self._watch_pyside_deepseek_finalize_progress(self.pyside_finalize_deepseek_progress_path))
            self._pyside_deepseek_progress_timer = timer
            timer.start(1000)

    def _history_offer_defaults(self, row: PySideHistoryRow) -> dict[str, str]:
        return {
            "candidate": row.candidate,
            "school": row.school,
            "position": row.position,
            "determination": row.status,
            "next_action": row.offer_action,
            "employment_type": "Full-time",
            "start_date": "",
            "start_time": "08:00 AM",
            "end_time": "05:00 PM",
            "hourly_pay": "",
            "hours_week": "40",
            "template_path": "",
            "output_dir": str(DEFAULT_BASE_DIR / "offers"),
        }

    def _open_history_offer(self, row: PySideHistoryRow) -> None:
        if not row.row_key:
            return
        self.selected_history_offer_row = row
        self._render_offer_page()
        self.sidebar.setCurrentRow(2)
        self.stack.setCurrentIndex(2)

    def _open_session_offer(self) -> None:
        self.selected_history_offer_row = None
        self._render_offer_page()
        self.sidebar.setCurrentRow(2)
        self.stack.setCurrentIndex(2)

    def _reload_history_model(self) -> None:
        history_rows = _build_pyside_history_rows(self.model.history_path)
        self.model = replace(
            self.model,
            home=replace(
                self.model.home,
                history_rows=history_rows,
                recent_interviews=_recent_interviews_from_history_rows(history_rows),
            ),
        )
        self._refresh_all_history_tables()

    def _deepseek_retry_job_path_for_row(self, row: PySideHistoryRow) -> Path | None:
        return resolve_deepseek_regeneration_job_path(
            {
                "history_id": row.row_key,
                "candidate_name": row.candidate,
                "interview_date": row.interview_date,
                "school": row.school,
                "track": row.position,
                "interview_notes_path": row.notes_path,
            },
            history_path=self.model.history_path,
            base_dir=self.model.history_path.parent / "interviews"
            if self.model.history_path.parent.name == "user_artifacts"
            else DEFAULT_BASE_DIR,
        )

    def _retry_history_deepseek(self, row: PySideHistoryRow) -> None:
        mode = self._choose_pyside_notes_regeneration_mode(row)
        if mode is None:
            return
        job_path = self._deepseek_retry_job_path_for_row(row)
        if job_path is None or not job_path.exists():
            self.QtWidgets.QMessageBox.warning(self.window, "DeepSeek Retry", "DeepSeek job file was not found.")
            return
        try:
            progress_path = regenerate_interview_notes_job(job_path, mode=mode)
        except (OSError, ValueError) as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "DeepSeek Retry", f"Could not retry DeepSeek processing: {exc}")
            return
        self._reload_history_model()
        label = "Regenerating interview notes document"
        if mode == "full":
            label = "Regenerating local DeepSeek output and interview notes document"
        self._show_pyside_finalize_progress(label)
        self._watch_pyside_deepseek_finalize_progress(progress_path)

    def _choose_pyside_notes_regeneration_mode(self, row: PySideHistoryRow) -> str | None:
        dialog = self.QtWidgets.QMessageBox(self.window)
        dialog.setWindowTitle("Regenerate Notes")
        dialog.setText(f"Regenerate interview notes for {row.candidate or 'this interview'}?")
        dialog.setInformativeText(
            "Choose full DeepSeek rerun when prompts changed.\n"
            "Choose document-only when layout or document formatting changed."
        )
        full_button = dialog.addButton("Full DeepSeek + Document", self.QtWidgets.QMessageBox.AcceptRole)
        document_button = dialog.addButton("Document Only", self.QtWidgets.QMessageBox.ActionRole)
        dialog.addButton(self.QtWidgets.QMessageBox.Cancel)
        dialog.setDefaultButton(full_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == full_button:
            return "full"
        if clicked == document_button:
            return "document_only"
        return None

    def _open_history_notes(self, row: PySideHistoryRow) -> None:
        path = Path(row.notes_path)
        if not path.exists():
            if row.deepseek_processing_status.strip().lower() == "failed":
                self._retry_history_deepseek(row)
                return
            self.QtWidgets.QMessageBox.warning(self.window, "Interview Notes", "Interview notes file was not found.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=True)
            else:
                subprocess.run(["xdg-open", str(path)], check=True)
        except OSError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Interview Notes", f"Could not open interview notes: {exc}")

    def _delete_history_row(self, row: PySideHistoryRow) -> None:
        if not row.row_key:
            return
        result = self.QtWidgets.QMessageBox.question(
            self.window,
            "Delete History Entry",
            f"Delete history entry for {row.candidate}?\n\nThis removes the row from interview history.",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if result != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if not self.history_store.delete_row(row.row_key):
            self.QtWidgets.QMessageBox.warning(self.window, "Delete History Entry", "History entry was not found.")
            return
        self._reload_history_model()

    def _offer_page(self) -> Any:
        page, layout = self._page()
        self.offer_page_layout = layout
        self._render_offer_page()
        return page

    def _render_offer_page(self) -> None:
        layout = getattr(self, "offer_page_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        layout.addWidget(self._label("Generate Offer", "Title"))
        frame, frame_layout = self._surface()
        frame_layout.addWidget(self._label("Offer Review Wizard", "SectionTitle"))
        if self.selected_history_offer_row is not None:
            defaults = self._history_offer_defaults(self.selected_history_offer_row)
        elif self.session is not None:
            defaults = self.session.offer_review_defaults()
        else:
            defaults = {}
        form = self.QtWidgets.QFormLayout()
        fields = [
            ("Candidate", "candidate"),
            ("School", "school"),
            ("Position", "position"),
            ("Determination", "determination"),
            ("Employment type", "employment_type"),
            ("Start date", "start_date"),
            ("Start time", "start_time"),
            ("End time", "end_time"),
            ("Hourly pay", "hourly_pay"),
            ("Hours/week", "hours_week"),
            ("Template path", "template_path"),
            ("Output folder", "output_dir"),
        ]
        self.offer_fields = {}
        for label, key in fields:
            field = self.QtWidgets.QLineEdit()
            field.setText(str(defaults.get(key, "")))
            self.offer_fields[key] = field
            form.addRow(label, field)
        frame_layout.addLayout(form)
        status = defaults.get("next_action") or "Complete interview review before generating offer."
        self.offer_status_label = self._label(f"Next action: {status}")
        frame_layout.addWidget(self.offer_status_label)
        generate = self._primary_button("Generate Offer")
        generate.clicked.connect(self._generate_offer_from_fields)
        frame_layout.addWidget(generate)
        layout.addWidget(frame)
        layout.addStretch(1)

    def _render_offer_document_from_fields(self) -> Path:
        candidate_name = self.offer_fields["candidate"].text().strip()
        first_name, last_name = _split_candidate_name(candidate_name)
        output_dir = Path(self.offer_fields["output_dir"].text().strip())
        created_on = date.today()
        output_path = output_dir / build_offer_filename(first_name, last_name, created_on)
        data = OfferInput(
            first_name=first_name,
            last_name=last_name,
            city=self.offer_fields["school"].text().strip(),
            position=self.offer_fields["position"].text().strip(),
            start_date=_parse_iso_or_us_date(self.offer_fields["start_date"].text().strip()),
            start_time_12h=self.offer_fields["start_time"].text().strip(),
            end_time_12h=self.offer_fields["end_time"].text().strip(),
            hourly_pay=float(self.offer_fields["hourly_pay"].text().strip()),
            hours=int(self.offer_fields["hours_week"].text().strip()),
            created_on=created_on,
        )
        return OfferLetterService.render_offer(Path(self.offer_fields["template_path"].text().strip()), output_path, data)

    def _generate_offer_from_fields(self) -> None:
        if self.session is None and self.selected_history_offer_row is None:
            self.offer_status_label.setText("Complete interview review before generating offer.")
            return
        try:
            if self.selected_history_offer_row is not None:
                output_path = self._render_offer_document_from_fields()
                updated = self.history_store.update_offer_state(
                    self.selected_history_offer_row.row_key,
                    "generated",
                    str(output_path),
                )
                if not updated:
                    raise ValueError("History row could not be updated.")
                self._reload_history_model()
            else:
                output_path = self.session.generate_offer_document(
                    template_path=Path(self.offer_fields["template_path"].text().strip()),
                    output_dir=Path(self.offer_fields["output_dir"].text().strip()),
                    start_date=_parse_iso_or_us_date(self.offer_fields["start_date"].text().strip()),
                    start_time_12h=self.offer_fields["start_time"].text().strip(),
                    end_time_12h=self.offer_fields["end_time"].text().strip(),
                    hourly_pay=float(self.offer_fields["hourly_pay"].text().strip()),
                    hours=int(self.offer_fields["hours_week"].text().strip()),
                    created_on=date.today(),
                )
        except Exception as exc:
            self.offer_status_label.setText(f"Offer not generated: {exc}")
            return
        self.offer_status_label.setText(f"Offer generated: {output_path}")

    def _admin_page(self) -> Any:
        page, layout = self._page()
        layout.addWidget(self._label("Admin Studio", "Title"))
        self.admin_studio = AdminStudio.load(self._admin_studio_paths())
        self.admin_draft = self.admin_studio.create_draft()
        self.admin_edit_mode = False
        self._admin_tables: dict[str, Any] = {}
        self._admin_table_editable_columns: dict[str, set[int]] = {}

        toolbar = self.QtWidgets.QHBoxLayout()
        self.admin_status_label = self._label("", "AdminStudioStatus")
        toolbar.addWidget(self.admin_status_label, 1)
        self.admin_edit_button = self.QtWidgets.QPushButton("Edit")
        self.admin_edit_button.setObjectName("AdminStudioEditButton")
        self.admin_edit_button.clicked.connect(lambda: self._set_admin_editing_enabled(True))
        self.admin_review_button = self._primary_button("Review changes")
        self.admin_review_button.setObjectName("AdminStudioReviewButton")
        self.admin_review_button.clicked.connect(self._review_admin_changes)
        self.admin_discard_button = self.QtWidgets.QPushButton("Discard")
        self.admin_discard_button.setObjectName("AdminStudioDiscardButton")
        self.admin_discard_button.clicked.connect(self._discard_admin_changes)
        toolbar.addWidget(self.admin_edit_button)
        toolbar.addWidget(self.admin_review_button)
        toolbar.addWidget(self.admin_discard_button)
        layout.addLayout(toolbar)

        workspace = self.QtWidgets.QSplitter()
        workspace.setObjectName("AdminStudioWorkspace")
        self.admin_section_list = self.QtWidgets.QListWidget()
        self.admin_section_list.setObjectName("AdminStudioSectionList")
        self.admin_section_list.setFixedWidth(230)
        self.admin_stack = self.QtWidgets.QStackedWidget()
        self.admin_stack.setObjectName("AdminStudioEditorStack")
        for section in self.admin_studio.summary(self.admin_draft).sections:
            self.admin_section_list.addItem(section.title)
            self.admin_stack.addWidget(self._admin_section_page(section.key, section.title, section.description))
        self.admin_section_list.currentRowChanged.connect(self.admin_stack.setCurrentIndex)
        workspace.addWidget(self.admin_section_list)
        workspace.addWidget(self.admin_stack)
        workspace.setStretchFactor(1, 1)
        layout.addWidget(workspace, 1)
        self.admin_section_list.setCurrentRow(0)
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()
        return page

    def _admin_studio_paths(self) -> AdminStudioPaths:
        return AdminStudioPaths(
            rubric_path=DEFAULT_RUBRIC_PATH,
            overrides_path=QUESTIONS_OVERRIDE_PATH,
            school_settings_path=SCHOOL_OFFER_SETTINGS_PATH,
            prompts_path=DEEPSEEK_PROMPTS_CONFIG_PATH,
        )

    def _admin_section_page(self, key: str, title: str, description: str) -> Any:
        tab, tab_layout = self._page()
        tab_layout.addWidget(self._label(title, "SectionTitle"))
        tab_layout.addWidget(self._label(description))
        if key == "questions":
            table = self._admin_questions_table()
            tab_layout.addWidget(table, 1)
            return tab
        if key == "rubrics":
            table = self._admin_rubrics_table()
            tab_layout.addWidget(table, 1)
            return tab
        if key == "templates":
            table = self._admin_school_settings_table()
            tab_layout.addWidget(table, 1)
            return tab
        if key == "prompts":
            table = self._admin_prompts_table()
            tab_layout.addWidget(table, 1)
            return tab
        rows = self._admin_readonly_rows(key)
        table = self._admin_table(key, ["Key", "Value"], rows, set())
        tab_layout.addWidget(table, 1)
        return tab

    def _admin_questions_table(self) -> Any:
        rows: list[list[str]] = []
        for track_key, flow in self.model.flows.items():
            for item in flow.items:
                rows.append([track_key, flow.label, item.question_id, item.kind, item.prompt])
        return self._admin_table("questions", ["Track Key", "Track", "Question ID", "Type", "Question"], rows, {4})

    def _admin_rubrics_table(self) -> Any:
        rows: list[list[str]] = []
        for trait in self.admin_draft.rubric.get("traits", []) or []:
            if not isinstance(trait, dict):
                continue
            rows.append(
                [
                    str(trait.get("id", "")),
                    str(trait.get("name", "")),
                    str(trait.get("priority", "")),
                    str(trait.get("weight", "")),
                    str(trait.get("primary_question", "")),
                ]
            )
        return self._admin_table("rubrics", ["Trait ID", "Name", "Priority", "Weight", "Question"], rows, {1, 2, 3, 4})

    def _admin_school_settings_table(self) -> Any:
        rows: list[list[str]] = []
        settings = default_school_offer_settings()
        for school, cfg in self.admin_draft.school_settings.items():
            settings.setdefault(str(school), {})
            settings[str(school)].update(cfg)
        for school, cfg in sorted(settings.items()):
            rows.append([school, str(cfg.get("interview_notes_dir", "")).strip()])
        table = self._admin_table("school_folders", ["School", "Interview notes folder"], rows, {1})
        table.setObjectName("PySideSchoolFolderSettingsTable")
        self.school_folder_settings_table = table
        return table

    def _admin_prompts_table(self) -> Any:
        rows = [[str(key), str(value)] for key, value in sorted(self.admin_draft.prompts.items()) if isinstance(value, str)]
        return self._admin_table("prompts", ["Prompt Key", "Template"], rows, {1})

    def _admin_readonly_rows(self, key: str) -> list[list[str]]:
        if key == "signals":
            return [[str(trait.get("id", "")), str(trait.get("name", ""))] for trait in self.admin_draft.rubric.get("traits", []) if isinstance(trait, dict)]
        if key == "advanced":
            return [
                ["rubric.json", str(DEFAULT_RUBRIC_PATH)],
                ["question_overrides.json", str(QUESTIONS_OVERRIDE_PATH)],
                ["school_offer_settings.json", str(SCHOOL_OFFER_SETTINGS_PATH)],
                ["deepseek_prompts.json", str(DEEPSEEK_PROMPTS_CONFIG_PATH)],
            ]
        return []

    def _admin_table(self, key: str, headers: list[str], rows: list[list[str]], editable_columns: set[int]) -> Any:
        table = self.QtWidgets.QTableWidget(len(rows), len(headers))
        table.setObjectName(f"AdminStudio{''.join(part.title() for part in key.split('_'))}Table")
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(True)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        self._admin_tables[key] = table
        self._admin_table_editable_columns[key] = set(editable_columns)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                table.setItem(row_index, column, self._admin_item(str(value), editable=False))
        return table

    def _admin_item(self, value: str, *, editable: bool) -> Any:
        item = self.QtWidgets.QTableWidgetItem(value)
        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, value)
        if editable:
            item.setFlags(item.flags() | self.QtCore.Qt.ItemFlag.ItemIsEditable)
        else:
            item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
        return item

    def _set_admin_editing_enabled(self, enabled: bool) -> None:
        self.admin_edit_mode = enabled
        triggers = (
            self.QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | self.QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
            | self.QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed
            if enabled
            else self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        for key, table in self._admin_tables.items():
            editable_columns = self._admin_table_editable_columns.get(key, set())
            table.setEditTriggers(triggers)
            for row_index in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row_index, column)
                    if item is None:
                        continue
                    if enabled and column in editable_columns:
                        item.setFlags(item.flags() | self.QtCore.Qt.ItemFlag.ItemIsEditable)
                    else:
                        item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
        self.admin_edit_button.setEnabled(not enabled)
        self.admin_review_button.setEnabled(enabled)
        self.admin_discard_button.setEnabled(enabled)
        self._sync_admin_status()

    def _sync_admin_status(self) -> None:
        summary = self.admin_studio.summary(self.admin_draft)
        status = f"Tracks: {summary.track_count}    Questions: {summary.question_count}    Unsaved files: {summary.dirty_count}"
        if summary.validation_errors:
            status = f"{status}    Validation: blocked"
        else:
            status = f"{status}    Validation: ready"
        self.admin_status_label.setText(status)

    def _capture_admin_table_edits(self) -> None:
        questions = self._admin_tables.get("questions")
        if questions is not None:
            for row_index in range(questions.rowCount()):
                question_item = questions.item(row_index, 4)
                if question_item.text() == question_item.data(self.QtCore.Qt.ItemDataRole.UserRole):
                    continue
                self.admin_draft.update_question_text(
                    questions.item(row_index, 0).text().strip(),
                    questions.item(row_index, 3).text().strip(),
                    questions.item(row_index, 2).text().strip(),
                    question_item.text().strip(),
                )
        rubrics = self._admin_tables.get("rubrics")
        if rubrics is not None:
            baseline_traits = {
                str(trait.get("id")): trait
                for trait in self.admin_draft.rubric.get("traits", []) or []
                if isinstance(trait, dict)
            }
            for row_index in range(rubrics.rowCount()):
                trait_id = rubrics.item(row_index, 0).text().strip()
                updates = {
                    "name": rubrics.item(row_index, 1).text().strip(),
                    "priority": rubrics.item(row_index, 2).text().strip(),
                    "weight": rubrics.item(row_index, 3).text().strip(),
                    "primary_question": rubrics.item(row_index, 4).text().strip(),
                }
                existing = baseline_traits.get(trait_id, {})
                if all(str(existing.get(key, "")).strip() == str(value).strip() for key, value in updates.items()):
                    continue
                self.admin_draft.update_trait(trait_id, updates)
        folders = self.school_folder_settings_table
        if folders is not None:
            for row_index in range(folders.rowCount()):
                notes_item = folders.item(row_index, 1)
                if notes_item.text() == notes_item.data(self.QtCore.Qt.ItemDataRole.UserRole):
                    continue
                school = folders.item(row_index, 0).text().strip()
                notes_dir = notes_item.text().strip()
                self.admin_draft.update_school_settings(school, {"interview_notes_dir": notes_dir})
        prompts = self._admin_tables.get("prompts")
        if prompts is not None:
            for row_index in range(prompts.rowCount()):
                prompt_item = prompts.item(row_index, 1)
                if prompt_item.text() == prompt_item.data(self.QtCore.Qt.ItemDataRole.UserRole):
                    continue
                self.admin_draft.update_prompt(prompts.item(row_index, 0).text().strip(), prompt_item.text())
        self._sync_admin_status()

    def _review_admin_changes(self) -> None:
        try:
            self._capture_admin_table_edits()
        except Exception as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Admin Studio", f"Admin changes are invalid: {exc}")
            return
        validation_errors = self.admin_draft.validate()
        if validation_errors:
            self.QtWidgets.QMessageBox.warning(self.window, "Admin Studio", "\n".join(validation_errors))
            return
        summary = self.admin_draft.change_summary()
        if not summary.changed_files:
            self.admin_status_label.setText("No admin changes to apply.")
            return
        message = "Apply these admin changes?\n\n" + "\n".join(summary.lines[:12])
        result = self.QtWidgets.QMessageBox.question(
            self.window,
            "Review Admin Changes",
            message,
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if result != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        applied = self.admin_studio.apply_draft(self.admin_draft, confirm=True)
        if not applied.applied:
            self.QtWidgets.QMessageBox.warning(self.window, "Admin Studio", "\n".join(applied.validation_errors or ["Admin changes were not applied."]))
            return
        self.admin_draft = self.admin_studio.create_draft()
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()
        self.admin_status_label.setText("Admin changes applied.")

    def _discard_admin_changes(self) -> None:
        self.admin_draft = self.admin_draft.discard()
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()

    def _onboarding_page(self) -> Any:
        page, layout = self._page()
        layout.addWidget(self._label("Onboarding", "Title"))
        try:
            state = JsonStore(Path.cwd()).load()
            board = build_pyside_onboarding_board(
                employees=state.employees,
                scheduler_settings=state.scheduler_settings,
                today=date.today(),
            )
        except Exception:
            board = PySideOnboardingBoard(overdue=0, due_today=0, due_soon=0, next_task="", rows=[])

        metrics, metrics_layout = self._surface()
        metrics_layout.addWidget(self._label("Today", "SectionTitle"))
        metrics_layout.addWidget(
            self._label(
                f"Overdue: {board.overdue}    Due today: {board.due_today}    Due soon: {board.due_soon}\n"
                f"Next task: {board.next_task or 'None'}"
            )
        )
        layout.addWidget(metrics)

        tasks, tasks_layout = self._surface()
        tasks_layout.addWidget(self._label("Employee Checklist", "SectionTitle"))
        table = self.QtWidgets.QTableWidget(len(board.rows), 5)
        table.setHorizontalHeaderLabels(["Employee", "School", "Next Task", "Due", "Status"])
        for row_index, row in enumerate(board.rows):
            values = [row["employee"], row["school"], row["next_task"], row["next_due"], row["status"]]
            for column, value in enumerate(values):
                table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        tasks_layout.addWidget(table)
        layout.addWidget(tasks, 1)
        return page

    def _candidates_page(self) -> Any:
        page, layout = self._page()
        layout.addWidget(self._label("Candidates", "Title"))
        board = build_pyside_candidate_board(self.model.home.history_rows)
        summary, summary_layout = self._surface()
        summary_layout.addWidget(self._label(f"{board.total_candidates} candidates", "SectionTitle"))
        summary_layout.addWidget(self._label("Candidate list, interview notes, and hiring status."))
        layout.addWidget(summary)

        table_frame, table_layout = self._surface()
        table_layout.addWidget(self._label("Candidate List", "SectionTitle"))
        table = self._create_history_table("PySideCandidateHistoryGrid")
        self.candidate_history_table = table
        self._refresh_history_table(table, board.history_rows)
        table_layout.addWidget(table)
        layout.addWidget(table_frame, 1)
        return page

    def _placeholder_page(self, title: str, body: str) -> Any:
        page, layout = self._page()
        layout.addWidget(self._label(title, "Title"))
        frame, frame_layout = self._surface()
        frame_layout.addWidget(self._label(body))
        layout.addWidget(frame)
        layout.addStretch(1)
        return page


def launch_pyside_interview_app(model: InterviewRedesignModel | None = None) -> int:
    _QtCore, _QtGui, QtWidgets = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _apply_styles(app)
    window = PySideInterviewWindow(model or build_interview_redesign_model())
    window.show()
    return app.exec()


def main() -> int:
    return launch_pyside_interview_app()


if __name__ == "__main__":
    raise SystemExit(main())
