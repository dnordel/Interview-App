from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from docx import Document
from data_store import InterviewHistoryStore, QuestionOverridesStore, RubricLoader
from interview_runtime import FinalizeGateways, build_finalize_context, enqueue_deepseek_finalize_job
from onboarding_operations import JsonStore, build_dashboard_today_summary, filtered_tasks, task_status
from platform_services import (
    DEFAULT_RUBRIC_PATH,
    DEFAULT_SCHOOL_OPTIONS,
    DEFAULT_BASE_DIR,
    INTERVIEW_HISTORY_PATH,
    QUESTIONS_OVERRIDE_PATH,
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


@dataclass(frozen=True)
class RecentInterview:
    candidate: str
    school: str
    role: str
    score: str
    status: str
    next_action: str


@dataclass(frozen=True)
class HomeModel:
    primary_action: str
    continue_action: str
    admin_visible_on_home: bool
    recent_interviews: list[RecentInterview]


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


@dataclass(frozen=True)
class PySideAdminStudioModel:
    sections: list[str]
    track_count: int
    question_count: int
    advanced_json_hidden: bool
    validation_warnings: list[str]
    rows: dict[str, list[dict[str, str]]]


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

    def start(self, *, candidate_name: str, school: str, track_key: str) -> None:
        if track_key not in self.model.flows:
            raise ValueError(f"Unknown track: {track_key}")
        self.candidate_name = candidate_name.strip()
        self.school = school.strip()
        self.track_key = track_key
        self.current_index = 0
        self.answers = {}
        self.qualification = {}
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
        if question.kind == "qualification" or qualification is not None:
            normalized = _normalize_qualification_payload(qualification or {})
            self.qualification = normalized
            answer["qualification"] = normalized
        self.answers[question.question_id] = answer
        self.current_index += 1
        self.save_draft()

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
        transcript_metadata = {
            "transcript_complete": True,
            "transcript_completeness_status": "complete",
            "remaining_question_indices": [],
        }
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
            "transcript_complete": True,
            "transcript_completeness_status": "complete",
            "remaining_question_indices": [],
            "deepseek_job_path": str(deepseek_job_path) if deepseek_job_available else "",
            "deepseek_progress_path": str(deepseek_progress_path) if deepseek_job_available else "",
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
        return cls(
            model=model,
            draft_path=Path(draft_path),
            candidate_name=str(payload.get("candidate_name", "")).strip(),
            school=str(payload.get("school", "")).strip(),
            track_key=track_key,
            current_index=max(0, current_index),
            qualification=_normalize_qualification_payload(qualification),
            answers={str(key): value for key, value in answers.items() if isinstance(value, dict)},
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
        self.history_store = InterviewHistoryStore(Path(history_path))
        self.state = SimpleNamespace(
            candidate_name=session.candidate_name,
            track=session.track_key,
            trait_inputs=self._trait_inputs(),
            custom_inputs=self._custom_inputs(),
            flow_recordings={},
            flow_candidate_transcripts=self._flow_candidate_transcripts(),
            referral_packet={"transcript_path": "", "interview_notes_path": ""},
            communication_log=[],
            to_dict=self._state_payload,
        )

    def _rubric_with_question_overrides(self) -> dict[str, Any]:
        return dict(self.session.model.rubric)

    def _safe_attr(self, _name: str) -> Any:
        return None

    def _collect_transcription_health_warnings(self) -> list[str]:
        return []

    def _hydrate_state_from_session_store(self) -> None:
        return None

    def _serialize_flow_audio_recordings(self) -> list[dict[str, Any]]:
        return []

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
        for index, question in enumerate(self._workflow_items(), start=1):
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
        return None

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
        transcripts: dict[int, str] = {}
        for index, question in enumerate(self._workflow_items(), start=1):
            answer = self.session.answers.get(question.question_id, {})
            transcripts[index - 1] = str(answer.get("notes", "") or "")
        return transcripts

    def _workflow_items(self) -> list[FlowQuestion]:
        return self.session._workflow_items()


def _history_text(row: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _load_recent_interviews(history_path: Path, *, limit: int = 6) -> list[RecentInterview]:
    try:
        rows = json.loads(Path(history_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rows = []
    if not isinstance(rows, list):
        rows = []

    recent: list[RecentInterview] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        recent.append(
            RecentInterview(
                candidate=_history_text(row, "candidate_name", "candidate", "name", default="Unknown candidate"),
                school=_history_text(row, "school", default=""),
                role=_history_text(row, "role", "track", default=""),
                score=_history_text(row, "score", "percent_of_max", "overall_score", default=""),
                status=_history_text(row, "status", "interview_status", "outcome", default=""),
                next_action=_history_text(row, "next_action", "recommended_next_action", default="Review"),
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
        cards.append(ScoreCard(label=score, description=description.split(".")[0].strip()))
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
    raw_followups = trait.get("followups", []) or trait.get("follow_up_prompts", []) or []
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
            recent_interviews=_load_recent_interviews(Path(history_path)),
        ),
        flows=flows,
        rubric=loader.data,
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


def build_pyside_candidate_board(history_rows: list[dict[str, Any]]) -> PySideCandidateBoard:
    by_candidate: dict[str, dict[str, str]] = {}
    for row in history_rows:
        candidate = _history_text(row, "candidate_name", "candidate", "name", default="Unknown candidate")
        if candidate in by_candidate:
            continue
        by_candidate[candidate] = {
            "candidate": candidate,
            "school": _history_text(row, "school", default=""),
            "role": _history_text(row, "role", "track", default=""),
            "score": _history_text(row, "score", "percent_of_max", "overall_score", default=""),
            "status": _history_text(row, "status", "interview_status", "outcome", default=""),
            "next_action": _history_text(row, "next_action", "recommended_next_action", default="Review"),
        }
    return PySideCandidateBoard(
        total_candidates=len(by_candidate),
        rows=list(by_candidate.values()),
    )


def build_pyside_admin_studio_model(model: InterviewRedesignModel) -> PySideAdminStudioModel:
    sections = ["Role Tracks", "Questions", "Rubrics", "Signals", "Templates", "Storage", "Security"]
    question_rows: list[dict[str, str]] = []
    for flow in model.flows.values():
        for item in flow.items:
            question_rows.append(
                {
                    "track": flow.label,
                    "type": item.kind,
                    "id": item.question_id,
                    "title": item.title,
                }
            )
    rows = {
        "Role Tracks": [
            {"key": key, "label": label, "questions": str(len(model.flows.get(key, TrackFlow(key, label, [])).items))}
            for key, label in model.track_labels.items()
        ],
        "Questions": question_rows,
        "Rubrics": [{"key": "traits", "label": str(len(model.rubric.get("traits", []) or [])), "questions": ""}],
        "Signals": [{"key": "disqualifiers", "label": str(len(model.rubric.get("absolute_disqualifiers", []) or [])), "questions": ""}],
        "Templates": [{"key": "offer", "label": "Configure in Offer Wizard", "questions": ""}],
        "Storage": [{"key": "drafts", "label": str(DEFAULT_BASE_DIR / "pyside_drafts"), "questions": ""}],
        "Security": [{"key": "privacy", "label": "Candidate/interview records stay local", "questions": ""}],
    }
    return PySideAdminStudioModel(
        sections=sections,
        track_count=len(model.track_labels),
        question_count=len(question_rows),
        advanced_json_hidden=True,
        validation_warnings=[
            "Review validation warnings before saving rubric or scoring changes.",
            "Keep raw JSON import/export behind Advanced.",
            "Do not expose candidate notes or file paths in logs.",
        ],
        rows=rows,
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


class PySide6UnavailableError(RuntimeError):
    pass


def _import_qt() -> Any:
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError as exc:
        raise PySide6UnavailableError(
            "PySide6 is not installed. Install requirements, then launch this redesign."
        ) from exc
    return QtCore, QtWidgets


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
        """
    )


class PySideInterviewWindow:
    def __init__(self, model: InterviewRedesignModel) -> None:
        QtCore, QtWidgets = _import_qt()
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.model = model
        self.session_track_key = next(iter(model.flows), "")
        self.session_index = 0
        self.session_answers: dict[str, dict[str, Any]] = {}
        self.session: PySideInterviewSession | None = None
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
        row.addWidget(self._label("UI:"))
        tk_button = self.QtWidgets.QPushButton("Tk UI")
        tk_button.clicked.connect(lambda: self._switch_to_ui_mode("tk"))
        row.addWidget(tk_button)
        pyside_button = self.QtWidgets.QPushButton("PySide UI")
        pyside_button.setEnabled(False)
        row.addWidget(pyside_button)
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
        begin = self._primary_button("Begin Interview")
        begin.clicked.connect(self._begin_selected_interview)
        setup_layout.addWidget(begin)
        layout.addWidget(setup)

        draft, draft_layout = self._surface()
        draft_layout.addWidget(self._label(self.model.home.continue_action, "SectionTitle"))
        latest_draft = latest_pyside_draft_path()
        draft_layout.addWidget(self._label(str(latest_draft) if latest_draft else "Latest saved interview appears here when available."))
        continue_button = self.QtWidgets.QPushButton("Continue")
        continue_button.setEnabled(latest_draft is not None)
        continue_button.clicked.connect(lambda: self._continue_latest_draft())
        draft_layout.addWidget(continue_button)
        layout.addWidget(draft)

        recent, recent_layout = self._surface()
        recent_layout.addWidget(self._label("Recent Interviews", "SectionTitle"))
        table = self.QtWidgets.QTableWidget(len(self.model.home.recent_interviews), 6)
        table.setHorizontalHeaderLabels(["School", "Candidate", "Role", "Score", "Status", "Next Action"])
        for row_index, row in enumerate(self.model.home.recent_interviews):
            values = [row.school, row.candidate, row.role, row.score, row.status, row.next_action]
            for column, value in enumerate(values):
                table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        recent_layout.addWidget(table)
        layout.addWidget(recent, 1)
        return page

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
        page, layout = self._scrollable_page()
        self.live_question_layout = layout
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

    def _active_question(self) -> FlowQuestion | None:
        if self.session is not None:
            return self.session.active_question()
        flow = self._current_flow()
        if flow is None or not flow.items:
            return None
        if self.session_index >= len(flow.items):
            return None
        return flow.items[self.session_index]

    def _save_and_next(self) -> None:
        item = self._active_question()
        if item is None:
            self.interview_tabs.setCurrentIndex(3)
            return
        qualification: dict[str, Any] | None = None
        if item.kind == "qualification":
            qualification = self._collect_qualification_from_fields()
            if qualification is None:
                return
        score = ""
        if hasattr(self, "score_group"):
            checked = self.score_group.checkedButton()
            score = checked.text().split(" ", 1)[0] if checked is not None else ""
        notes = self.live_notes.toPlainText() if hasattr(self, "live_notes") else ""
        quick_actions = [
            checkbox.text()
            for checkbox in getattr(self, "quick_action_checks", [])
            if checkbox.isChecked()
        ]
        if self.session is not None:
            self.session.save_answer_and_advance(
                notes=notes,
                score=score,
                quick_actions=quick_actions,
                qualification=qualification,
            )
            self.session_index = self.session.current_index
            self.session_answers = dict(self.session.answers)
        else:
            answer = {
                "kind": item.kind,
                "title": item.title,
                "score": score,
                "notes": notes,
            }
            if qualification is not None:
                answer["qualification"] = qualification
            self.session_answers[item.question_id] = answer
            self.session_index += 1
        if self._active_question() is None:
            self._render_review_page()
            self._render_offer_page()
            self.interview_tabs.setCurrentIndex(3)
            return
        self._render_live_question_page()
        self._render_offer_page()

    def _render_live_question_page(self) -> None:
        layout = getattr(self, "live_question_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        item = self._active_question() or self._first_flow_item(kind="trait") or self._first_flow_item()
        if item is None:
            layout.addWidget(self._label("No configured interview questions."))
            return

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
            option_text = self._label(card.description, "ScoreOptionText")
            row_layout.addWidget(radio, 0)
            row_layout.addWidget(option_text, 1)
            self.score_group.addButton(radio)
            right_layout.addWidget(row)
        if not item.score_cards:
            right_layout.addWidget(self._label("Non-scored question"))
        right_layout.addWidget(self._label("Quick Actions", "SectionTitle"))
        self.quick_action_checks = []
        for action in item.quick_actions:
            checkbox = self.QtWidgets.QCheckBox(action)
            self.quick_action_checks.append(checkbox)
            right_layout.addWidget(checkbox)
        right_layout.addStretch(1)
        save_next = self._primary_button("Save & Next")
        save_next.clicked.connect(self._save_and_next)
        right_layout.addWidget(save_next)
        split.addWidget(right, 1)
        layout.addLayout(split, 1)

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
        actions.addWidget(self.QtWidgets.QPushButton("Generate Offer"))
        actions.addWidget(self.QtWidgets.QPushButton("Return Home"))
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
        try:
            result = self.session.finalize_interview(base_dir=DEFAULT_BASE_DIR, history_path=INTERVIEW_HISTORY_PATH)
            output_path = result["out_path"]
        except Exception as exc:
            self.review_status_label.setText(f"Interview notes not generated: {exc}")
            return
        self.review_status_label.setText(f"Interview finalized: {output_path}")

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
        defaults = self.session.offer_review_defaults() if self.session is not None else {}
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

    def _generate_offer_from_fields(self) -> None:
        if self.session is None:
            self.offer_status_label.setText("Complete interview review before generating offer.")
            return
        try:
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
        admin = build_pyside_admin_studio_model(self.model)
        summary, summary_layout = self._surface()
        summary_layout.addWidget(
            self._label(
                f"Tracks: {admin.track_count}    Questions: {admin.question_count}    Advanced JSON hidden: {'Yes' if admin.advanced_json_hidden else 'No'}"
            )
        )
        for warning in admin.validation_warnings:
            summary_layout.addWidget(self._label(warning))
        layout.addWidget(summary)
        tabs = self.QtWidgets.QTabWidget()
        for title in admin.sections:
            tab, tab_layout = self._page()
            tab_layout.addWidget(self._label(f"{title} editor", "SectionTitle"))
            tab_layout.addWidget(self._label("Advanced configuration is separated from live interview workflow."))
            rows = admin.rows.get(title, [])
            table = self.QtWidgets.QTableWidget(len(rows), 4)
            table.setHorizontalHeaderLabels(["Key", "Label", "Type", "Detail"])
            for row_index, row in enumerate(rows):
                values = [
                    row.get("key", row.get("id", "")),
                    row.get("label", row.get("title", "")),
                    row.get("type", ""),
                    row.get("questions", row.get("track", "")),
                ]
                for column, value in enumerate(values):
                    table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(value))
            table.horizontalHeader().setStretchLastSection(True)
            tab_layout.addWidget(table, 1)
            tabs.addTab(tab, title)
        layout.addWidget(tabs, 1)
        return page

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
        board = build_pyside_candidate_board(
            [
                {
                    "candidate_name": row.candidate,
                    "school": row.school,
                    "track": row.role,
                    "score": row.score,
                    "status": row.status,
                    "next_action": row.next_action,
                }
                for row in self.model.home.recent_interviews
            ]
        )
        summary, summary_layout = self._surface()
        summary_layout.addWidget(self._label(f"{board.total_candidates} candidates", "SectionTitle"))
        summary_layout.addWidget(self._label("Candidate list, interview notes, and hiring status."))
        layout.addWidget(summary)

        table_frame, table_layout = self._surface()
        table_layout.addWidget(self._label("Candidate List", "SectionTitle"))
        table = self.QtWidgets.QTableWidget(len(board.rows), 6)
        table.setHorizontalHeaderLabels(["Candidate", "School", "Role", "Score", "Status", "Next Action"])
        for row_index, row in enumerate(board.rows):
            values = [row["candidate"], row["school"], row["role"], row["score"], row["status"], row["next_action"]]
            for column, value in enumerate(values):
                table.setItem(row_index, column, self.QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
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
    QtCore, QtWidgets = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _apply_styles(app)
    window = PySideInterviewWindow(model or build_interview_redesign_model())
    window.show()
    return app.exec()


def main() -> int:
    return launch_pyside_interview_app()


if __name__ == "__main__":
    raise SystemExit(main())
