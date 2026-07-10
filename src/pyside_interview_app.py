from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from admin_studio import DEFAULT_DEEPSEEK_MODEL, DEEPSEEK_MODEL_CHOICES, AdminStudio, AdminStudioPaths
from docx import Document
from data_store import (
    InterviewHistoryStore,
    InterviewAppSettingsStore,
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
    IndeedTranscriptImportResult,
    InterviewSessionStore,
    build_finalize_progress_tasks,
    build_finalize_context,
    build_flow_time_windows,
    enqueue_deepseek_finalize_job,
    format_finalize_progress_tasks,
    map_indeed_transcript_to_questions,
    load_candidate_segments,
    map_segments_to_flow_indices,
    parse_indeed_transcript_text,
    regenerate_interview_notes_job,
    resolve_deepseek_regeneration_job_path,
    resolve_default_windows_system_device,
    resolve_runtime,
)
from notification_service import (
    EMAIL_ACCOUNT_SETTINGS_PATH,
    NOTIFICATION_RULES_PATH,
    NOTIFICATION_TEMPLATE_FIELDS,
    SUPPORTED_NOTIFICATION_EVENTS,
    load_email_account_settings,
    notification_service_from_onboarding,
    notification_service_from_email_account_settings,
    save_email_account_settings,
)
from onboarding_operations import EmailSettings, JsonStore, build_dashboard_today_summary, filtered_tasks, task_status, verify_email_connection
from platform_services import (
    CONFIG_DIR,
    DEFAULT_RUBRIC_PATH,
    DEFAULT_SCHOOL_OPTIONS,
    DEFAULT_BASE_DIR,
    INTERVIEW_HISTORY_PATH,
    INTERVIEW_APP_SETTINGS_PATH,
    QUESTIONS_OVERRIDE_PATH,
    SCHOOL_OFFER_SETTINGS_PATH,
    atomic_write_json,
    compose_intro_script,
)
from scoring_reporting import OfferInput, OfferLetterService, ScoringEngine, build_offer_filename
from scoring_reporting import build_integration_payload, serialize_integration_payload
from scoring_reporting import CANONICAL_DEGREE_TYPES, CandidateQualification, validate_candidate_qualification
from staffing_dashboard_v2 import StaffingDashboardV2Page, configure_v2_scroll_areas
from staffing_service import StaffingService
from staffing_store import StaffingEditLock, StaffingStore


APP_TITLE = "Interview Assistant"
NAVIGATION = ["Interviews", "Candidates", "Offers", "Staffing", "Staffing v2", "Onboarding", "Admin"]
DIRECTOR_STAFFING_NAVIGATION = ["Staffing v2"]
SETUP_STEPS = ["Candidate", "Interview Plan", "Ready"]
STAFFING_DB_PATH = DEFAULT_BASE_DIR / "staffing_dashboard.sqlite3"
STAFFING_REFERRAL_QUEUE_PATH = DEFAULT_BASE_DIR / "staffing_referrals.pending.jsonl"
STAFFING_SEED_PATH = CONFIG_DIR / "staffing_seed.json"
STAFFING_PERMIT_VALUES = [
    "unknown",
    "no_permit_or_application",
    "permit_in_process",
    "teacher_permit_approved",
    "no_units_needed",
]
STAFFING_STATUS_VALUES = ["dont_need_now", "need_now", "coming", "filled", "replace"]
STAFFING_POSITION_TYPES = ["Teacher", "Aide", "Assistant", "Director", "Float", "Chef", "Office", "Custodian"]
STAFFING_PROGRAM_VALUES = ["Preschool", "Infant", "Toddler", "School Age", "Support"]
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
    deepseek_job_path: str = ""
    deepseek_progress_path: str = ""
    candidate_email: str = ""
    offer_path: str = ""
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
    director_staffing_school: str = ""


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
    interview_date: str = ""
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
        self.interview_date = date.today().isoformat()
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

    def update_review_score(self, question_id: str, score: int | str | None) -> None:
        target_id = str(question_id).strip()
        flow_item = next(
            (item for item in self._workflow_items() if item.question_id == target_id),
            None,
        )
        if flow_item is None or flow_item.kind != "trait":
            raise ValueError("Review score updates require a scored interview question.")
        if score in (None, ""):
            score_text = ""
        else:
            try:
                score_int = int(score)
            except (TypeError, ValueError) as exc:
                raise ValueError("Review score must be blank or between 1 and 5.") from exc
            if score_int == 0:
                score_text = ""
            elif 1 <= score_int <= 5:
                score_text = str(score_int)
            else:
                raise ValueError("Review score must be blank or between 1 and 5.")
        answer = dict(self.answers.get(target_id, {}))
        answer.setdefault("kind", flow_item.kind)
        answer.setdefault("title", flow_item.title)
        answer.setdefault("prompt", flow_item.prompt)
        answer.setdefault("notes", "")
        answer.setdefault("quick_actions", [])
        answer["score"] = score_text
        if score_text:
            answer.pop("skipped", None)
            answer.pop("skip_reason", None)
        self.answers[target_id] = answer
        self.save_draft()

    def import_indeed_transcript_file(self, path: Path) -> IndeedTranscriptImportResult:
        source = Path(path)
        if source.suffix.lower() != ".txt":
            raise ValueError("Indeed transcript import accepts .txt files only.")
        text = source.read_text(encoding="utf-8")
        return self.import_indeed_transcript_text(text)

    def import_indeed_transcript_text(self, text: str) -> IndeedTranscriptImportResult:
        questions = [
            {"flow_index": index, "question_id": item.question_id, "prompt": item.prompt}
            for index, item in enumerate(self._workflow_items())
            if item.kind != "intro"
        ]
        result = map_indeed_transcript_to_questions(parse_indeed_transcript_text(text), questions)
        matched_ids = {match.question_id for match in result.matches}
        by_id = {item.question_id: (index, item) for index, item in enumerate(self._workflow_items())}
        for match in result.matches:
            _flow_index, item = by_id.get(match.question_id, (match.flow_index, None))
            if item is None:
                continue
            self.answers[item.question_id] = {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": match.candidate_transcript,
                "score": "",
                "quick_actions": [],
                "imported_transcript": True,
            }
            self.flow_candidate_transcripts[match.flow_index] = match.candidate_transcript
            self.flow_recordings[match.flow_index] = {
                "flow_index": match.flow_index,
                "candidate_transcript": match.candidate_transcript,
                "source": "indeed_transcript_import",
            }
        for question_id, (flow_index, item) in by_id.items():
            if item.kind == "intro" or question_id in matched_ids:
                continue
            self.answers[question_id] = {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": "",
                "score": "",
                "quick_actions": [],
                "skipped": True,
                "skip_reason": "not_found_in_indeed_transcript",
                "imported_transcript": True,
            }
            self.flow_candidate_transcripts.pop(flow_index, None)
            self.flow_recordings.pop(flow_index, None)
        first_unrated = next(
            (
                index
                for index, item in enumerate(self._workflow_items())
                if item.kind == "trait"
                and item.question_id in matched_ids
                and not str(self.answers.get(item.question_id, {}).get("score") or "").strip()
            ),
            None,
        )
        if first_unrated is not None:
            self.current_index = first_unrated
        else:
            self.current_index = len(self._workflow_items())
        self.save_draft()
        return result

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
            if answer.get("skipped"):
                trait_inputs[item.question_id] = {
                    "raw_score": None,
                    "question_notes": notes,
                    "trait_notes": notes,
                    "absolute_disqualifier": False,
                    "no_example_after_followups": False,
                    "skipped": True,
                    "skip_reason": str(answer.get("skip_reason") or "skipped"),
                }
                continue
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
            history_path=Path(output_dir) / "interview_history.sqlite3",
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
        history_id = active_gateways.persist_finalize_history(adapter, context, "")
        export_basic_report = getattr(active_gateways, "export_basic_report", None)
        out_path = (
            export_basic_report(adapter, context)
            if callable(export_basic_report)
            else active_gateways.export_report(adapter, context)
        )
        notes_path = adapter.state.referral_packet.get("interview_notes_path", "") or str(out_path)
        adapter.history_store.update_row(
            history_id,
            {
                "saved_report_path": str(out_path),
                "interview_notes_path": notes_path,
                "notes_path": notes_path,
                "report_path": str(out_path),
            },
        )
        integration_path = active_gateways.export_integration(adapter, context)
        director_packet, comm_log_path = active_gateways.send_referral(adapter, context, out_path, integration_path)
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
            "deepseek_job_path": "",
            "deepseek_progress_path": "",
            "history_id": history_id,
        }

    def _transcript_metadata(self) -> dict[str, Any]:
        total = len(self._workflow_items())
        missing: list[int] = []
        items = self._workflow_items()
        for flow_idx in range(total):
            if flow_idx < len(items) and self.answers.get(items[flow_idx].question_id, {}).get("skipped"):
                continue
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
            "interview_date": self.interview_date,
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
        self._save_interview_session_snapshots()

    def _save_interview_session_snapshots(self) -> None:
        if not self.candidate_name.strip() or not self.interview_date.strip() or not self.track_key.strip():
            return
        interview_id = self._interview_session_id()
        snapshots: list[tuple[int, FlowQuestion, dict[str, Any], str]] = []
        for flow_idx, item in enumerate(self._workflow_items()):
            answer = self.answers.get(item.question_id, {})
            transcript = str(self.flow_candidate_transcripts.get(flow_idx) or "").strip()
            if not transcript:
                transcript = str((self.flow_recordings.get(flow_idx) or {}).get("candidate_transcript") or "").strip()
            if not answer and not transcript:
                continue
            snapshots.append((flow_idx, item, answer, transcript))
        if not snapshots:
            return
        store = InterviewSessionStore(DEFAULT_BASE_DIR)
        for flow_idx, item, answer, transcript in snapshots:
            store.save_question_snapshot(
                interview_id=interview_id,
                candidate_name=self.candidate_name,
                interview_date=self.interview_date,
                flow_idx=flow_idx,
                item_type=item.kind,
                item_id=item.question_id,
                notes=self._session_snapshot_notes(item, answer),
                candidate_transcript=transcript,
            )

    def _interview_session_id(self) -> str:
        raw = f"Candidate_{self.candidate_name}_{self.interview_date}"
        safe = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
        return safe or "Candidate"

    def _session_snapshot_notes(self, item: FlowQuestion, answer: dict[str, Any]) -> dict[str, Any]:
        notes_text = str(answer.get("notes") or "")
        skipped = bool(answer.get("skipped", False))
        quick_actions = [str(action) for action in answer.get("quick_actions", []) or []]
        return {
            "question_text": str(answer.get("prompt") or item.prompt),
            "title": str(answer.get("title") or item.title),
            "answer": notes_text,
            "raw_score": None if skipped else _coerce_session_score(answer.get("score")),
            "question_notes": notes_text,
            "trait_notes": notes_text,
            "verbatim_notes": notes_text,
            "absolute_disqualifier": False if skipped else "Disqualifier observed" in quick_actions,
            "no_example_after_followups": False if skipped else "Candidate gave no example" in quick_actions,
            "skipped": skipped,
            "skip_reason": str(answer.get("skip_reason") or "") if skipped else "",
            "selected_signal_ids": [],
        }

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
            interview_date=str(payload.get("interview_date", "") or date.today().isoformat()).strip(),
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
            "deepseek_summary_model": DEFAULT_DEEPSEEK_MODEL,
            "deepseek_summary_timeout_seconds": 600,
            "deepseek_prompt_templates": {},
        }
        self.settings.update(InterviewAppSettingsStore(INTERVIEW_APP_SETTINGS_PATH).load())
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
                    "skipped": bool(answer.get("skipped")),
                    "skip_reason": str(answer.get("skip_reason") or "") if answer.get("skipped") else "",
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
                    "skipped": bool(answer.get("skipped")),
                    "skip_reason": str(answer.get("skip_reason") or "") if answer.get("skipped") else "",
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
        seen_scored_question = False
        for index, item in enumerate(_flow_tx):
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "trait":
                seen_scored_question = True
                item["candidate_transcript"] = by_flow_index.get(index, str(item.get("candidate_transcript") or ""))
                continue
            if seen_scored_question and item_type in {"custom", "qualification"}:
                manual_notes = str(item.get("answer") or item.get("evaluator_notes") or "").strip()
                item["candidate_transcript"] = manual_notes or by_flow_index.get(index, str(item.get("candidate_transcript") or ""))
                continue
            item["candidate_transcript"] = by_flow_index.get(index, str(item.get("candidate_transcript") or ""))

    def _rewrite_live_transcript_docx_from_flow(self, _flow_tx: list[dict[str, Any]]) -> None:
        return None

    def _state_payload(self) -> dict[str, Any]:
        track = self.session.model.flows.get(self.session.track_key)
        return {
            "candidate": {
                "name": self.session.candidate_name,
                "candidate_name": self.session.candidate_name,
                "interview_date": self.session.interview_date,
                "school": self.session.school,
                "track": self.session.track_key,
                "position": track.label if track is not None else self.session.track_key,
                "qualification": self._qualification_payload(),
            }
        }

    def _qualification_payload(self) -> dict[str, Any]:
        qualification = dict(self.session.qualification or {})
        if qualification:
            return qualification
        why_ece = self.session.answers.get("Why-ECE", {})
        if isinstance(why_ece, dict) and isinstance(why_ece.get("qualification"), dict):
            return dict(why_ece["qualification"])
        return {}

    def _trait_inputs(self) -> dict[str, dict[str, Any]]:
        inputs: dict[str, dict[str, Any]] = {}
        for question in self._workflow_items():
            if question.kind != "trait":
                continue
            answer = self.session.answers.get(question.question_id, {})
            quick_actions = [str(action) for action in answer.get("quick_actions", []) or []]
            notes = str(answer.get("notes", "") or "")
            skipped = bool(answer.get("skipped"))
            inputs[question.question_id] = {
                "raw_score": None if skipped else _coerce_session_score(answer.get("score")),
                "question_notes": notes,
                "trait_notes": notes,
                "verbatim_notes": notes,
                "absolute_disqualifier": False if skipped else "Disqualifier observed" in quick_actions,
                "no_example_after_followups": False if skipped else "Candidate gave no example" in quick_actions,
                "skipped": skipped,
                "skip_reason": str(answer.get("skip_reason") or "") if skipped else "",
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
                "skipped": bool(answer.get("skipped")),
                "skip_reason": str(answer.get("skip_reason") or "") if answer.get("skipped") else "",
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


def _director_referral_outcome(status: str) -> str:
    normalized = _normalize_history_search(status).replace("-", " ")
    if normalized == "hire":
        return "hire"
    if normalized == "borderline":
        return "borderline"
    return ""


def _director_referral_rating(score: str) -> float | None:
    text = str(score or "").strip().replace("%", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if 1 <= value <= 10:
        return value
    if 10 < value <= 100:
        return round(value / 10, 2)
    return None


def _staffing_school_slug(school: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(school or "").strip().lower()).strip("_")
    return slug


def staffing_db_path_for_school(school: str, *, base_path: Path | None = None) -> Path:
    resolved_base = Path(base_path or STAFFING_DB_PATH)
    slug = _staffing_school_slug(school)
    if not slug:
        return resolved_base
    return resolved_base.with_name(f"{resolved_base.stem}_{slug}{resolved_base.suffix}")


def _bootstrap_school_staffing_db_from_base(school: str, school_path: Path, *, base_path: Path | None = None) -> None:
    if not str(school or "").strip():
        return
    source = Path(base_path or STAFFING_DB_PATH)
    target = Path(school_path)
    if source == target or target.exists() or not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(f"{source.name}{suffix}")
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(f"{target.name}{suffix}"))


def _append_staffing_referral_queue(payload: dict[str, Any], *, queue_path: Path | None = None) -> None:
    target = Path(queue_path or STAFFING_REFERRAL_QUEUE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "operation": "director_candidate_referral",
        "payload": payload,
        "queued_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def _pop_staffing_referral_queue_for_school(school: str, *, queue_path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(queue_path or STAFFING_REFERRAL_QUEUE_PATH)
    if not target.exists():
        return []
    school_filter = str(school or "").strip()
    matched: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            remaining.append({"raw": line})
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if school_filter and str(payload.get("school", "")).strip() != school_filter:
            remaining.append(record)
            continue
        matched.append(payload)
    if remaining:
        with target.open("w", encoding="utf-8") as file:
            for record in remaining:
                raw = record.get("raw") if isinstance(record, dict) else None
                if raw is not None:
                    file.write(str(raw) + "\n")
                else:
                    file.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    else:
        target.unlink()
    return matched


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


def _notification_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _table_text(table: Any, row: int, column: int) -> str:
    item = table.item(row, column)
    return item.text().strip() if item is not None else ""


def _qualification_notification_payload(qualification: Any) -> dict[str, str]:
    payload = qualification.to_dict() if hasattr(qualification, "to_dict") else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "has_degree": _notification_text(payload.get("has_degree")),
        "degree_type": _notification_text(payload.get("degree_type")),
        "degree_in_ece": _notification_text(payload.get("degree_in_ece")),
        "ece_units_completed": _notification_text(payload.get("ece_units_completed")),
        "total_units_completed": _notification_text(payload.get("total_units_completed")),
        "infant_toddler_class_completed": _notification_text(payload.get("infant_toddler_class_completed")),
        "years_experience": _notification_text(payload.get("years_experience")),
    }


def _staffing_school_summary(rows: list[Any]) -> str:
    counts = {"filled": 0, "need_now": 0, "coming": 0, "replace": 0, "dont_need_now": 0}
    classrooms = set()
    for row in rows:
        counts[str(row.status)] = counts.get(str(row.status), 0) + 1
        classrooms.add(str(row.classroom))
    return (
        f"Classrooms: {len(classrooms)}    Filled: {counts['filled']}    Need Now: {counts['need_now']}    "
        f"Coming: {counts['coming']}    Replace: {counts['replace']}    Don't Need: {counts['dont_need_now']}"
    )


def _staffing_display_status(status: str) -> str:
    return {
        "dont_need_now": "Don't Need",
        "need_now": "Need Now",
        "coming": "Coming",
        "filled": "Filled",
        "replace": "Replace",
    }.get(str(status), str(status).replace("_", " ").title())


def _staffing_display_permit(status: str) -> str:
    return {
        "unknown": "Unknown",
        "no_permit_or_application": "No Permit or Application",
        "permit_in_process": "Permit in Process",
        "teacher_permit_approved": "Teacher Permit Approved",
        "no_units_needed": "Not Required",
    }.get(str(status), str(status).replace("_", " ").title())


def _staffing_days_open_text(opened_date: str) -> str:
    try:
        opened = datetime.fromisoformat(str(opened_date).replace("Z", "+00:00")).date()
    except ValueError:
        return "-"
    days = max(0, (date.today() - opened).days)
    return f"{days} days" if days != 1 else "1 day"


def _staffing_ratio_summary(rows: list[Any]) -> str:
    ratios = []
    for row in rows:
        ratio = str(getattr(row, "ratio_group", "") or "").strip()
        if ratio and ratio not in ratios:
            ratios.append(ratio)
    return "    ".join(ratios)


def _staffing_priority_status(rows: list[Any]) -> str:
    statuses = {str(row.status) for row in rows}
    if "need_now" in statuses:
        return "Need Now"
    if "replace" in statuses:
        return "Replace"
    if "coming" in statuses:
        return "Coming"
    if rows and all(str(row.status) == "filled" for row in rows):
        return "Low"
    return "Review"


def _staffing_classroom_list_status(rows: list[Any]) -> str:
    statuses = {str(getattr(row, "status", "") or "") for row in rows}
    for status in ("need_now", "replace", "coming", "dont_need_now", "filled"):
        if status in statuses:
            return status
    return ""


def _staffing_classroom_list_label(classroom: str, rows: list[Any]) -> str:
    counts = {"need_now": 0, "replace": 0, "filled": 0, "dont_need_now": 0}
    for row in rows:
        status = str(getattr(row, "status", "") or "")
        if status in counts:
            counts[status] += 1
    return (
        f"{classroom}\n"
        f"Need: {counts['need_now']} - Replace: {counts['replace']} - "
        f"Filled: {counts['filled']} - Don't Need: {counts['dont_need_now']}"
    )


def _staffing_classroom_list_color(rows: list[Any]) -> str:
    need_now = sum(1 for row in rows if str(getattr(row, "status", "") or "") == "need_now")
    replace = sum(1 for row in rows if str(getattr(row, "status", "") or "") == "replace")
    if need_now > 0:
        return "#FEF08A"
    if replace > 0:
        return "#FF0000"
    return "#BBF7D0"


def _staffing_status_color(status: str) -> str:
    return {
        "replace": "#FF0000",
        "need_now": "#FEF08A",
        "coming": "#A02B93",
        "filled": "#BBF7D0",
        "dont_need_now": "#5BC0DE",
    }.get(str(status), "#FFFFFF")


def _staffing_permit_color(status: str) -> str:
    return {
        "no_permit_or_application": "#DCFCE7",
        "permit_in_process": "#86EFAC",
        "teacher_permit_approved": "#22C55E",
        "no_units_needed": "#FCE7F3",
        "unknown": "#E5E7EB",
    }.get(str(status), "#FFFFFF")


def _staffing_school_tab_color(index: int) -> str:
    colors = ["#1d4ed8", "#047857", "#b45309", "#7c3aed", "#be123c", "#0f766e"]
    return colors[int(index) % len(colors)]


def _table_assignment_id(table: Any, row: int) -> int | None:
    if row < 0:
        return None
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is None:
            continue
        try:
            return int(item.data(0x0100))
        except (TypeError, ValueError):
            continue
    return None


def _staffing_slot_label(row: Any) -> str:
    slot_group = str(getattr(row, "slot_group", "") or "").strip()
    if slot_group:
        return slot_group.title()
    return str(getattr(row, "position_type", "") or "").strip()


def _offer_school_code(school: str) -> str:
    normalized = str(school or "").strip().casefold()
    if normalized == "hawthorne":
        return "HAW"
    if normalized == "north long beach":
        return "NLB"
    if normalized == "palmdale":
        return "PMD"
    return str(school or "").strip()


def _offer_school_location(school: str) -> str:
    normalized = str(school or "").strip()
    return normalized or "your school"


def _ensure_offer_pdf_path(offer_path: str) -> str:
    path_text = str(offer_path or "").strip()
    if not path_text:
        return ""
    source = Path(path_text)
    if source.suffix.casefold() == ".pdf" and source.is_file():
        return str(source)
    if source.suffix.casefold() != ".docx" or not source.is_file():
        return ""
    pdf_path = source.with_suffix(".pdf")
    if pdf_path.is_file():
        return str(pdf_path)
    word = None
    document = None
    try:
        import win32com.client  # type: ignore[import-not-found]

        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        document = word.Documents.Open(str(source))
        document.ExportAsFixedFormat(str(pdf_path), 17)
    except Exception:
        return ""
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
    return str(pdf_path) if pdf_path.is_file() else ""


def _build_pyside_history_rows(
    history_path: Path,
    *,
    school: str = "",
    outcome: str = "",
    search: str = "",
    limit: int | None = None,
) -> list[PySideHistoryRow]:
    store = InterviewHistoryStore(Path(history_path))
    if any(str(value or "").strip() for value in (school, outcome, search)) or limit:
        rows = store.load_filtered(school=school, outcome=outcome, search=search, limit=limit)
    else:
        rows = store.load()
    return _pyside_history_rows_from_payloads(rows, store)


def _pyside_history_rows_from_payloads(rows: Sequence[dict[str, Any]], store: InterviewHistoryStore) -> list[PySideHistoryRow]:
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
                deepseek_job_path=_history_text(row, "deepseek_job_path", default=""),
                deepseek_progress_path=_history_text(row, "deepseek_progress_path", default=""),
                candidate_email=_history_text(row, "candidate_email", "email", "candidateEmail", default=""),
                offer_path=_history_text(row, "offer_letter_path", "offer_path", default=""),
                deepseek_processing_status=_history_text(row, "deepseek_processing_status", default="").strip().lower(),
                deepseek_processing_warning=_history_text(row, "deepseek_processing_warning", default=""),
            )
        )
    return list(reversed(history_rows))


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


class _InMemoryRubricLoader:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get_traits_for_track(self, track_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for trait in self.data.get("traits", []) or []:
            if not isinstance(trait, dict):
                continue
            applicable = trait.get("applicable_tracks", []) or []
            if "all" in applicable or track_key in applicable:
                out.append(trait)
        return out


class _InMemoryQuestionOverridesStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get_trait_order(self, track_key: str) -> list[str]:
        return list((self.data.get("track_trait_order", {}) or {}).get(track_key, []) or [])

    def get_trait_question_override(self, trait_id: str) -> str | None:
        value = (self.data.get("trait_question_overrides", {}) or {}).get(trait_id)
        if isinstance(value, str) and value.strip():
            return value
        return None

    def list_custom_questions(self, track_key: str) -> list[dict[str, Any]]:
        items = list((self.data.get("custom_questions", {}) or {}).get(track_key, []) or [])
        return sorted(items, key=lambda item: (int(item.get("order", 999999)), str(item.get("text", "")).lower()))

    def ensure_flow(
        self,
        track_key: str,
        valid_trait_ids_in_order: list[str],
        valid_custom_ids_in_order: list[str],
    ) -> list[dict[str, Any]]:
        raw = list((self.data.get("track_question_flow", {}) or {}).get(track_key, []) or [])
        valid_traits = set(valid_trait_ids_in_order)
        valid_customs = set(valid_custom_ids_in_order)
        out: list[dict[str, Any]] = []
        seen_traits: set[str] = set()
        seen_customs: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip().lower()
            item_id = str(item.get("id", "")).strip()
            if item_type == "trait" and item_id in valid_traits and item_id not in seen_traits:
                out.append({"type": "trait", "id": item_id})
                seen_traits.add(item_id)
            if item_type == "custom" and item_id in valid_customs and item_id not in seen_customs:
                out.append({"type": "custom", "id": item_id})
                seen_customs.add(item_id)
        for trait_id in valid_trait_ids_in_order:
            if trait_id not in seen_traits:
                out.append({"type": "trait", "id": trait_id})
        for custom_id in valid_custom_ids_in_order:
            if custom_id not in seen_customs:
                out.append({"type": "custom", "id": custom_id})
        return out


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


def build_director_staffing_model(
    model: InterviewRedesignModel | None = None,
    *,
    school: str = "",
) -> InterviewRedesignModel:
    base_model = model or build_interview_redesign_model()
    return replace(
        base_model,
        app_title="Director Staffing Dashboard",
        navigation=list(DIRECTOR_STAFFING_NAVIGATION),
        director_staffing_school=str(school or "").strip(),
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


def latest_pyside_draft_path(drafts_dir: Path | None = None) -> Path | None:
    folder = Path(drafts_dir) if drafts_dir is not None else DEFAULT_BASE_DIR / "pyside_drafts"
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
        QLabel {
            background: transparent;
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
        QFrame#PySideSidebar {
            background: #061831;
            border: 0;
        }
        QLabel#PySideSidebarBrand {
            color: #ffffff;
            font-size: 18px;
            font-weight: 700;
            padding: 18px 14px;
        }
        QLabel#PySideSidebarUser {
            color: #e5e7eb;
            padding: 12px 14px;
        }
        QListWidget#PySideSidebarNavigation {
            background: #111827;
            color: #e5e7eb;
            border: 0;
            padding: 8px;
        }
        QListWidget#PySideSidebarNavigation::item {
            padding: 10px;
            border-radius: 6px;
        }
        QListWidget#PySideSidebarNavigation::item:selected {
            background: #2563eb;
        }
        QListWidget#AdminStudioSectionList {
            background: #ffffff;
            border: 0;
            padding: 10px 8px;
        }
        QListWidget#AdminStudioSectionList::item {
            padding: 11px 12px;
            border-radius: 6px;
            color: #0f172a;
        }
        QListWidget#AdminStudioSectionList::item:disabled {
            color: #475569;
            font-weight: 700;
            padding-top: 18px;
            background: transparent;
        }
        QListWidget#AdminStudioSectionList::item:selected {
            background: #eaf2ff;
            color: #2563eb;
            border-left: 4px solid #2563eb;
        }
        QFrame#AdminStudioSidebarRail {
            background: #ffffff;
            border-right: 1px solid #e2e8f0;
        }
        QFrame#AdminStudioWorkspace {
            background: #f8fafc;
            border: 0;
        }
        QFrame#AdminStudioSidebarBrandCard {
            background: #ffffff;
            border: 0;
            border-bottom: 1px solid #e2e8f0;
        }
        QScrollArea#AdminStudioToolbarScroll {
            background: #ffffff;
            border: 0;
            border-bottom: 1px solid #e2e8f0;
        }
        QLabel#AdminStudioWorkspaceActionsLabel {
            color: #475569;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 2px 12px;
            font-size: 12px;
        }
        QLabel#AdminStudioPageTitle {
            color: #0f172a;
            font-size: 26px;
            font-weight: 800;
        }
        QLabel#AdminStudioPageSubtitle {
            color: #475569;
            font-size: 14px;
        }
        QFrame#AdminStudioPageHeader {
            background: transparent;
            border: 0;
        }
        QFrame#AdminStudioMetricStrip,
        QFrame#AdminStudioToolbarCard {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
        }
        QFrame#AdminStudioConceptPanel {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 8px;
        }
        QFrame#AdminStudioValidationIssueCard {
            background: #fff7ed;
            border: 1px solid #fdba74;
            border-radius: 8px;
        }
        QLabel#AdminStudioConceptTitle {
            font-size: 16px;
            font-weight: 700;
        }
        QLabel#AdminStudioRubricEditorTitle,
        QLabel#AdminStudioSignalDetailTitle {
            font-size: 20px;
            font-weight: 800;
            color: #0f172a;
        }
        QLabel#AdminStudioChip {
            color: #0f3f8c;
            background: #eaf2ff;
            border: 1px solid #bfdbfe;
            border-radius: 10px;
            padding: 4px 9px;
            font-size: 12px;
        }
        QLabel#AdminStudioChip[adminChipVariant="success"] {
            color: #15803d;
            background: #dcfce7;
            border-color: #bbf7d0;
        }
        QLabel#AdminStudioChip[adminChipVariant="warning"] {
            color: #b45309;
            background: #fef3c7;
            border-color: #fde68a;
        }
        QLabel#AdminStudioChip[adminChipVariant="danger"] {
            color: #dc2626;
            background: #fee2e2;
            border-color: #fecaca;
        }
        QLabel#AdminStudioChip[adminChipVariant="neutral"] {
            color: #475569;
            background: #f1f5f9;
            border-color: #e2e8f0;
        }
        QLabel#AdminStudioTracksPill,
        QLabel#AdminStudioQuestionsPill,
        QLabel#AdminStudioUnsavedPill,
        QLabel#AdminStudioValidationPill {
            color: #1d4ed8;
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            border-radius: 8px;
            padding: 8px 12px;
            font-weight: 650;
        }
        QLabel#AdminStudioValidationPill[adminStatus="blocked"] {
            color: #dc2626;
            background: #fee2e2;
            border-color: #fecaca;
        }
        QLabel#AdminStudioValidationPill[adminStatus="ready"] {
            color: #15803d;
            background: #dcfce7;
            border-color: #bbf7d0;
        }
        QLabel#AdminStudioUnsavedPill[adminStatus="dirty"] {
            color: #b45309;
            background: #fef3c7;
            border-color: #fde68a;
        }
        QLabel#AdminStudioValidationSeverity {
            color: #dc2626;
            font-weight: 700;
        }
        QFrame#AdminStudioDashboardCard,
        QFrame#AdminStudioDraftChangesPanel,
        QFrame#AdminStudioPublishingReadinessPanel,
        QFrame#AdminStudioValidationReviewPanel,
        QFrame#AdminStudioQuickLinksPanel,
        QFrame#AdminStudioQuestionFlowSection,
        QFrame#AdminStudioQuestionCard,
        QFrame#AdminStudioQuestionEditDrawer,
        QFrame#AdminStudioRubricTraitCardsPanel,
        QFrame#AdminStudioTraitDetailPanel,
        QFrame#AdminStudioSignalHintListPanel,
        QFrame#AdminStudioSignalDetailPanel,
        QFrame#AdminStudioNotificationRuleListPanel,
        QFrame#AdminStudioNotificationEditPanel,
        QFrame#AdminStudioPromptTemplateListPanel,
        QFrame#AdminStudioPromptEditorPanel,
        QFrame#AdminStudioPromptInspectorPanel,
        QFrame#AdminStudioPromptRightInspectorPanel,
        QFrame#AdminStudioSchoolFolderCardsPanel,
        QFrame#AdminStudioSchoolDetailDrawer,
        QFrame#AdminStudioOfferTemplateHealthPanel,
        QFrame#AdminStudioJsonFilesPanel,
        QFrame#AdminStudioJsonFileDetailPanel,
        QFrame#AdminStudioSelectedModelPanel,
        QFrame#AdminStudioModelOptionCard,
        QFrame#AdminStudioValidationSummaryCard,
        QFrame#AdminStudioPublishAvailabilityPanel,
        QFrame#AdminStudioEnvironmentCard,
        QFrame#AdminStudioUserCard {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
        }
        QFrame#AdminStudioDashboardCard:hover,
        QFrame#AdminStudioQuestionCard:hover,
        QFrame#AdminStudioTraitCard:hover,
        QFrame#AdminStudioSchoolFolderCard:hover,
        QFrame#AdminStudioNotificationRuleCard:hover,
        QFrame#AdminStudioPromptTemplateCard:hover {
            border-color: #93c5fd;
            background: #f8fbff;
        }
        QFrame#AdminStudioValidationBlockedBanner {
            background: #fff1f2;
            border: 1px solid #fca5a5;
            border-radius: 8px;
        }
        QPushButton[adminButtonRole="primary"] {
            color: #ffffff;
            background: #2563eb;
            border: 1px solid #2563eb;
            border-radius: 6px;
            font-weight: 700;
        }
        QPushButton[adminButtonRole="secondary"] {
            color: #2563eb;
            background: #ffffff;
            border: 1px solid #bfdbfe;
            border-radius: 6px;
            font-weight: 650;
        }
        QPushButton[adminButtonRole="danger"] {
            color: #dc2626;
            background: #ffffff;
            border: 1px solid #fca5a5;
            border-radius: 6px;
            font-weight: 650;
        }
        QPushButton:disabled {
            color: #94a3b8;
            background: #f8fafc;
            border-color: #e2e8f0;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            padding: 7px;
            color: #0f172a;
        }
        QPlainTextEdit[readOnly="true"] {
            background: #f8fafc;
        }
        QListWidget#PySideStaffingClassroomList {
            background: #ffffff;
            color: #172033;
            border: 0;
            padding: 6px;
        }
        QListWidget#PySideStaffingClassroomList::item {
            padding: 10px 12px;
            border-radius: 6px;
            color: #172033;
        }
        QListWidget#PySideStaffingClassroomList::item:selected {
            background: #eaf2ff;
            color: #075dde;
            border-left: 3px solid #0b63f6;
        }
        QFrame#PySideStaffingMetricCard {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 8px;
        }
        QLabel#PySideStaffingMetricValue {
            font-size: 18px;
            font-weight: 700;
        }
        QLabel#PySideStaffingClassroomTitle {
            font-size: 22px;
            font-weight: 700;
        }
        QLabel#PySideStaffingPriorityBadge {
            color: #b91c1c;
            border: 1px solid #fca5a5;
            border-radius: 6px;
            padding: 4px 10px;
        }
        QLabel#PySideStaffingUpdatedLabel {
            background: transparent;
            padding-right: 8px;
        }
        QFrame#PySideStaffingDetailDrawer {
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 8px;
        }
        QLabel#PySideStaffingDrawerTitle {
            font-size: 20px;
            font-weight: 700;
        }
        QLabel#PySideStaffingDrawerName {
            font-size: 18px;
            font-weight: 700;
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
    def __init__(self, model: InterviewRedesignModel, *, defer_secondary_pages: bool = False) -> None:
        QtCore, QtGui, QtWidgets = _import_qt()
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.model = model
        self.director_staffing_mode = list(model.navigation) == DIRECTOR_STAFFING_NAVIGATION
        self.director_staffing_school = str(getattr(model, "director_staffing_school", "") or "").strip()
        self.session_track_key = next(iter(model.flows), "")
        self.session_index = 0
        self.session_answers: dict[str, dict[str, Any]] = {}
        self.session: PySideInterviewSession | None = None
        self.selected_history_offer_row: PySideHistoryRow | None = None
        self.history_store = InterviewHistoryStore(model.history_path)
        self.school_offer_store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
        staffing_db_path = (
            staffing_db_path_for_school(self.director_staffing_school)
            if self.director_staffing_mode
            else STAFFING_DB_PATH
        )
        _bootstrap_school_staffing_db_from_base(self.director_staffing_school, staffing_db_path)
        self.staffing_store = StaffingStore(staffing_db_path)
        self._staffing_referral_queue_timer: Any | None = None
        self.staffing_status_label: Any | None = None
        self.staffing_metrics_label: Any | None = None
        self.staffing_table: Any | None = None
        self.staffing_tabs: Any | None = None
        self.staffing_school_selector: Any | None = None
        self.staffing_classroom_selector: Any | None = None
        self.staffing_classroom_list: Any | None = None
        self.staffing_classroom_title: Any | None = None
        self.staffing_classroom_subtitle: Any | None = None
        self.staffing_priority_badge: Any | None = None
        self.staffing_metric_cards_layout: Any | None = None
        self.staffing_positions_table: Any | None = None
        self.staffing_detail_drawer: Any | None = None
        self.staffing_detail_drawer_layout: Any | None = None
        self._staffing_rows_by_school: dict[str, list[Any]] = {}
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
        self._overwrite_next_live_boundary_timestamp = False
        self._startup_notifications_scheduled = False
        self._recording_interface_preload_started = False
        class ResponsiveMainWindow(QtWidgets.QMainWindow):
            def resize(inner_self, *args: Any) -> None:
                super().resize(*args)
                callback = getattr(inner_self, "_responsive_callback", None)
                if callback is not None:
                    callback()

            def resizeEvent(inner_self, event: Any) -> None:
                super().resizeEvent(event)
                callback = getattr(inner_self, "_responsive_callback", None)
                if callback is not None:
                    callback()

        self.window = ResponsiveMainWindow()
        self.window._responsive_callback = self._apply_responsive_layout
        self.window.setWindowFlags(standard_window_control_flags(QtCore))
        self.window.setWindowTitle(model.app_title)
        self.window.resize(*self._initial_window_size())
        self._fit_window_to_available_screen()
        self.stack = QtWidgets.QStackedWidget()
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setObjectName("PySideSidebarNavigation")
        sidebar_items = (
            ["Dashboard", "Classrooms", "People", "History", "Reports", "Settings"]
            if self.director_staffing_mode
            else list(model.navigation)
        )
        for item in sidebar_items:
            nav_item = QtWidgets.QListWidgetItem(item)
            nav_item.setToolTip(item)
            self.sidebar.addItem(nav_item)
        if self.director_staffing_mode:
            self.sidebar.currentRowChanged.connect(lambda _index: self.stack.setCurrentIndex(0))
        else:
            self.sidebar.currentRowChanged.connect(self._select_main_nav_row)
        self.sidebar_panel = QtWidgets.QFrame()
        self.sidebar_panel.setObjectName("PySideSidebar")
        self.sidebar_panel.setMinimumWidth(160)
        self.sidebar_panel.setMaximumWidth(240)
        self.sidebar_panel.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)
        sidebar_layout = QtWidgets.QVBoxLayout(self.sidebar_panel)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        brand = self._label("Launch Pad\nLearning", "PySideSidebarBrand")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(self.sidebar, 1)
        sidebar_layout.addWidget(self._label("AD   Admin User", "PySideSidebarUser"))

        root = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.sidebar_panel)

        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)
        self.window.setCentralWidget(root)

        page_builders = {
            "Interviews": self._interviews_page,
            "Candidates": self._candidates_page,
            "Offers": self._offer_page,
            "Staffing": self._staffing_page,
            "Staffing v2": self._staffing_v2_page,
            "Onboarding": self._onboarding_page,
            "Admin": self._admin_page,
        }
        self._main_nav_page_builders = page_builders
        self._main_nav_page_names = list(model.navigation)
        self._main_nav_pages_built: set[int] = set()
        for index, name in enumerate(model.navigation):
            builder = page_builders.get(name)
            if builder is not None:
                should_build_now = (not defer_secondary_pages) or index == 0 or self.director_staffing_mode
                if should_build_now:
                    self.stack.addWidget(builder())
                    self._main_nav_pages_built.add(index)
                else:
                    self.stack.addWidget(self._deferred_main_nav_page(name))
        if self.director_staffing_mode:
            self.sidebar_panel.hide()
        self.sidebar.setCurrentRow(0)
        self._apply_responsive_layout()

    def _select_main_nav_row(self, index: int) -> None:
        if index < 0:
            return
        self._ensure_main_nav_page(index)
        self.stack.setCurrentIndex(index)
        item = self.sidebar.item(index) if hasattr(self, "sidebar") else None
        nav_text = item.text() if item is not None else ""
        if hasattr(self, "sidebar_panel"):
            self.sidebar_panel.setVisible(nav_text != "Staffing v2")
        if nav_text == "Staffing v2" and getattr(self, "staffing_v2_dashboard", None) is not None:
            self.QtCore.QTimer.singleShot(0, self.staffing_v2_dashboard._sync_staffing_v2_scroll_ranges)

    def _deferred_main_nav_page(self, name: str) -> Any:
        page = self.QtWidgets.QWidget()
        page.setObjectName(f"PySideDeferred{name.replace(' ', '')}Page")
        return page

    def _ensure_main_nav_page(self, index: int) -> None:
        if index in self._main_nav_pages_built:
            return
        if index < 0 or index >= len(self._main_nav_page_names):
            return
        name = self._main_nav_page_names[index]
        builder = self._main_nav_page_builders.get(name)
        if builder is None:
            self._main_nav_pages_built.add(index)
            return
        previous = self.stack.widget(index)
        replacement = builder()
        self.stack.removeWidget(previous)
        previous.deleteLater()
        self.stack.insertWidget(index, replacement)
        self._main_nav_pages_built.add(index)

    def show(self) -> None:
        self._fit_window_to_available_screen()
        self.window.showMaximized()
        self._schedule_startup_notifications()
        self._schedule_recording_interface_preload()

    def _schedule_startup_notifications(self) -> None:
        if self._startup_notifications_scheduled:
            return
        self._startup_notifications_scheduled = True
        self.QtCore.QTimer.singleShot(0, self._run_due_notifications_safely)

    def _schedule_recording_interface_preload(self) -> None:
        if getattr(self, "_recording_interface_preload_started", False):
            return
        self._recording_interface_preload_started = True
        self.QtCore.QTimer.singleShot(0, self._preload_recording_interface_async)

    def _recording_runtime_settings(self) -> dict[str, str]:
        return {
            "whisper_model": "large-v3",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
            "whisper_openvino_model": "OpenVINO/whisper-small-int8-ov",
        }

    def _preload_recording_interface_async(self) -> None:
        worker = threading.Thread(target=self._preload_recording_interface, daemon=True)
        worker.start()

    def _preload_recording_interface(self) -> None:
        try:
            resolve_runtime(self._recording_runtime_settings())
            if sys.platform.startswith("win"):
                resolve_default_windows_system_device()
        except Exception as exc:
            self.recording_warning = f"Recording preload unavailable: {exc}"

    def _initial_window_size(self) -> tuple[int, int]:
        screen = self.QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return (1100, 740)
        available = screen.availableGeometry()
        available_width = max(int(available.width()), 640)
        available_height = max(int(available.height()), 480)
        width = min(1180, max(720, available_width - 160))
        height = min(760, max(560, available_height - 160))
        width = min(width, max(640, available_width - 40))
        height = min(height, max(480, available_height - 40))
        return (width, height)

    def _fit_window_to_available_screen(self) -> None:
        if self.window.isMaximized() or self.window.isFullScreen():
            return
        screen = self.window.screen() or self.QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_width = max(640, int(available.width()))
        max_height = max(480, int(available.height()))
        if self.window.width() > max_width or self.window.height() > max_height:
            self.window.resize(min(self.window.width(), max_width), min(self.window.height(), max_height))
        geometry = self.window.geometry()
        min_x = int(available.x())
        min_y = int(available.y())
        max_x = int(available.right()) - int(geometry.width()) + 1
        max_y = int(available.bottom()) - int(geometry.height()) + 1
        x = min_x if max_x < min_x else min(max(int(geometry.x()), min_x), max_x)
        y = min_y if max_y < min_y else min(max(int(geometry.y()), min_y), max_y)
        self.window.move(x, y)

    def _apply_responsive_layout(self) -> None:
        if not hasattr(self, "sidebar_panel"):
            return
        width = int(self.window.width())
        if width < 900:
            sidebar_min = 104
            sidebar_max = 168
            admin_min = 132
            admin_max = 184
        else:
            sidebar_min = 160
            sidebar_max = 240
            admin_min = 170
            admin_max = 260
        sidebar_width = self._scaled_list_width(self.sidebar, 44) if hasattr(self, "sidebar") else sidebar_min
        self.sidebar_panel.setMinimumWidth(max(sidebar_min, min(sidebar_width, 360)))
        self.sidebar_panel.setMaximumWidth(max(sidebar_max, min(sidebar_width + 8, 420)))
        section_list = getattr(self, "admin_section_list", None)
        admin_sidebar_rail = getattr(self, "admin_sidebar_rail", None)
        if section_list is not None:
            admin_width = max(admin_min, self._scaled_list_width(section_list, 56))
            admin_min = min(admin_width, 640)
            admin_max = max(admin_max, min(admin_min + 24, 680))
        if admin_sidebar_rail is not None:
            admin_sidebar_rail.setMinimumWidth(admin_min)
            admin_sidebar_rail.setMaximumWidth(admin_max)
        if section_list is not None:
            section_list.setMinimumWidth(admin_min)
            section_list.setMaximumWidth(admin_max)

    def _page(self) -> Any:
        QtWidgets = self.QtWidgets

        class ResponsivePageScrollArea(QtWidgets.QScrollArea):
            def resizeEvent(inner_self, event: Any) -> None:
                super().resizeEvent(event)
                child = inner_self.widget()
                if child is not None:
                    child.setMaximumWidth(inner_self.viewport().width())

        page = ResponsivePageScrollArea()
        page.setObjectName("PySidePageScrollArea")
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        page.setMinimumSize(0, 0)
        content = self.QtWidgets.QWidget()
        content.setObjectName("PySidePageScrollContent")
        content.setMinimumWidth(0)
        content.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Expanding, self.QtWidgets.QSizePolicy.Policy.Minimum)
        layout = self.QtWidgets.QVBoxLayout(content)
        layout.setSizeConstraint(self.QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        page.setWidget(content)
        content.setMaximumWidth(page.viewport().width())
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

    def _admin_panel(self, object_name: str, *, margins: tuple[int, int, int, int] = (16, 14, 16, 14), spacing: int = 10) -> tuple[Any, Any]:
        frame, layout = self._surface()
        frame.setObjectName(object_name)
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        return frame, layout

    def _admin_chip(self, text: str, variant: str = "info", object_name: str = "AdminStudioChip") -> Any:
        chip = self._label(text, object_name)
        chip.setWordWrap(False)
        chip.setProperty("adminChipVariant", variant)
        return chip

    def _admin_action_button(self, text: str, object_name: str, *, role: str = "secondary") -> Any:
        if role == "primary":
            button = self._primary_button(text)
        else:
            button = self.QtWidgets.QPushButton(text)
            self._make_button_readable(button)
        button.setObjectName(object_name)
        button.setProperty("adminButtonRole", role)
        return button

    def _admin_page_header(self, title: str, description: str) -> Any:
        header, layout = self._admin_panel("AdminStudioPageHeader", margins=(0, 0, 0, 4), spacing=4)
        layout.addWidget(self._label(title, "AdminStudioPageTitle"))
        if description:
            layout.addWidget(self._label(description, "AdminStudioPageSubtitle"))
        return header

    def _admin_metric_strip(self, specs: list[tuple[str, str, str]]) -> Any:
        strip, layout = self._admin_panel("AdminStudioMetricStrip", margins=(14, 10, 14, 10), spacing=10)
        row = self.QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for label, value, variant in specs:
            row.addWidget(self._admin_chip(f"{label}: {value}", variant))
        row.addStretch(1)
        layout.addLayout(row)
        return strip

    def _admin_backing_table_container(self, table: Any) -> Any:
        container = self.QtWidgets.QWidget()
        container.setObjectName(f"{table.objectName()}Container")
        container.setProperty("adminBackingField", True)
        container.setMaximumHeight(0)
        layout = self.QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table)
        return container

    def _refresh_widget_style(self, widget: Any) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _primary_button(self, text: str) -> Any:
        button = self.QtWidgets.QPushButton(text)
        button.setObjectName("PrimaryButton")
        button.setProperty("adminButtonRole", "primary")
        self._make_button_readable(button)
        return button

    def _make_button_readable(self, button: Any) -> Any:
        button.setToolTip(button.text())
        metrics = button.fontMetrics()
        button.setMinimumHeight(max(34, metrics.height() + 18))
        text_width = metrics.horizontalAdvance(button.text())
        button.setMinimumWidth(max(92, button.sizeHint().width(), text_width + 40))
        button.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Minimum, self.QtWidgets.QSizePolicy.Policy.Fixed)
        return button

    def _scaled_list_width(self, list_widget: Any, padding: int) -> int:
        metrics = list_widget.fontMetrics()
        widest = 0
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            if item is None:
                continue
            widest = max(widest, metrics.horizontalAdvance(item.text()))
        return widest + int(padding)

    def _horizontal_scroll_panel(self, child_layout: Any, object_name: str) -> Any:
        content = self.QtWidgets.QWidget()
        content.setLayout(child_layout)
        content.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Minimum, self.QtWidgets.QSizePolicy.Policy.Fixed)
        scroll = self.QtWidgets.QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Expanding, self.QtWidgets.QSizePolicy.Policy.Fixed)
        scroll.setWidget(content)
        return scroll

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

        latest_draft = latest_pyside_draft_path(self._drafts_dir())
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
        import_button = self.QtWidgets.QPushButton("Import Indeed Transcript")
        import_button.setObjectName("ImportIndeedTranscriptButton")
        import_button.clicked.connect(self._import_indeed_transcript_from_home)
        action_row.addWidget(import_button, 2)
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
        rows = _build_pyside_history_rows(
            self.model.history_path,
            school=self.history_school_filter_text,
            outcome=self.history_outcome_filter_text,
        )
        if not self.history_search_text:
            return rows
        filtered: list[PySideHistoryRow] = []
        for row in rows:
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
        generate_label = "Generate" if row.deepseek_processing_status.strip().lower() == "not_started" else "Regenerate"
        regenerate_button = self.QtWidgets.QPushButton(generate_label)
        regenerate_button.setMaximumWidth(105)
        regenerate_button.setProperty("history_row_key", row.row_key)
        regenerate_button.setEnabled(bool(row.row_key) and row.deepseek_processing_status.strip().lower() != "processing")
        regenerate_button.setToolTip("Generate or regenerate DeepSeek interview notes from saved interview data.")
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
                widget.setParent(None)
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

    def _import_indeed_transcript_from_home(self) -> None:
        label = self.home_role_combo.currentText() if hasattr(self, "home_role_combo") else ""
        track_key = self._track_key_for_label(label)
        candidate_name = self.home_candidate_input.text().strip() if hasattr(self, "home_candidate_input") else ""
        school = self.home_school_combo.currentText().strip() if hasattr(self, "home_school_combo") else ""
        if not candidate_name:
            self.QtWidgets.QMessageBox.warning(self.window, "Indeed Transcript", "Enter candidate name before importing.")
            return
        file_name, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.window,
            "Import Indeed Transcript",
            str(Path.home()),
            "Text files (*.txt)",
        )
        if not file_name:
            return
        draft_path = self._default_draft_path(candidate_name or "Candidate")
        session = PySideInterviewSession(model=self.model, draft_path=draft_path)
        try:
            session.start(candidate_name=candidate_name, school=school, track_key=track_key)
            result = session.import_indeed_transcript_file(Path(file_name))
        except Exception as exc:  # noqa: BLE001
            self.QtWidgets.QMessageBox.warning(self.window, "Indeed Transcript", f"Could not import transcript: {exc}")
            return
        self.session = session
        self.session_track_key = session.track_key
        self.session_index = session.current_index
        self.session_answers = dict(session.answers)
        skipped_count = len(result.unmatched_question_ids)
        self._render_live_question_page()
        self._render_review_page()
        self._render_offer_page()
        self._refresh_home_draft_panel()
        self.interview_tabs.setCurrentIndex(2 if session.active_question() is not None else 3)
        if hasattr(self, "home_draft_label"):
            self.home_draft_label.setText(
                f"Imported Indeed transcript: {result.mapped_count} answers split, {skipped_count} questions marked skipped."
            )

    def _continue_latest_draft(self) -> None:
        draft_path = latest_pyside_draft_path(self._drafts_dir())
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
        latest_draft = latest_pyside_draft_path(self._drafts_dir())
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
        draft_path = latest_pyside_draft_path(self._drafts_dir())
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
        raw_date = self.session.interview_date if self.session and self.session.interview_date else date.today().isoformat()
        raw = f"{self.session.candidate_name if self.session else 'Candidate'}_{raw_date}"
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

            runtime_config = resolve_runtime(self._recording_runtime_settings())
            self.recording_session = start_recording(
                os_name="windows" if sys.platform.startswith("win") else "linux",
                output_dir=DEFAULT_BASE_DIR,
                base_name=self.recording_base_name,
                win_mic_device=DEFAULT_WINDOWS_MIC_DEVICE,
                win_sys_device=resolve_default_windows_system_device() if sys.platform.startswith("win") else None,
                whisper_model=runtime_config.model,
                whisper_device=runtime_config.device,
                whisper_compute_type=runtime_config.compute_type,
                whisper_backend=runtime_config.backend,
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
        overwrite = self._overwrite_next_live_boundary_timestamp
        for mark in reversed(self.session.flow_time_marks):
            if int(mark.get("flow_index", -1)) != flow_idx:
                continue
            if overwrite or "end_t" not in mark:
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
            self.recording_started_monotonic = None

    def _should_stop_recording_after_question(self, flow_index: int, item: FlowQuestion) -> bool:
        return False

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
            if item.kind == "intro":
                self._start_pyside_interview_recording()
            self.session_index = self.session.current_index
            self.session_answers = dict(self.session.answers)
            if self.session.active_question() is not None and boundary_elapsed is not None:
                self._mark_flow_timestamp_at(
                    self.session.current_index,
                    boundary_elapsed,
                    overwrite=self._overwrite_next_live_boundary_timestamp,
                )
            self._overwrite_next_live_boundary_timestamp = False
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
                self._mark_flow_timestamp_at(
                    self.session_index,
                    boundary_elapsed,
                    overwrite=self._overwrite_next_live_boundary_timestamp,
                )
            self._overwrite_next_live_boundary_timestamp = False
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
            self._overwrite_next_live_boundary_timestamp = True
        elif self.session_index > 0:
            self.session_index -= 1
            self._overwrite_next_live_timestamp = True
            self._overwrite_next_live_boundary_timestamp = True
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
        stored_answer = self.session.answers.get(item.question_id, {}) if self.session is not None else {}
        notes_label = "Imported Answer Transcript" if stored_answer.get("imported_transcript") else "Manual Notes"
        left_layout.addWidget(self._label(notes_label))
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
        workflow_items = self.session._workflow_items() if self.session is not None else []
        total = len(workflow_items)
        answers = self.session.answers if self.session is not None else self.session_answers
        answered = len(answers)
        summary, summary_layout = self._surface()
        summary_layout.addWidget(self._label("Interviewer Closeout", "Title"))
        if self.session is not None:
            track = self.model.flows.get(self.session.track_key)
            candidate_details = [
                f"Candidate: {self.session.candidate_name or 'Unknown'}",
                f"School: {self.session.school or 'Unknown'}",
                f"Position: {track.label if track is not None else self.session.track_key}",
            ]
            summary_layout.addWidget(self._label(" | ".join(candidate_details)))
        summary_layout.addWidget(self._label("Interview saved"))
        summary_layout.addWidget(self._label("Report files are being prepared in the background. You can return Home."))
        summary_layout.addWidget(self._label(f"Captured {answered} of {total} configured interview responses."))
        review = self.session.review_summary() if self.session is not None else None
        if review is not None:
            summary_layout.addWidget(self._label(f"Manual Score: {review.percent_of_max}%", "SectionTitle"))
            summary_layout.addWidget(self._label(f"Determination: {review.outcome}"))
            summary_layout.addWidget(self._label(f"Next Step: {review.next_action}"))
        checklist = self.QtWidgets.QListWidget()
        checklist.setObjectName("PySideReviewNeedsList")
        missing_scores = review.missing_scores if review is not None else []
        skipped_items = [
            str(answer.get("title") or question_id)
            for question_id, answer in answers.items()
            if isinstance(answer, dict) and answer.get("skipped")
        ]
        for item in missing_scores:
            checklist.addItem(f"Missing score: {item}")
        for item in skipped_items:
            checklist.addItem(f"Skipped: {item}")
        if checklist.count() == 0:
            checklist.addItem("No missing scores or skipped questions.")
        summary_layout.addWidget(self._label("Needs Review", "SectionTitle"))
        summary_layout.addWidget(checklist)

        question_table = self.QtWidgets.QTableWidget(0, 5)
        question_table.setObjectName("PySideReviewQuestionTable")
        question_table.setHorizontalHeaderLabels(["Question", "Score", "Notes", "Transcript", "Flags"])
        question_table.verticalHeader().setVisible(False)
        question_table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        question_table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        question_table.horizontalHeader().setStretchLastSection(True)
        for row, item in enumerate(workflow_items):
            answer = answers.get(item.question_id, {})
            if not isinstance(answer, dict):
                answer = {}
            question_table.insertRow(row)
            score_value = str(answer.get("score") or "").strip()
            skipped = bool(answer.get("skipped"))
            if skipped:
                score_text = "Skipped"
            elif item.kind == "trait" and not score_value:
                score_text = "Missing"
            else:
                score_text = score_value or "Not scored"
            note_text = "Yes" if str(answer.get("notes") or "").strip() else "No"
            flags: list[str] = []
            quick_actions = [str(action) for action in answer.get("quick_actions", []) or []]
            if skipped:
                flags.append("Skipped")
            if "Disqualifier observed" in quick_actions:
                flags.append("Disqualifier")
            if "Candidate gave no example" in quick_actions:
                flags.append("No example")
            transcript_text = ""
            if self.session is not None:
                transcript_text = str(self.session.flow_candidate_transcripts.get(row, "") or "").strip()
                if not transcript_text:
                    recording = self.session.flow_recordings.get(row, {}) or {}
                    transcript_text = str(recording.get("candidate_transcript") or "").strip()
            values = [item.title, score_text, note_text, transcript_text or "Not generated", ", ".join(flags) or "-"]
            for column, value in enumerate(values):
                if column == 1 and item.kind == "trait":
                    continue
                question_table.setItem(row, column, self.QtWidgets.QTableWidgetItem(value))
            if item.kind == "trait":
                rating = self.QtWidgets.QSpinBox()
                rating.setObjectName(f"PySideReviewRating_{item.question_id}")
                rating.setRange(0, 5)
                rating.setSpecialValueText("Missing")
                rating.setToolTip("Change interviewer rating after reading notes and transcript.")
                rating.setKeyboardTracking(False)
                rating.setValue(_coerce_session_score(score_value) or 0)
                rating.valueChanged.connect(
                    lambda value, question_id=item.question_id: self._update_review_rating(question_id, value),
                )
                question_table.setCellWidget(row, 1, rating)
        question_table.resizeColumnsToContents()
        summary_layout.addWidget(self._label("Question Score Review", "SectionTitle"))
        summary_layout.addWidget(question_table)
        summary_layout.addWidget(self._label("Send candidate to director interview if required by your hiring workflow."))
        actions = self.QtWidgets.QHBoxLayout()
        home_button = self.QtWidgets.QPushButton("Home")
        home_button.clicked.connect(lambda: self.interview_tabs.setCurrentIndex(0))
        actions.addWidget(home_button)
        summary_layout.addLayout(actions)
        self.review_status_label = self._label("")
        summary_layout.addWidget(self.review_status_label)
        layout.addWidget(summary)
        layout.addStretch(1)

    def _update_review_rating(self, question_id: str, value: int) -> None:
        if self.session is None:
            return
        try:
            self.session.update_review_score(question_id, value)
        except ValueError as exc:
            self.review_status_label.setText(str(exc))
            return
        self.session_answers = dict(self.session.answers)
        self._render_review_page()

    def _default_draft_path(self, candidate_name: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in candidate_name).strip("_") or "Candidate"
        return self._drafts_dir() / f"{safe_name}.json"

    def _drafts_dir(self) -> Path:
        history_path = Path(getattr(self.model, "history_path", "") or "")
        if str(history_path):
            return history_path.parent / "pyside_drafts"
        return DEFAULT_BASE_DIR / "pyside_drafts"

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
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        session = self.session

        class _PySideLiveRefreshFinalizeGateways(FinalizeGateways):
            def persist_finalize_history(self, app: Any, context: Any, out_path: str) -> str:
                history_id = super().persist_finalize_history(app, context, out_path)
                results.put({"ok": True, "event": "history_persisted", "history_id": history_id})
                return history_id

        def _worker() -> None:
            try:
                self._report_pyside_finalize_progress("Stopping recording and transcribing")
                self._stop_pyside_interview_recording()
                self._report_pyside_finalize_progress("Building interview notes")
                result = session.finalize_interview(
                    base_dir=DEFAULT_BASE_DIR,
                    history_path=INTERVIEW_HISTORY_PATH,
                    gateways=_PySideLiveRefreshFinalizeGateways(),
                )
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
        if message.get("event") == "history_persisted":
            self._reload_history_model()
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
        self._emit_pyside_rating_notification(result)
        self._record_staffing_director_referral_from_finalize_result(result)
        self._reload_history_model()
        if isinstance(result, dict) and result.get("deepseek_progress_path"):
            self._watch_pyside_deepseek_finalize_progress(result.get("deepseek_progress_path"))
        else:
            self._report_pyside_finalize_progress("Interview finalized")
            self._refresh_pyside_finalize_progress()
            self._schedule_close_pyside_finalize_progress()

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

    def _schedule_close_pyside_finalize_progress(self) -> None:
        if self.pyside_finalize_progress_dialog is None:
            return
        self.QtCore.QTimer.singleShot(2500, self._close_pyside_finalize_progress)

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
        self._attach_latest_deepseek_progress_from_history()
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

    def _attach_latest_deepseek_progress_from_history(self) -> None:
        if self.pyside_finalize_deepseek_progress_path is not None:
            return
        history_path = Path(getattr(self.model, "history_path", ""))
        if not str(history_path):
            return
        rows = InterviewHistoryStore(history_path).load()
        allowed_dirs = {
            (history_path.parent / "deepseek_jobs").resolve(),
            (history_path.parent / "interviews" / "deepseek_jobs").resolve(),
        }
        if history_path.parent.name == "user_artifacts":
            allowed_dirs.add((history_path.parent / "interviews" / "deepseek_jobs").resolve())
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            status = str(row.get("deepseek_processing_status") or "").strip().lower()
            if status != "processing":
                continue
            progress_value = str(row.get("deepseek_progress_path") or "").strip()
            if not progress_value:
                continue
            progress_path = Path(progress_value)
            if not progress_path.name.startswith("deepseek-finalize-") or progress_path.suffix != ".json":
                continue
            if not progress_path.name.endswith(".progress.json"):
                continue
            try:
                resolved = progress_path.resolve()
            except OSError:
                continue
            if resolved.parent not in allowed_dirs:
                continue
            if resolved.exists():
                self.pyside_finalize_deepseek_progress_path = resolved
                return

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
            if status == "complete":
                self._schedule_close_pyside_finalize_progress()
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
        if row.offer_status == "generated":
            self._advance_pyside_offer_status(row, "approved", "Offer marked approved.")
            return
        if row.offer_status == "approved":
            self._advance_pyside_offer_status(row, "accepted", "Offer marked accepted.")
            return
        if row.offer_status == "accepted":
            self._advance_pyside_offer_status(row, "welcome_email_sent", "Welcome email marked sent.")
            self.sidebar.setCurrentRow(4)
            self.stack.setCurrentIndex(4)
            return
        self.selected_history_offer_row = row
        self._render_offer_page()
        self.sidebar.setCurrentRow(2)
        self.stack.setCurrentIndex(2)

    def _advance_pyside_offer_status(self, row: PySideHistoryRow, status: str, message: str) -> None:
        if self._update_pyside_offer_status(row.row_key, status, "", row):
            self._reload_history_model()
            self.window.statusBar().showMessage(message, 3500)

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
                "deepseek_job_path": row.deepseek_job_path,
                "deepseek_progress_path": row.deepseek_progress_path,
            },
            history_path=self.model.history_path,
            base_dir=self.model.history_path.parent / "interviews"
            if self.model.history_path.parent.name == "user_artifacts"
            else DEFAULT_BASE_DIR,
        )

    def _retry_history_deepseek(self, row: PySideHistoryRow) -> None:
        mode = "full" if row.deepseek_processing_status.strip().lower() == "not_started" else self._choose_pyside_notes_regeneration_mode(row)
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
                updated = self._update_pyside_offer_status(
                    self.selected_history_offer_row.row_key,
                    "generated",
                    str(output_path),
                    self.selected_history_offer_row,
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
                self._emit_pyside_session_offer_generated(output_path)
        except Exception as exc:
            self.offer_status_label.setText(f"Offer not generated: {exc}")
            return
        self.offer_status_label.setText(f"Offer generated: {output_path}")

    def _update_pyside_offer_status(self, row_key: str, status: str, offer_path: str = "", row: PySideHistoryRow | None = None) -> bool:
        updated = self.history_store.update_offer_state(row_key, status, offer_path)
        if not updated:
            return False
        if row is not None:
            self._emit_pyside_offer_notification(row, status)
        return True

    def _emit_pyside_session_offer_generated(self, output_path: Path) -> None:
        if self.session is None:
            return
        payload = {
            "candidate": self.session.candidate_name,
            "candidate_name": self.session.candidate_name,
            "candidate_email": "",
            "school": self.session.school,
            "school_code": _offer_school_code(self.session.school),
            "school_location": _offer_school_location(self.session.school),
            "director_name": "",
            "position": self.offer_fields["position"].text().strip() if hasattr(self, "offer_fields") else self.session.track_key,
            "offer_status": "generated",
            "offer_path": str(output_path),
            "offer_pdf_path": "",
            "onboarding_guide_path": str(self.settings.get("welcome_onboarding_pdf_path", "")).strip() if hasattr(self, "settings") else "",
            "reply_by_date": (date.today() + timedelta(days=3)).isoformat(),
            "generated_date": date.today().isoformat(),
            "interview_date": str(getattr(self.session, "interview_date", "") or ""),
            "start_date": self.offer_fields["start_date"].text().strip() if hasattr(self, "offer_fields") and "start_date" in self.offer_fields else "",
            "notice_given": "",
            "date_notice_given": "",
            "final_working_day": "",
            "last_working_day": "",
        }
        payload.update(_qualification_notification_payload(getattr(self.session, "qualification", None)))
        key = f"{self.session.candidate_name}:{payload['interview_date']}:{output_path}"
        try:
            self._notification_service().emit_event("offer.generated", payload, f"{key}:offer.generated")
        except Exception:
            return

    def _emit_pyside_offer_notification(self, row: PySideHistoryRow, status: str) -> None:
        event_type = {
            "generated": "offer.generated",
            "approved": "offer.approved",
            "accepted": "offer.accepted",
            "welcome_email_sent": "offer.welcome_email_sent",
        }.get(str(status or "").strip().lower())
        if not event_type:
            return
        offer_pdf_path = _ensure_offer_pdf_path(row.offer_path) if event_type == "offer.approved" else ""
        payload = {
            "candidate": row.candidate,
            "candidate_name": row.candidate,
            "candidate_email": row.candidate_email,
            "school": row.school,
            "school_code": _offer_school_code(row.school),
            "school_location": _offer_school_location(row.school),
            "director_name": "",
            "position": row.position,
            "offer_status": str(status or "").strip().lower(),
            "offer_path": row.offer_path,
            "offer_pdf_path": offer_pdf_path,
            "onboarding_guide_path": str(self.settings.get("welcome_onboarding_pdf_path", "")).strip() if hasattr(self, "settings") else "",
            "reply_by_date": (date.today() + timedelta(days=3)).isoformat(),
            "generated_date": date.today().isoformat(),
            "start_date": self.offer_fields["start_date"].text().strip() if hasattr(self, "offer_fields") and "start_date" in self.offer_fields else "",
            "notice_given": "",
            "date_notice_given": "",
            "final_working_day": "",
            "last_working_day": "",
        }
        payload.update(_qualification_notification_payload(getattr(self.session, "qualification", None)))
        try:
            self._notification_service().emit_event(event_type, payload, f"{row.row_key}:{event_type}")
        except Exception:
            return

    def _emit_pyside_rating_notification(self, result: dict[str, Any]) -> None:
        scoring = result.get("scoring", {}) if isinstance(result, dict) else {}
        if not isinstance(scoring, dict):
            return
        outcome = str(scoring.get("outcome", "") or "").strip()
        event_type = {
            "hire": "interview.rating.hire",
            "borderline": "interview.rating.borderline",
        }.get(outcome.lower())
        if not event_type:
            return
        payload = {
            "candidate_name": self.session.candidate_name,
            "school": self.session.school,
            "director_name": "",
            "position": self.session.position,
            "interview_date": self.session.interview_date,
            "outcome": outcome,
            "score": str(scoring.get("percent_of_max_label") or scoring.get("percent_of_max") or ""),
            "history_id": str(result.get("history_id", "") or ""),
            "start_date": "",
            "notice_given": "",
            "date_notice_given": "",
            "final_working_day": "",
            "last_working_day": "",
        }
        payload.update(_qualification_notification_payload(getattr(self.session, "qualification", None)))
        key = payload["history_id"] or f"{self.session.candidate_name}:{self.session.interview_date}:{event_type}"
        try:
            self._notification_service().emit_event(event_type, payload, f"{key}:{event_type}")
        except Exception:
            return

    def _admin_page(self) -> Any:
        page, layout = self._page()
        self.admin_studio = AdminStudio.load(self._admin_studio_paths())
        self.admin_draft = self.admin_studio.create_draft()
        self.admin_edit_mode = False
        self._admin_tables: dict[str, Any] = {}
        self._admin_table_editable_columns: dict[str, set[int]] = {}
        self._admin_syncing_table_edits = False

        toolbar = self.QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 6)
        toolbar.setSpacing(10)
        self.admin_status_label = self._label("", "AdminStudioStatus")
        self.admin_status_label.setVisible(False)
        toolbar.addWidget(self.admin_status_label, 1)
        self.admin_tracks_pill = self._label("", "AdminStudioTracksPill")
        self.admin_questions_pill = self._label("", "AdminStudioQuestionsPill")
        self.admin_unsaved_pill = self._label("", "AdminStudioUnsavedPill")
        self.admin_validation_pill = self._label("", "AdminStudioValidationPill")
        for pill in (
            self.admin_tracks_pill,
            self.admin_questions_pill,
            self.admin_unsaved_pill,
            self.admin_validation_pill,
        ):
            pill.setWordWrap(False)
            toolbar.addWidget(pill)
        action_group = self.QtWidgets.QVBoxLayout()
        action_label = self._label("Workspace actions", "AdminStudioWorkspaceActionsLabel")
        action_label.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
        action_group.addWidget(action_label)
        action_buttons = self.QtWidgets.QHBoxLayout()
        self.admin_edit_button = self._admin_action_button("Start Editing", "AdminStudioEditButton")
        self.admin_edit_button.clicked.connect(lambda: self._set_admin_editing_enabled(True))
        self.admin_save_draft_button = self._admin_action_button("Save Draft", "AdminStudioSaveDraftButton")
        self.admin_save_draft_button.clicked.connect(self._save_admin_draft)
        self.admin_review_button = self._admin_action_button("Review Changes", "AdminStudioReviewButton")
        self.admin_review_button.clicked.connect(self._show_admin_review_changes_dialog)
        self.admin_publish_button = self._admin_action_button("Publish Changes", "AdminStudioPublishButton", role="primary")
        self.admin_publish_button.clicked.connect(self._show_admin_publish_confirmation_dialog)
        self.admin_discard_button = self._admin_action_button("Discard", "AdminStudioDiscardButton", role="danger")
        self.admin_discard_button.clicked.connect(self._show_admin_discard_confirmation_dialog)
        action_buttons.addWidget(self.admin_edit_button)
        action_buttons.addWidget(self.admin_save_draft_button)
        action_buttons.addWidget(self.admin_review_button)
        action_buttons.addWidget(self.admin_publish_button)
        action_buttons.addWidget(self.admin_discard_button)
        action_group.addLayout(action_buttons)
        toolbar.addLayout(action_group)
        layout.addWidget(self._horizontal_scroll_panel(toolbar, "AdminStudioToolbarScroll"))

        workspace = self.QtWidgets.QSplitter()
        workspace.setObjectName("AdminStudioWorkspace")
        self.admin_sidebar_rail = self.QtWidgets.QFrame()
        self.admin_sidebar_rail.setObjectName("AdminStudioSidebarRail")
        self.admin_sidebar_rail.setMinimumWidth(170)
        self.admin_sidebar_rail.setMaximumWidth(260)
        self.admin_sidebar_rail.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Preferred, self.QtWidgets.QSizePolicy.Policy.Expanding)
        admin_rail_layout = self.QtWidgets.QVBoxLayout(self.admin_sidebar_rail)
        admin_rail_layout.setContentsMargins(0, 0, 0, 0)
        admin_rail_layout.setSpacing(10)
        brand, brand_layout = self._admin_panel("AdminStudioSidebarBrandCard", margins=(18, 16, 18, 12), spacing=2)
        brand_layout.addWidget(self._label("Admin Studio", "AdminStudioConceptTitle"))
        brand_layout.addWidget(self._label("Staffing Management", "AdminStudioPageSubtitle"))
        admin_rail_layout.addWidget(brand)
        self.admin_section_list = self.QtWidgets.QListWidget()
        self.admin_section_list.setObjectName("AdminStudioSectionList")
        self.admin_section_list.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.admin_section_list.setTextElideMode(self.QtCore.Qt.TextElideMode.ElideNone)
        self.admin_section_list.setMinimumWidth(170)
        self.admin_section_list.setMaximumWidth(260)
        self.admin_section_list.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Preferred, self.QtWidgets.QSizePolicy.Policy.Expanding)
        self.admin_stack = self.QtWidgets.QStackedWidget()
        self.admin_stack.setObjectName("AdminStudioEditorStack")
        self._admin_nav_stack_indexes: dict[int, int] = {}
        current_group = ""
        for section in self.admin_studio.summary(self.admin_draft).sections:
            if section.group != current_group:
                current_group = section.group
                group_item = self.QtWidgets.QListWidgetItem(current_group)
                group_item.setFlags(group_item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsSelectable & ~self.QtCore.Qt.ItemFlag.ItemIsEnabled)
                group_item.setData(self.QtCore.Qt.ItemDataRole.UserRole, "group")
                self.admin_section_list.addItem(group_item)
            nav_item = self.QtWidgets.QListWidgetItem(section.title)
            nav_item.setIcon(self._admin_nav_icon(section.key))
            nav_item.setToolTip(section.title)
            nav_item.setData(self.QtCore.Qt.ItemDataRole.UserRole, section.key)
            self.admin_section_list.addItem(nav_item)
            self._admin_nav_stack_indexes[self.admin_section_list.count() - 1] = self.admin_stack.count()
            self.admin_stack.addWidget(self._admin_section_page(section.key, section.title, section.description))
        self.admin_section_list.currentRowChanged.connect(self._select_admin_nav_row)
        admin_rail_layout.addWidget(self.admin_section_list, 1)
        environment_card, environment_layout = self._surface()
        environment_card.setObjectName("AdminStudioEnvironmentCard")
        environment_layout.addWidget(self._label("Environment", "AdminStudioConceptTitle"))
        environment_layout.addWidget(self._admin_chip("Production", "success"))
        environment_layout.addWidget(self._label("v1.4.0"))
        admin_rail_layout.addWidget(environment_card)
        user_card, user_layout = self._surface()
        user_card.setObjectName("AdminStudioUserCard")
        user_layout.addWidget(self._label("DN   David Nord", "AdminStudioConceptTitle"))
        user_layout.addWidget(self._label("Super Admin"))
        admin_rail_layout.addWidget(user_card)
        workspace.addWidget(self.admin_sidebar_rail)
        workspace.addWidget(self.admin_stack)
        workspace.setStretchFactor(1, 1)
        layout.addWidget(workspace, 1)
        self.admin_section_list.setCurrentRow(1)
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()
        self._configure_admin_v2_scroll_areas()
        return page

    def _admin_nav_icon(self, key: str) -> Any:
        standard_pixmap = self.QtWidgets.QStyle.StandardPixmap
        icon_key = {
            "dashboard": standard_pixmap.SP_ComputerIcon,
            "questions_flow": standard_pixmap.SP_FileDialogDetailedView,
            "rubrics": standard_pixmap.SP_DialogApplyButton,
            "signal_hints": standard_pixmap.SP_MessageBoxInformation,
            "templates_folders": standard_pixmap.SP_DirIcon,
            "notifications": standard_pixmap.SP_MessageBoxWarning,
            "deepseek_model": standard_pixmap.SP_DriveHDIcon,
            "deepseek_prompts": standard_pixmap.SP_FileIcon,
            "advanced_json": standard_pixmap.SP_FileDialogInfoView,
            "validation": standard_pixmap.SP_DialogHelpButton,
            "email_settings": standard_pixmap.SP_DialogSaveButton,
        }.get(key, standard_pixmap.SP_FileIcon)
        return self.window.style().standardIcon(icon_key)

    def _select_admin_nav_row(self, row: int) -> None:
        stack_index = self._admin_nav_stack_indexes.get(row)
        if stack_index is None:
            for next_row in range(row + 1, self.admin_section_list.count()):
                if next_row in self._admin_nav_stack_indexes:
                    self.admin_section_list.setCurrentRow(next_row)
                    return
            return
        item = self.admin_section_list.item(row)
        if item and item.data(self.QtCore.Qt.ItemDataRole.UserRole) == "dashboard":
            self._refresh_admin_dashboard_page()
        if item and item.data(self.QtCore.Qt.ItemDataRole.UserRole) == "validation":
            self._refresh_admin_validation_page()
        self.admin_stack.setCurrentIndex(stack_index)
        self.QtCore.QTimer.singleShot(0, self._configure_admin_v2_scroll_areas)

    def _refresh_admin_dashboard_page(self) -> None:
        container = getattr(self, "admin_dashboard_container", None)
        current = getattr(self, "admin_dashboard_page_widget", None)
        if container is None or current is None:
            return
        layout = container.layout()
        if layout is None:
            return
        index = layout.indexOf(current)
        if index < 0:
            return
        layout.removeWidget(current)
        current.setParent(None)
        current.deleteLater()
        replacement = self._admin_dashboard_page()
        self.admin_dashboard_page_widget = replacement
        layout.insertWidget(index, replacement, 1)
        self._configure_admin_v2_scroll_areas()

    def _refresh_admin_validation_page(self) -> None:
        container = getattr(self, "admin_validation_container", None)
        current = getattr(self, "admin_validation_page_widget", None)
        if container is None or current is None:
            return
        layout = container.layout()
        if layout is None:
            return
        index = layout.indexOf(current)
        if index < 0:
            return
        layout.removeWidget(current)
        current.setParent(None)
        current.deleteLater()
        replacement = self._admin_validation_content()
        self.admin_validation_page_widget = replacement
        layout.insertWidget(index, replacement, 1)
        self._configure_admin_v2_scroll_areas()

    def _admin_studio_paths(self) -> AdminStudioPaths:
        return AdminStudioPaths(
            rubric_path=DEFAULT_RUBRIC_PATH,
            overrides_path=QUESTIONS_OVERRIDE_PATH,
            school_settings_path=SCHOOL_OFFER_SETTINGS_PATH,
            prompts_path=DEEPSEEK_PROMPTS_CONFIG_PATH,
            app_settings_path=INTERVIEW_APP_SETTINGS_PATH,
            notification_rules_path=NOTIFICATION_RULES_PATH,
        )

    def _configure_admin_v2_scroll_areas(self) -> None:
        for root in (getattr(self, "admin_sidebar_rail", None), getattr(self, "admin_stack", None)):
            if root is not None:
                configure_v2_scroll_areas(self.QtWidgets, root, self.QtCore)

    def _admin_section_page(self, key: str, title: str, description: str) -> Any:
        tab, tab_layout = self._page()
        tab_layout.addWidget(self._admin_page_header(title, description))
        if key == "dashboard":
            self.admin_dashboard_container = tab_layout.parentWidget()
            self.admin_dashboard_page_widget = self._admin_dashboard_page()
            tab_layout.addWidget(self.admin_dashboard_page_widget, 1)
            return tab
        if key == "questions":
            self.admin_questions_container = tab_layout.parentWidget()
            self.admin_questions_flow_widget = self._admin_questions_flow_cards()
            tab_layout.addWidget(self.admin_questions_flow_widget, 2)
            table = self._admin_questions_table()
            tab_layout.addWidget(self._admin_backing_table_container(table))
            return tab
        if key == "rubrics":
            self.admin_rubrics_layout = tab_layout
            self.admin_rubrics_trait_widget = self._admin_rubric_trait_cards()
            tab_layout.addWidget(self.admin_rubrics_trait_widget, 2)
            table = self._admin_rubrics_table()
            self.admin_rubrics_table_widget = table
            tab_layout.addWidget(self._admin_backing_table_container(table))
            return tab
        if key == "signals":
            tab_layout.addWidget(self._admin_signal_hint_cards(), 1)
            return tab
        if key == "templates":
            tab_layout.addWidget(self._admin_templates_cards(), 2)
            table = self._admin_school_settings_table()
            tab_layout.addWidget(self._admin_backing_table_container(table))
            return tab
        if key == "notifications":
            self.admin_notifications_layout = tab_layout
            self.admin_notification_rule_widget = self._admin_notification_rule_cards()
            tab_layout.addWidget(self.admin_notification_rule_widget, 2)
            controls = self.QtWidgets.QHBoxLayout()
            edit_button = self.QtWidgets.QPushButton("Create/Modify Template")
            edit_button.setObjectName("AdminStudioNotificationTemplateButton")
            edit_button.clicked.connect(self._open_notification_template_dialog)
            self.admin_notification_template_button = edit_button
            controls.addWidget(edit_button)
            controls.addStretch(1)
            tab_layout.addLayout(controls)
            table = self._admin_notifications_table()
            tab_layout.addWidget(self._admin_backing_table_container(table))
            return tab
        if key == "deepseek_model":
            tab_layout.addWidget(self._admin_model_option_cards(), 2)
            tab_layout.addWidget(self._admin_deepseek_model_selector(), 1)
            return tab
        if key == "prompts":
            tab_layout.addWidget(self._admin_prompt_summary_strip())
            tab_layout.addWidget(self._admin_prompt_editor_cards(), 2)
            table = self._admin_prompts_table()
            tab_layout.addWidget(self._admin_backing_table_container(table))
            return tab
        if key == "advanced":
            tab_layout.addWidget(self._admin_advanced_json_cards(), 2)
            return tab
        if key == "validation":
            self.admin_validation_container = tab_layout.parentWidget()
            self.admin_validation_page_widget = self._admin_validation_content()
            tab_layout.addWidget(self.admin_validation_page_widget, 1)
            return tab
        if key == "email_settings":
            tab_layout.addWidget(self._admin_email_settings_page(), 1)
            return tab
        rows = self._admin_readonly_rows(key)
        table = self._admin_table(key, ["Key", "Value"], rows, set())
        tab_layout.addWidget(table, 1)
        return tab

    def _admin_dashboard_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        summary = self.admin_studio.summary(self.admin_draft)
        layout.addWidget(
            self._admin_metric_strip(
                [
                    ("Tracks", str(summary.track_count), "info"),
                    ("Questions", str(summary.question_count), "info"),
                    ("Unsaved changes", str(summary.dirty_count), "warning" if summary.dirty_count else "neutral"),
                    ("Validation blocked", f"{len(summary.validation_errors)} issues", "danger" if summary.validation_errors else "success"),
                ]
            )
        )
        cards = self.QtWidgets.QGridLayout()
        cards.setSpacing(12)
        for index, section in enumerate(summary.sections):
            if section.key == "dashboard":
                continue
            card, card_layout = self._admin_panel("AdminStudioDashboardCard")
            title_row = self.QtWidgets.QHBoxLayout()
            icon_label = self.QtWidgets.QLabel()
            icon_label.setObjectName("AdminStudioDashboardCardIcon")
            icon_label.setPixmap(self._admin_nav_icon(section.key).pixmap(24, 24))
            title_row.addWidget(icon_label)
            title_row.addWidget(self._label(section.title, "AdminStudioConceptTitle"), 1)
            card_layout.addLayout(title_row)
            card_layout.addWidget(self._label(section.description))
            chip_text = f"{section.item_count} items" if section.item_count else "Ready"
            if section.key == "questions":
                chip_text = f"{summary.track_count} tracks · {summary.question_count} questions"
            if section.key == "validation":
                chip_text = f"{len(summary.validation_errors)} blocking issues"
            chip_variant = "danger" if section.key == "validation" and summary.validation_errors else "info"
            card_layout.addWidget(self._admin_chip(chip_text, chip_variant))
            button = self._admin_action_button(
                "Review Issues" if section.key == "validation" else "Open",
                f"AdminStudioDashboardOpen_{self._admin_object_suffix(section.key)}",
                role="danger" if section.key == "validation" and summary.validation_errors else "secondary",
            )
            button.clicked.connect(lambda _checked=False, key=section.key: self._select_admin_section_by_key(key))
            card_layout.addWidget(button)
            cards.addWidget(card, index // 3, index % 3)
        layout.addLayout(cards)

        panels = self.QtWidgets.QGridLayout()
        panels.setSpacing(12)
        panels.addWidget(self._admin_dashboard_validation_panel(summary.validation_errors), 0, 0)
        panels.addWidget(self._admin_dashboard_draft_changes_panel(summary), 0, 1)
        panels.addWidget(self._admin_dashboard_publishing_readiness_panel(summary), 1, 0)
        panels.addWidget(self._admin_named_panel(
            "AdminStudioQuickLinksPanel",
            "Quick Links",
            "Shortcuts",
            "Create notifications, browse folders, open prompt templates, or view validation rules.",
            [
                "Create / Modify Notification Template",
                "Browse School Folders",
                "Open Prompt Template Editor",
                "View Validation Rules",
                "View All Settings",
            ],
        ), 1, 1)
        layout.addLayout(panels)
        history_button = self._admin_action_button("View Version History", "AdminStudioGlobalVersionHistoryButton")
        history_button.clicked.connect(self._show_admin_global_version_history_dialog)
        layout.addWidget(history_button)
        layout.addStretch(1)
        return page

    def _admin_dashboard_draft_changes_panel(self, summary: Any) -> Any:
        panel, panel_layout = self._admin_panel("AdminStudioDraftChangesPanel")
        panel_layout.addWidget(self._label("Draft Changes", "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._admin_chip(f"{summary.dirty_count} Unsaved", "warning" if summary.dirty_count else "neutral"))
        change_summary = self.admin_draft.change_summary()
        if not change_summary.changed_files:
            panel_layout.addWidget(self._label("No unsaved changes at this time."))
            panel_layout.addWidget(self._label("All configurations are saved."))
        else:
            panel_layout.addWidget(self._label("Draft edits are waiting for review."))
            for filename in change_summary.changed_files:
                row, row_layout = self._surface()
                row.setObjectName("AdminStudioDraftChangeRow")
                row_layout.addWidget(self._label(filename, "AdminStudioConceptTitle"))
                matching_lines = [line for line in change_summary.lines if self._admin_review_line_matches_file(filename, line)]
                if not matching_lines and change_summary.lines:
                    matching_lines = change_summary.lines[:2]
                if not matching_lines:
                    matching_lines = ["File payload changed."]
                for line in matching_lines[:2]:
                    row_layout.addWidget(self._label(line))
                before_after = self.admin_draft.changed_payloads().get(filename)
                if before_after is not None:
                    for line in self._admin_review_payload_diff_lines(filename, before_after[0], before_after[1])[:2]:
                        row_layout.addWidget(self._label(line))
                panel_layout.addWidget(row)
        button = self._admin_action_button("View Change History", "AdminStudioDraftChangesPanel_View_Change_History")
        button.clicked.connect(self._show_admin_review_changes_dialog)
        panel_layout.addWidget(button)
        return panel

    def _admin_dashboard_publishing_readiness_panel(self, summary: Any) -> Any:
        panel, panel_layout = self._admin_panel("AdminStudioPublishingReadinessPanel")
        panel_layout.addWidget(self._label("Publishing Readiness", "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._admin_chip("Blocked" if summary.validation_errors else "Ready", "danger" if summary.validation_errors else "success"))
        checks = self._admin_dashboard_publishing_readiness_checks(summary.validation_errors)
        for label, status in checks:
            row, row_layout = self._surface()
            row.setObjectName("AdminStudioPublishingReadinessRow")
            row_layout.addWidget(self._label(f"{label}: {status}"))
            panel_layout.addWidget(row)
        button = self._admin_action_button("View System Health", "AdminStudioPublishingReadinessPanel_View_System_Health")
        button.clicked.connect(lambda _checked=False: self._select_admin_section_by_key("advanced"))
        panel_layout.addWidget(button)
        return panel

    def _admin_dashboard_publishing_readiness_checks(self, validation_errors: list[str]) -> list[tuple[str, str]]:
        lowered = [str(error).lower() for error in validation_errors]
        folder_issue = any("folder" in error or "path" in error or "school" in error for error in lowered)
        prompt_issue = any("prompt" in error for error in lowered)
        notification_issue = any("notification" in error or "recipient" in error or "rule" in error for error in lowered)
        json_issue = any("json" in error for error in lowered)
        return [
            ("Folder health", "Needs attention" if folder_issue else "Healthy"),
            ("Prompt validation", "Needs attention" if prompt_issue else "Up to date"),
            ("Notification completeness", "Needs attention" if notification_issue else "Good"),
            ("JSON file health", "Needs attention" if json_issue else "Healthy"),
        ]

    def _admin_dashboard_validation_panel(self, validation_errors: list[str]) -> Any:
        panel, panel_layout = self._admin_panel("AdminStudioValidationReviewPanel")
        panel_layout.addWidget(self._label("Validation Review", "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._admin_chip(f"{len(validation_errors)} Blocking Issues", "danger" if validation_errors else "success"))
        panel_layout.addWidget(self._label("These issues are blocking publishing." if validation_errors else "All current admin settings pass validation."))
        for error in validation_errors[:3]:
            target_key = self._admin_validation_target_key(error)
            row, row_layout = self._surface()
            row.setObjectName("AdminStudioValidationReviewIssueRow")
            row_layout.addWidget(self._label(error, "AdminStudioValidationReviewIssueText"), 1)
            issue = self._admin_action_button(error, "AdminStudioValidationReviewIssue", role="danger")
            issue.setToolTip("Open affected admin section")
            issue.setProperty("adminValidationTarget", target_key)
            issue.clicked.connect(lambda _checked=False, issue_text=error, key=target_key: self._route_admin_validation_issue(issue_text, key))
            row_layout.addWidget(issue)
            panel_layout.addWidget(row)
        button = self._admin_action_button("Review All Issues", "AdminStudioValidationReviewPanel_Review_All_Issues", role="danger" if validation_errors else "secondary")
        button.clicked.connect(lambda _checked=False: self._select_admin_section_by_key("validation"))
        panel_layout.addWidget(button)
        return panel

    def _admin_named_panel(self, object_name: str, title: str, badge: str, body: str, actions: list[str]) -> Any:
        panel, panel_layout = self._admin_panel(object_name)
        panel_layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._admin_chip(badge, "neutral"))
        panel_layout.addWidget(self._label(body))
        for action in actions:
            button = self._admin_action_button(action, f"{object_name}_{self._admin_object_suffix(action)}")
            target_key = self._admin_dashboard_action_target(action)
            if target_key:
                button.clicked.connect(lambda _checked=False, key=target_key, action_text=action: self._run_admin_dashboard_action(action_text, key))
            panel_layout.addWidget(button)
        return panel

    def _admin_dashboard_action_target(self, action: str) -> str:
        return {
            "Review All Issues": "validation",
            "View Change History": "dashboard",
            "View System Health": "advanced",
            "Create / Modify Notification Template": "notifications",
            "Browse School Folders": "templates",
            "Open Prompt Template Editor": "prompts",
            "View Validation Rules": "validation",
            "View All Settings": "advanced",
        }.get(str(action or "").strip(), "")

    def _run_admin_dashboard_action(self, action: str, target_key: str) -> None:
        self._select_admin_section_by_key(target_key)
        if str(action or "").strip() == "Create / Modify Notification Template" and bool(getattr(self, "admin_edit_mode", False)):
            self._create_admin_notification_rule_editor()

    def _select_admin_section_by_key(self, key: str) -> None:
        for row in range(self.admin_section_list.count()):
            if self.admin_section_list.item(row).data(self.QtCore.Qt.ItemDataRole.UserRole) == key:
                self.admin_section_list.setCurrentRow(row)
                return

    def _admin_questions_flow_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        flow_panel, flow_layout = self._surface()
        flow_layout.addWidget(self._label("Select Track", "AdminStudioConceptTitle"))
        track_row = self.QtWidgets.QHBoxLayout()
        track_grid = self.QtWidgets.QGridLayout()
        self.admin_question_track_buttons = {}
        self.admin_question_first_by_track = {}
        flows = self._admin_question_flows()
        tab_columns = len(flows) if len(flows) <= 4 else (len(flows) + 1) // 2
        for index, flow in enumerate(flows):
            button = self.QtWidgets.QPushButton(flow.label)
            button.setObjectName(f"AdminStudioTrackTab_{self._admin_object_suffix(flow.track_key)}")
            button.setCheckable(True)
            button.setProperty("adminTrackKey", flow.track_key)
            button.setProperty("adminTrackTabRow", index // max(tab_columns, 1))
            self._make_button_readable(button)
            button.clicked.connect(lambda _checked=False, track_key=flow.track_key: self._select_admin_questions_track(track_key))
            self.admin_question_track_buttons[flow.track_key] = button
            track_grid.addWidget(button, index // max(tab_columns, 1), index % max(tab_columns, 1))
        track_row.addLayout(track_grid, 1)
        add_track = self.QtWidgets.QPushButton("+ Add Track")
        add_track.setObjectName("AdminStudioAddTrackButton")
        add_track.setProperty("adminRequiresEdit", True)
        add_track.setEnabled(False)
        add_track.clicked.connect(self._show_admin_track_dialog)
        track_row.addWidget(add_track)
        flow_layout.addLayout(track_row)

        feature_row = self.QtWidgets.QHBoxLayout()
        for text in ("65 Question cards", "Drag & drop", "Type chips", "Right edit panel"):
            feature_row.addWidget(self._label(text, "AdminStudioChip"))
        feature_row.addStretch(1)
        flow_layout.addLayout(feature_row)

        self.admin_question_flow_stack = self.QtWidgets.QStackedWidget()
        self.admin_question_flow_stack.setObjectName("AdminStudioQuestionFlowStack")
        for flow in flows:
            page = self.QtWidgets.QWidget()
            page.setProperty("adminTrackKey", flow.track_key)
            page_layout = self.QtWidgets.QVBoxLayout(page)
            first_question: tuple[str, int, FlowQuestion] | None = None
            sections = self._admin_question_sections(flow.items)
            for section_name, items in sections:
                if not items:
                    continue
                section, section_layout = self._surface()
                section.setObjectName("AdminStudioQuestionFlowSection")
                section_layout.addWidget(self._label(f"{section_name}    {len(items)} question{'s' if len(items) != 1 else ''}", "AdminStudioConceptTitle"))
                for order, item in items:
                    if first_question is None:
                        first_question = (flow.track_key, order, item)
                    card, card_layout = self._surface()
                    card.setObjectName("AdminStudioQuestionCard")
                    row = self.QtWidgets.QHBoxLayout()
                    row.addWidget(self._label("⋮⋮"))
                    row.addWidget(self._label(str(order), "AdminStudioChip"))
                    button = self.QtWidgets.QPushButton(item.question_id)
                    button.setObjectName(f"AdminStudioQuestionCardButton_{self._admin_object_suffix(item.question_id)}")
                    button.clicked.connect(
                        lambda _checked=False, track_key=flow.track_key, item_order=order, question=item: self._select_admin_question_card(track_key, item_order, question)
                    )
                    row.addWidget(button)
                    row.addWidget(self._label(item.kind, "AdminStudioChip"))
                    prompt = self._label(item.prompt, f"AdminStudioQuestionPrompt_{self._admin_object_suffix(item.question_id)}")
                    prompt.setMinimumWidth(260)
                    prompt.setSizePolicy(self.QtWidgets.QSizePolicy.Policy.Expanding, self.QtWidgets.QSizePolicy.Policy.Preferred)
                    row.addWidget(prompt, 1)
                    move_up = self.QtWidgets.QPushButton("↑")
                    move_up.setObjectName(f"AdminStudioQuestionMoveUp_{self._admin_object_suffix(item.question_id)}")
                    move_up.setToolTip("Move question up")
                    move_up.setMaximumWidth(44)
                    move_up.setProperty("adminRequiresEdit", True)
                    move_up.setEnabled(False)
                    move_up.clicked.connect(
                        lambda _checked=False, track_key=flow.track_key, item_order=order: self._move_admin_question(track_key, item_order - 1, item_order - 2)
                    )
                    row.addWidget(move_up)
                    move_down = self.QtWidgets.QPushButton("↓")
                    move_down.setObjectName(f"AdminStudioQuestionMoveDown_{self._admin_object_suffix(item.question_id)}")
                    move_down.setToolTip("Move question down")
                    move_down.setMaximumWidth(44)
                    move_down.setProperty("adminRequiresEdit", True)
                    move_down.setEnabled(False)
                    move_down.clicked.connect(
                        lambda _checked=False, track_key=flow.track_key, item_order=order: self._move_admin_question(track_key, item_order - 1, item_order)
                    )
                    row.addWidget(move_down)
                    duplicate = self.QtWidgets.QPushButton("⧉")
                    duplicate.setObjectName(f"AdminStudioQuestionDuplicate_{self._admin_object_suffix(item.question_id)}")
                    duplicate.setToolTip("Duplicate question")
                    duplicate.setMaximumWidth(44)
                    duplicate.setProperty("adminRequiresEdit", True)
                    duplicate.setEnabled(False)
                    duplicate.clicked.connect(
                        lambda _checked=False, track_key=flow.track_key, question=item: self._duplicate_admin_question(track_key, question.kind, question.question_id)
                    )
                    row.addWidget(duplicate)
                    more = self.QtWidgets.QPushButton("⋯")
                    more.setObjectName(f"AdminStudioQuestionMore_{self._admin_object_suffix(item.question_id)}")
                    more.setToolTip("More question actions")
                    more.setMaximumWidth(44)
                    row.addWidget(more)
                    card_layout.addLayout(row)
                    section_layout.addWidget(card)
                page_layout.addWidget(section)
            page_layout.addStretch(1)
            self.admin_question_flow_stack.addWidget(page)
            if first_question is not None:
                self.admin_question_first_by_track[flow.track_key] = first_question
        flow_layout.addWidget(self.admin_question_flow_stack, 1)
        add_question = self.QtWidgets.QPushButton("+ Add Question")
        add_question.setObjectName("AdminStudioAddQuestionDropZone")
        add_question.setProperty("adminRequiresEdit", True)
        add_question.setEnabled(False)
        add_question.clicked.connect(self._start_admin_new_question)
        add_question.setStyleSheet("border: 1px dashed #93c5fd; color: #2563eb; padding: 12px;")
        flow_layout.addWidget(add_question)
        layout.addWidget(flow_panel, 2)
        layout.addWidget(self._admin_question_edit_drawer(), 1)
        if flows:
            preferred_track = str(getattr(self, "admin_current_question_track_key", "") or "").strip()
            selected_track = preferred_track if any(flow.track_key == preferred_track for flow in flows) else flows[0].track_key
            self._select_admin_questions_track(selected_track)
        return group

    def _admin_question_flows(self) -> list[TrackFlow]:
        loader = _InMemoryRubricLoader(self.admin_draft.rubric)
        store = _InMemoryQuestionOverridesStore(self.admin_draft.overrides)
        tracks = self.admin_draft.rubric.get("tracks", {}) or {}
        disqualifiers = [str(item).strip() for item in self.admin_draft.rubric.get("absolute_disqualifiers", []) if str(item).strip()]
        flows: list[TrackFlow] = []
        for track_key, value in tracks.items():
            if not isinstance(value, dict):
                continue
            flows.append(
                _build_track_flow(
                    loader=loader,
                    store=store,
                    track_key=str(track_key),
                    track_label=str(value.get("label", track_key)),
                    disqualifiers=disqualifiers,
                )
            )
        return flows

    def _refresh_admin_questions_flow_cards(self) -> None:
        container = getattr(self, "admin_questions_container", None)
        current = getattr(self, "admin_questions_flow_widget", None)
        if container is None or current is None:
            return
        layout = container.layout()
        if layout is None:
            return
        index = layout.indexOf(current)
        if index < 0:
            return
        layout.removeWidget(current)
        current.setParent(None)
        current.deleteLater()
        replacement = self._admin_questions_flow_cards()
        self.admin_questions_flow_widget = replacement
        layout.insertWidget(index, replacement, 2)
        if self.admin_edit_mode:
            self._set_admin_editing_enabled(True)

    def _select_admin_questions_track(self, track_key: str) -> None:
        clean_key = str(track_key or "").strip()
        self.admin_current_question_track_key = clean_key
        buttons = getattr(self, "admin_question_track_buttons", {})
        for key, button in buttons.items():
            button.setChecked(key == clean_key)
        stack = getattr(self, "admin_question_flow_stack", None)
        if stack is not None:
            for index in range(stack.count()):
                if stack.widget(index).property("adminTrackKey") == clean_key:
                    stack.setCurrentIndex(index)
                    break
        first_question = getattr(self, "admin_question_first_by_track", {}).get(clean_key)
        if first_question is not None and hasattr(self, "admin_question_drawer_id"):
            self._select_admin_question_card(*first_question)

    def _admin_question_sections(self, items: list[FlowQuestion]) -> list[tuple[str, list[tuple[int, FlowQuestion]]]]:
        opening: list[tuple[int, FlowQuestion]] = []
        qualification: list[tuple[int, FlowQuestion]] = []
        core_traits: list[tuple[int, FlowQuestion]] = []
        closing: list[tuple[int, FlowQuestion]] = []
        for index, item in enumerate(items, start=1):
            title = item.question_id.lower()
            if "closing" in title or "close" in title:
                closing.append((index, item))
            elif item.kind == "trait":
                core_traits.append((index, item))
            elif not opening:
                opening.append((index, item))
            else:
                qualification.append((index, item))
        return [
            ("Opening", opening),
            ("Qualification", qualification),
            ("Core Traits", core_traits),
            ("Closing", closing),
        ]

    def _move_admin_question(self, track_key: str, from_index: int, to_index: int) -> None:
        try:
            self.admin_draft.move_question(track_key, from_index, to_index)
        except ValueError as exc:
            self.admin_status_label.setText(str(exc))
            return
        self._refresh_admin_questions_flow_cards()
        self._sync_admin_status()

    def _show_admin_track_dialog(self) -> None:
        dialog = self._build_admin_track_dialog()
        self.admin_track_dialog = dialog
        dialog.show()

    def _build_admin_track_dialog(self) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioTrackDialog")
        dialog.setWindowTitle("Create/Edit Track")
        dialog.resize(560, 420)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Create/Edit Track", "SectionTitle"))
        layout.addWidget(self._label("Add a track to the draft flow. Publishing still requires review."))
        form = self.QtWidgets.QFormLayout()
        name = self.QtWidgets.QLineEdit()
        name.setObjectName("AdminStudioTrackName")
        key = self.QtWidgets.QLineEdit()
        key.setObjectName("AdminStudioTrackKey")
        description = self.QtWidgets.QPlainTextEdit()
        description.setObjectName("AdminStudioTrackDescription")
        description.setMinimumHeight(110)
        active = self.QtWidgets.QCheckBox("Active")
        active.setObjectName("AdminStudioTrackActive")
        active.setChecked(True)
        name.textChanged.connect(lambda text: key.setText(self._suggest_admin_track_key(text)) if not key.text().strip() else None)
        form.addRow("Track Name", name)
        form.addRow("Track Key", key)
        form.addRow("Description", description)
        form.addRow("Status", active)
        layout.addLayout(form)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        actions.addWidget(cancel)
        save = self._primary_button("Save Track")
        save.setObjectName("AdminStudioSaveTrackButton")
        save.clicked.connect(lambda _checked=False: self._save_admin_track_dialog(dialog))
        actions.addWidget(save)
        layout.addLayout(actions)
        return dialog

    def _save_admin_track_dialog(self, dialog: Any) -> None:
        name = dialog.findChild(self.QtWidgets.QLineEdit, "AdminStudioTrackName")
        key = dialog.findChild(self.QtWidgets.QLineEdit, "AdminStudioTrackKey")
        description = dialog.findChild(self.QtWidgets.QPlainTextEdit, "AdminStudioTrackDescription")
        active = dialog.findChild(self.QtWidgets.QCheckBox, "AdminStudioTrackActive")
        try:
            self.admin_draft.add_track(
                key.text() if key is not None else "",
                name.text() if name is not None else "",
                description.toPlainText() if description is not None else "",
                active=bool(active.isChecked()) if active is not None else True,
            )
        except ValueError as exc:
            self.QtWidgets.QMessageBox.warning(dialog, "Create/Edit Track", str(exc))
            return
        self._sync_admin_status()
        dialog.close()

    def _suggest_admin_track_key(self, label: str) -> str:
        lowered = str(label or "").strip().lower()
        chars = [ch if ch.isalnum() else "_" for ch in lowered]
        return "_".join(part for part in "".join(chars).split("_") if part)

    def _admin_question_edit_drawer(self) -> Any:
        drawer, layout = self._surface()
        drawer.setObjectName("AdminStudioQuestionEditDrawer")
        layout.addWidget(self._label("Edit Question", "AdminStudioConceptTitle"))
        layout.addWidget(self._label("Currently editing", "AdminStudioChip"))
        form = self.QtWidgets.QFormLayout()
        self.admin_question_drawer_number = self._label("", "AdminStudioQuestionDrawerNumber")
        self.admin_question_drawer_id = self._label("", "AdminStudioQuestionDrawerId")
        self.admin_question_drawer_new_id = self.QtWidgets.QLineEdit()
        self.admin_question_drawer_new_id.setObjectName("AdminStudioQuestionDrawerNewId")
        self.admin_question_drawer_new_id.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_new_label = self.QtWidgets.QLineEdit()
        self.admin_question_drawer_new_label.setObjectName("AdminStudioQuestionDrawerNewLabel")
        self.admin_question_drawer_new_label.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_new_section = self.QtWidgets.QComboBox()
        self.admin_question_drawer_new_section.setObjectName("AdminStudioQuestionDrawerNewSection")
        self.admin_question_drawer_new_section.addItems(["Opening", "Qualification", "Core Traits", "Closing"])
        self.admin_question_drawer_new_section.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_new_position = self.QtWidgets.QSpinBox()
        self.admin_question_drawer_new_position.setObjectName("AdminStudioQuestionDrawerNewPosition")
        self.admin_question_drawer_new_position.setRange(1, 200)
        self.admin_question_drawer_new_position.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_text = self.QtWidgets.QPlainTextEdit()
        self.admin_question_drawer_text.setObjectName("AdminStudioQuestionDrawerText")
        self.admin_question_drawer_text.setMinimumHeight(140)
        self.admin_question_drawer_text.setEnabled(False)
        self.admin_question_drawer_text.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_text_counter = self._label("0 / 1000", "AdminStudioQuestionDrawerTextCounter")
        self.admin_question_drawer_text.textChanged.connect(self._sync_admin_question_drawer_counters)
        self.admin_question_drawer_type = self.QtWidgets.QComboBox()
        self.admin_question_drawer_type.setObjectName("AdminStudioQuestionDrawerType")
        self.admin_question_drawer_type.addItems(["qualification", "custom", "trait", "practical", "follow-up", "closing"])
        self.admin_question_drawer_type.setEnabled(False)
        self.admin_question_drawer_type.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_linked_trait = self.QtWidgets.QComboBox()
        self.admin_question_drawer_linked_trait.setObjectName("AdminStudioQuestionDrawerLinkedTrait")
        self.admin_question_drawer_linked_trait.addItem("Select a trait (optional)", "")
        for trait in self.admin_draft.rubric.get("traits", []) or []:
            if not isinstance(trait, dict):
                continue
            trait_id = str(trait.get("id", "")).strip()
            trait_name = str(trait.get("name", trait_id)).strip()
            if trait_id:
                self.admin_question_drawer_linked_trait.addItem(trait_name or trait_id, trait_id)
        self.admin_question_drawer_linked_trait.setEnabled(False)
        self.admin_question_drawer_linked_trait.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_required = self.QtWidgets.QCheckBox("Required")
        self.admin_question_drawer_required.setObjectName("AdminStudioQuestionDrawerRequired")
        self.admin_question_drawer_required.setChecked(True)
        self.admin_question_drawer_required.setEnabled(False)
        self.admin_question_drawer_required.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_score = self.QtWidgets.QCheckBox("Score this question")
        self.admin_question_drawer_score.setObjectName("AdminStudioQuestionDrawerScore")
        self.admin_question_drawer_score.setChecked(True)
        self.admin_question_drawer_score.setEnabled(False)
        self.admin_question_drawer_score.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_scoring_weight = self.QtWidgets.QComboBox()
        self.admin_question_drawer_scoring_weight.setObjectName("AdminStudioQuestionDrawerScoringWeight")
        self.admin_question_drawer_scoring_weight.addItems(["Standard (1x)", "Higher", "Lower", "Custom"])
        self.admin_question_drawer_scoring_weight.setEnabled(False)
        self.admin_question_drawer_scoring_weight.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_flag_weak = self.QtWidgets.QCheckBox("Flag for review")
        self.admin_question_drawer_flag_weak.setObjectName("AdminStudioQuestionDrawerFlagWeak")
        self.admin_question_drawer_flag_weak.setChecked(False)
        self.admin_question_drawer_flag_weak.setEnabled(False)
        self.admin_question_drawer_flag_weak.setProperty("adminQuestionDrawerEdit", True)
        form.addRow("Question #", self.admin_question_drawer_number)
        form.addRow("Question ID", self.admin_question_drawer_id)
        form.addRow("New Question ID", self.admin_question_drawer_new_id)
        form.addRow("Label", self.admin_question_drawer_new_label)
        form.addRow("Section", self.admin_question_drawer_new_section)
        form.addRow("Position", self.admin_question_drawer_new_position)
        form.addRow("Question Text", self.admin_question_drawer_text)
        form.addRow("", self.admin_question_drawer_text_counter)
        form.addRow("Type", self.admin_question_drawer_type)
        form.addRow("Linked Trait", self.admin_question_drawer_linked_trait)
        form.addRow("Section", self._label("Core Traits"))
        form.addRow("Required", self.admin_question_drawer_required)
        form.addRow("Score This Question", self.admin_question_drawer_score)
        form.addRow("Scoring Weight", self.admin_question_drawer_scoring_weight)
        form.addRow("Flag Weak Response", self.admin_question_drawer_flag_weak)
        notes = self.QtWidgets.QPlainTextEdit("Look for empathy, validation, and child-centered responses.")
        notes.setObjectName("AdminStudioQuestionDrawerNotes")
        notes.setMinimumHeight(100)
        notes.setEnabled(False)
        notes.setProperty("adminQuestionDrawerEdit", True)
        self.admin_question_drawer_notes = notes
        self.admin_question_drawer_notes_counter = self._label("0 / 500", "AdminStudioQuestionDrawerNotesCounter")
        notes.textChanged.connect(self._sync_admin_question_drawer_counters)
        form.addRow("Internal Notes", notes)
        form.addRow("", self.admin_question_drawer_notes_counter)
        layout.addLayout(form)
        actions = self.QtWidgets.QHBoxLayout()
        delete_button = self.QtWidgets.QPushButton("Delete Question")
        delete_button.setObjectName("AdminStudioQuestionDrawerDelete")
        delete_button.setProperty("adminRequiresEdit", True)
        delete_button.setEnabled(False)
        delete_button.clicked.connect(self._delete_admin_question_drawer)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        actions.addWidget(self.QtWidgets.QPushButton("Cancel"))
        self.admin_question_drawer_save = self._primary_button("Save Changes")
        self.admin_question_drawer_save.setObjectName("AdminStudioQuestionDrawerSave")
        self.admin_question_drawer_save.setProperty("adminRequiresEdit", True)
        self.admin_question_drawer_save.setEnabled(False)
        self.admin_question_drawer_save.clicked.connect(self._save_admin_question_drawer)
        actions.addWidget(self.admin_question_drawer_save)
        layout.addLayout(actions)
        self._set_admin_new_question_fields_visible(False)
        return drawer

    def _set_admin_new_question_fields_visible(self, visible: bool) -> None:
        for widget in (
            self.admin_question_drawer_new_id,
            self.admin_question_drawer_new_label,
            self.admin_question_drawer_new_section,
            self.admin_question_drawer_new_position,
        ):
            widget.setVisible(visible)

    def _select_admin_question_card(self, track_key: str, order: int, question: FlowQuestion) -> None:
        self.admin_selected_question = (track_key, question.kind, question.question_id)
        self._set_admin_new_question_fields_visible(False)
        self.admin_question_drawer_number.setText(str(order))
        self.admin_question_drawer_id.setText(question.question_id)
        self.admin_question_drawer_text.setPlainText(question.prompt)
        type_index = self.admin_question_drawer_type.findText(question.kind)
        if type_index >= 0:
            self.admin_question_drawer_type.setCurrentIndex(type_index)
        linked_index = self.admin_question_drawer_linked_trait.findData(question.question_id if question.kind == "trait" else "")
        self.admin_question_drawer_linked_trait.setCurrentIndex(max(linked_index, 0))
        self.admin_question_drawer_required.setChecked(True)
        self.admin_question_drawer_score.setChecked(True)
        self.admin_question_drawer_scoring_weight.setCurrentIndex(0)
        self.admin_question_drawer_flag_weak.setChecked(False)
        self._sync_admin_question_drawer_counters()

    def _start_admin_new_question(self) -> None:
        track_key = str(getattr(self, "admin_current_question_track_key", "") or "").strip() or next(iter(self.model.flows), "")
        custom_questions = self.admin_draft.overrides.get("custom_questions", {}).get(track_key, [])
        position = len(custom_questions or []) + 1
        self.admin_selected_question = (track_key, "new_custom", "")
        self._set_admin_new_question_fields_visible(True)
        self.admin_question_drawer_number.setText(str(position))
        self.admin_question_drawer_id.setText("New question")
        self.admin_question_drawer_new_id.clear()
        self.admin_question_drawer_new_label.clear()
        self.admin_question_drawer_new_section.setCurrentText("Qualification")
        self.admin_question_drawer_new_position.setValue(position)
        self.admin_question_drawer_text.clear()
        type_index = self.admin_question_drawer_type.findText("custom")
        if type_index >= 0:
            self.admin_question_drawer_type.setCurrentIndex(type_index)
        self.admin_question_drawer_linked_trait.setCurrentIndex(0)
        self.admin_question_drawer_required.setChecked(True)
        self.admin_question_drawer_score.setChecked(True)
        self.admin_question_drawer_scoring_weight.setCurrentIndex(0)
        self.admin_question_drawer_flag_weak.setChecked(False)
        self._sync_admin_question_drawer_counters()

    def _sync_admin_question_drawer_counters(self) -> None:
        if hasattr(self, "admin_question_drawer_text_counter"):
            self.admin_question_drawer_text_counter.setText(f"{len(self.admin_question_drawer_text.toPlainText())} / 1000")
        if hasattr(self, "admin_question_drawer_notes_counter"):
            self.admin_question_drawer_notes_counter.setText(f"{len(self.admin_question_drawer_notes.toPlainText())} / 500")

    def _save_admin_question_drawer(self) -> None:
        selected = getattr(self, "admin_selected_question", None)
        if selected is None:
            return
        track_key, question_type, question_id = selected
        text = self.admin_question_drawer_text.toPlainText().strip()
        if question_type == "new_custom":
            self.admin_draft.add_custom_question(
                track_key,
                self.admin_question_drawer_new_id.text().strip(),
                self.admin_question_drawer_new_label.text().strip(),
                text,
                section=self.admin_question_drawer_new_section.currentText().strip(),
                position=self.admin_question_drawer_new_position.value(),
            )
            questions = self._admin_tables.get("questions")
            if questions is not None:
                row_index = questions.rowCount()
                questions.insertRow(row_index)
                values = [
                    track_key,
                    self.model.flows.get(track_key).label if track_key in self.model.flows else track_key,
                    self.admin_question_drawer_new_id.text().strip(),
                    "custom",
                    text,
                ]
                for column, value in enumerate(values):
                    item = self.QtWidgets.QTableWidgetItem(value)
                    item.setData(self.QtCore.Qt.ItemDataRole.UserRole, value)
                    questions.setItem(row_index, column, item)
            self._refresh_admin_questions_flow_cards()
            self._sync_admin_status()
            return
        questions = self._admin_tables.get("questions")
        if questions is not None:
            for row_index in range(questions.rowCount()):
                if (
                    questions.item(row_index, 0).text().strip() == track_key
                    and questions.item(row_index, 2).text().strip() == question_id
                    and questions.item(row_index, 3).text().strip() == question_type
                ):
                    questions.item(row_index, 4).setText(text)
                    break
        self.admin_draft.update_question_text(track_key, question_type, question_id, text)
        self._sync_admin_status()

    def _duplicate_admin_question(self, track_key: str, question_type: str, question_id: str) -> None:
        try:
            new_id = self.admin_draft.duplicate_question(track_key, question_type, question_id)
        except ValueError as exc:
            self.admin_status_label.setText(str(exc))
            return
        questions = self._admin_tables.get("questions")
        custom_questions = self.admin_draft.overrides.get("custom_questions", {}).get(track_key, [])
        created = next((item for item in custom_questions if isinstance(item, dict) and str(item.get("id", "")) == new_id), None)
        if questions is not None and isinstance(created, dict):
            row_index = questions.rowCount()
            questions.insertRow(row_index)
            values = [
                track_key,
                self.model.flows.get(track_key).label if track_key in self.model.flows else track_key,
                new_id,
                "custom",
                str(created.get("text", "")),
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, value)
                questions.setItem(row_index, column, item)
        self._refresh_admin_questions_flow_cards()
        self._sync_admin_status()

    def _delete_admin_question_drawer(self) -> None:
        selected = getattr(self, "admin_selected_question", None)
        if selected is None:
            return
        track_key, question_type, question_id = selected
        try:
            self.admin_draft.delete_question(track_key, question_type, question_id)
        except ValueError as exc:
            self.admin_status_label.setText(str(exc))
            return
        questions = self._admin_tables.get("questions")
        if questions is not None:
            for row_index in range(questions.rowCount() - 1, -1, -1):
                if (
                    questions.item(row_index, 0).text().strip() == track_key
                    and questions.item(row_index, 2).text().strip() == question_id
                    and questions.item(row_index, 3).text().strip() == question_type
                ):
                    questions.removeRow(row_index)
                    break
        self.admin_selected_question = None
        self.admin_question_drawer_number.clear()
        self.admin_question_drawer_id.setText("Select a question")
        self.admin_question_drawer_text.clear()
        self._sync_admin_question_drawer_counters()
        self._refresh_admin_questions_flow_cards()
        self._sync_admin_status()

    def _admin_object_suffix(self, value: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in str(value))

    def _admin_rubric_trait_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        card_panel, card_layout = self._surface()
        card_panel.setObjectName("AdminStudioRubricTraitCardsPanel")
        card_panel.setProperty("adminRubricViewMode", "grid")
        summary = self.admin_studio.summary(self.admin_draft)
        summary_row = self.QtWidgets.QHBoxLayout()
        summary_row.addWidget(self._label(f"Total Traits\n{len(self.admin_draft.rubric.get('traits', []) or [])}", "AdminStudioChip"))
        critical_count = sum(1 for trait in self.admin_draft.rubric.get("traits", []) or [] if isinstance(trait, dict) and str(trait.get("priority", "")).lower() == "critical")
        summary_row.addWidget(self._label(f"Critical Traits\n{critical_count}", "AdminStudioChip"))
        summary_row.addWidget(self._label(f"Questions\n{summary.question_count}", "AdminStudioChip"))
        summary_row.addStretch(1)
        card_layout.addLayout(summary_row)
        filter_row = self.QtWidgets.QHBoxLayout()
        self.admin_rubric_search = self.QtWidgets.QLineEdit()
        self.admin_rubric_search.setObjectName("AdminStudioRubricSearchInput")
        self.admin_rubric_search.setPlaceholderText("Search traits...")
        self.admin_rubric_search.textChanged.connect(self._filter_admin_rubric_traits)
        filter_row.addWidget(self.admin_rubric_search, 2)
        self.admin_rubric_priority_filter = self.QtWidgets.QComboBox()
        self.admin_rubric_priority_filter.setObjectName("AdminStudioRubricPriorityFilter")
        self.admin_rubric_priority_filter.addItems(["All priorities", "Critical", "High", "Medium", "Low"])
        self.admin_rubric_priority_filter.currentTextChanged.connect(self._filter_admin_rubric_traits)
        filter_row.addWidget(self.admin_rubric_priority_filter)
        self.admin_rubric_weight_filter = self.QtWidgets.QComboBox()
        self.admin_rubric_weight_filter.setObjectName("AdminStudioRubricWeightFilter")
        self.admin_rubric_weight_filter.addItems(["All weights", "Weight 3+", "Weight 2", "Weight 1"])
        self.admin_rubric_weight_filter.currentTextChanged.connect(self._filter_admin_rubric_traits)
        filter_row.addWidget(self.admin_rubric_weight_filter)
        self.admin_rubric_linked_question_filter = self.QtWidgets.QComboBox()
        self.admin_rubric_linked_question_filter.setObjectName("AdminStudioRubricLinkedQuestionFilter")
        self.admin_rubric_linked_question_filter.addItems(["All linked states", "Has linked question", "Missing linked question"])
        self.admin_rubric_linked_question_filter.currentTextChanged.connect(self._filter_admin_rubric_traits)
        filter_row.addWidget(self.admin_rubric_linked_question_filter)
        for chip in ("Score descriptors", "More filters"):
            filter_row.addWidget(self._label(chip, "AdminStudioChip"))
        self.admin_rubric_view_toggle = self.QtWidgets.QComboBox()
        self.admin_rubric_view_toggle.setObjectName("AdminStudioRubricViewToggle")
        self.admin_rubric_view_toggle.addItems(["Grid view", "List view"])
        self.admin_rubric_view_toggle.currentTextChanged.connect(
            lambda text, panel=card_panel: panel.setProperty("adminRubricViewMode", "list" if str(text).startswith("List") else "grid")
        )
        filter_row.addWidget(self.admin_rubric_view_toggle)
        card_layout.addLayout(filter_row)
        card_layout.addWidget(self._label("Traits for: Infant/Toddler & Preschool", "AdminStudioConceptTitle"))
        self.admin_rubric_trait_cards = []
        for trait in self.admin_draft.rubric.get("traits", []) or []:
            if not isinstance(trait, dict):
                continue
            card, layout_inner = self._surface()
            card.setObjectName("AdminStudioTraitCard")
            trait_id = str(trait.get("id", ""))
            card.setObjectName(f"AdminStudioTraitCard_{self._admin_object_suffix(trait_id)}")
            card.setProperty("adminRubricPriority", str(trait.get("priority", "")))
            card.setProperty("adminRubricFilterMatch", True)
            button = self.QtWidgets.QPushButton(str(trait.get("name", "")))
            button.setObjectName(f"AdminStudioTraitCardButton_{self._admin_object_suffix(trait_id)}")
            self._make_button_readable(button)
            button.clicked.connect(lambda _checked=False, selected_trait=dict(trait): self._select_admin_rubric_trait(selected_trait))
            layout_inner.addWidget(button)
            layout_inner.addWidget(self._label(f"{trait.get('id', '')}        Weight {trait.get('weight', '')}"))
            layout_inner.addWidget(self._label(str(trait.get("priority", "Priority")), "AdminStudioChip"))
            layout_inner.addWidget(self._label(str(trait.get("primary_question", ""))))
            card_layout.addWidget(card)
            self.admin_rubric_trait_cards.append((card, button, dict(trait)))
        add_trait = self.QtWidgets.QPushButton("+ Add Trait")
        add_trait.setObjectName("AdminStudioAddTraitButton")
        add_trait.setProperty("adminRequiresEdit", True)
        add_trait.setEnabled(False)
        card_layout.addWidget(add_trait)
        layout.addWidget(card_panel, 2)
        first_trait = next((trait for trait in self.admin_draft.rubric.get("traits", []) or [] if isinstance(trait, dict)), {})
        layout.addWidget(self._admin_rubric_trait_editor(first_trait), 4)
        if first_trait:
            self._select_admin_rubric_trait(first_trait)
        return group

    def _filter_admin_rubric_traits(self, *_args: Any) -> None:
        query = str(getattr(self, "admin_rubric_search", None).text() if hasattr(self, "admin_rubric_search") else "").strip().lower()
        priority_filter = str(getattr(self, "admin_rubric_priority_filter", None).currentText() if hasattr(self, "admin_rubric_priority_filter") else "All priorities")
        weight_filter = str(getattr(self, "admin_rubric_weight_filter", None).currentText() if hasattr(self, "admin_rubric_weight_filter") else "All weights")
        linked_filter = str(getattr(self, "admin_rubric_linked_question_filter", None).currentText() if hasattr(self, "admin_rubric_linked_question_filter") else "All linked states")
        for card, button, trait in getattr(self, "admin_rubric_trait_cards", []):
            haystack = " ".join(
                [
                    str(trait.get("id", "")),
                    str(trait.get("name", "")),
                    str(trait.get("priority", "")),
                    str(trait.get("primary_question", "")),
                    str(trait.get("description", "")),
                ]
            ).lower()
            query_match = not query or query in haystack
            priority_match = priority_filter == "All priorities" or str(trait.get("priority", "")) == priority_filter
            try:
                trait_weight = int(float(str(trait.get("weight", "0") or "0")))
            except ValueError:
                trait_weight = 0
            weight_match = (
                weight_filter == "All weights"
                or (weight_filter == "Weight 3+" and trait_weight >= 3)
                or (weight_filter == "Weight 2" and trait_weight == 2)
                or (weight_filter == "Weight 1" and trait_weight == 1)
            )
            linked_question_text = str(trait.get("primary_question", "")).strip()
            has_linked_question = bool(linked_question_text) and linked_question_text.lower() not in {
                "no linked question configured.",
                "not configured",
            }
            linked_match = (
                linked_filter == "All linked states"
                or (linked_filter == "Has linked question" and has_linked_question)
                or (linked_filter == "Missing linked question" and not has_linked_question)
            )
            matches = query_match and priority_match and weight_match and linked_match
            card.setVisible(matches)
            card.setProperty("adminRubricFilterMatch", matches)
            button.setProperty("adminRubricFilterMatch", matches)

    def _admin_rubric_trait_editor(self, trait: dict[str, Any]) -> Any:
        panel, layout = self._surface()
        panel.setObjectName("AdminStudioTraitDetailPanel")
        layout.addWidget(self._label("Edit Rubric Trait", "AdminStudioRubricEditorTitle"))
        tabs = self.QtWidgets.QTabWidget()
        tabs.setObjectName("AdminStudioRubricEditorTabs")
        for title in ("Overview", "Score Descriptors", "Sample Answers", "Publish Rules"):
            tab = self.QtWidgets.QWidget()
            tabs.addTab(tab, title)
        layout.addWidget(tabs)

        main = self.QtWidgets.QHBoxLayout()
        overview, overview_layout = self._surface()
        form = self.QtWidgets.QFormLayout()
        self.admin_rubric_trait_id = self.QtWidgets.QLineEdit()
        self.admin_rubric_trait_id.setObjectName("AdminStudioRubricTraitId")
        self.admin_rubric_trait_id.setEnabled(False)
        self.admin_rubric_trait_name = self.QtWidgets.QLineEdit()
        self.admin_rubric_trait_name.setObjectName("AdminStudioRubricTraitName")
        self.admin_rubric_trait_name.setProperty("adminRubricEdit", True)
        self.admin_rubric_trait_name.setEnabled(False)
        self.admin_rubric_priority = self.QtWidgets.QComboBox()
        self.admin_rubric_priority.setObjectName("AdminStudioRubricPriority")
        self.admin_rubric_priority.addItems(["Critical", "High", "Medium", "Low"])
        self.admin_rubric_priority.setProperty("adminRubricEdit", True)
        self.admin_rubric_priority.setEnabled(False)
        self.admin_rubric_weight = self.QtWidgets.QSpinBox()
        self.admin_rubric_weight.setObjectName("AdminStudioRubricTraitWeight")
        self.admin_rubric_weight.setRange(1, 10)
        self.admin_rubric_weight.setProperty("adminRubricEdit", True)
        self.admin_rubric_weight.setEnabled(False)
        weight_control = self.QtWidgets.QWidget()
        weight_layout = self.QtWidgets.QHBoxLayout(weight_control)
        weight_layout.setContentsMargins(0, 0, 0, 0)
        weight_layout.setSpacing(6)
        weight_minus = self.QtWidgets.QPushButton("-")
        weight_minus.setObjectName("AdminStudioRubricWeightMinus")
        weight_minus.setProperty("adminRequiresEdit", True)
        weight_minus.setEnabled(False)
        weight_minus.clicked.connect(lambda _checked=False: self.admin_rubric_weight.setValue(max(self.admin_rubric_weight.minimum(), self.admin_rubric_weight.value() - 1)))
        weight_plus = self.QtWidgets.QPushButton("+")
        weight_plus.setObjectName("AdminStudioRubricWeightPlus")
        weight_plus.setProperty("adminRequiresEdit", True)
        weight_plus.setEnabled(False)
        weight_plus.clicked.connect(lambda _checked=False: self.admin_rubric_weight.setValue(min(self.admin_rubric_weight.maximum(), self.admin_rubric_weight.value() + 1)))
        weight_layout.addWidget(weight_minus)
        weight_layout.addWidget(self.admin_rubric_weight)
        weight_layout.addWidget(weight_plus)
        self.admin_rubric_primary_question = self.QtWidgets.QPlainTextEdit()
        self.admin_rubric_primary_question.setObjectName("AdminStudioRubricPrimaryQuestion")
        self.admin_rubric_primary_question.setMinimumHeight(90)
        self.admin_rubric_primary_question.setProperty("adminRubricEdit", True)
        self.admin_rubric_primary_question.setEnabled(False)
        form.addRow("Trait ID", self.admin_rubric_trait_id)
        form.addRow("Trait Name", self.admin_rubric_trait_name)
        form.addRow("Priority", self.admin_rubric_priority)
        form.addRow("Weight", weight_control)
        form.addRow("Primary Question", self.admin_rubric_primary_question)
        overview_layout.addLayout(form)
        track_panel, track_layout = self._surface()
        track_panel.setObjectName("AdminStudioRubricApplicableTracksPanel")
        track_layout.addWidget(self._label("Applicable Tracks", "AdminStudioConceptTitle"))
        self.admin_rubric_track_checkboxes = {}
        track_keys = [str(key) for key in (self.admin_draft.rubric.get("tracks", {}) or {}).keys()]
        for track_key in track_keys:
            track_label = str((self.admin_draft.rubric.get("tracks", {}) or {}).get(track_key, {}).get("label", track_key))
            checkbox = self.QtWidgets.QCheckBox(track_label)
            checkbox.setObjectName(f"AdminStudioRubricApplicableTrack_{self._admin_object_suffix(track_key)}")
            checkbox.setProperty("adminRubricTrackKey", track_key)
            checkbox.setProperty("adminRubricEdit", True)
            checkbox.setEnabled(False)
            track_layout.addWidget(checkbox)
            self.admin_rubric_track_checkboxes[track_key] = checkbox
        overview_layout.addWidget(track_panel)
        overview_layout.addWidget(self._label("Score 1 = automatic no hire", "AdminStudioChip"))
        main.addWidget(overview, 3)

        side = self.QtWidgets.QVBoxLayout()
        side.addWidget(self._admin_named_panel(
            "AdminStudioRubricValidationImpactPanel",
            "Validation & Impact",
            "No validation issues",
            "Included in scoring. Applies to configured tracks. High-risk scoring edits require review.",
            ["Review publish rules"],
        ))
        linked_panel, linked_layout = self._surface()
        linked_panel.setObjectName("AdminStudioRubricLinkedQuestionPanel")
        linked_layout.addWidget(self._label("Linked Question", "AdminStudioConceptTitle"))
        linked_layout.addWidget(self._label("Open in Questions & Flow", "AdminStudioChip"))
        self.admin_rubric_linked_question_preview = self._label(str(trait.get("primary_question", "")))
        self.admin_rubric_linked_question_preview.setObjectName("AdminStudioRubricLinkedQuestionPreview")
        linked_layout.addWidget(self.admin_rubric_linked_question_preview)
        linked_open = self.QtWidgets.QPushButton("Open in Questions & Flow")
        linked_open.setObjectName("AdminStudioRubricLinkedQuestionOpen")
        linked_open.clicked.connect(lambda _checked=False: self._open_admin_rubric_linked_question(str(getattr(self, "admin_selected_rubric_trait_id", ""))))
        linked_layout.addWidget(linked_open)
        side.addWidget(linked_panel)
        main.addLayout(side, 1)
        layout.addLayout(main)

        rows = self.QtWidgets.QHBoxLayout()
        descriptors_panel, descriptors_layout = self._surface()
        descriptors_layout.addWidget(self._label("Score Descriptors", "AdminStudioConceptTitle"))
        self.admin_rubric_descriptor_editors = {}
        for score in ("5", "4", "3", "2", "1"):
            row, row_layout = self._surface()
            row.setObjectName("AdminStudioRubricDescriptorRow")
            row_layout.addWidget(self._label(score, "AdminStudioChip"))
            descriptor = self.QtWidgets.QPlainTextEdit()
            descriptor.setObjectName(f"AdminStudioRubricDescriptorText_{score}")
            descriptor.setMinimumHeight(58)
            descriptor.setPlainText(str((trait.get("descriptors", {}) or {}).get(score, "")))
            descriptor.setProperty("adminRubricEdit", True)
            descriptor.setEnabled(False)
            row_layout.addWidget(descriptor, 1)
            self.admin_rubric_descriptor_editors[score] = descriptor
            descriptors_layout.addWidget(row)
        rows.addWidget(descriptors_panel)
        samples_panel, samples_layout = self._surface()
        samples_layout.addWidget(self._label("Sample Answers", "AdminStudioConceptTitle"))
        sample_answers = trait.get("sample_answers", {}) or {}
        self.admin_rubric_sample_answer_editors = {}
        for score in ("5", "4", "3", "2", "1"):
            row, row_layout = self._surface()
            row.setObjectName("AdminStudioRubricSampleAnswerRow")
            row_layout.addWidget(self._label(score, "AdminStudioChip"))
            sample_text = str(sample_answers.get(score, "") or "No sample answer configured.")
            sample_answer = self.QtWidgets.QPlainTextEdit()
            sample_answer.setObjectName(f"AdminStudioRubricSampleAnswerText_{score}")
            sample_answer.setMinimumHeight(58)
            sample_answer.setPlainText(sample_text)
            sample_answer.setProperty("adminRubricEdit", True)
            sample_answer.setEnabled(False)
            row_layout.addWidget(sample_answer, 1)
            self.admin_rubric_sample_answer_editors[score] = sample_answer
            samples_layout.addWidget(row)
        rows.addWidget(samples_panel)
        layout.addLayout(rows)

        publish_panel, publish_layout = self._surface()
        publish_panel.setObjectName("AdminStudioRubricPublishRulesPanel")
        publish_layout.addWidget(self._label("Publish Rules", "AdminStudioConceptTitle"))
        for rule_text in (
            "Automatic no-hire when score is 1 and rule enabled.",
            "Critical trait changes require review before publishing.",
            "Missing linked questions block publishing.",
        ):
            row, row_layout = self._surface()
            row.setObjectName("AdminStudioRubricPublishRuleRow")
            row_layout.addWidget(self._label(rule_text))
            publish_layout.addWidget(row)
        layout.addWidget(publish_panel)

        actions = self.QtWidgets.QHBoxLayout()
        delete_button = self.QtWidgets.QPushButton("Delete Trait")
        delete_button.setObjectName("AdminStudioRubricDeleteTrait")
        delete_button.setProperty("adminRequiresEdit", True)
        delete_button.setEnabled(False)
        delete_button.clicked.connect(self._delete_admin_rubric_trait)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        actions.addWidget(self.QtWidgets.QPushButton("Cancel"))
        duplicate = self.QtWidgets.QPushButton("Duplicate")
        duplicate.setObjectName("AdminStudioRubricDuplicateTrait")
        duplicate.setProperty("adminRequiresEdit", True)
        duplicate.setEnabled(False)
        duplicate.clicked.connect(self._duplicate_admin_rubric_trait)
        actions.addWidget(duplicate)
        self.admin_rubric_save_button = self._primary_button("Save Changes")
        self.admin_rubric_save_button.setObjectName("AdminStudioRubricSaveChanges")
        self.admin_rubric_save_button.setProperty("adminRequiresEdit", True)
        self.admin_rubric_save_button.setEnabled(False)
        self.admin_rubric_save_button.clicked.connect(self._save_admin_rubric_trait_editor)
        actions.addWidget(self.admin_rubric_save_button)
        layout.addLayout(actions)
        return panel

    def _select_admin_rubric_trait(self, trait: dict[str, Any]) -> None:
        self.admin_selected_rubric_trait_id = str(trait.get("id", ""))
        self.admin_rubric_trait_id.setText(self.admin_selected_rubric_trait_id)
        self.admin_rubric_trait_name.setText(str(trait.get("name", "")))
        priority = str(trait.get("priority", "Critical"))
        index = self.admin_rubric_priority.findText(priority)
        self.admin_rubric_priority.setCurrentIndex(index if index >= 0 else 0)
        try:
            self.admin_rubric_weight.setValue(int(float(str(trait.get("weight", "1")))))
        except ValueError:
            self.admin_rubric_weight.setValue(1)
        self.admin_rubric_primary_question.setPlainText(str(trait.get("primary_question", "")))
        if hasattr(self, "admin_rubric_linked_question_preview"):
            self.admin_rubric_linked_question_preview.setText(str(trait.get("primary_question", "")))
        applicable_tracks = {str(track) for track in (trait.get("applicable_tracks", []) or [])}
        check_all = "all" in applicable_tracks
        for track_key, checkbox in getattr(self, "admin_rubric_track_checkboxes", {}).items():
            checkbox.setChecked(check_all or track_key in applicable_tracks)
        descriptors = trait.get("descriptors", {}) or {}
        for score, editor in getattr(self, "admin_rubric_descriptor_editors", {}).items():
            editor.setPlainText(str(descriptors.get(score, "")))
        sample_answers = trait.get("sample_answers", {}) or {}
        for score, editor in getattr(self, "admin_rubric_sample_answer_editors", {}).items():
            editor.setPlainText(str(sample_answers.get(score, "") or "No sample answer configured."))

    def _open_admin_rubric_linked_question(self, trait_id: str) -> None:
        clean_trait_id = str(trait_id or "").strip()
        if not clean_trait_id:
            return
        self._select_admin_section_by_key("questions")
        for flow in self.model.flows.values():
            for order, question in enumerate(flow.items, start=1):
                if question.question_id == clean_trait_id:
                    self._select_admin_question_card(flow.track_key, order, question)
                    return

    def _save_admin_rubric_trait_editor(self) -> None:
        trait_id = str(getattr(self, "admin_selected_rubric_trait_id", "") or "").strip()
        if not trait_id:
            return
        updates = {
            "name": self.admin_rubric_trait_name.text().strip(),
            "priority": self.admin_rubric_priority.currentText().strip(),
            "weight": str(self.admin_rubric_weight.value()),
            "primary_question": self.admin_rubric_primary_question.toPlainText().strip(),
            "descriptors": {
                score: editor.toPlainText().strip()
                for score, editor in getattr(self, "admin_rubric_descriptor_editors", {}).items()
            },
            "applicable_tracks": [
                track_key
                for track_key, checkbox in getattr(self, "admin_rubric_track_checkboxes", {}).items()
                if checkbox.isChecked()
            ],
            "sample_answers": {
                score: editor.toPlainText().strip()
                for score, editor in getattr(self, "admin_rubric_sample_answer_editors", {}).items()
            },
        }
        self.admin_draft.update_trait(trait_id, updates)
        rubrics = self._admin_tables.get("rubrics")
        if rubrics is not None:
            for row_index in range(rubrics.rowCount()):
                if rubrics.item(row_index, 0).text().strip() == trait_id:
                    rubrics.item(row_index, 1).setText(updates["name"])
                    rubrics.item(row_index, 2).setText(updates["priority"])
                    rubrics.item(row_index, 3).setText(updates["weight"])
                    rubrics.item(row_index, 4).setText(updates["primary_question"])
                    break
        self._sync_admin_status()

    def _refresh_admin_rubric_trait_cards(self, selected_trait_id: str = "") -> None:
        layout = getattr(self, "admin_rubrics_layout", None)
        current = getattr(self, "admin_rubrics_trait_widget", None)
        if layout is None or current is None:
            return
        index = layout.indexOf(current)
        if index < 0:
            return
        layout.removeWidget(current)
        current.setParent(None)
        current.deleteLater()
        replacement = self._admin_rubric_trait_cards()
        self.admin_rubrics_trait_widget = replacement
        layout.insertWidget(index, replacement, 2)
        if self.admin_edit_mode:
            self._set_admin_editing_enabled(True)
        if selected_trait_id:
            selected_trait = next(
                (
                    trait
                    for trait in self.admin_draft.rubric.get("traits", []) or []
                    if isinstance(trait, dict) and str(trait.get("id", "")).strip() == selected_trait_id
                ),
                None,
            )
            if selected_trait is not None:
                self._select_admin_rubric_trait(selected_trait)

    def _duplicate_admin_rubric_trait(self) -> None:
        trait_id = str(getattr(self, "admin_selected_rubric_trait_id", "") or "").strip()
        if not trait_id:
            return
        try:
            new_id = self.admin_draft.duplicate_trait(trait_id)
        except ValueError as exc:
            self.admin_status_label.setText(str(exc))
            return
        new_trait = next(
            (
                trait
                for trait in self.admin_draft.rubric.get("traits", []) or []
                if isinstance(trait, dict) and str(trait.get("id", "")).strip() == new_id
            ),
            None,
        )
        rubrics = self._admin_tables.get("rubrics")
        if rubrics is not None and isinstance(new_trait, dict):
            self._admin_syncing_table_edits = True
            try:
                row_index = rubrics.rowCount()
                rubrics.insertRow(row_index)
                values = [
                    str(new_trait.get("id", "")),
                    str(new_trait.get("name", "")),
                    str(new_trait.get("priority", "")),
                    str(new_trait.get("weight", "")),
                    str(new_trait.get("primary_question", "")),
                ]
                for column, value in enumerate(values):
                    rubrics.setItem(row_index, column, self._admin_item(value, editable=False))
            finally:
                self._admin_syncing_table_edits = False
        self._refresh_admin_rubric_trait_cards(new_id)
        self._sync_admin_status()

    def _delete_admin_rubric_trait(self) -> None:
        trait_id = str(getattr(self, "admin_selected_rubric_trait_id", "") or "").strip()
        if not trait_id:
            return
        try:
            self.admin_draft.delete_trait(trait_id)
        except ValueError as exc:
            self.admin_status_label.setText(str(exc))
            return
        rubrics = self._admin_tables.get("rubrics")
        if rubrics is not None:
            self._admin_syncing_table_edits = True
            try:
                for row_index in range(rubrics.rowCount() - 1, -1, -1):
                    if rubrics.item(row_index, 0).text().strip() == trait_id:
                        rubrics.removeRow(row_index)
                        break
            finally:
                self._admin_syncing_table_edits = False
        first_trait = next((trait for trait in self.admin_draft.rubric.get("traits", []) or [] if isinstance(trait, dict)), None)
        self.admin_selected_rubric_trait_id = ""
        self._refresh_admin_rubric_trait_cards(str(first_trait.get("id", "")) if isinstance(first_trait, dict) else "")
        self._sync_admin_status()

    def _admin_signal_hint_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        list_panel, list_layout = self._surface()
        list_panel.setObjectName("AdminStudioSignalHintListPanel")
        traits = [trait for trait in self.admin_draft.rubric.get("traits", []) or [] if isinstance(trait, dict)]
        summary_panel, summary = self._surface()
        summary_panel.setObjectName("AdminStudioSignalSummaryStrip")
        for chip in (f"{len(traits)} hint groups", "Searchable", "Grouped by trait", "Read-only reference", "Scoring context"):
            summary.addWidget(self._label(chip, "AdminStudioChip"))
        summary.addStretch(1)
        list_layout.addWidget(summary_panel)
        self.admin_signal_search = self.QtWidgets.QLineEdit()
        self.admin_signal_search.setObjectName("AdminStudioSignalSearchInput")
        self.admin_signal_search.setPlaceholderText("Search signal hints by trait, keyword, or phrase...")
        list_layout.addWidget(self.admin_signal_search)
        category_row = self.QtWidgets.QHBoxLayout()
        self.admin_signal_category_buttons = []
        all_category = self.QtWidgets.QPushButton("All")
        all_category.setObjectName("AdminStudioSignalCategory_All")
        all_category.setProperty("adminSignalCategory", "all")
        all_category.setProperty("adminSignalCategorySelected", True)
        all_category.clicked.connect(lambda _checked=False: self._set_admin_signal_category("all"))
        category_row.addWidget(all_category)
        self.admin_signal_category_buttons.append(all_category)
        fixed_categories = ["Empathy", "Regulation", "Accountability", "Guidance", "Teamwork", "Communication", "Structure", "Other"]
        used_categories = {"all"}
        for category in fixed_categories:
            button = self.QtWidgets.QPushButton(category)
            button.setObjectName(f"AdminStudioSignalCategory_{self._admin_object_suffix(category)}")
            button.setProperty("adminSignalCategory", category)
            button.setProperty("adminSignalCategorySelected", False)
            button.clicked.connect(lambda _checked=False, selected_category=category: self._set_admin_signal_category(selected_category))
            category_row.addWidget(button)
            self.admin_signal_category_buttons.append(button)
            used_categories.add(category.lower())
        for trait in traits:
            trait_name = str(trait.get("name", "")).strip()
            if not trait_name or trait_name.lower() in used_categories:
                continue
            button = self.QtWidgets.QPushButton(trait_name)
            button.setObjectName(f"AdminStudioSignalCategory_{self._admin_object_suffix(trait_name)}")
            button.setProperty("adminSignalCategory", trait_name)
            button.setProperty("adminSignalCategorySelected", False)
            button.clicked.connect(lambda _checked=False, category=trait_name: self._set_admin_signal_category(category))
            category_row.addWidget(button)
            self.admin_signal_category_buttons.append(button)
        category_row.addStretch(1)
        list_layout.addLayout(category_row)
        list_layout.addWidget(self._label(f"Hint Groups ({len(traits)})", "AdminStudioConceptTitle"))
        self.admin_signal_hint_cards: list[tuple[Any, Any, dict[str, Any]]] = []
        for trait in traits:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioSignalHintGroup")
            button = self.QtWidgets.QPushButton(str(trait.get("name", "")))
            button.setObjectName(f"AdminStudioSignalHintButton_{self._admin_object_suffix(str(trait.get('id', '')))}")
            self._make_button_readable(button)
            button.clicked.connect(lambda _checked=False, selected_trait=dict(trait): self._select_admin_signal_hint(selected_trait))
            card_layout.addWidget(button)
            card_layout.addWidget(self._label(str(trait.get("description", "Grouped by trait signal definitions."))))
            card_layout.addWidget(self._label(f"{self._admin_signal_hint_count(trait)} signals", "AdminStudioChip"))
            list_layout.addWidget(card)
            self.admin_signal_hint_cards.append((card, button, dict(trait)))
        self.admin_signal_search.textChanged.connect(self._filter_admin_signal_hints)
        layout.addWidget(list_panel, 1)
        selected = traits[0] if traits else {}
        detail_panel, detail_layout = self._surface()
        detail_panel.setObjectName("AdminStudioSignalDetailPanel")
        self.admin_signal_detail_title = self._label("", "AdminStudioSignalDetailTitle")
        detail_layout.addWidget(self.admin_signal_detail_title)
        self.admin_signal_detail_badge = self._label("", "AdminStudioChip")
        detail_layout.addWidget(self.admin_signal_detail_badge)
        detail_layout.addWidget(self._label("Definition", "AdminStudioConceptTitle"))
        self.admin_signal_definition = self._label("", "AdminStudioSignalDefinitionText")
        detail_layout.addWidget(self.admin_signal_definition)
        detail_layout.addWidget(self._label("Scoring Meaning", "AdminStudioConceptTitle"))
        self.admin_signal_scoring = self._label("", "AdminStudioSignalScoringMeaningText")
        detail_layout.addWidget(self.admin_signal_scoring)
        levels = self.QtWidgets.QHBoxLayout()
        for object_name, tone, title, text, background, border, color in (
            ("AdminStudioSignalLevelHighCard", "high", "High", "Stays specific, child-centered, and calm.", "#dcfce7", "#86efac", "#166534"),
            ("AdminStudioSignalLevelModerateCard", "moderate", "Moderate", "Shows some evidence but needs more detail.", "#fef9c3", "#fde047", "#854d0e"),
            ("AdminStudioSignalLevelLowCard", "low", "Low", "Vague, adult-centered, or missing evidence.", "#fee2e2", "#fca5a5", "#991b1b"),
        ):
            level_card, level_layout = self._surface()
            level_card.setObjectName(object_name)
            level_card.setProperty("adminSignalLevelTone", tone)
            level_card.setStyleSheet(
                f"QFrame#{object_name} {{ background: {background}; border: 1px solid {border}; border-radius: 8px; color: {color}; }}"
            )
            level_layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
            level_layout.addWidget(self._label(text))
            levels.addWidget(level_card)
        detail_layout.addLayout(levels)
        detail_layout.addWidget(self._label("Example Candidate Phrases", "AdminStudioConceptTitle"))
        self.admin_signal_examples = self._label("", "AdminStudioSignalExamplePhrases")
        detail_layout.addWidget(self.admin_signal_examples)
        detail_layout.addWidget(self._label("Usage Notes", "AdminStudioConceptTitle"))
        self.admin_signal_usage_notes = self._label("", "AdminStudioSignalUsageNotes")
        detail_layout.addWidget(self.admin_signal_usage_notes)
        self.admin_signal_footer_metadata = self._label("", "AdminStudioSignalFooterMetadata")
        detail_layout.addWidget(self.admin_signal_footer_metadata)
        layout.addWidget(detail_panel, 2)
        if selected:
            self._select_admin_signal_hint(selected)
        return group

    def _admin_signal_hint_count(self, trait: dict[str, Any]) -> int:
        hints = trait.get("signal_hints")
        if isinstance(hints, list) and hints:
            return len(hints)
        descriptors = trait.get("descriptors")
        if isinstance(descriptors, dict):
            return len([value for value in descriptors.values() if str(value).strip()])
        return 0

    def _admin_signal_hint_values(self, trait: dict[str, Any]) -> list[str]:
        hints = trait.get("signal_hints")
        if isinstance(hints, list):
            return [str(value).strip() for value in hints if str(value).strip()]
        descriptors = trait.get("descriptors")
        if isinstance(descriptors, dict):
            return [str(value).strip() for value in descriptors.values() if str(value).strip()]
        return []

    def _admin_signal_trait_categories(self, trait: dict[str, Any]) -> set[str]:
        text = " ".join(
            [
                str(trait.get("name", "")),
                str(trait.get("id", "")),
                str(trait.get("priority", "")),
                str(trait.get("description", "")),
                " ".join(self._admin_signal_hint_values(trait)),
                " ".join(self._admin_signal_example_values(trait)),
            ]
        ).lower()
        categories: set[str] = set()
        keyword_map = {
            "Empathy": ("empathy", "respect", "warmth", "child-centered", "children"),
            "Regulation": ("regulation", "stress", "calm", "overwhelmed", "composure"),
            "Accountability": ("accountability", "reliability", "follow through", "commitment", "responsible"),
            "Guidance": ("guidance", "behavior", "routine", "redirect", "support"),
            "Teamwork": ("team", "collaborat", "coworker", "colleague", "adult"),
            "Communication": ("communication", "communicat", "language", "listen", "explain"),
            "Structure": ("structure", "plan", "organized", "schedule", "routine"),
        }
        for category, keywords in keyword_map.items():
            if any(keyword in text for keyword in keywords):
                categories.add(category)
        if not categories:
            categories.add("Other")
        return categories

    def _admin_signal_example_values(self, trait: dict[str, Any]) -> list[str]:
        samples = trait.get("sample_answers")
        if isinstance(samples, dict):
            values = [str(samples.get(str(score), "")).strip() for score in range(5, 0, -1)]
            return [value for value in values if value]
        return self._admin_signal_hint_values(trait)

    def _set_admin_signal_category(self, category: str) -> None:
        self.admin_signal_selected_category = str(category or "all")
        for button in getattr(self, "admin_signal_category_buttons", []):
            button.setProperty("adminSignalCategorySelected", button.property("adminSignalCategory") == self.admin_signal_selected_category)
        self._filter_admin_signal_hints(self.admin_signal_search.text() if hasattr(self, "admin_signal_search") else "")

    def _select_admin_signal_hint(self, trait: dict[str, Any]) -> None:
        name = str(trait.get("name", "Signal detail"))
        trait_id = str(trait.get("id", "")).strip()
        definition = str(trait.get("description", "Grouped by trait signal definitions."))
        hints = self._admin_signal_hint_values(trait)
        examples = self._admin_signal_example_values(trait)
        usage = str(trait.get("usage_notes", "Look for real examples of past situations and concrete outcomes."))
        for card, button, stored_trait in getattr(self, "admin_signal_hint_cards", []):
            selected = str(stored_trait.get("id", "")).strip() == trait_id
            card.setProperty("adminSignalSelected", selected)
            button.setProperty("adminSignalSelected", selected)
        self.admin_signal_detail_title.setText(name)
        self.admin_signal_detail_badge.setText(f"{self._admin_signal_hint_count(trait)} signals")
        self.admin_signal_definition.setText(definition)
        self.admin_signal_scoring.setText(
            "Higher scores indicate stronger, more specific evidence. "
            f"Signals: {'; '.join(hints[:4]) if hints else 'No explicit signals configured.'}"
        )
        self.admin_signal_examples.setText("; ".join(examples[:6]) if examples else "No examples configured.")
        self.admin_signal_usage_notes.setText(usage)
        primary_category = self._admin_signal_primary_category(trait)
        self.admin_signal_footer_metadata.setText(f"Category: {primary_category} - Last updated: May 9, 2025 - Status: Up to date")

    def _filter_admin_signal_hints(self, text: str) -> None:
        query = text.strip().lower()
        category = str(getattr(self, "admin_signal_selected_category", "all") or "all")
        for card, button, trait in getattr(self, "admin_signal_hint_cards", []):
            haystack = " ".join(
                [
                    str(trait.get("name", "")),
                    str(trait.get("id", "")),
                    str(trait.get("description", "")),
                    " ".join(self._admin_signal_hint_values(trait)),
                    " ".join(self._admin_signal_example_values(trait)),
                ]
            ).lower()
            category_matches = (
                category == "all"
                or str(trait.get("name", "")).strip() == category
                or category in self._admin_signal_trait_categories(trait)
            )
            matches = category_matches and (not query or query in haystack)
            card.setVisible(matches)
            button.setProperty("adminSignalSearchMatch", matches)

    def _admin_signal_primary_category(self, trait: dict[str, Any]) -> str:
        categories = self._admin_signal_trait_categories(trait)
        for category in ("Empathy", "Regulation", "Accountability", "Guidance", "Teamwork", "Communication", "Structure", "Other"):
            if category in categories:
                return category
        return "Other"

    def _admin_notification_rule_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        rules_panel, rules_layout = self._surface()
        rules_panel.setObjectName("AdminStudioNotificationRuleListPanel")
        rules_panel.setProperty("adminNotificationViewMode", "list")
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Rule cards", "AdminStudioConceptTitle"))
        header.addStretch(1)
        header.addWidget(self._label(f"{len(self.admin_draft.notification_rules)} rules", "AdminStudioChip"))
        rules_layout.addLayout(header)
        rules_toolbar = self.QtWidgets.QHBoxLayout()
        create_button = self.QtWidgets.QPushButton("Create / Modify Template")
        create_button.setObjectName("AdminStudioNotificationCreateTemplateButton")
        create_button.setProperty("adminRequiresEdit", True)
        create_button.setEnabled(False)
        create_button.clicked.connect(self._create_admin_notification_rule_editor)
        self._make_button_readable(create_button)
        rules_toolbar.addWidget(create_button)
        self.admin_notification_event_filter = self.QtWidgets.QComboBox()
        self.admin_notification_event_filter.setObjectName("AdminStudioNotificationEventFilter")
        self.admin_notification_event_filter.setEditable(True)
        self.admin_notification_event_filter.addItem("All events")
        for event_type in sorted({str(rule.event_type or "").strip() for rule in self.admin_draft.notification_rules if str(rule.event_type or "").strip()}):
            self.admin_notification_event_filter.addItem(event_type)
        self.admin_notification_event_filter.currentTextChanged.connect(self._filter_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_event_filter)
        self.admin_notification_enabled_filter = self.QtWidgets.QComboBox()
        self.admin_notification_enabled_filter.setObjectName("AdminStudioNotificationEnabledStatusFilter")
        self.admin_notification_enabled_filter.addItems(["All statuses", "Enabled", "Disabled"])
        self.admin_notification_enabled_filter.currentTextChanged.connect(self._filter_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_enabled_filter)
        self.admin_notification_timing_filter = self.QtWidgets.QComboBox()
        self.admin_notification_timing_filter.setObjectName("AdminStudioNotificationTimingFilter")
        self.admin_notification_timing_filter.addItems(["All timings", "When event happens", "Reference date"])
        self.admin_notification_timing_filter.currentTextChanged.connect(self._filter_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_timing_filter)
        self.admin_notification_recipients_filter = self.QtWidgets.QComboBox()
        self.admin_notification_recipients_filter.setObjectName("AdminStudioNotificationRecipientsFilter")
        self.admin_notification_recipients_filter.addItems(["All recipients", "Has recipients", "No recipients"])
        self.admin_notification_recipients_filter.currentTextChanged.connect(self._filter_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_recipients_filter)
        self.admin_notification_template_filter = self.QtWidgets.QComboBox()
        self.admin_notification_template_filter.setObjectName("AdminStudioNotificationTemplateFilter")
        self.admin_notification_template_filter.addItems(["All templates", "Complete templates", "Missing subject", "Missing body"])
        self.admin_notification_template_filter.currentTextChanged.connect(self._filter_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_template_filter)
        self.admin_notification_sort = self.QtWidgets.QComboBox()
        self.admin_notification_sort.setObjectName("AdminStudioNotificationSortBy")
        self.admin_notification_sort.addItems(["Sort by: Event", "Sort by: Recipients"])
        self.admin_notification_sort.currentTextChanged.connect(self._sort_admin_notification_rules)
        rules_toolbar.addWidget(self.admin_notification_sort)
        self.admin_notification_view_toggle = self.QtWidgets.QComboBox()
        self.admin_notification_view_toggle.setObjectName("AdminStudioNotificationViewToggle")
        self.admin_notification_view_toggle.addItems(["List", "Grid"])
        self.admin_notification_view_toggle.currentTextChanged.connect(self._set_admin_notification_view_mode)
        rules_toolbar.addWidget(self.admin_notification_view_toggle)
        for chip in ("Event", "Timing", "Recipients", "Subject/body preview"):
            rules_toolbar.addWidget(self._label(chip, "AdminStudioChip"))
        clear_filters = self.QtWidgets.QPushButton("Clear filters")
        clear_filters.setObjectName("AdminStudioNotificationClearFilters")
        clear_filters.clicked.connect(self._clear_admin_notification_filters)
        rules_toolbar.addWidget(clear_filters)
        rules_toolbar.addStretch(1)
        rules_layout.addWidget(self._horizontal_scroll_panel(rules_toolbar, "AdminStudioNotificationToolbarScroll"))
        self.admin_notification_rules_layout = rules_layout
        self.admin_notification_rule_cards = []
        for rule in self.admin_draft.notification_rules:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioNotificationRuleCard")
            card.setProperty("adminNotificationEvent", str(rule.event_type or ""))
            card.setProperty("adminNotificationObjectSuffix", self._admin_object_suffix(rule.event_type))
            card.setProperty("adminNotificationActive", bool(rule.active))
            card.setProperty("adminNotificationFilterMatch", True)
            card.setProperty("adminNotificationSelected", False)
            card.setProperty("adminNotificationSortRank", 0)
            card.setProperty("adminNotificationViewMode", "list")
            card.setStyleSheet("QFrame#AdminStudioNotificationRuleCard { background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; }")
            button = self.QtWidgets.QPushButton(rule.event_type)
            button.setObjectName(f"AdminStudioNotificationRuleButton_{self._admin_object_suffix(rule.event_type)}")
            self._make_button_readable(button)
            button.clicked.connect(lambda _checked=False, selected_rule=rule: self._select_admin_notification_rule(selected_rule))
            card_layout.addWidget(button)
            card_layout.addWidget(self._label(f"ID: {rule.id if rule.id is not None else 'Draft'}"))
            card_layout.addWidget(self._label(rule.label or rule.event_type))
            card_layout.addWidget(self._label("Enabled" if rule.active else "Disabled", "AdminStudioChip"))
            meta = self.QtWidgets.QHBoxLayout()
            meta.addWidget(self._label(f"Send timing\n{self._admin_notification_schedule_text(rule)}"))
            meta.addWidget(self._label(f"Recipients\n{len([recipient for recipient in rule.recipients if recipient.active])}"))
            card_layout.addLayout(meta)
            card_layout.addWidget(self._label(f"Subject\n{rule.subject_template or 'Missing subject template'}"))
            card_layout.addWidget(self._label(f"Body preview\n{rule.body_template[:180] if rule.body_template else 'Missing body template'}"))
            open_rule = self.QtWidgets.QPushButton("Open >")
            open_rule.setObjectName(f"AdminStudioNotificationOpenRule_{self._admin_object_suffix(rule.event_type)}")
            open_rule.clicked.connect(lambda _checked=False, selected_rule=rule: self._select_admin_notification_rule(selected_rule))
            self._make_button_readable(open_rule)
            card_layout.addWidget(open_rule)
            rules_layout.addWidget(card)
            self.admin_notification_rule_cards.append((card, rule))
        self._sort_admin_notification_rules()
        layout.addWidget(rules_panel, 2)
        editor, editor_layout = self._surface()
        editor.setObjectName("AdminStudioNotificationEditPanel")
        editor_layout.addWidget(self._label("Edit Notification Rule", "AdminStudioNotificationEditorTitle"))
        self.admin_notification_editor_meta = self._label("", "AdminStudioNotificationEditorMeta")
        editor_layout.addWidget(self.admin_notification_editor_meta)
        editor_layout.addWidget(self._label("Rule basics · Recipients · Email template", "AdminStudioChip"))
        form = self.QtWidgets.QFormLayout()
        self.admin_notification_rule_label = self.QtWidgets.QLineEdit()
        self.admin_notification_rule_label.setObjectName("AdminStudioNotificationRuleLabel")
        self.admin_notification_rule_event = self.QtWidgets.QLineEdit()
        self.admin_notification_rule_event.setObjectName("AdminStudioNotificationRuleEvent")
        self.admin_notification_rule_active = self.QtWidgets.QCheckBox("Enabled")
        self.admin_notification_rule_active.setObjectName("AdminStudioNotificationRuleActive")
        self.admin_notification_rule_timing = self.QtWidgets.QComboBox()
        self.admin_notification_rule_timing.setObjectName("AdminStudioNotificationRuleTiming")
        for timing_key in ("event", "date_offset"):
            self.admin_notification_rule_timing.addItem(self._admin_notification_timing_label(timing_key), timing_key)
        self.admin_notification_date_field = self.QtWidgets.QComboBox()
        self.admin_notification_date_field.setObjectName("AdminStudioNotificationDateField")
        self.admin_notification_date_field.setEditable(True)
        for date_key in self._admin_notification_date_fields():
            self.admin_notification_date_field.addItem(self._admin_notification_date_field_label(date_key), date_key)
        self.admin_notification_offset_direction = self.QtWidgets.QComboBox()
        self.admin_notification_offset_direction.setObjectName("AdminStudioNotificationOffsetDirection")
        self.admin_notification_offset_direction.addItems(["Before", "On", "After"])
        self.admin_notification_rule_offset = self.QtWidgets.QSpinBox()
        self.admin_notification_rule_offset.setObjectName("AdminStudioNotificationRuleOffsetDays")
        self.admin_notification_rule_offset.setRange(0, 365)
        self.admin_notification_rule_recipients = self.QtWidgets.QLineEdit()
        self.admin_notification_rule_recipients.setObjectName("AdminStudioNotificationRuleRecipients")
        self.admin_notification_rule_subject = self.QtWidgets.QLineEdit()
        self.admin_notification_rule_subject.setObjectName("AdminStudioNotificationRuleSubject")
        self.admin_notification_rule_body = self.QtWidgets.QPlainTextEdit()
        self.admin_notification_rule_body.setObjectName("AdminStudioNotificationRuleBody")
        self.admin_notification_rule_body.setMinimumHeight(140)
        for widget in (
            self.admin_notification_rule_label,
            self.admin_notification_rule_event,
            self.admin_notification_rule_active,
            self.admin_notification_rule_timing,
            self.admin_notification_date_field,
            self.admin_notification_offset_direction,
            self.admin_notification_rule_offset,
            self.admin_notification_rule_recipients,
            self.admin_notification_rule_subject,
            self.admin_notification_rule_body,
        ):
            widget.setProperty("adminNotificationEdit", True)
            widget.setEnabled(False)
        self.admin_notification_rule_active.toggled.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_rule_timing.currentTextChanged.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_date_field.currentTextChanged.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_offset_direction.currentTextChanged.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_rule_offset.valueChanged.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_rule_recipients.textChanged.connect(self._sync_admin_notification_recipients_from_text)
        self.admin_notification_rule_subject.textChanged.connect(self._sync_admin_notification_rule_validation)
        self.admin_notification_rule_body.textChanged.connect(self._sync_admin_notification_rule_validation)
        form.addRow("Label", self.admin_notification_rule_label)
        form.addRow("Event", self.admin_notification_rule_event)
        form.addRow("Enabled", self.admin_notification_rule_active)
        form.addRow("Trigger type", self.admin_notification_rule_timing)
        form.addRow("Reference date", self.admin_notification_date_field)
        form.addRow("Send timing", self.admin_notification_offset_direction)
        form.addRow("Number of days", self.admin_notification_rule_offset)
        form.addRow("Recipients", self.admin_notification_rule_recipients)
        form.addRow("Subject", self.admin_notification_rule_subject)
        form.addRow("Body", self.admin_notification_rule_body)
        editor_layout.addLayout(form)
        subject_tools, subject_tools_layout = self._surface()
        subject_tools.setObjectName("AdminStudioNotificationSubjectTools")
        subject_tools_layout.addWidget(self._label("Subject Variables", "AdminStudioConceptTitle"))
        subject_variable_row = self.QtWidgets.QHBoxLayout()
        for variable in ("position_name", "candidate_name", "school", "company_name"):
            button = self.QtWidgets.QPushButton(f"{{{variable}}}")
            button.setObjectName(f"AdminStudioNotificationSubjectVariable_{variable}")
            button.setProperty("adminRequiresEdit", True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, token=f"{{{variable}}}": self._insert_admin_notification_subject_variable(token))
            subject_variable_row.addWidget(button)
        subject_variable_row.addStretch(1)
        subject_tools_layout.addLayout(subject_variable_row)
        editor_layout.addWidget(subject_tools)
        body_toolbar, body_toolbar_layout = self._surface()
        body_toolbar.setObjectName("AdminStudioNotificationBodyToolbar")
        body_toolbar_layout.addWidget(self._label("Email Template Tools", "AdminStudioConceptTitle"))
        toolbar_row = self.QtWidgets.QHBoxLayout()
        for label, object_name, snippet in (
            ("Bold", "AdminStudioNotificationBodyBold", "**bold text**"),
            ("Italic", "AdminStudioNotificationBodyItalic", "_italic text_"),
            ("Bullets", "AdminStudioNotificationBodyBullets", "\n- list item"),
            ("Variables", "AdminStudioNotificationBodyVariables", "{position_name}"),
        ):
            button = self.QtWidgets.QPushButton(label)
            button.setObjectName(object_name)
            button.setProperty("adminRequiresEdit", True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, text=snippet: self._insert_admin_notification_body_snippet(text))
            toolbar_row.addWidget(button)
        toolbar_row.addStretch(1)
        body_toolbar_layout.addLayout(toolbar_row)
        editor_layout.addWidget(body_toolbar)
        self.admin_notification_schedule_summary = self._label("", "AdminStudioNotificationScheduleSummary")
        editor_layout.addWidget(self.admin_notification_schedule_summary)
        self.admin_notification_recipient_chips, self.admin_notification_recipient_chips_layout = self._surface()
        self.admin_notification_recipient_chips.setObjectName("AdminStudioNotificationRecipientChips")
        self.admin_notification_recipient_chips_layout.addWidget(self._label("Recipients", "AdminStudioConceptTitle"))
        editor_layout.addWidget(self.admin_notification_recipient_chips)
        editor_layout.addWidget(self._label("{hiring_manager_name}  {position_name}  {candidate_name}  {company_name}", "AdminStudioChip"))
        variable_row = self.QtWidgets.QHBoxLayout()
        for variable in ("hiring_manager_name", "position_name", "candidate_name", "company_name"):
            button = self.QtWidgets.QPushButton(f"{{{variable}}}")
            button.setObjectName(f"AdminStudioNotificationVariable_{variable}")
            button.setProperty("adminRequiresEdit", True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, token=f"{{{variable}}}": self._insert_admin_notification_variable(token))
            variable_row.addWidget(button)
        variable_row.addStretch(1)
        editor_layout.addLayout(variable_row)
        self.admin_notification_variables_panel, variables_layout = self._surface()
        self.admin_notification_variables_panel.setObjectName("AdminStudioNotificationVariablesPreviewPanel")
        variables_layout.addWidget(self._label("Variables Preview", "AdminStudioConceptTitle"))
        self.admin_notification_variables_preview = self._label("", "AdminStudioNotificationVariablesPreview")
        variables_layout.addWidget(self.admin_notification_variables_preview)
        editor_layout.addWidget(self.admin_notification_variables_panel)
        self.admin_notification_validation_panel, validation_layout = self._surface()
        self.admin_notification_validation_panel.setObjectName("AdminStudioNotificationValidationPanel")
        validation_layout.addWidget(self._label("Validation", "AdminStudioConceptTitle"))
        self.admin_notification_rule_validation = self._label("", "AdminStudioNotificationRuleValidation")
        validation_layout.addWidget(self.admin_notification_rule_validation)
        editor_layout.addWidget(self.admin_notification_validation_panel)
        actions = self.QtWidgets.QHBoxLayout()
        delete_button = self.QtWidgets.QPushButton("Delete Rule")
        delete_button.setObjectName("AdminStudioNotificationRuleDelete")
        delete_button.setProperty("adminRequiresEdit", True)
        delete_button.setEnabled(False)
        delete_button.clicked.connect(self._delete_admin_notification_rule_editor)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        preview = self.QtWidgets.QPushButton("Preview")
        preview.setObjectName("AdminStudioNotificationPreviewButton")
        preview.clicked.connect(self._show_admin_notification_preview_dialog)
        actions.addWidget(preview)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("AdminStudioNotificationRuleCancel")
        cancel.clicked.connect(self._cancel_admin_notification_rule_editor)
        actions.addWidget(cancel)
        self.admin_notification_rule_save = self._primary_button("Save Changes")
        self.admin_notification_rule_save.setObjectName("AdminStudioNotificationRuleSave")
        self.admin_notification_rule_save.setProperty("adminRequiresEdit", True)
        self.admin_notification_rule_save.setEnabled(False)
        self.admin_notification_rule_save.clicked.connect(self._save_admin_notification_rule_editor)
        actions.addWidget(self.admin_notification_rule_save)
        editor_layout.addLayout(actions)
        layout.addWidget(editor, 1)
        if self.admin_draft.notification_rules:
            self._select_admin_notification_rule(self.admin_draft.notification_rules[0])
        return group

    def _sort_admin_notification_rules(self) -> None:
        sort_text = self.admin_notification_sort.currentText() if hasattr(self, "admin_notification_sort") else "Sort by: Event"

        def active_recipient_count(rule: Any) -> int:
            return len([recipient for recipient in getattr(rule, "recipients", []) if getattr(recipient, "active", True)])

        if sort_text == "Sort by: Recipients":
            ordered = sorted(
                getattr(self, "admin_notification_rule_cards", []),
                key=lambda item: (-active_recipient_count(item[1]), str(item[1].event_type or "")),
            )
        else:
            ordered = sorted(
                getattr(self, "admin_notification_rule_cards", []),
                key=lambda item: str(item[1].event_type or ""),
            )

        layout = getattr(self, "admin_notification_rules_layout", None)
        if layout is None:
            self.admin_notification_rule_cards = ordered
            return
        for card, _rule in ordered:
            layout.removeWidget(card)
        for rank, (card, _rule) in enumerate(ordered):
            card.setProperty("adminNotificationSortRank", rank)
            layout.insertWidget(2 + rank, card)
        self.admin_notification_rule_cards = ordered
        self._filter_admin_notification_rules()

    def _set_admin_notification_view_mode(self) -> None:
        selected_text = self.admin_notification_view_toggle.currentText() if hasattr(self, "admin_notification_view_toggle") else "List"
        mode = "grid" if selected_text == "Grid" else "list"
        panel = getattr(self, "admin_notification_rules_layout", None)
        parent_widget = panel.parentWidget() if panel is not None else None
        if parent_widget is not None:
            parent_widget.setProperty("adminNotificationViewMode", mode)
        for card, _rule in getattr(self, "admin_notification_rule_cards", []):
            card.setProperty("adminNotificationViewMode", mode)

    def _filter_admin_notification_rules(self) -> None:
        selected_event = self.admin_notification_event_filter.currentText().strip() if hasattr(self, "admin_notification_event_filter") else "All events"
        selected_status = self.admin_notification_enabled_filter.currentText()
        selected_timing = self.admin_notification_timing_filter.currentText() if hasattr(self, "admin_notification_timing_filter") else "All timings"
        selected_recipients = self.admin_notification_recipients_filter.currentText() if hasattr(self, "admin_notification_recipients_filter") else "All recipients"
        selected_template = self.admin_notification_template_filter.currentText() if hasattr(self, "admin_notification_template_filter") else "All templates"
        for card, rule in getattr(self, "admin_notification_rule_cards", []):
            event_type = str(getattr(rule, "event_type", "") or "").strip()
            event_matches = selected_event in {"", "All events"} or event_type == selected_event
            status_matches = (
                selected_status == "All statuses"
                or (selected_status == "Enabled" and bool(rule.active))
                or (selected_status == "Disabled" and not bool(rule.active))
            )
            timing = str(getattr(rule, "trigger_timing", "event") or "event").strip()
            timing_matches = (
                selected_timing == "All timings"
                or (selected_timing == "When event happens" and timing == "event")
                or (selected_timing == "Reference date" and timing == "date_offset")
            )
            active_recipient_count = len([recipient for recipient in getattr(rule, "recipients", []) if getattr(recipient, "active", True)])
            recipient_matches = (
                selected_recipients == "All recipients"
                or (selected_recipients == "Has recipients" and active_recipient_count > 0)
                or (selected_recipients == "No recipients" and active_recipient_count == 0)
            )
            has_subject = bool(str(getattr(rule, "subject_template", "") or "").strip())
            has_body = bool(str(getattr(rule, "body_template", "") or "").strip())
            template_matches = (
                selected_template == "All templates"
                or (selected_template == "Complete templates" and has_subject and has_body)
                or (selected_template == "Missing subject" and not has_subject)
                or (selected_template == "Missing body" and not has_body)
            )
            matches = event_matches and status_matches and timing_matches and recipient_matches and template_matches
            card.setProperty("adminNotificationFilterMatch", matches)
            card.setVisible(matches)

    def _clear_admin_notification_filters(self) -> None:
        if hasattr(self, "admin_notification_event_filter"):
            self.admin_notification_event_filter.setCurrentText("All events")
        self.admin_notification_enabled_filter.setCurrentText("All statuses")
        if hasattr(self, "admin_notification_timing_filter"):
            self.admin_notification_timing_filter.setCurrentText("All timings")
        if hasattr(self, "admin_notification_recipients_filter"):
            self.admin_notification_recipients_filter.setCurrentText("All recipients")
        if hasattr(self, "admin_notification_template_filter"):
            self.admin_notification_template_filter.setCurrentText("All templates")
        self._filter_admin_notification_rules()

    def _refresh_admin_notification_rule_cards(self, selected_event: str = "") -> None:
        layout = getattr(self, "admin_notifications_layout", None)
        current = getattr(self, "admin_notification_rule_widget", None)
        if layout is None or current is None:
            return
        index = layout.indexOf(current)
        if index < 0:
            return
        layout.removeWidget(current)
        current.setParent(None)
        current.deleteLater()
        replacement = self._admin_notification_rule_cards()
        self.admin_notification_rule_widget = replacement
        layout.insertWidget(index, replacement, 2)
        if self.admin_edit_mode:
            self._set_admin_editing_enabled(True)
        if selected_event:
            selected = next(
                (rule for rule in self.admin_draft.notification_rules if str(rule.event_type or "") == selected_event),
                None,
            )
            if selected is not None:
                self._select_admin_notification_rule(selected)

    def _create_admin_notification_rule_editor(self) -> None:
        self.admin_selected_notification_rule_id = ""
        self.admin_selected_notification_event = ""
        for card, _rule in getattr(self, "admin_notification_rule_cards", []):
            card.setProperty("adminNotificationSelected", False)
            card.setStyleSheet("QFrame#AdminStudioNotificationRuleCard { background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; }")
        self.admin_notification_editor_meta.setText("ID: draft · new notification rule")
        self.admin_notification_rule_label.clear()
        self.admin_notification_rule_event.clear()
        self.admin_notification_rule_active.setChecked(False)
        self.admin_notification_rule_timing.setCurrentIndex(0)
        self.admin_notification_date_field.setCurrentIndex(0)
        self._set_admin_notification_offset_controls(0)
        self.admin_notification_selected_recipients = []
        self.admin_notification_rule_recipients.clear()
        self._render_admin_notification_recipient_chips()
        self.admin_notification_rule_subject.clear()
        self.admin_notification_rule_body.clear()
        self._sync_admin_notification_rule_validation()
        self.admin_notification_rule_event.setFocus()

    def _admin_notification_date_fields(self) -> list[str]:
        preferred = [
            "start_date",
            "generated_date",
            "date_notice_given",
            "last_working_day",
            "notice_given",
            "final_working_day",
            "interview_date",
        ]
        known_fields = [
            str(field)
            for field in NOTIFICATION_TEMPLATE_FIELDS
            if str(field).endswith("_date") or str(field).endswith("_day") or str(field) in preferred
        ]
        return list(dict.fromkeys(preferred + known_fields))

    def _admin_notification_timing_label(self, timing: str) -> str:
        labels = {
            "event": "When event happens",
            "date_offset": "Before/on/after a reference date",
        }
        key = str(timing or "").strip()
        return labels.get(key, key.replace("_", " ").strip() or "Trigger type")

    def _admin_notification_selected_timing(self) -> str:
        data = self.admin_notification_rule_timing.currentData(self.QtCore.Qt.ItemDataRole.UserRole)
        if data:
            return str(data).strip()
        return self.admin_notification_rule_timing.currentText().strip()

    def _admin_notification_date_field_label(self, field: str) -> str:
        labels = {
            "start_date": "Employee start date",
            "generated_date": "Offer generated date",
            "date_notice_given": "Date notice given",
            "last_working_day": "Last working day",
            "notice_given": "Date notice given",
            "final_working_day": "Last working day",
            "interview_date": "Interview date",
        }
        key = str(field or "").strip()
        if key in labels:
            return labels[key]
        return key.replace("_", " ").strip() or "Date field"

    def _admin_notification_selected_date_field(self) -> str:
        typed = self.admin_notification_date_field.currentText().strip()
        current_index = self.admin_notification_date_field.currentIndex()
        if current_index >= 0 and typed != self.admin_notification_date_field.itemText(current_index):
            return typed
        data = self.admin_notification_date_field.currentData(self.QtCore.Qt.ItemDataRole.UserRole)
        if data:
            return str(data).strip()
        return typed

    def _admin_notification_schedule_text(self, rule: Any) -> str:
        timing = str(getattr(rule, "trigger_timing", "") or "event").strip()
        if timing != "date_offset":
            return "When event happens"
        field = str(getattr(rule, "date_field", "") or "start_date").strip() or "start_date"
        field_label = self._admin_notification_date_field_label(field).lower()
        try:
            offset = int(getattr(rule, "offset_days", 0) or 0)
        except (TypeError, ValueError):
            offset = 0
        if offset < 0:
            return f"{abs(offset)} {'day' if abs(offset) == 1 else 'days'} before {field_label}"
        if offset > 0:
            return f"{offset} {'day' if offset == 1 else 'days'} after {field_label}"
        return f"On {field_label}"

    def _set_admin_notification_offset_controls(self, offset_days: int) -> None:
        if offset_days < 0:
            self.admin_notification_offset_direction.setCurrentText("Before")
        elif offset_days > 0:
            self.admin_notification_offset_direction.setCurrentText("After")
        else:
            self.admin_notification_offset_direction.setCurrentText("On")
        self.admin_notification_rule_offset.setValue(abs(int(offset_days or 0)))

    def _admin_notification_signed_offset_days(self) -> int:
        days = abs(int(self.admin_notification_rule_offset.value()))
        direction = self.admin_notification_offset_direction.currentText()
        if direction == "Before":
            return -days
        if direction == "After":
            return days
        return 0

    def _sync_admin_notification_schedule_summary(self) -> None:
        if not hasattr(self, "admin_notification_schedule_summary"):
            return
        rule = SimpleNamespace(
            trigger_timing=self._admin_notification_selected_timing(),
            date_field=self._admin_notification_selected_date_field(),
            offset_days=self._admin_notification_signed_offset_days(),
        )
        self.admin_notification_schedule_summary.setText(self._admin_notification_schedule_text(rule))

    def _sync_admin_notification_recipients_from_text(self) -> None:
        existing = {
            str(recipient.get("email", "")).strip(): recipient
            for recipient in getattr(self, "admin_notification_selected_recipients", [])
        }
        recipients: list[dict[str, str]] = []
        for email in self._admin_notification_recipient_emails():
            original = existing.get(email, {})
            recipients.append(
                {
                    "email": email,
                    "role_label": str(original.get("role_label", "") or "").strip(),
                    "name": str(original.get("name", "") or "").strip(),
                }
            )
        self.admin_notification_selected_recipients = recipients
        self._render_admin_notification_recipient_chips()
        self._sync_admin_notification_rule_validation()

    def _admin_notification_recipient_emails(self) -> list[str]:
        text = self.admin_notification_rule_recipients.text() if hasattr(self, "admin_notification_rule_recipients") else ""
        return [email.strip() for email in str(text or "").split(",") if email.strip()]

    def _render_admin_notification_recipient_chips(self) -> None:
        layout = getattr(self, "admin_notification_recipient_chips_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        layout.addWidget(self._label("Recipients", "AdminStudioConceptTitle"))
        for recipient in getattr(self, "admin_notification_selected_recipients", []):
            email = str(recipient.get("email", "") or "").strip()
            if not email:
                continue
            label = str(recipient.get("role_label", "") or recipient.get("name", "") or "Recipient").strip()
            row = self.QtWidgets.QHBoxLayout()
            row.addWidget(self._label(f"{label}  {email}", "AdminStudioChip"))
            remove = self.QtWidgets.QPushButton("Remove")
            remove.setObjectName(f"AdminStudioNotificationRecipientRemove_{self._admin_object_suffix(email)}")
            remove.setProperty("adminRequiresEdit", True)
            remove.setEnabled(bool(getattr(self, "admin_edit_mode", False)))
            remove.clicked.connect(lambda _checked=False, selected_email=email: self._remove_admin_notification_recipient(selected_email))
            row.addWidget(remove)
            row.addStretch(1)
            layout.addLayout(row)
        if not getattr(self, "admin_notification_selected_recipients", []):
            layout.addWidget(self._label("No recipients configured.", "AdminStudioValidationBlocked"))

    def _remove_admin_notification_recipient(self, email: str) -> None:
        selected_email = str(email or "").strip()
        remaining = [recipient for recipient in getattr(self, "admin_notification_selected_recipients", []) if recipient.get("email") != selected_email]
        self.admin_notification_selected_recipients = remaining
        self.admin_notification_rule_recipients.setText(", ".join(recipient["email"] for recipient in remaining))
        self._render_admin_notification_recipient_chips()
        self._sync_admin_notification_rule_validation()

    def _select_admin_notification_rule(self, rule: Any) -> None:
        self.admin_selected_notification_rule_id = str(rule.id or "")
        self.admin_selected_notification_event = str(rule.event_type or "")
        for card, stored_rule in getattr(self, "admin_notification_rule_cards", []):
            selected = str(stored_rule.event_type or "") == self.admin_selected_notification_event
            card.setProperty("adminNotificationSelected", selected)
            if selected:
                card.setStyleSheet("QFrame#AdminStudioNotificationRuleCard { background: #eff6ff; border: 1px solid #2563eb; border-radius: 8px; }")
            else:
                card.setStyleSheet("QFrame#AdminStudioNotificationRuleCard { background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; }")
        self.admin_notification_editor_meta.setText(
            f"ID: {self.admin_selected_notification_rule_id or 'draft'} · {self.admin_selected_notification_event}"
        )
        self.admin_notification_rule_label.setText(str(rule.label or rule.event_type))
        self.admin_notification_rule_event.setText(str(rule.event_type or ""))
        self.admin_notification_rule_active.setChecked(bool(rule.active))
        timing_index = self.admin_notification_rule_timing.findData(str(rule.trigger_timing or "event"), self.QtCore.Qt.ItemDataRole.UserRole)
        self.admin_notification_rule_timing.setCurrentIndex(timing_index if timing_index >= 0 else 0)
        date_field = str(getattr(rule, "date_field", "") or "start_date").strip()
        date_index = self.admin_notification_date_field.findData(date_field, self.QtCore.Qt.ItemDataRole.UserRole)
        if date_index >= 0:
            self.admin_notification_date_field.setCurrentIndex(date_index)
        else:
            self.admin_notification_date_field.setEditText(date_field)
        self._set_admin_notification_offset_controls(int(rule.offset_days or 0))
        self.admin_notification_selected_recipients = [
            {"email": recipient.email, "role_label": recipient.role_label, "name": recipient.name}
            for recipient in rule.recipients
            if recipient.active
        ]
        self.admin_notification_rule_recipients.setText(", ".join(recipient["email"] for recipient in self.admin_notification_selected_recipients))
        self._render_admin_notification_recipient_chips()
        self.admin_notification_rule_subject.setText(str(rule.subject_template or ""))
        self.admin_notification_rule_body.setPlainText(str(rule.body_template or ""))
        self._sync_admin_notification_rule_validation()

    def _delete_admin_notification_rule_editor(self) -> None:
        rule_id = str(getattr(self, "admin_selected_notification_rule_id", "") or "").strip()
        event_type = str(getattr(self, "admin_selected_notification_event", "") or "").strip()
        if rule_id:
            self.admin_draft.delete_notification_rule(int(rule_id))
        elif event_type:
            self.admin_draft.notification_rules = [
                rule for rule in self.admin_draft.notification_rules if str(rule.event_type or "") != event_type
            ]
        else:
            return

        layout = getattr(self, "admin_notification_rules_layout", None)
        remaining_cards = []
        for card, rule in getattr(self, "admin_notification_rule_cards", []):
            rule_matches = (rule_id and str(rule.id or "") == rule_id) or (not rule_id and str(rule.event_type or "") == event_type)
            if rule_matches:
                if layout is not None:
                    layout.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
                continue
            remaining_cards.append((card, rule))
        self.admin_notification_rule_cards = remaining_cards

        notifications = self._admin_tables.get("notifications")
        if notifications is not None:
            for row_index in range(notifications.rowCount() - 1, -1, -1):
                id_item = notifications.item(row_index, 0)
                event_item = notifications.item(row_index, 1)
                id_matches = bool(rule_id) and id_item is not None and id_item.text().strip() == rule_id
                event_matches = event_item is not None and event_item.text().strip() == event_type
                if id_matches or event_matches:
                    notifications.removeRow(row_index)
                    break

        self.admin_selected_notification_rule_id = ""
        self.admin_selected_notification_event = ""
        if self.admin_notification_rule_cards:
            self._sort_admin_notification_rules()
            self._select_admin_notification_rule(self.admin_notification_rule_cards[0][1])
        else:
            self.admin_notification_editor_meta.setText("No notification rule selected")
            self.admin_notification_rule_label.clear()
            self.admin_notification_rule_event.clear()
            self.admin_notification_rule_active.setChecked(False)
            self.admin_notification_rule_timing.setCurrentIndex(0)
            self.admin_notification_date_field.setCurrentIndex(0)
            self.admin_notification_offset_direction.setCurrentText("On")
            self.admin_notification_rule_offset.setValue(0)
            self.admin_notification_rule_recipients.clear()
            self.admin_notification_selected_recipients = []
            self._render_admin_notification_recipient_chips()
            self.admin_notification_rule_subject.clear()
            self.admin_notification_rule_body.clear()
            self._sync_admin_notification_rule_validation()
        self._sync_admin_status()

    def _cancel_admin_notification_rule_editor(self) -> None:
        rule_id = str(getattr(self, "admin_selected_notification_rule_id", "") or "").strip()
        event_type = str(getattr(self, "admin_selected_notification_event", "") or "").strip()
        for rule in self.admin_draft.notification_rules:
            id_matches = bool(rule_id) and str(rule.id or "") == rule_id
            event_matches = bool(event_type) and str(rule.event_type or "") == event_type
            if id_matches or event_matches:
                self._select_admin_notification_rule(rule)
                return
        if self.admin_draft.notification_rules:
            self._select_admin_notification_rule(self.admin_draft.notification_rules[0])
            return
        self._create_admin_notification_rule_editor()

    def _insert_admin_notification_variable(self, token: str) -> None:
        if not self.admin_notification_rule_body.isEnabled():
            return
        self._insert_admin_notification_body_snippet(str(token or ""))

    def _insert_admin_notification_subject_variable(self, token: str) -> None:
        if not self.admin_notification_rule_subject.isEnabled():
            return
        text = self.admin_notification_rule_subject.text()
        position = self.admin_notification_rule_subject.cursorPosition()
        inserted = str(token or "")
        self.admin_notification_rule_subject.setText(text[:position] + inserted + text[position:])
        self.admin_notification_rule_subject.setCursorPosition(position + len(inserted))
        self._sync_admin_notification_rule_validation()

    def _insert_admin_notification_body_snippet(self, text: str) -> None:
        if not self.admin_notification_rule_body.isEnabled():
            return
        cursor = self.admin_notification_rule_body.textCursor()
        cursor.insertText(str(text or ""))
        self.admin_notification_rule_body.setTextCursor(cursor)
        self._sync_admin_notification_rule_validation()

    def _sync_admin_notification_rule_validation(self) -> None:
        self._sync_admin_notification_schedule_summary()
        subject = self.admin_notification_rule_subject.text().strip()
        body = self.admin_notification_rule_body.toPlainText().strip()
        recipients = self.admin_notification_rule_recipients.text().strip()
        active = self.admin_notification_rule_active.isChecked()
        timing = self._admin_notification_selected_timing()
        date_field = self._admin_notification_selected_date_field()
        issues: list[str] = []
        if active and not subject:
            issues.append("Missing subject template.")
        if active and not body:
            issues.append("Missing body template.")
        if active and not recipients:
            issues.append("At least one recipient is required.")
        if active and timing == "date_offset" and not date_field:
            issues.append("Choose which date field controls this notification.")
        sample = self._admin_notification_preview_sample()
        if date_field and date_field not in sample:
            sample[date_field] = "2026-07-10"
        _, subject_unresolved = self._render_admin_notification_template(subject, sample)
        _, body_unresolved = self._render_admin_notification_template(body, sample)
        unresolved = sorted(set(subject_unresolved + body_unresolved))
        fields: list[str] = []
        try:
            fields = [
                field_name.split(".", 1)[0].split("[", 1)[0]
                for _, field_name, _, _ in Formatter().parse(f"{subject} {body}")
                if field_name
            ]
        except ValueError:
            fields = ["invalid template"]
        visible_fields = fields or list(NOTIFICATION_TEMPLATE_FIELDS[:4])
        self.admin_notification_variables_preview.setText("  ".join(f"{{{field}}}" for field in dict.fromkeys(visible_fields)))
        if unresolved:
            issues.append(f"Unknown variables: {', '.join(unresolved)}.")
        if issues:
            self.admin_notification_rule_validation.setText(" ".join(issues))
            return
        self.admin_notification_rule_validation.setText("No issues found. Subject and body templates look good.")

    def _show_admin_notification_preview_dialog(self) -> None:
        dialog = self._build_admin_notification_preview_dialog()
        self.admin_notification_preview_dialog = dialog
        dialog.show()

    def _build_admin_notification_preview_dialog(self) -> Any:
        sample = self._admin_notification_preview_sample()
        date_field = self._admin_notification_selected_date_field()
        if date_field and date_field not in sample:
            sample[date_field] = "2026-07-10"
        subject, subject_unresolved = self._render_admin_notification_template(
            self.admin_notification_rule_subject.text(),
            sample,
        )
        body, body_unresolved = self._render_admin_notification_template(
            self.admin_notification_rule_body.toPlainText(),
            sample,
        )
        unresolved = sorted(set(subject_unresolved + body_unresolved))
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioNotificationPreviewDialog")
        dialog.setWindowTitle("Notification Template Preview")
        dialog.resize(680, 520)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Notification Template Preview", "SectionTitle"))
        layout.addWidget(self._label("Rendered with sample staffing data."))
        layout.addWidget(self._label("Subject", "AdminStudioConceptTitle"))
        layout.addWidget(self._label(subject or "(blank subject)"))
        layout.addWidget(self._label("Body", "AdminStudioConceptTitle"))
        layout.addWidget(self._label(body or "(blank body)"))
        body_view = self.QtWidgets.QPlainTextEdit(body or "(blank body)")
        body_view.setObjectName("AdminStudioNotificationPreviewBody")
        body_view.setReadOnly(True)
        body_view.setMinimumHeight(180)
        layout.addWidget(body_view, 1)
        if unresolved:
            layout.addWidget(self._label(f"Unresolved variables: {', '.join(unresolved)}", "AdminStudioValidationBlocked"))
        else:
            layout.addWidget(self._label("All variables resolved.", "AdminStudioChip"))
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _admin_notification_preview_sample(self) -> dict[str, str]:
        return {
            "candidate_name": "Jordan Rivera",
            "company_name": "Launch Pad Learning",
            "department": "Education",
            "hiring_manager_name": "Harper Lee",
            "location": "Palmdale",
            "final_working_day": "2026-07-31",
            "generated_date": "2026-07-05",
            "notice_given": "2026-07-17",
            "date_notice_given": "2026-07-17",
            "last_working_day": "2026-07-31",
            "position": "Preschool Teacher",
            "position_name": "Preschool Teacher",
            "recruiter_name": "David Nord",
            "school": "Palmdale",
            "start_date": "2026-07-10",
        }

    def _render_admin_notification_template(self, template: str, sample: dict[str, str]) -> tuple[str, list[str]]:
        unresolved: list[str] = []
        try:
            fields = [field_name for _, field_name, _, _ in Formatter().parse(str(template or "")) if field_name]
        except ValueError:
            return str(template or ""), ["invalid template"]
        rendered = str(template or "")
        for field_name in fields:
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            if root not in sample:
                unresolved.append(root)
                continue
            rendered = rendered.replace("{" + field_name + "}", sample[root])
        return rendered, unresolved

    def _save_admin_notification_rule_editor(self) -> None:
        event_type = self.admin_notification_rule_event.text().strip()
        if not event_type:
            return
        self.admin_draft.update_notification_rule(
            event_type,
            {
                "id": str(getattr(self, "admin_selected_notification_rule_id", "") or ""),
                "label": self.admin_notification_rule_label.text().strip(),
                "active": "true" if self.admin_notification_rule_active.isChecked() else "false",
                "trigger_timing": self._admin_notification_selected_timing(),
                "date_field": self._admin_notification_selected_date_field(),
                "offset_days": str(self._admin_notification_signed_offset_days()),
                "subject_template": self.admin_notification_rule_subject.text().strip(),
                "body_template": self.admin_notification_rule_body.toPlainText().strip(),
                "recipients": self.admin_notification_rule_recipients.text().strip(),
            },
        )
        notifications = self._admin_tables.get("notifications")
        table_updated = False
        if notifications is not None:
            for row_index in range(notifications.rowCount()):
                id_item = notifications.item(row_index, 0)
                event_item = notifications.item(row_index, 1)
                if event_item is None:
                    continue
                id_matches = bool(self.admin_selected_notification_rule_id) and id_item is not None and id_item.text().strip() == self.admin_selected_notification_rule_id
                event_matches = event_item.text().strip() == event_type
                if id_matches or event_matches:
                    notifications.item(row_index, 2).setText(self.admin_notification_rule_label.text().strip())
                    notifications.item(row_index, 3).setText("true" if self.admin_notification_rule_active.isChecked() else "false")
                    notifications.item(row_index, 4).setText(self._admin_notification_selected_timing())
                    notifications.item(row_index, 5).setText(self._admin_notification_selected_date_field())
                    notifications.item(row_index, 6).setText(str(self._admin_notification_signed_offset_days()))
                    notifications.item(row_index, 7).setText(self.admin_notification_rule_subject.text().strip())
                    notifications.item(row_index, 8).setText(self.admin_notification_rule_body.toPlainText().strip())
                    notifications.item(row_index, 9).setText(self.admin_notification_rule_recipients.text().strip())
                    table_updated = True
                    break
            if not table_updated:
                insert_at = max(0, notifications.rowCount() - 1)
                notifications.insertRow(insert_at)
                values = [
                    str(getattr(self, "admin_selected_notification_rule_id", "") or ""),
                    event_type,
                    self.admin_notification_rule_label.text().strip(),
                    "true" if self.admin_notification_rule_active.isChecked() else "false",
                    self._admin_notification_selected_timing(),
                    self._admin_notification_selected_date_field(),
                    str(self._admin_notification_signed_offset_days()),
                    self.admin_notification_rule_subject.text().strip(),
                    self.admin_notification_rule_body.toPlainText().strip(),
                    self.admin_notification_rule_recipients.text().strip(),
                ]
                for column, value in enumerate(values):
                    notifications.setItem(insert_at, column, self.QtWidgets.QTableWidgetItem(value))
        self._sync_admin_notification_rule_validation()
        self._sync_admin_status()
        self._refresh_admin_notification_rule_cards(event_type)

    def _admin_prompt_summary_strip(self) -> Any:
        strip, layout = self._surface()
        strip.setObjectName("AdminStudioPromptSummaryStrip")
        row = self.QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for title, value in (
            ("Variables", "12 tokens"),
            ("Preview", "Live preview"),
            ("Version review", "5 versions"),
            ("Validation", "1 warning"),
        ):
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioPromptSummaryCard")
            card_layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
            card_layout.addWidget(self._label(value, "AdminStudioChip"))
            row.addWidget(card, 1)
        row.addStretch(1)
        settings = self.QtWidgets.QPushButton("Prompt Settings")
        settings.setObjectName("AdminStudioPromptSettingsButton")
        self._make_button_readable(settings)
        settings.clicked.connect(self._show_admin_prompt_settings_dialog)
        row.addWidget(settings)
        new_prompt = self.QtWidgets.QPushButton("New Prompt")
        new_prompt.setObjectName("AdminStudioPromptSummaryNewPromptButton")
        new_prompt.setProperty("adminRequiresEdit", True)
        new_prompt.setEnabled(False)
        self._make_button_readable(new_prompt)
        new_prompt.clicked.connect(self._create_admin_prompt_template)
        row.addWidget(new_prompt)
        layout.addLayout(row)
        return strip

    def _admin_prompt_editor_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        prompt_panel, prompt_layout = self._surface()
        prompt_panel.setObjectName("AdminStudioPromptEditorPanel")
        prompt_layout.addWidget(self._label("Prompt Templates", "AdminStudioConceptTitle"))
        self.admin_prompt_search = self.QtWidgets.QLineEdit()
        self.admin_prompt_search.setObjectName("AdminStudioPromptSearch")
        self.admin_prompt_search.setPlaceholderText("Search prompts...")
        self.admin_prompt_search.textChanged.connect(self._filter_admin_prompt_templates)
        prompt_layout.addWidget(self.admin_prompt_search)
        self.admin_prompt_template_cards: list[Any] = []
        self.admin_prompt_template_list_layout = prompt_layout
        first_prompt: tuple[str, str] | None = None
        for key, value in sorted(self.admin_draft.prompts.items()):
            if not isinstance(value, str):
                continue
            if first_prompt is None:
                first_prompt = (str(key), str(value))
            self._add_admin_prompt_template_card(str(key), str(value))
        new_prompt = self.QtWidgets.QPushButton("New Prompt Template")
        new_prompt.setObjectName("AdminStudioNewPromptTemplateButton")
        new_prompt.setProperty("adminRequiresEdit", True)
        new_prompt.setEnabled(False)
        self._make_button_readable(new_prompt)
        new_prompt.clicked.connect(self._create_admin_prompt_template)
        prompt_layout.addWidget(new_prompt)
        layout.addWidget(prompt_panel, 2)
        editor_panel, editor_layout = self._surface()
        editor_panel.setObjectName("AdminStudioPromptInspectorPanel")
        self.admin_prompt_editor_title = self._label("", "AdminStudioPromptEditorTitle")
        editor_layout.addWidget(self.admin_prompt_editor_title)
        self.admin_prompt_selected_status = self._label("", "AdminStudioPromptSelectedStatus")
        editor_layout.addWidget(self.admin_prompt_selected_status)
        self.admin_prompt_selected_description = self._label("", "AdminStudioPromptSelectedDescription")
        editor_layout.addWidget(self.admin_prompt_selected_description)
        self.admin_prompt_selected_metadata = self._label("", "AdminStudioPromptSelectedMetadata")
        editor_layout.addWidget(self.admin_prompt_selected_metadata)
        editor_layout.addWidget(self._label("Version notes", "AdminStudioConceptTitle"))
        self.admin_prompt_version_note = self.QtWidgets.QLineEdit()
        self.admin_prompt_version_note.setObjectName("AdminStudioPromptVersionNote")
        self.admin_prompt_version_note.setPlaceholderText("Describe what changed before publishing this prompt.")
        self.admin_prompt_version_note.setProperty("adminPromptEdit", True)
        self.admin_prompt_version_note.setEnabled(False)
        editor_layout.addWidget(self.admin_prompt_version_note)
        editor_toolbar = self.QtWidgets.QHBoxLayout()
        editor_toolbar.addWidget(self._label("Template", "AdminStudioChip"))
        self.admin_prompt_format_dropdown = self.QtWidgets.QComboBox()
        self.admin_prompt_format_dropdown.setObjectName("AdminStudioPromptFormatDropdown")
        self.admin_prompt_format_dropdown.addItems(["JSON", "Text"])
        self.admin_prompt_format_dropdown.currentTextChanged.connect(self._sync_admin_prompt_editor_footer)
        editor_toolbar.addWidget(self.admin_prompt_format_dropdown)
        editor_toolbar.addStretch(1)
        expand_button = self.QtWidgets.QPushButton("Expand")
        expand_button.setObjectName("AdminStudioPromptExpandButton")
        self._make_button_readable(expand_button)
        expand_button.clicked.connect(self._show_admin_prompt_expand_dialog)
        editor_toolbar.addWidget(expand_button)
        editor_layout.addLayout(editor_toolbar)
        self.admin_prompt_template_editor = self.QtWidgets.QPlainTextEdit()
        self.admin_prompt_template_editor.setObjectName("AdminStudioPromptTemplateEditor")
        self.admin_prompt_template_editor.setMinimumHeight(220)
        self.admin_prompt_template_editor.setProperty("adminPromptEdit", True)
        self.admin_prompt_template_editor.setEnabled(False)
        self.admin_prompt_template_editor.textChanged.connect(self._sync_admin_prompt_validation)
        self.admin_prompt_template_editor.textChanged.connect(self._sync_admin_prompt_editor_footer)
        self.admin_prompt_template_editor.cursorPositionChanged.connect(self._sync_admin_prompt_editor_footer)
        editor_layout.addWidget(self.admin_prompt_template_editor)
        self.admin_prompt_editor_footer = self._label(
            "JSON/text | Ln 1, Col 1 | Spaces: 2 | UTF-8 | LF",
            "AdminStudioPromptEditorFooter",
        )
        editor_layout.addWidget(self.admin_prompt_editor_footer)
        self.admin_prompt_validation = self._label("", "AdminStudioPromptValidation")
        editor_layout.addWidget(self.admin_prompt_validation)
        prompt_variables = ("payload_json", "transcript", "track", "candidate_name", "flow_index", "question_id", "question_label", "skipped", "timestamp")
        self.admin_prompt_variables = self._label(
            "  ".join(f"{{{variable}}}" for variable in prompt_variables),
            "AdminStudioPromptVariables",
        )
        editor_layout.addWidget(self.admin_prompt_variables)
        variable_row = self.QtWidgets.QHBoxLayout()
        for variable in prompt_variables:
            button = self.QtWidgets.QPushButton(f"{{{variable}}}")
            button.setObjectName(f"AdminStudioPromptVariable_{variable}")
            button.setProperty("adminRequiresEdit", True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, token=f"{{{variable}}}": self._insert_admin_prompt_variable(token))
            variable_row.addWidget(button)
        variable_row.addStretch(1)
        editor_layout.addLayout(variable_row)
        editor_layout.addWidget(self._label("Version History", "AdminStudioConceptTitle"))
        editor_layout.addWidget(self._label("v3 Draft · v2 Published · v1 Published"))
        history_button = self.QtWidgets.QPushButton("View Version History")
        history_button.setObjectName("AdminStudioVersionHistoryButton")
        history_button.clicked.connect(self._show_admin_version_history_dialog)
        editor_layout.addWidget(history_button)
        preview_button = self.QtWidgets.QPushButton("Open Preview")
        preview_button.setObjectName("AdminStudioPromptPreviewButton")
        preview_button.clicked.connect(self._show_admin_prompt_preview_dialog)
        editor_layout.addWidget(preview_button)
        self.admin_prompt_save_button = self._primary_button("Save Changes")
        self.admin_prompt_save_button.setObjectName("AdminStudioPromptSave")
        self.admin_prompt_save_button.setProperty("adminRequiresEdit", True)
        self.admin_prompt_save_button.setEnabled(False)
        self.admin_prompt_save_button.clicked.connect(self._save_admin_prompt_editor)
        editor_layout.addWidget(self.admin_prompt_save_button)
        layout.addWidget(editor_panel, 2)
        right_panel, right_layout = self._surface()
        right_panel.setObjectName("AdminStudioPromptRightInspectorPanel")
        tabs = self.QtWidgets.QTabWidget()
        tabs.setObjectName("AdminStudioPromptInspectorTabs")
        inspector_tab = self.QtWidgets.QWidget()
        inspector_layout = self.QtWidgets.QVBoxLayout(inspector_tab)
        inspector_layout.addWidget(self._label("Variables", "AdminStudioConceptTitle"))
        inspector_layout.addWidget(self._label("  ".join(f"{{{variable}}}" for variable in prompt_variables), "AdminStudioPromptInspectorVariables"))
        inspector_layout.addWidget(self._label("Required variables", "AdminStudioConceptTitle"))
        inspector_layout.addWidget(self._label("answer_summary_user requires {payload_json}. executive_summary_user requires {transcript}."))
        inspector_layout.addWidget(self._label("Open Preview"))
        inspector_layout.addWidget(self._label("Use sample data to confirm variables resolve before publishing."))
        inspector_layout.addStretch(1)
        activity_tab = self.QtWidgets.QWidget()
        activity_layout = self.QtWidgets.QVBoxLayout(activity_tab)
        activity_layout.addWidget(self._label("Version History", "AdminStudioConceptTitle"))
        activity_layout.addWidget(self._label("v3 Draft · Today · David Nord"))
        activity_layout.addWidget(self._label("Save Draft updates version history."))
        activity_layout.addWidget(self._label("Publish Changes publishes the prompt version if validation passes."))
        activity_layout.addWidget(self._label("v2 Published · Previous publish · David Nord"))
        activity_layout.addStretch(1)
        tabs.addTab(inspector_tab, "Inspector")
        tabs.addTab(activity_tab, "Activity")
        right_layout.addWidget(tabs)
        layout.addWidget(right_panel, 1)
        if first_prompt is not None:
            self._select_admin_prompt_template(first_prompt[0], first_prompt[1])
        return group

    def _add_admin_prompt_template_card(self, key: str, value: str) -> Any:
        card, card_layout = self._surface()
        card.setObjectName("AdminStudioPromptTemplateCard")
        card.setProperty("adminPromptKey", str(key))
        card.setProperty("adminPromptSearchText", f"{key} {value}".lower())
        button = self.QtWidgets.QPushButton(str(key))
        button.setObjectName(f"AdminStudioPromptTemplateButton_{self._admin_object_suffix(str(key))}")
        self._make_button_readable(button)
        button.clicked.connect(lambda _checked=False, prompt_key=str(key), prompt_value=str(value): self._select_admin_prompt_template(prompt_key, prompt_value))
        card_layout.addWidget(button)
        card_layout.addWidget(self._label("Warning" if not value.strip() else "OK", "AdminStudioChip"))
        card_layout.addWidget(self._label(value[:240]))
        self.admin_prompt_template_cards.append(card)
        self.admin_prompt_template_list_layout.addWidget(card)
        return card

    def _filter_admin_prompt_templates(self, text: str) -> None:
        query = str(text or "").strip().lower()
        for card in getattr(self, "admin_prompt_template_cards", []):
            search_text = str(card.property("adminPromptSearchText") or "")
            card.setVisible(not query or query in search_text)

    def _create_admin_prompt_template(self) -> None:
        if not self.admin_edit_mode:
            return
        index = 1
        while f"custom_prompt_{index}" in self.admin_draft.prompts:
            index += 1
        key = f"custom_prompt_{index}"
        value = ""
        self.admin_draft.update_prompt(key, value)
        self._add_admin_prompt_template_card(key, value)
        table = self._admin_tables.get("prompts")
        if table is not None:
            row = table.rowCount()
            table.insertRow(row)
            for column, cell_value in enumerate([key, value]):
                item = self.QtWidgets.QTableWidgetItem(cell_value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, cell_value)
                if column != 1:
                    item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, column, item)
        self._filter_admin_prompt_templates(self.admin_prompt_search.text())
        self._select_admin_prompt_template(key, value)
        self._sync_admin_status()

    def _select_admin_prompt_template(self, key: str, value: str) -> None:
        self.admin_selected_prompt_key = str(key)
        self.admin_prompt_editor_title.setText(str(key))
        self.admin_prompt_selected_status.setText(self._admin_prompt_status(str(key), str(value)))
        self.admin_prompt_selected_description.setText(self._admin_prompt_description(str(key)))
        self.admin_prompt_selected_metadata.setText(self._admin_prompt_metadata(str(key)))
        version_note = getattr(self, "admin_prompt_version_note", None)
        if version_note is not None:
            version_note.setText(str(self.admin_draft.prompt_version_notes.get(str(key), "")))
        self.admin_prompt_template_editor.setPlainText(str(value))
        self._sync_admin_prompt_validation()
        self._sync_admin_prompt_editor_footer()

    def _admin_prompt_status(self, key: str, value: str) -> str:
        if not str(value or "").strip():
            return "Warning"
        if key == "answer_summary_user":
            return "Warning"
        return "OK"

    def _admin_prompt_description(self, key: str) -> str:
        descriptions = {
            "answer_summary_user": "Early childhood hiring notes summary",
            "executive_summary_user": "Executive summary section",
        }
        return descriptions.get(key, "Custom DeepSeek prompt template")

    def _admin_prompt_metadata(self, key: str) -> str:
        version = "v3" if key == "answer_summary_user" else "v2" if key == "executive_summary_user" else "v1"
        status = "Draft" if key.endswith("_user") or key.startswith("custom_prompt_") else "Published"
        return f"Version: {version} · Status: {status} · Last modified: May 21, 2025 · 10:42 AM"

    def _sync_admin_prompt_validation(self) -> None:
        text = self.admin_prompt_template_editor.toPlainText().strip()
        if not text:
            self.admin_prompt_validation.setText("Prompt cannot be blank.")
            return
        _rendered, unresolved = self._render_admin_prompt_template(text, self._admin_prompt_preview_sample())
        if unresolved:
            self.admin_prompt_validation.setText(f"Unknown variables: {', '.join(sorted(set(unresolved)))}.")
            return
        prompt_key = str(getattr(self, "admin_selected_prompt_key", "") or "")
        required_variables: set[str] = set()
        if prompt_key == "answer_summary_user":
            required_variables.add("payload_json")
        if prompt_key == "executive_summary_user":
            required_variables.add("transcript")
        fields = {
            field_name.split(".", 1)[0].split("[", 1)[0]
            for _, field_name, _, _ in Formatter().parse(text)
            if field_name
        }
        missing = sorted(required_variables - fields)
        if missing:
            self.admin_prompt_validation.setText(f"Required variables missing: {', '.join(missing)}.")
            return
        self.admin_prompt_validation.setText("JSON/text prompt looks ready.")

    def _sync_admin_prompt_editor_footer(self) -> None:
        editor = getattr(self, "admin_prompt_template_editor", None)
        footer = getattr(self, "admin_prompt_editor_footer", None)
        if editor is None or footer is None:
            return
        cursor = editor.textCursor()
        line = cursor.blockNumber() + 1
        column = cursor.columnNumber() + 1
        text = editor.toPlainText()
        line_ending = "CRLF" if "\r\n" in text else "LF"
        format_dropdown = getattr(self, "admin_prompt_format_dropdown", None)
        format_label = format_dropdown.currentText() if format_dropdown is not None else "JSON"
        footer.setText(f"{format_label} | Ln {line}, Col {column} | Spaces: 2 | UTF-8 | {line_ending}")

    def _insert_admin_prompt_variable(self, token: str) -> None:
        if not self.admin_prompt_template_editor.isEnabled():
            return
        cursor = self.admin_prompt_template_editor.textCursor()
        cursor.insertText(str(token or ""))
        self.admin_prompt_template_editor.setTextCursor(cursor)
        self._sync_admin_prompt_validation()

    def _show_admin_prompt_expand_dialog(self) -> None:
        dialog = self._build_admin_prompt_expand_dialog()
        self.admin_prompt_expand_dialog = dialog
        dialog.show()

    def _build_admin_prompt_expand_dialog(self) -> Any:
        prompt_key = str(getattr(self, "admin_selected_prompt_key", "") or "Prompt")
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioPromptExpandDialog")
        dialog.setWindowTitle("Expanded Prompt Editor")
        dialog.resize(900, 700)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Expanded Prompt Editor", "SectionTitle"))
        layout.addWidget(self._label(prompt_key, "AdminStudioConceptTitle"))
        expanded_editor = self.QtWidgets.QPlainTextEdit(self.admin_prompt_template_editor.toPlainText())
        expanded_editor.setObjectName("AdminStudioPromptExpandEditor")
        expanded_editor.setEnabled(self.admin_prompt_template_editor.isEnabled())
        expanded_editor.setMinimumHeight(460)
        layout.addWidget(expanded_editor, 1)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        apply_button = self.QtWidgets.QPushButton("Apply to Editor")
        apply_button.setObjectName("AdminStudioPromptExpandApplyButton")
        apply_button.setEnabled(self.admin_prompt_template_editor.isEnabled())
        apply_button.clicked.connect(lambda: self.admin_prompt_template_editor.setPlainText(expanded_editor.toPlainText()))
        actions.addWidget(apply_button)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _show_admin_prompt_settings_dialog(self) -> None:
        dialog = self._build_admin_prompt_settings_dialog()
        self.admin_prompt_settings_dialog = dialog
        dialog.show()

    def _build_admin_prompt_settings_dialog(self) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioPromptSettingsDialog")
        dialog.setWindowTitle("Prompt Settings")
        dialog.resize(720, 520)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Prompt Settings", "SectionTitle"))
        layout.addWidget(self._label("Validation policy", "AdminStudioConceptTitle"))
        policy_card, policy_layout = self._surface()
        policy_card.setObjectName("AdminStudioPromptSettingsPolicyCard")
        policy_layout.addWidget(self._label("Unknown variables block publishing."))
        policy_layout.addWidget(self._label("Blank prompts block publishing."))
        policy_layout.addWidget(self._label("Version notes are required for changed prompts before publish."))
        layout.addWidget(policy_card)
        layout.addWidget(self._label("Required variables", "AdminStudioConceptTitle"))
        required_card, required_layout = self._surface()
        required_card.setObjectName("AdminStudioPromptSettingsRequiredVariables")
        required_layout.addWidget(self._label("answer_summary_user requires {payload_json}."))
        required_layout.addWidget(self._label("executive_summary_user requires {transcript}."))
        required_layout.addWidget(self._label("Custom prompts may use any supported sample-data variable."))
        layout.addWidget(required_card)
        layout.addWidget(self._label("Supported variables", "AdminStudioConceptTitle"))
        variables = ("payload_json", "transcript", "track", "candidate_name", "flow_index", "question_id", "question_label", "skipped", "timestamp")
        layout.addWidget(self._label("  ".join(f"{{{variable}}}" for variable in variables), "AdminStudioPromptSettingsVariables"))
        layout.addStretch(1)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _show_admin_version_history_dialog(self) -> None:
        dialog = self._build_admin_version_history_dialog()
        self.admin_version_history_dialog = dialog
        dialog.show()

    def _show_admin_global_version_history_dialog(self) -> None:
        dialog = self._build_admin_version_history_dialog("All admin artifacts")
        self.admin_version_history_dialog = dialog
        dialog.show()

    def _build_admin_version_history_dialog(self, item_name: str | None = None) -> Any:
        item_name = str(item_name or getattr(self, "admin_selected_prompt_key", "") or "Admin item")
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioVersionHistoryDialog")
        dialog.setWindowTitle("Version History")
        dialog.resize(760, 560)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Version History", "SectionTitle"))
        layout.addWidget(self._label(item_name, "AdminStudioConceptTitle"))
        layout.addWidget(self._label("Who changed what and when. Draft entries still require Review Changes before publish."))
        entries = self._admin_version_history_entries(item_name)
        for entry in entries:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioVersionHistoryEntry")
            card_layout.addWidget(self._label(f"{entry['version']} · {entry['status']}", "AdminStudioChip"))
            card_layout.addWidget(self._label(f"{entry['date']} · {entry['user']}"))
            card_layout.addWidget(self._label(entry["note"]))
            card_layout.addWidget(self._label(entry["changed"]))
            layout.addWidget(card)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _admin_version_history_entries(self, item_name: str) -> list[dict[str, str]]:
        if item_name == "All admin artifacts":
            return [
                {
                    "version": "Prompts",
                    "status": "Draft",
                    "date": "Today",
                    "user": "David Nord",
                    "note": "Prompt template change prepared for review",
                    "changed": "DeepSeek Prompts",
                },
                {
                    "version": "Rubrics",
                    "status": "Published",
                    "date": "Previous publish",
                    "user": "David Nord",
                    "note": "Trait weights and descriptors reviewed",
                    "changed": "Rubrics",
                },
                {
                    "version": "Notifications",
                    "status": "Published",
                    "date": "Previous publish",
                    "user": "David Nord",
                    "note": "Notification rules and recipients reviewed",
                    "changed": "Notifications",
                },
                {
                    "version": "JSON files",
                    "status": "Published",
                    "date": "Initial setup",
                    "user": "David Nord",
                    "note": "Source configuration snapshot",
                    "changed": "Advanced JSON",
                },
            ]
        return [
            {
                "version": "v3",
                "status": "Draft",
                "date": "Today",
                "user": "David Nord",
                "note": "Changed prompt template",
                "changed": item_name,
            },
            {
                "version": "v2",
                "status": "Published",
                "date": "Previous publish",
                "user": "David Nord",
                "note": "Refined instructions and examples",
                "changed": item_name,
            },
            {
                "version": "v1",
                "status": "Published",
                "date": "Initial setup",
                "user": "David Nord",
                "note": "Initial version",
                "changed": item_name,
            },
        ]

    def _show_admin_prompt_preview_dialog(self) -> None:
        dialog = self._build_admin_prompt_preview_dialog()
        self.admin_prompt_preview_dialog = dialog
        dialog.show()

    def _build_admin_prompt_preview_dialog(self) -> Any:
        sample = self._admin_prompt_preview_sample()
        rendered, unresolved = self._render_admin_prompt_template(self.admin_prompt_template_editor.toPlainText(), sample)
        response = {
            "candidate_name": sample["candidate_name"],
            "summary": "Candidate described calming a child during transition.",
            "evidence": ["calm voice", "child-level posture"],
        }
        response_text = json.dumps(response, indent=2)
        try:
            json.loads(response_text)
            validation = "JSON validation: ready"
        except json.JSONDecodeError as exc:
            validation = f"JSON validation error: {exc.msg}"
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioPromptPreviewDialog")
        dialog.setWindowTitle("Prompt Preview")
        dialog.resize(760, 620)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Prompt Preview", "SectionTitle"))
        layout.addWidget(self._label("Rendered with sample interview transcript data."))
        layout.addWidget(self._label("Rendered prompt", "AdminStudioConceptTitle"))
        layout.addWidget(self._label(rendered[:900] or "(blank prompt)"))
        rendered_view = self.QtWidgets.QPlainTextEdit(rendered or "(blank prompt)")
        rendered_view.setObjectName("AdminStudioPromptPreviewRenderedPrompt")
        rendered_view.setReadOnly(True)
        rendered_view.setMinimumHeight(150)
        layout.addWidget(rendered_view, 1)
        layout.addWidget(self._label("Model response preview", "AdminStudioConceptTitle"))
        layout.addWidget(self._label(response["summary"]))
        response_view = self.QtWidgets.QPlainTextEdit(response_text)
        response_view.setObjectName("AdminStudioPromptPreviewModelResponse")
        response_view.setReadOnly(True)
        response_view.setMinimumHeight(140)
        layout.addWidget(response_view, 1)
        layout.addWidget(self._label(validation, "AdminStudioChip"))
        if unresolved:
            layout.addWidget(self._label(f"Unresolved variables: {', '.join(sorted(set(unresolved)))}", "AdminStudioValidationBlocked"))
        else:
            layout.addWidget(self._label("All variables resolved.", "AdminStudioChip"))
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _admin_prompt_preview_sample(self) -> dict[str, str]:
        payload = {
            "candidate_name": "Maya Patel",
            "track": "Preschool",
            "answers": [
                {
                    "flow_index": 3,
                    "question_id": "trait_1",
                    "answer": "Candidate described calming a child during transition.",
                }
            ],
        }
        return {
            "candidate_name": "Maya Patel",
            "flow_index": "3",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "question_id": "trait_1",
            "question_label": "Empathy & Respect for Children",
            "skipped": "false",
            "timestamp": "2026-07-03T09:00:00",
            "track": "Preschool",
            "transcript": "Candidate described calming a child during transition.",
        }

    def _render_admin_prompt_template(self, template: str, sample: dict[str, str]) -> tuple[str, list[str]]:
        unresolved: list[str] = []
        try:
            fields = [field_name for _, field_name, _, _ in Formatter().parse(str(template or "")) if field_name]
        except ValueError:
            return str(template or ""), ["invalid template"]
        rendered = str(template or "")
        for field_name in fields:
            root = field_name.split(".", 1)[0].split("[", 1)[0]
            if root not in sample:
                unresolved.append(root)
                continue
            rendered = rendered.replace("{" + field_name + "}", sample[root])
        return rendered, unresolved

    def _save_admin_prompt_editor(self) -> None:
        key = str(getattr(self, "admin_selected_prompt_key", "") or "").strip()
        if not key:
            return
        value = self.admin_prompt_template_editor.toPlainText()
        self.admin_draft.update_prompt(key, value)
        version_note = getattr(self, "admin_prompt_version_note", None)
        if version_note is not None:
            self.admin_draft.update_prompt_version_note(key, version_note.text())
        table = self._admin_tables.get("prompts")
        if table is not None:
            for row_index in range(table.rowCount()):
                key_item = table.item(row_index, 0)
                if key_item is not None and key_item.text().strip() == key:
                    table.item(row_index, 1).setText(value)
                    break
        self._sync_admin_prompt_validation()
        self._sync_admin_status()

    def _admin_advanced_json_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        outer_layout = self.QtWidgets.QVBoxLayout(group)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        summary_strip, summary_layout = self._surface()
        summary_strip.setObjectName("AdminStudioJsonSummaryStrip")
        summary_cards = (
            ("Read-only review", "Files are protected and cannot be edited in this view."),
            ("Last modified", "Source JSON metadata"),
            ("Validation status", "Across 5 files"),
            ("Open in editor", "Open the selected file in the JSON editor."),
        )
        for title, detail in summary_cards:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioJsonSummaryCard")
            card_layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
            card_layout.addWidget(self._label(detail))
            summary_layout.addWidget(card)
        summary_layout.addStretch(1)
        outer_layout.addWidget(summary_strip)
        content = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        files_panel, files_layout = self._surface()
        files_panel.setObjectName("AdminStudioJsonFilesPanel")
        files_layout.addWidget(self._label("JSON Files (5)", "AdminStudioConceptTitle"))
        files = [
            ("rubric.json", DEFAULT_RUBRIC_PATH, "Core configuration"),
            ("question_overrides.json", QUESTIONS_OVERRIDE_PATH, "Override rules"),
            ("school_offer_settings.json", SCHOOL_OFFER_SETTINGS_PATH, "School offers & settings"),
            ("deepseek_prompts.json", DEEPSEEK_PROMPTS_CONFIG_PATH, "AI prompt templates"),
            ("interview_app_settings.json", INTERVIEW_APP_SETTINGS_PATH, "Interview app configuration"),
        ]
        self.admin_json_file_cards = {}
        first_file: tuple[str, Path, str] | None = None
        for name, path, description in files:
            path_obj = Path(path)
            if first_file is None:
                first_file = (name, path_obj, description)
            card, card_layout = self._surface()
            card.setObjectName(f"AdminStudioJsonFileCard_{self._admin_object_suffix(name)}")
            card.setProperty("adminJsonFileName", name)
            card.setProperty("adminJsonSelected", False)
            self.admin_json_file_cards[name] = card
            button = self.QtWidgets.QPushButton(name)
            button.setObjectName(f"AdminStudioJsonFileButton_{self._admin_object_suffix(name)}")
            self._make_button_readable(button)
            button.clicked.connect(lambda _checked=False, file_name=name, file_path=path_obj, file_description=description: self._select_admin_json_file(file_name, file_path, file_description))
            card_layout.addWidget(button)
            card_layout.addWidget(self._label(description))
            card_layout.addWidget(self._label("Healthy" if path_obj.exists() else "Review", "AdminStudioChip"))
            card_layout.addWidget(self._label(self._admin_file_size_label(path_obj)))
            files_layout.addWidget(card)
        layout.addWidget(files_panel, 1)
        viewer_panel, viewer_layout = self._surface()
        viewer_panel.setObjectName("AdminStudioJsonFileDetailPanel")
        self.admin_json_selected_file = self._label("", "AdminStudioJsonSelectedFile")
        viewer_layout.addWidget(self.admin_json_selected_file)
        viewer_actions = self.QtWidgets.QHBoxLayout()
        readonly_mode = self.QtWidgets.QComboBox()
        readonly_mode.setObjectName("AdminStudioJsonViewerMode")
        readonly_mode.addItem("Read-only")
        readonly_mode.setEnabled(False)
        viewer_actions.addWidget(readonly_mode)
        copy_viewer = self.QtWidgets.QPushButton("Copy")
        copy_viewer.setObjectName("AdminStudioCopyJsonViewerButton")
        copy_viewer.clicked.connect(self._copy_admin_json_viewer_text)
        viewer_actions.addWidget(copy_viewer)
        expand = self.QtWidgets.QPushButton("Expand")
        expand.setObjectName("AdminStudioExpandJsonViewerButton")
        expand.clicked.connect(self._show_admin_json_expand_dialog)
        viewer_actions.addWidget(expand)
        viewer_actions.addStretch(1)
        viewer_layout.addLayout(viewer_actions)
        viewer_row = self.QtWidgets.QHBoxLayout()
        self.admin_json_line_numbers = self.QtWidgets.QPlainTextEdit()
        self.admin_json_line_numbers.setObjectName("AdminStudioJsonLineNumbers")
        self.admin_json_line_numbers.setReadOnly(True)
        self.admin_json_line_numbers.setFixedWidth(48)
        self.admin_json_line_numbers.setMinimumHeight(260)
        self.admin_json_line_numbers.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.admin_json_line_numbers.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.admin_json_line_numbers.setFocusPolicy(self.QtCore.Qt.FocusPolicy.NoFocus)
        viewer_row.addWidget(self.admin_json_line_numbers)
        self.admin_json_code_viewer = self.QtWidgets.QPlainTextEdit()
        self.admin_json_code_viewer.setObjectName("AdminStudioJsonCodeViewer")
        self.admin_json_code_viewer.setReadOnly(True)
        self.admin_json_code_viewer.setMinimumHeight(260)
        self.admin_json_code_viewer.verticalScrollBar().valueChanged.connect(
            self.admin_json_line_numbers.verticalScrollBar().setValue
        )
        self.admin_json_syntax_highlighter = self._install_admin_json_syntax_highlighter(
            self.admin_json_code_viewer.document()
        )
        viewer_row.addWidget(self.admin_json_code_viewer, 1)
        viewer_layout.addLayout(viewer_row)
        self.admin_json_viewer_footer = self._label("JSON | Line 1, Column 1 | Ready", "AdminStudioJsonViewerFooter")
        viewer_layout.addWidget(self.admin_json_viewer_footer)
        self.admin_json_copy_status = self._label("", "AdminStudioJsonCopyStatus")
        viewer_layout.addWidget(self.admin_json_copy_status)
        viewer_layout.addWidget(self._label("File details", "AdminStudioConceptTitle"))
        detail_actions = self.QtWidgets.QHBoxLayout()
        detail_actions.addStretch(1)
        copy_path = self.QtWidgets.QPushButton("Copy Path")
        copy_path.setObjectName("AdminStudioCopyJsonPathButton")
        copy_path.clicked.connect(self._copy_admin_json_file_path)
        detail_actions.addWidget(copy_path)
        viewer_layout.addLayout(detail_actions)
        self.admin_json_file_path = self._label("", "AdminStudioJsonFilePath")
        viewer_layout.addWidget(self.admin_json_file_path)
        self.admin_json_validation_result = self._label("", "AdminStudioJsonValidationResult")
        viewer_layout.addWidget(self.admin_json_validation_result)
        self.admin_json_file_summary = self._label("", "AdminStudioJsonFileSummary")
        viewer_layout.addWidget(self.admin_json_file_summary)
        self.admin_json_issue_card, issue_layout = self._surface()
        self.admin_json_issue_card.setObjectName("AdminStudioJsonIssueCard")
        issue_layout.addWidget(self._label("Issues (0)", "AdminStudioConceptTitle"))
        self.admin_json_issue_text = self._label("No issues found.", "AdminStudioJsonIssueText")
        issue_layout.addWidget(self.admin_json_issue_text)
        self.admin_json_issue_jump = self.QtWidgets.QPushButton("Jump to issue")
        self.admin_json_issue_jump.setObjectName("AdminStudioJsonIssueJumpButton")
        self.admin_json_issue_jump.setEnabled(False)
        self.admin_json_issue_jump.clicked.connect(self._jump_admin_json_issue_line)
        issue_layout.addWidget(self.admin_json_issue_jump)
        viewer_layout.addWidget(self.admin_json_issue_card)
        readonly_panel, readonly_layout = self._surface()
        readonly_panel.setObjectName("AdminStudioJsonReadOnlyNoticePanel")
        self.admin_json_readonly_notice = self._label("This is a read-only view. To make changes, open the file in the JSON editor.", "AdminStudioChip")
        readonly_layout.addWidget(self.admin_json_readonly_notice)
        viewer_layout.addWidget(readonly_panel)
        open_editor = self.QtWidgets.QPushButton("Open in Editor")
        open_editor.setObjectName("AdminStudioOpenJsonEditorButton")
        open_editor.clicked.connect(self._show_admin_json_editor_dialog)
        viewer_layout.addWidget(open_editor)
        layout.addWidget(viewer_panel, 2)
        outer_layout.addWidget(content, 1)
        if first_file is not None:
            self._select_admin_json_file(first_file[0], first_file[1], first_file[2])
        return group

    def _copy_admin_json_viewer_text(self) -> None:
        text = self.admin_json_code_viewer.toPlainText()
        self.QtWidgets.QApplication.clipboard().setText(text)
        name, _path, _description = getattr(self, "admin_selected_json_file", ("selected file", Path(), ""))
        self.admin_json_copy_status.setText(f"Copied {name} to clipboard." if text else "No JSON text to copy.")

    def _copy_admin_json_file_path(self) -> None:
        name, path, _description = getattr(self, "admin_selected_json_file", ("selected file", Path(), ""))
        path_text = str(path)
        self.QtWidgets.QApplication.clipboard().setText(path_text)
        self.admin_json_copy_status.setText(f"Copied path for {name} to clipboard." if path_text else "No JSON path to copy.")

    def _sync_admin_json_line_numbers(self) -> None:
        line_count = max(self.admin_json_code_viewer.document().blockCount(), 1)
        marker_line = getattr(self, "admin_json_line_marker", None)
        lines = []
        for index in range(1, line_count + 1):
            lines.append(f"! {index}" if marker_line == index else str(index))
        self.admin_json_line_numbers.setPlainText("\n".join(lines))

    def _install_admin_json_syntax_highlighter(self, document: Any) -> Any:
        QtGui = self.QtGui

        class JsonSyntaxHighlighter(QtGui.QSyntaxHighlighter):
            def __init__(inner_self, target_document: Any) -> None:
                super().__init__(target_document)
                inner_self.setObjectName("AdminStudioJsonSyntaxHighlighter")
                inner_self.setProperty("adminSyntax", "json")
                inner_self._formats = {
                    "key": _json_format("#7c3aed", bold=True),
                    "string": _json_format("#047857"),
                    "number": _json_format("#b45309"),
                    "literal": _json_format("#dc2626", bold=True),
                }

            def highlightBlock(inner_self, text: str) -> None:
                for match in re.finditer(r'"([^"\\]|\\.)*"', text):
                    inner_self.setFormat(match.start(), match.end() - match.start(), inner_self._formats["string"])
                for match in re.finditer(r'"([^"\\]|\\.)*"(?=\s*:)', text):
                    inner_self.setFormat(match.start(), match.end() - match.start(), inner_self._formats["key"])
                for match in re.finditer(r'(?<![\w.])-?\d+(\.\d+)?([eE][+-]?\d+)?(?![\w.])', text):
                    inner_self.setFormat(match.start(), match.end() - match.start(), inner_self._formats["number"])
                for match in re.finditer(r'\b(true|false|null)\b', text):
                    inner_self.setFormat(match.start(), match.end() - match.start(), inner_self._formats["literal"])

        def _json_format(color: str, *, bold: bool = False) -> Any:
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(color))
            if bold:
                fmt.setFontWeight(QtGui.QFont.Weight.Bold)
            return fmt

        return JsonSyntaxHighlighter(document)

    def _highlight_admin_json_issue_line(self, line: int | None) -> None:
        selections: list[Any] = []
        if line is not None:
            block = self.admin_json_code_viewer.document().findBlockByNumber(max(int(line), 1) - 1)
            if block.isValid():
                cursor = self.admin_json_code_viewer.textCursor()
                cursor.setPosition(block.position())
                selection = self.QtWidgets.QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(self.QtGui.QColor("#fee2e2"))
                selection.format.setProperty(
                    self.QtGui.QTextFormat.Property.FullWidthSelection,
                    True,
                )
                selections.append(selection)
        self.admin_json_code_viewer.setExtraSelections(selections)

    def _jump_admin_json_issue_line(self) -> None:
        line = max(int(getattr(self, "admin_json_issue_line", 1) or 1), 1)
        cursor = self.admin_json_code_viewer.textCursor()
        block = self.admin_json_code_viewer.document().findBlockByNumber(line - 1)
        cursor.setPosition(max(block.position(), 0))
        self.admin_json_code_viewer.setTextCursor(cursor)
        self.admin_json_code_viewer.setFocus()

    def _admin_file_size_label(self, path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return "Missing"
        if size < 1024:
            return f"{size} B"
        return f"{size / 1024:.1f} KB"

    def _select_admin_json_file(self, name: str, path: Path, description: str) -> None:
        self.admin_selected_json_file = (name, Path(path), description)
        for card_name, card in getattr(self, "admin_json_file_cards", {}).items():
            selected = card_name == name
            card.setProperty("adminJsonSelected", selected)
            card.setProperty("adminCardTone", "selected" if selected else "")
            card.style().unpolish(card)
            card.style().polish(card)
        self.admin_json_selected_file.setText(name)
        self.admin_json_file_path.setText(str(path))
        self.admin_json_file_summary.setText(description)
        self.admin_json_issue_text.setText("No issues found.")
        self.admin_json_issue_line = 1
        self.admin_json_line_marker = None
        self.admin_json_issue_jump.setEnabled(False)
        self.admin_json_viewer_footer.setText("JSON | Line 1, Column 1 | Ready")
        if not path.exists():
            self.admin_json_code_viewer.setPlainText("File not found.")
            self.admin_json_line_marker = 1
            self._sync_admin_json_line_numbers()
            self._highlight_admin_json_issue_line(1)
            self.admin_json_validation_result.setText("1 issue")
            self.admin_json_issue_text.setText("Line 1: File not found.")
            self.admin_json_issue_line = 1
            self.admin_json_issue_jump.setEnabled(True)
            self.admin_json_viewer_footer.setText("JSON | Line 1, Column 1 | 1 issue on this line")
            return
        try:
            raw_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = json.loads(raw_text)
            display = json.dumps(parsed, indent=2, ensure_ascii=False)
            self.admin_json_validation_result.setText("Healthy")
            issue_line: int | None = None
            self.admin_json_line_marker = None
        except Exception as exc:
            display = raw_text
            self.admin_json_validation_result.setText("1 issue")
            line = getattr(exc, "lineno", 1)
            column = getattr(exc, "colno", 1)
            self.admin_json_issue_text.setText(f"Line {line}: {exc}")
            self.admin_json_issue_line = int(line)
            self.admin_json_line_marker = int(line)
            self.admin_json_issue_jump.setEnabled(True)
            self.admin_json_viewer_footer.setText(f"JSON | Line {line}, Column {column} | 1 issue on this line")
            self.admin_json_file_summary.setText(f"{description}\nIssue: {exc}")
            issue_line = int(line)
        self.admin_json_code_viewer.setPlainText(display)
        self._sync_admin_json_line_numbers()
        self._highlight_admin_json_issue_line(issue_line)

    def _show_admin_json_editor_dialog(self) -> None:
        dialog = self._build_admin_json_editor_dialog()
        self.admin_json_editor_dialog = dialog
        dialog.show()

    def _show_admin_json_expand_dialog(self) -> None:
        dialog = self._build_admin_json_expand_dialog()
        self.admin_json_expand_dialog = dialog
        dialog.show()

    def _build_admin_json_expand_dialog(self) -> Any:
        name, _path, description = getattr(self, "admin_selected_json_file", ("selected file", Path(), ""))
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioJsonExpandDialog")
        dialog.setWindowTitle("Expanded JSON Viewer")
        dialog.resize(920, 720)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Expanded JSON Viewer", "SectionTitle"))
        layout.addWidget(self._label(f"{name} · {description}", "AdminStudioConceptTitle"))
        viewer = self.QtWidgets.QPlainTextEdit(self.admin_json_code_viewer.toPlainText())
        viewer.setObjectName("AdminStudioJsonExpandViewer")
        viewer.setReadOnly(True)
        viewer.setMinimumHeight(520)
        layout.addWidget(viewer, 1)
        actions = self.QtWidgets.QHBoxLayout()
        copy = self.QtWidgets.QPushButton("Copy")
        copy.setObjectName("AdminStudioJsonExpandCopyButton")
        copy.clicked.connect(lambda: self.QtWidgets.QApplication.clipboard().setText(viewer.toPlainText()))
        actions.addWidget(copy)
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _build_admin_json_editor_dialog(self) -> Any:
        name, path, description = getattr(self, "admin_selected_json_file", ("", Path(), ""))
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioJsonEditorDialog")
        dialog.setWindowTitle("JSON Editor")
        dialog.resize(820, 680)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("JSON Editor", "SectionTitle"))
        layout.addWidget(self._label(f"{name} · {description}"))
        layout.addWidget(self._label("Stronger warnings: validate JSON before saving. Draft changes still require Review Changes and Publish Changes.", "AdminStudioValidationBlocked"))
        editor = self.QtWidgets.QPlainTextEdit()
        editor.setObjectName("AdminStudioJsonEditorText")
        editor.setMinimumHeight(420)
        editor.setPlainText(self._admin_json_editor_initial_text(str(name), Path(path)))
        editor.setProperty("adminJsonEdit", True)
        editor.setEnabled(bool(getattr(self, "admin_edit_mode", False)))
        layout.addWidget(editor, 1)
        status = self._label("JSON validation: ready", "AdminStudioJsonEditorValidation")
        layout.addWidget(status)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        actions.addWidget(cancel)
        save = self._primary_button("Save Draft")
        save.setObjectName("AdminStudioJsonEditorSaveDraft")
        save.setProperty("adminRequiresEdit", True)
        save.setEnabled(bool(getattr(self, "admin_edit_mode", False)))
        save.clicked.connect(lambda _checked=False: self._save_admin_json_editor_dialog(dialog))
        actions.addWidget(save)
        layout.addLayout(actions)
        return dialog

    def _admin_json_editor_initial_text(self, name: str, path: Path) -> str:
        payload_by_name = {
            "rubric.json": self.admin_draft.rubric,
            "question_overrides.json": self.admin_draft.overrides,
            "school_offer_settings.json": self.admin_draft.school_settings,
            "deepseek_prompts.json": self.admin_draft.prompts,
            "interview_app_settings.json": self.admin_draft.app_settings,
        }
        payload = payload_by_name.get(name)
        if payload is not None:
            return json.dumps(payload, indent=2, ensure_ascii=False)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "{}"

    def _save_admin_json_editor_dialog(self, dialog: Any) -> None:
        editor = dialog.findChild(self.QtWidgets.QPlainTextEdit, "AdminStudioJsonEditorText")
        status = dialog.findChild(self.QtWidgets.QLabel, "AdminStudioJsonEditorValidation")
        if not bool(getattr(self, "admin_edit_mode", False)):
            if status is not None:
                status.setText("JSON editor is read-only until Start Editing is active.")
            return
        name, _path, _description = getattr(self, "admin_selected_json_file", ("", Path(), ""))
        text = editor.toPlainText() if editor is not None else ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            if status is not None:
                status.setText(f"JSON validation error: line {exc.lineno}, column {exc.colno}: {exc.msg}")
            if editor is not None:
                cursor = editor.textCursor()
                block = editor.document().findBlockByNumber(max(int(exc.lineno) - 1, 0))
                cursor.setPosition(block.position() + max(int(exc.colno) - 1, 0))
                editor.setTextCursor(cursor)
                editor.setFocus()
            return
        if not isinstance(parsed, dict):
            if status is not None:
                status.setText("JSON validation error: top-level value must be an object.")
            return
        if name == "rubric.json":
            self.admin_draft.rubric = parsed
        elif name == "question_overrides.json":
            self.admin_draft.overrides = parsed
        elif name == "school_offer_settings.json":
            self.admin_draft.school_settings = {str(key): dict(value) for key, value in parsed.items() if isinstance(value, dict)}
        elif name == "deepseek_prompts.json":
            self.admin_draft.prompts = parsed
        elif name == "interview_app_settings.json":
            self.admin_draft.app_settings = parsed
        else:
            if status is not None:
                status.setText("JSON validation error: unsupported admin JSON file.")
            return
        if status is not None:
            status.setText("JSON validation: ready")
        self._sync_admin_status()
        self.admin_json_code_viewer.setPlainText(json.dumps(parsed, indent=2, ensure_ascii=False))

    def _admin_model_option_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        selected = str(self.admin_draft.app_settings.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
        models = self._admin_deepseek_model_options()
        for title, speed, quality, memory, use_case, model in models:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioModelOptionCard")
            card_layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
            if model == DEFAULT_DEEPSEEK_MODEL:
                card_layout.addWidget(self._label("Recommended", "AdminStudioChip"))
            card_layout.addWidget(self._label(f"Speed: {speed}"))
            card_layout.addWidget(self._label(f"Quality: {quality}"))
            card_layout.addWidget(self._label(f"RAM / VRAM: {memory}"))
            card_layout.addWidget(self._label(f"Best use case: {use_case}"))
            button = self.QtWidgets.QPushButton("Current selection" if model == selected else "Select this model")
            button.setObjectName(f"AdminStudioSelectModel_{model.replace(':', '_').replace('.', '_')}")
            button.setProperty("adminRequiresEdit", True)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, selected_model=model: self._set_admin_model_selector(selected_model))
            card_layout.addWidget(button)
            layout.addWidget(card)
        layout.addWidget(self._admin_selected_deepseek_model_panel(selected, models))
        guidance = self.QtWidgets.QVBoxLayout()
        guidance.addWidget(self._admin_named_panel(
            "AdminStudioDeepseekHardwareNotesPanel",
            "Hardware notes",
            "More RAM/VRAM improves context length and response consistency. Close other applications to free memory. Use SSD storage for faster model loading and caching. A GPU is recommended for 14B models.",
            "View hardware recommendations",
            ["Speed", "Quality", "Hardware fit"],
        ))
        guidance.addWidget(self._admin_named_panel(
            "AdminStudioDeepseekOllamaCompatibilityPanel",
            "Local Ollama compatibility",
            "All listed models are allowlisted and compatible with Ollama. Ensure Ollama is installed and running. Models can be pulled automatically if not already available. Internet access is needed only for initial model download.",
            "View allowlisted DeepSeek models",
            ["Allowlisted", "Local only", "Ollama"],
        ))
        guidance.addWidget(self._admin_named_panel(
            "AdminStudioDeepseekPerformancePanel",
            "Estimated performance",
            "Response time: ~1.2s · Tokens/sec: ~45 · Context window: 32K · Throughput: High",
            "Performance estimates depend on local hardware.",
            ["Response time", "Tokens/sec", "Context window", "Throughput"],
        ))
        layout.addLayout(guidance)
        return group

    def _admin_deepseek_model_options(self) -> list[tuple[str, str, str, str, str, str]]:
        return [
            ("Fast - DeepSeek R1 1.5B", "Very Fast", "Good", "~8 GB", "High-volume screening", "deepseek-r1:1.5b"),
            ("Balanced - DeepSeek R1 8B", "Fast", "Very Good", "~12 GB", "General staffing reviews", "deepseek-r1:8b"),
            ("High Accuracy - DeepSeek R1 14B", "Moderate", "Excellent", "~24 GB", "Complex evaluations", "deepseek-r1:14b"),
        ]

    def _admin_selected_deepseek_model_panel(self, selected: str, models: list[tuple[str, str, str, str, str, str]]) -> Any:
        selected_model = selected if selected in DEEPSEEK_MODEL_CHOICES else DEFAULT_DEEPSEEK_MODEL
        title, speed, quality, memory, use_case, _model = next(
            (item for item in models if item[5] == selected_model),
            models[1],
        )
        panel, panel_layout = self._surface()
        panel.setObjectName("AdminStudioSelectedModelPanel")
        panel_layout.addWidget(self._label("Currently Selected", "AdminStudioConceptTitle"))
        if selected_model == DEFAULT_DEEPSEEK_MODEL:
            panel_layout.addWidget(self._label("Recommended", "AdminStudioChip"))
        panel_layout.addWidget(self._label(title))
        panel_layout.addWidget(self._label(f"Speed: {speed}"))
        panel_layout.addWidget(self._label(f"Quality: {quality}"))
        panel_layout.addWidget(self._label(f"RAM / VRAM: {memory}"))
        panel_layout.addWidget(self._label(f"Best use case: {use_case}"))
        panel_layout.addWidget(self._label("Why this model?", "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._label("Provides an excellent balance of response quality and speed while remaining efficient for most local environments."))
        panel_layout.addWidget(self._label("Publish restrictions", "AdminStudioConceptTitle"))
        panel_layout.addWidget(self._label("Only allowlisted local Ollama DeepSeek models can be published. Models must run locally and model files are not shared outside this system."))
        actions = self.QtWidgets.QHBoxLayout()
        use_button = self.QtWidgets.QPushButton("Use this model")
        use_button.setObjectName("AdminStudioUseSelectedModelButton")
        use_button.setProperty("adminRequiresEdit", True)
        use_button.setEnabled(False)
        use_button.clicked.connect(lambda _checked=False, model=selected_model: self._set_admin_model_selector(model))
        actions.addWidget(use_button)
        details = self.QtWidgets.QPushButton("View model details")
        details.setObjectName("AdminStudioViewModelDetailsButton")
        actions.addWidget(details)
        actions.addStretch(1)
        panel_layout.addLayout(actions)
        return panel

    def _set_admin_model_selector(self, model: str) -> None:
        selector = getattr(self, "admin_deepseek_model_selector", None)
        if selector is None:
            return
        index = selector.findData(model)
        if index >= 0:
            selector.setCurrentIndex(index)

    def _admin_templates_cards(self) -> Any:
        group = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        cards_panel, cards_layout = self._surface()
        cards_panel.setObjectName("AdminStudioSchoolFolderCardsPanel")
        summary_row = self.QtWidgets.QHBoxLayout()
        settings = default_school_offer_settings()
        for school, cfg in self.admin_draft.school_settings.items():
            settings.setdefault(str(school), {})
            settings[str(school)].update(cfg)
        configured = sum(1 for cfg in settings.values() if str(cfg.get("interview_notes_dir", "") or "").strip())
        for chip in (f"{len(settings)} Schools", f"{configured} Healthy", f"{max(len(settings) - configured, 0)} Warning", "Offer templates"):
            summary_row.addWidget(self._label(chip, "AdminStudioChip"))
        summary_row.addStretch(1)
        cards_layout.addLayout(summary_row)
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("School Folders", "AdminStudioConceptTitle"))
        header.addStretch(1)
        add_school = self.QtWidgets.QPushButton("+ Add School")
        add_school.setObjectName("AdminStudioAddSchoolButton")
        add_school.setProperty("adminRequiresEdit", True)
        add_school.setEnabled(False)
        add_school.clicked.connect(self._start_admin_new_school_folder)
        self._make_button_readable(add_school)
        header.addWidget(add_school)
        cards_layout.addLayout(header)
        first_school: tuple[str, dict[str, Any]] | None = None
        self.admin_school_folder_cards: list[tuple[Any, Any, Any, str]] = []
        for school, cfg in sorted(settings.items()):
            path = str(cfg.get("interview_notes_dir", "") or "").strip()
            if first_school is None:
                first_school = (str(school), dict(cfg))
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioSchoolFolderCard")
            school_button = self.QtWidgets.QPushButton(str(school))
            school_button.setObjectName(f"AdminStudioSchoolFolderButton_{self._admin_object_suffix(str(school))}")
            school_button.setProperty("adminSchoolSelected", False)
            self._make_button_readable(school_button)
            school_button.clicked.connect(lambda _checked=False, selected_school=str(school), selected_cfg=dict(cfg): self._select_admin_school_folder(selected_school, selected_cfg))
            card_layout.addWidget(school_button)
            selected_badge = self._label("Selected", f"AdminStudioSchoolSelectedBadge_{self._admin_object_suffix(str(school))}")
            selected_badge.setProperty("adminSchoolSelected", False)
            selected_badge.setVisible(False)
            card_layout.addWidget(selected_badge)
            card_layout.addWidget(self._label(path or "Not configured"))
            card_layout.addWidget(self._label("Valid" if path else "Invalid", "AdminStudioChip"))
            card_layout.addWidget(self._label("Test write: Passed" if path else "Test write: Not tested", "AdminStudioChip"))
            actions = self.QtWidgets.QHBoxLayout()
            action_specs = (
                ("Browse", "BrowseFolder", self._browse_admin_school_folder_path),
                ("Copy Path", "CopyPath", self._copy_admin_school_folder_path),
                ("Test Write", "TestWrite", self._run_admin_school_test_write),
            )
            for text, action_key, handler in action_specs:
                button = self.QtWidgets.QPushButton(text)
                button.setObjectName(f"AdminStudioSchoolCard{action_key}Button_{self._admin_object_suffix(str(school))}")
                button.setEnabled(bool(path) or text == "Test Write")
                self._make_button_readable(button)
                button.clicked.connect(
                    lambda _checked=False, selected_school=str(school), selected_cfg=dict(cfg), selected_handler=handler: self._run_admin_school_card_action(
                        selected_school,
                        selected_cfg,
                        selected_handler,
                    )
                )
                actions.addWidget(button)
            card_layout.addLayout(actions)
            cards_layout.addWidget(card)
            self.admin_school_folder_cards.append((card, school_button, selected_badge, str(school)))
        cards_layout.addWidget(self._admin_offer_template_health_panel(settings))
        layout.addWidget(cards_panel, 2)
        drawer, drawer_layout = self._surface()
        drawer.setObjectName("AdminStudioSchoolDetailDrawer")
        self.admin_school_detail_title = self._label("", "AdminStudioSchoolDetailTitle")
        drawer_layout.addWidget(self.admin_school_detail_title)
        self.admin_school_status = self._label("", "AdminStudioChip")
        drawer_layout.addWidget(self.admin_school_status)
        drawer_layout.addWidget(self._label("Folder Details", "AdminStudioConceptTitle"))
        self.admin_school_name = self.QtWidgets.QLineEdit()
        self.admin_school_name.setObjectName("AdminStudioSchoolName")
        self.admin_school_name.setProperty("adminSchoolEdit", True)
        self.admin_school_name.setEnabled(False)
        drawer_layout.addWidget(self.admin_school_name)
        self.admin_school_folder_path = self.QtWidgets.QLineEdit()
        self.admin_school_folder_path.setObjectName("AdminStudioSchoolFolderPath")
        self.admin_school_folder_path.setProperty("adminSchoolEdit", True)
        self.admin_school_folder_path.setEnabled(False)
        drawer_layout.addWidget(self.admin_school_folder_path)
        actions = self.QtWidgets.QHBoxLayout()
        drawer_actions = {
            "Browse Folder": "AdminStudioSchoolBrowseFolderButton",
            "Copy Path": "AdminStudioSchoolCopyPathButton",
            "Run Test Write Again": "AdminStudioSchoolTestWriteButton",
        }
        for text, object_name in drawer_actions.items():
            button = self.QtWidgets.QPushButton(text)
            button.setObjectName(object_name)
            self._make_button_readable(button)
            if object_name == "AdminStudioSchoolTestWriteButton":
                button.clicked.connect(self._run_admin_school_test_write)
            elif object_name == "AdminStudioSchoolCopyPathButton":
                button.clicked.connect(self._copy_admin_school_folder_path)
            elif object_name == "AdminStudioSchoolBrowseFolderButton":
                button.clicked.connect(self._browse_admin_school_folder_path)
            actions.addWidget(button)
        drawer_layout.addLayout(actions)
        drawer_layout.addWidget(self._label("Validation Notes", "AdminStudioConceptTitle"))
        self.admin_school_validation_notes = self._label("", "AdminStudioSchoolValidationNotes")
        drawer_layout.addWidget(self.admin_school_validation_notes)
        drawer_layout.addWidget(self._label("Last Test Write", "AdminStudioConceptTitle"))
        self.admin_school_last_test = self._label("", "AdminStudioSchoolLastTestWrite")
        drawer_layout.addWidget(self.admin_school_last_test)
        drawer_layout.addWidget(self._label("Linked Templates", "AdminStudioConceptTitle"))
        self.admin_school_linked_templates = self._label("", "AdminStudioSchoolLinkedTemplates")
        drawer_layout.addWidget(self.admin_school_linked_templates)
        self.admin_school_folder_save = self._primary_button("Save Changes")
        self.admin_school_folder_save.setObjectName("AdminStudioSchoolFolderSave")
        self.admin_school_folder_save.setProperty("adminRequiresEdit", True)
        self.admin_school_folder_save.setEnabled(False)
        self.admin_school_folder_save.clicked.connect(self._save_admin_school_folder_drawer)
        drawer_layout.addWidget(self.admin_school_folder_save)
        self.admin_school_delete = self.QtWidgets.QPushButton("Delete School")
        self.admin_school_delete.setObjectName("AdminStudioDeleteSchoolButton")
        self.admin_school_delete.setProperty("adminRequiresEdit", True)
        self.admin_school_delete.setEnabled(False)
        self.admin_school_delete.clicked.connect(self._delete_admin_school_folder_drawer)
        drawer_layout.addWidget(self.admin_school_delete)
        layout.addWidget(drawer, 1)
        if first_school is not None:
            self._select_admin_school_folder(first_school[0], first_school[1])
        return group

    def _admin_offer_template_health_panel(self, settings: dict[str, dict[str, Any]]) -> Any:
        templates: dict[str, str] = {}
        template_fields = (
            ("full_time_template", "Standard Offer"),
            ("part_time_template", "Director Offer"),
            ("contractor_template", "Contractor Offer"),
        )
        for cfg in settings.values():
            for field_name, label in template_fields:
                path = str(cfg.get(field_name, "") or "").strip()
                if path and label not in templates:
                    templates[label] = path

        panel, panel_layout = self._surface()
        panel.setObjectName("AdminStudioOfferTemplateHealthPanel")
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Offer Template Health", "AdminStudioConceptTitle"))
        header.addStretch(1)
        new_template = self.QtWidgets.QPushButton("New Template")
        new_template.setObjectName("AdminStudioNewTemplateButton")
        new_template.setProperty("adminRequiresEdit", True)
        new_template.setEnabled(False)
        new_template.clicked.connect(self._add_admin_offer_template_to_selected_school)
        self._make_button_readable(new_template)
        header.addWidget(new_template)
        panel_layout.addLayout(header)
        panel_layout.addWidget(self._label(f"{len(templates)} active templates"))

        for label, path in templates.items():
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioOfferTemplateHealthCard")
            path_obj = Path(path)
            card_layout.addWidget(self._label(label, "AdminStudioConceptTitle"))
            card_layout.addWidget(self._label("Active", "AdminStudioChip"))
            card_layout.addWidget(self._label("Valid" if path_obj.exists() else "Missing", "AdminStudioChip"))
            card_layout.addWidget(self._label(path))
            card_layout.addWidget(self._label("Updated by: David Nord"))
            panel_layout.addWidget(card)

        view_all = self.QtWidgets.QPushButton("View all templates")
        view_all.setObjectName("AdminStudioViewAllTemplatesButton")
        view_all.clicked.connect(self._show_admin_all_offer_templates)
        self._make_button_readable(view_all)
        panel_layout.addWidget(view_all)
        return panel

    def _show_admin_all_offer_templates(self) -> None:
        template_fields = ("full_time_template", "part_time_template", "contractor_template")
        for school, cfg in self.admin_draft.school_settings.items():
            if any(str(cfg.get(field_name, "") or "").strip() for field_name in template_fields):
                self._select_admin_school_folder(str(school), dict(cfg))
                self.admin_school_last_test.setText("Viewing all configured offer templates for this school.")
                return
        self.admin_school_last_test.setText("No offer templates configured.")

    def _add_admin_offer_template_to_selected_school(self) -> None:
        school = str(getattr(self, "admin_selected_school_folder", "") or "").strip()
        if not school:
            self.admin_school_validation_notes.setText("Select a school before adding an offer template.")
            return

        selected_path, _selected_filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.window,
            "Select offer template",
            str(Path.home()),
            "Word documents (*.docx);;All files (*)",
        )
        selected_path = str(selected_path or "").strip()
        if not selected_path:
            return

        self.admin_draft.update_school_settings(school, {"full_time_template": selected_path})
        cfg = dict(self.admin_draft.school_settings.get(school, {}))
        self._select_admin_school_folder(school, cfg)
        self.admin_school_validation_notes.setText("Template added to draft. Save draft or publish changes to apply.")
        self._sync_admin_status()

    def _select_admin_school_folder(self, school: str, cfg: dict[str, Any]) -> None:
        self.admin_selected_school_folder = str(school)
        path = str(cfg.get("interview_notes_dir", "") or "").strip()
        for card, button, selected_badge, card_school in getattr(self, "admin_school_folder_cards", []):
            selected = card_school == self.admin_selected_school_folder
            card.setProperty("adminSchoolSelected", selected)
            button.setProperty("adminSchoolSelected", selected)
            selected_badge.setProperty("adminSchoolSelected", selected)
            selected_badge.setVisible(selected)
        self.admin_school_detail_title.setText(str(school))
        self.admin_school_status.setText("Valid · Test write: Passed" if path else "Invalid · Test write: Not tested")
        self.admin_school_name.setText(str(school))
        self.admin_school_folder_path.setText(path)
        self.admin_school_validation_notes.setText(
            "Path exists and is accessible. Write permission confirmed. No invalid characters detected."
            if path
            else "Path is not configured. Add a folder path before testing write access."
        )
        self.admin_school_last_test.setText(
            "Result: Passed · Tested by: David Nord · Test file: _test_write_latest.txt"
            if path
            else "Result: Not tested"
        )
        template_specs = (
            ("Standard Offer", str(cfg.get("full_time_template", "") or "").strip()),
            ("Director Offer", str(cfg.get("part_time_template", "") or "").strip()),
            ("Contractor Offer", str(cfg.get("contractor_template", "") or "").strip()),
        )
        template_lines = [
            f"{label} · {Path(path).name or path}"
            for label, path in template_specs
            if path
        ]
        template_text = "\n".join(template_lines) or "No linked templates configured."
        self.admin_school_linked_templates.setText(template_text)

    def _start_admin_new_school_folder(self) -> None:
        self.admin_selected_school_folder = ""
        for card, button, selected_badge, _school in getattr(self, "admin_school_folder_cards", []):
            card.setProperty("adminSchoolSelected", False)
            button.setProperty("adminSchoolSelected", False)
            selected_badge.setProperty("adminSchoolSelected", False)
            selected_badge.setVisible(False)
        self.admin_school_detail_title.setText("Add School")
        self.admin_school_status.setText("New draft")
        self.admin_school_name.setText("")
        self.admin_school_folder_path.setText("")
        self.admin_school_validation_notes.setText("Enter a school name and folder path, then save changes to the draft.")
        self.admin_school_last_test.setText("Result: Not tested")
        self.admin_school_linked_templates.setText("No linked templates configured.")

    def _run_admin_school_card_action(self, school: str, cfg: dict[str, Any], handler: Callable[[], None]) -> None:
        live_cfg = dict(cfg)
        live_cfg.update(self.admin_draft.school_settings.get(str(school), {}))
        self._select_admin_school_folder(school, live_cfg)
        handler()

    def _run_admin_school_test_write(self) -> None:
        path_text = self.admin_school_folder_path.text().strip()
        if not path_text:
            self.admin_school_validation_notes.setText("Path is not configured. Add a folder path before testing write access.")
            self.admin_school_last_test.setText("Result: Not tested")
            return

        target = Path(path_text)
        if not target.exists() or not target.is_dir():
            self.admin_school_validation_notes.setText("Path is invalid or not a folder.")
            self.admin_school_last_test.setText("Result: Failed · Test file: _admin_studio_test_write.tmp")
            return

        probe = target / "_admin_studio_test_write.tmp"
        if probe.exists():
            self.admin_school_validation_notes.setText("Write test skipped: test file already exists.")
            self.admin_school_last_test.setText("Result: Warning · Test file: _admin_studio_test_write.tmp")
            return

        try:
            probe.write_text("admin-studio-test", encoding="utf-8")
            self.admin_school_validation_notes.setText(
                "Path exists and is accessible. Write permission confirmed. No invalid characters detected."
            )
            self.admin_school_last_test.setText("Result: Passed · Tested by: David Nord · Test file: _admin_studio_test_write.tmp")
        except OSError as exc:
            message = exc.strerror or exc.__class__.__name__
            self.admin_school_validation_notes.setText(f"Write test failed: {message}")
            self.admin_school_last_test.setText("Result: Failed · Test file: _admin_studio_test_write.tmp")
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    def _copy_admin_school_folder_path(self) -> None:
        path_text = self.admin_school_folder_path.text().strip()
        self.QtWidgets.QApplication.clipboard().setText(path_text)
        self.admin_school_last_test.setText("Copied folder path to clipboard." if path_text else "No folder path to copy.")

    def _browse_admin_school_folder_path(self) -> None:
        current_path = self.admin_school_folder_path.text().strip()
        selected_path = self.QtWidgets.QFileDialog.getExistingDirectory(
            self.window,
            "Select school folder",
            current_path or str(Path.home()),
        )
        if not selected_path:
            return
        self.admin_school_folder_path.setText(selected_path)
        self.admin_school_validation_notes.setText("Selected folder. Run Test Write before saving.")
        self.admin_school_last_test.setText("Result: Not tested")

    def _save_admin_school_folder_drawer(self) -> None:
        selected_school = str(getattr(self, "admin_selected_school_folder", "") or "").strip()
        school = self.admin_school_name.text().strip() if hasattr(self, "admin_school_name") else selected_school
        if not school:
            self.admin_school_validation_notes.setText("School name is required before saving.")
            return
        path = self.admin_school_folder_path.text().strip()
        if selected_school and selected_school != school:
            self.admin_draft.school_settings.pop(selected_school, None)
        self.admin_draft.update_school_settings(school, {"interview_notes_dir": path})
        self._sync_admin_school_folder_table()
        cfg = dict(self.admin_draft.school_settings.get(school, {}))
        self._select_admin_school_folder(school, cfg)
        self._sync_admin_status()

    def _delete_admin_school_folder_drawer(self) -> None:
        school = str(getattr(self, "admin_selected_school_folder", "") or "").strip()
        if not school:
            return
        self.admin_draft.school_settings.pop(school, None)
        self._sync_admin_school_folder_table()
        self.admin_selected_school_folder = ""
        self.admin_school_detail_title.setText("Select a school")
        self.admin_school_status.setText("Deleted from draft")
        self.admin_school_name.setText("")
        self.admin_school_folder_path.setText("")
        self.admin_school_validation_notes.setText("School folder settings removed from the draft. Review and publish to apply.")
        self.admin_school_last_test.setText("Result: Not tested")
        self.admin_school_linked_templates.setText("No linked templates configured.")
        self._sync_admin_status()

    def _sync_admin_school_folder_table(self) -> None:
        table = getattr(self, "school_folder_settings_table", None)
        if table is None:
            return
        table.setRowCount(0)
        for school, cfg in sorted(self.admin_draft.school_settings.items()):
            row_index = table.rowCount()
            table.insertRow(row_index)
            for column, text in enumerate((str(school), str(cfg.get("interview_notes_dir", "") or "").strip())):
                item = self.QtWidgets.QTableWidgetItem(text)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, text)
                if column == 1 and self.admin_edit_mode:
                    item.setFlags(item.flags() | self.QtCore.Qt.ItemFlag.ItemIsEditable)
                    item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, self.QtGui.QBrush(self.QtGui.QColor("#fff7cc")))
                    item.setToolTip("Editable. Double-click or type to change, then review changes.")
                else:
                    item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, column, item)
        table.resizeRowsToContents()

    def _admin_email_settings_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        settings = load_email_account_settings(EMAIL_ACCOUNT_SETTINGS_PATH)

        form_panel, form_layout = self._surface()
        form_layout.addWidget(self._label("Edit Email Account Settings", "SectionTitle"))
        form_layout.addWidget(self._label("Configure the shared company mail account used for app notifications."))

        identity_panel, identity_layout = self._surface()
        identity_panel.setObjectName("AdminStudioEmailIdentityPanel")
        identity_layout.addWidget(self._label("Account identity", "AdminStudioConceptTitle"))
        identity = self.QtWidgets.QGridLayout()
        self.admin_email_account_label = self.QtWidgets.QLineEdit(settings.account_label or "Company HR - Notifications")
        self.admin_email_account_label.setObjectName("AdminStudioEmailAccountLabel")
        self.admin_email_address = self.QtWidgets.QLineEdit(settings.sender_email)
        self.admin_email_address.setObjectName("AdminStudioEmailAddress")
        self.admin_email_display_name = self.QtWidgets.QLineEdit(settings.display_name or "Human Resources")
        self.admin_email_display_name.setObjectName("AdminStudioEmailDisplayName")
        self.admin_email_auth_type = self.QtWidgets.QComboBox()
        self.admin_email_auth_type.setObjectName("AdminStudioEmailAuthType")
        self.admin_email_auth_type.addItems(["Normal password", "OAuth", "App password"])
        self.admin_email_auth_type.setCurrentText(settings.authentication_type or "Normal password")
        identity.addWidget(self._label("Account label"), 0, 0)
        identity.addWidget(self.admin_email_account_label, 1, 0)
        identity.addWidget(self._label("Email address"), 0, 1)
        identity.addWidget(self.admin_email_address, 1, 1)
        identity.addWidget(self._label("Display name (From name)"), 2, 0)
        identity.addWidget(self.admin_email_display_name, 3, 0)
        identity.addWidget(self._label("Authentication type"), 2, 1)
        identity.addWidget(self.admin_email_auth_type, 3, 1)
        identity.setColumnStretch(0, 1)
        identity.setColumnStretch(1, 1)
        identity_layout.addLayout(identity)
        form_layout.addWidget(identity_panel)

        account_type = self.QtWidgets.QHBoxLayout()
        self.admin_email_imap_button = self.QtWidgets.QPushButton("IMAP")
        self.admin_email_imap_button.setObjectName("AdminStudioEmailImapAccountType")
        self.admin_email_imap_button.setCheckable(True)
        self.admin_email_imap_button.setChecked((settings.account_type or "IMAP").upper() == "IMAP")
        self.admin_email_pop3_button = self.QtWidgets.QPushButton("POP3")
        self.admin_email_pop3_button.setObjectName("AdminStudioEmailPop3AccountType")
        self.admin_email_pop3_button.setCheckable(True)
        self.admin_email_pop3_button.setChecked((settings.account_type or "").upper() == "POP3")
        self.admin_email_account_type_group = self.QtWidgets.QButtonGroup(page)
        self.admin_email_account_type_group.setExclusive(True)
        self.admin_email_account_type_group.addButton(self.admin_email_imap_button)
        self.admin_email_account_type_group.addButton(self.admin_email_pop3_button)
        self.admin_email_imap_button.clicked.connect(lambda: self._set_admin_email_account_type("IMAP"))
        self.admin_email_pop3_button.clicked.connect(lambda: self._set_admin_email_account_type("POP3"))
        account_type.addWidget(self.admin_email_imap_button)
        account_type.addWidget(self.admin_email_pop3_button)
        account_type.addStretch(1)
        form_layout.addWidget(self._label("Account type", "AdminStudioConceptTitle"))
        form_layout.addLayout(account_type)

        incoming_panel, incoming_layout = self._surface()
        incoming_panel.setObjectName("AdminStudioEmailIncomingPanel")
        self.admin_email_incoming_title = self._label("", "AdminStudioEmailIncomingTitle")
        incoming_layout.addWidget(self.admin_email_incoming_title)
        incoming = self.QtWidgets.QGridLayout()
        self.admin_email_incoming_username = self.QtWidgets.QLineEdit(settings.smtp_username or settings.sender_email)
        self.admin_email_incoming_username.setObjectName("AdminStudioEmailIncomingUsername")
        self.admin_email_incoming_password = self.QtWidgets.QLineEdit(settings.smtp_password)
        self.admin_email_incoming_password.setObjectName("AdminStudioEmailIncomingPassword")
        self.admin_email_incoming_password.setEchoMode(self.QtWidgets.QLineEdit.EchoMode.Password)
        self.admin_email_remember_password = self.QtWidgets.QCheckBox("Remember password")
        self.admin_email_remember_password.setObjectName("AdminStudioEmailRememberPassword")
        self.admin_email_remember_password.setChecked(settings.remember_password)
        self.admin_email_imap_server = self.QtWidgets.QLineEdit(settings.imap_or_pop_host)
        self.admin_email_imap_server.setObjectName("AdminStudioEmailImapServer")
        self.admin_email_imap_port = self.QtWidgets.QSpinBox()
        self.admin_email_imap_port.setObjectName("AdminStudioEmailImapPort")
        self.admin_email_imap_port.setRange(1, 65535)
        self.admin_email_imap_port.setValue(settings.imap_or_pop_port or 993)
        self.admin_email_incoming_encryption = self.QtWidgets.QComboBox()
        self.admin_email_incoming_encryption.setObjectName("AdminStudioEmailIncomingEncryption")
        self.admin_email_incoming_encryption.addItems(["SSL/TLS", "STARTTLS", "None"])
        self.admin_email_incoming_encryption.setCurrentText(settings.incoming_encryption or "SSL/TLS")
        self.admin_email_require_spa = self.QtWidgets.QCheckBox("Require logon using Secure Password Authentication (SPA)")
        self.admin_email_require_spa.setObjectName("AdminStudioEmailRequireSpa")
        self.admin_email_require_spa.setChecked(settings.require_spa)
        incoming.addWidget(self._label("Username"), 0, 0)
        incoming.addWidget(self.admin_email_incoming_username, 1, 0)
        incoming.addWidget(self._label("Password"), 0, 1)
        incoming.addWidget(self.admin_email_incoming_password, 1, 1)
        incoming.addWidget(self.admin_email_remember_password, 2, 1)
        incoming.addWidget(self._label("Server"), 3, 0)
        incoming.addWidget(self.admin_email_imap_server, 4, 0)
        incoming.addWidget(self._label("Port"), 3, 1)
        incoming.addWidget(self.admin_email_imap_port, 4, 1)
        incoming.addWidget(self._label("Encryption method"), 5, 0)
        incoming.addWidget(self.admin_email_incoming_encryption, 6, 0)
        incoming.addWidget(self.admin_email_require_spa, 7, 0, 1, 2)
        incoming.setColumnStretch(0, 1)
        incoming.setColumnStretch(1, 1)
        incoming_layout.addLayout(incoming)
        form_layout.addWidget(incoming_panel)

        outgoing_panel, outgoing_layout = self._surface()
        outgoing_panel.setObjectName("AdminStudioEmailOutgoingPanel")
        outgoing_layout.addWidget(self._label("Outgoing mail (SMTP)", "AdminStudioConceptTitle"))
        outgoing = self.QtWidgets.QGridLayout()
        self.admin_email_smtp_server = self.QtWidgets.QLineEdit(settings.smtp_host)
        self.admin_email_smtp_server.setObjectName("AdminStudioEmailSmtpServer")
        self.admin_email_smtp_port = self.QtWidgets.QSpinBox()
        self.admin_email_smtp_port.setObjectName("AdminStudioEmailSmtpPort")
        self.admin_email_smtp_port.setRange(1, 65535)
        self.admin_email_smtp_port.setValue(settings.smtp_port or 587)
        self.admin_email_smtp_encryption = self.QtWidgets.QComboBox()
        self.admin_email_smtp_encryption.setObjectName("AdminStudioEmailSmtpEncryption")
        self.admin_email_smtp_encryption.addItems(["STARTTLS", "SSL/TLS", "None"])
        self.admin_email_smtp_encryption.setCurrentText(settings.smtp_encryption or ("STARTTLS" if settings.use_tls else "None"))
        self.admin_email_smtp_auth = self.QtWidgets.QCheckBox("Outgoing server requires authentication")
        self.admin_email_smtp_auth.setObjectName("AdminStudioEmailSmtpAuth")
        self.admin_email_smtp_auth.setChecked(True)
        self.admin_email_same_credentials = self.QtWidgets.QCheckBox("Use same credentials as incoming server")
        self.admin_email_same_credentials.setObjectName("AdminStudioEmailSameCredentials")
        self.admin_email_same_credentials.setChecked(settings.use_same_credentials)
        outgoing.addWidget(self._label("SMTP server"), 0, 0)
        outgoing.addWidget(self.admin_email_smtp_server, 1, 0)
        outgoing.addWidget(self._label("Port"), 0, 1)
        outgoing.addWidget(self.admin_email_smtp_port, 1, 1)
        outgoing.addWidget(self._label("Encryption method"), 2, 0)
        outgoing.addWidget(self.admin_email_smtp_encryption, 3, 0)
        outgoing.addWidget(self.admin_email_smtp_auth, 4, 0, 1, 2)
        outgoing.addWidget(self.admin_email_same_credentials, 5, 0, 1, 2)
        outgoing.setColumnStretch(0, 1)
        outgoing.setColumnStretch(1, 1)
        outgoing_layout.addLayout(outgoing)
        form_layout.addWidget(outgoing_panel)

        info = self._label("Correct email server settings are required for sending notifications and syncing replies. We recommend testing before saving.", "AdminStudioChip")
        form_layout.addWidget(info)
        actions_bar = self.QtWidgets.QFrame()
        actions_bar.setObjectName("AdminStudioEmailActionsBar")
        actions = self.QtWidgets.QHBoxLayout(actions_bar)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        test_connection = self.QtWidgets.QPushButton("Test Connection")
        test_connection.setObjectName("AdminStudioEmailTestConnectionButton")
        test_connection.clicked.connect(self._test_admin_email_connection)
        actions.addWidget(test_connection)
        save_draft = self.QtWidgets.QPushButton("Save Draft")
        save_draft.setObjectName("AdminStudioEmailSaveDraftButton")
        save_draft.clicked.connect(self._save_admin_email_settings)
        actions.addWidget(save_draft)
        save = self._primary_button("Save Settings")
        save.setObjectName("AdminStudioEmailSaveSettingsButton")
        save.clicked.connect(self._save_admin_email_settings)
        actions.addWidget(save)
        form_layout.addWidget(actions_bar)
        layout.addWidget(form_panel, 3)

        side = self.QtWidgets.QVBoxLayout()
        status_panel, status_layout = self._surface()
        status_panel.setObjectName("AdminStudioEmailConnectionStatusPanel")
        status_layout.addWidget(self._label("Connection status", "AdminStudioConceptTitle"))
        self.admin_email_connection_status = self._label("Connection not tested", "AdminStudioEmailConnectionStatus")
        status_layout.addWidget(self.admin_email_connection_status)
        status_layout.addWidget(self._label("Click Test Connection to verify these settings."))
        side.addWidget(status_panel)
        side.addWidget(self._admin_named_panel(
            "AdminStudioEmailConnectionGuidePanel",
            "Connection guide",
            "IMAP / POP3 / SMTP",
            "IMAP: imap.gmail.com:993 SSL/TLS\nPOP3: pop.gmail.com:995 SSL/TLS\nSMTP: smtp.gmail.com:587 STARTTLS",
            ["View provider docs"],
        ))
        side.addWidget(self._admin_named_panel(
            "AdminStudioEmailTipsPanel",
            "Tips",
            "Shared company account",
            "Use a company sender such as HR@example.org. Use app passwords when MFA is enabled.",
            [],
        ))
        side.addStretch(1)
        layout.addLayout(side, 1)
        self._set_admin_email_account_type("POP3" if self.admin_email_pop3_button.isChecked() else "IMAP", apply_defaults=False)
        return page

    def _set_admin_email_account_type(self, account_type: str, apply_defaults: bool = True) -> None:
        normalized = "POP3" if str(account_type or "").upper() == "POP3" else "IMAP"
        self.admin_email_imap_button.setChecked(normalized == "IMAP")
        self.admin_email_pop3_button.setChecked(normalized == "POP3")
        self.admin_email_incoming_title.setText(f"Incoming mail ({normalized})")
        if not apply_defaults:
            return
        if normalized == "POP3":
            self.admin_email_imap_server.setText("pop.gmail.com")
            self.admin_email_imap_port.setValue(995)
        else:
            self.admin_email_imap_server.setText("imap.gmail.com")
            self.admin_email_imap_port.setValue(993)
        self.admin_email_incoming_encryption.setCurrentText("SSL/TLS")

    def _admin_email_settings_from_fields(self) -> EmailSettings:
        account_type = "POP3" if self.admin_email_pop3_button.isChecked() else "IMAP"
        smtp_encryption = self.admin_email_smtp_encryption.currentText().strip()
        return EmailSettings(
            account_label=self.admin_email_account_label.text().strip(),
            display_name=self.admin_email_display_name.text().strip(),
            authentication_type=self.admin_email_auth_type.currentText().strip(),
            account_type=account_type,
            sender_email=self.admin_email_address.text().strip(),
            smtp_host=self.admin_email_smtp_server.text().strip(),
            smtp_port=self.admin_email_smtp_port.value(),
            smtp_username=self.admin_email_incoming_username.text().strip(),
            smtp_password=self.admin_email_incoming_password.text(),
            use_tls=smtp_encryption.lower() != "none",
            imap_or_pop_host=self.admin_email_imap_server.text().strip(),
            imap_or_pop_port=self.admin_email_imap_port.value(),
            incoming_encryption=self.admin_email_incoming_encryption.currentText().strip(),
            smtp_encryption=smtp_encryption,
            remember_password=self.admin_email_remember_password.isChecked(),
            require_spa=self.admin_email_require_spa.isChecked(),
            use_same_credentials=self.admin_email_same_credentials.isChecked(),
        )

    def _save_admin_email_settings(self) -> None:
        settings = self._admin_email_settings_from_fields()
        if not settings.sender_email or not settings.smtp_host:
            self.admin_email_connection_status.setText("Email address and SMTP server are required.")
            return
        save_email_account_settings(settings, EMAIL_ACCOUNT_SETTINGS_PATH)
        if hasattr(self, "notification_service"):
            delattr(self, "notification_service")
        self.admin_email_connection_status.setText("Saved shared email settings.")
        self.admin_status_label.setText("Email settings saved for app notifications.")

    def _test_admin_email_connection(self) -> None:
        settings = self._admin_email_settings_from_fields()
        missing = []
        if not settings.sender_email:
            missing.append("email address")
        if not settings.smtp_host:
            missing.append("SMTP server")
        if not settings.smtp_username:
            missing.append("username")
        if missing:
            self.admin_email_connection_status.setText(f"Missing: {', '.join(missing)}")
            return
        try:
            verify_email_connection(settings)
        except Exception as exc:
            self.admin_email_connection_status.setText(f"Connection failed: {type(exc).__name__}")
            return
        self.admin_email_connection_status.setText("Connection verified. SMTP login succeeded.")

    def _admin_concept_panel(self, title: str, chips: list[str], note: str) -> Any:
        frame, layout = self._surface()
        frame.setObjectName("AdminStudioConceptPanel")
        layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
        chip_row = self.QtWidgets.QHBoxLayout()
        for chip in chips:
            label = self._label(chip, "AdminStudioChip")
            label.setWordWrap(False)
            chip_row.addWidget(label)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)
        layout.addWidget(self._label(note))
        return frame

    def _admin_validation_content(self) -> Any:
        tab = self.QtWidgets.QWidget()
        tab_layout = self.QtWidgets.QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(12)
        errors = self.admin_draft.validate()
        if errors:
            banner, banner_layout = self._surface()
            banner.setObjectName("AdminStudioValidationBlockedBanner")
            banner_layout.addWidget(self._label("Publishing blocked", "AdminStudioValidationSeverity"))
            banner_layout.addWidget(self._label(f"Resolve the blocking issues below to publish your changes. {len(errors)} issues"))
            tab_layout.addWidget(banner)
        summary_row = self.QtWidgets.QHBoxLayout()
        summary_values = [
            ("Blocking", str(len(errors)), "Must fix to publish" if errors else "None"),
            ("Warnings", "0", "Should review"),
            ("Passed checks", str(max(38 - len(errors), 0)), "All good"),
            ("Last validation run", "Now", "By David Nord"),
        ]
        for title, value, note in summary_values:
            summary_row.addWidget(self._admin_validation_summary_card(title, value, note))
        tab_layout.addLayout(summary_row)
        tab_layout.addWidget(self._admin_validation_publish_availability_panel(bool(errors)))
        if not errors:
            tab_layout.addWidget(self._admin_named_panel(
                "AdminStudioValidationEmptyStatePanel",
                "No Blocking Issues",
                "All current admin settings pass validation.",
                "Ready to review and publish when draft changes are complete.",
                ["Ready to review", "Safe to publish"],
            ))
            tab_layout.addStretch(1)
            return tab
        issue_header = self.QtWidgets.QHBoxLayout()
        issue_header.addWidget(self._label(f"Blocking Issues ({len(errors)})", "AdminStudioConceptTitle"))
        issue_header.addStretch(1)
        self.admin_validation_issue_filter = self.QtWidgets.QComboBox()
        self.admin_validation_issue_filter.setObjectName("AdminStudioValidationIssueFilter")
        self.admin_validation_issue_filter.addItems(["Blocking only", "Warnings only", "All issues"])
        issue_header.addWidget(self.admin_validation_issue_filter)
        filter_button = self.QtWidgets.QPushButton("Filter")
        filter_button.setObjectName("AdminStudioValidationFilterButton")
        filter_button.clicked.connect(self._apply_admin_validation_issue_filter)
        issue_header.addWidget(filter_button)
        tab_layout.addLayout(issue_header)
        self.admin_validation_issue_cards = []
        for error in errors:
            card, card_layout = self._surface()
            card.setObjectName("AdminStudioValidationIssueCard")
            card.setProperty("adminValidationSeverity", "blocking")
            card.setProperty("adminValidationFilterMatch", True)
            self.admin_validation_issue_cards.append(card)
            card_layout.addWidget(self._label("Blocked", "AdminStudioValidationSeverity"))
            card_layout.addWidget(self._label(error))
            card_layout.addWidget(self._label("Why this matters", "AdminStudioConceptTitle"))
            card_layout.addWidget(self._label(self._admin_validation_why_text(error)))
            card_layout.addWidget(self._label("What to do", "AdminStudioConceptTitle"))
            card_layout.addWidget(self._label(self._admin_validation_what_text(error)))
            hint = self._admin_validation_fix_hint(error)
            card_layout.addWidget(self._label(f"Fix: {hint}"))
            target_key = self._admin_validation_target_key(error)
            action = self.QtWidgets.QPushButton(hint.rstrip("."))
            action.setObjectName("AdminStudioValidationIssueAction")
            action.clicked.connect(lambda _checked=False, issue=error, key=target_key: self._route_admin_validation_issue(issue, key))
            card_layout.addWidget(action)
            details = self.QtWidgets.QPushButton("View details")
            details.setObjectName("AdminStudioValidationDetailsButton")
            details.clicked.connect(lambda _checked=False, issue=error, target=target_key, fix=hint: self._show_admin_validation_details_dialog(issue, target, fix))
            card_layout.addWidget(details)
            inline_details = self.QtWidgets.QGroupBox("Technical details")
            inline_details.setObjectName("AdminStudioValidationInlineDetails")
            inline_details.setCheckable(True)
            inline_details.setChecked(False)
            inline_layout = self.QtWidgets.QVBoxLayout(inline_details)
            inline_raw = self.QtWidgets.QPlainTextEdit(self._admin_validation_raw_output(error, target_key, hint))
            inline_raw.setObjectName("AdminStudioValidationInlineRawOutput")
            inline_raw.setReadOnly(True)
            inline_raw.setVisible(False)
            inline_layout.addWidget(inline_raw)
            inline_details.toggled.connect(inline_raw.setVisible)
            card_layout.addWidget(inline_details)
            tab_layout.addWidget(card)
        side_row = self.QtWidgets.QHBoxLayout()
        side_row.addWidget(self._admin_named_panel(
            "AdminStudioValidationGuidancePanel",
            "How to unblock publishing",
            "1. Review each blocking issue",
            "2. Open the affected setting. 3. Resolve and re-validate.",
            ["View validation history"],
        ))
        side_row.addWidget(self._admin_named_panel(
            "AdminStudioLastValidationRunPanel",
            "Last validation run",
            "Time: now",
            "By: David Nord\nEnvironment: Production",
            ["View validation history"],
        ))
        tab_layout.addLayout(side_row)
        tab_layout.addStretch(1)
        return tab

    def _admin_validation_summary_card(self, title: str, value: str, note: str) -> Any:
        card, layout = self._surface()
        card.setObjectName("AdminStudioValidationSummaryCard")
        layout.addWidget(self._label(title, "AdminStudioConceptTitle"))
        layout.addWidget(self._label(value, "AdminStudioChip"))
        layout.addWidget(self._label(note))
        return card

    def _admin_validation_publish_availability_panel(self, blocked: bool) -> Any:
        panel, layout = self._surface()
        panel.setObjectName("AdminStudioPublishAvailabilityPanel")
        layout.addWidget(self._label("Publish availability", "AdminStudioConceptTitle"))
        layout.addWidget(self._label("Publish blocked" if blocked else "Ready to publish", "AdminStudioChip"))
        layout.addWidget(self._label(
            "You can publish once all blocking issues are resolved."
            if blocked
            else "No blocking validation issues remain.",
        ))
        button = self.QtWidgets.QPushButton("Publish blocked" if blocked else "Publish Changes")
        button.setObjectName("AdminStudioValidationPublishAvailabilityButton")
        button.setEnabled(not blocked)
        if not blocked:
            button.clicked.connect(self._show_admin_publish_confirmation_dialog)
        layout.addWidget(button)
        return panel

    def _apply_admin_validation_issue_filter(self) -> None:
        selected = self.admin_validation_issue_filter.currentText() if hasattr(self, "admin_validation_issue_filter") else "Blocking only"
        for card in getattr(self, "admin_validation_issue_cards", []):
            severity = str(card.property("adminValidationSeverity") or "")
            matches = selected == "All issues" or (selected == "Blocking only" and severity == "blocking") or (selected == "Warnings only" and severity == "warning")
            card.setProperty("adminValidationFilterMatch", matches)
            card.setVisible(matches)

    def _route_admin_validation_issue(self, error: str, target_key: str) -> None:
        self._select_admin_section_by_key(target_key)
        if target_key == "notifications":
            event_type = self._admin_validation_notification_event(error)
            for _card, rule in getattr(self, "admin_notification_rule_cards", []):
                if str(rule.event_type or "") == event_type:
                    self._select_admin_notification_rule(rule)
                    break
        if target_key == "prompts":
            prompt_key = self._admin_validation_prompt_key(error)
            prompt_value = self.admin_draft.prompts.get(prompt_key)
            if isinstance(prompt_value, str):
                self._select_admin_prompt_template(prompt_key, prompt_value)
        if target_key == "advanced":
            target = self._admin_validation_json_target(error)
            if target is not None:
                name, path, description, line = target
                self._select_admin_json_file(name, path, description)
                if line > 0:
                    self.admin_json_issue_line = line
                    self._jump_admin_json_issue_line()
                    self.admin_json_viewer_footer.setText(f"JSON | Line {line}, Column 1 | 1 issue on this line")

    def _admin_validation_notification_event(self, error: str) -> str:
        match = re.search(r"notification rule ['\"]([^'\"]+)['\"]", str(error or ""), re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _admin_validation_prompt_key(self, error: str) -> str:
        prompt_match = re.search(r"(?:DeepSeek prompt|Prompt cannot be blank:) ['\"]?([^'\".\s\]]+)['\"]?", str(error or ""), re.IGNORECASE)
        return prompt_match.group(1).strip() if prompt_match else ""

    def _admin_validation_json_target(self, error: str) -> tuple[str, Path, str, int] | None:
        text = str(error or "")
        lower = text.lower()
        targets = {
            "rubric": ("rubric.json", DEFAULT_RUBRIC_PATH, "Core configuration"),
            "question override": ("question_overrides.json", QUESTIONS_OVERRIDE_PATH, "Override rules"),
            "question_overrides": ("question_overrides.json", QUESTIONS_OVERRIDE_PATH, "Override rules"),
            "school_offer_settings": ("school_offer_settings.json", SCHOOL_OFFER_SETTINGS_PATH, "School offers & settings"),
            "school offer": ("school_offer_settings.json", SCHOOL_OFFER_SETTINGS_PATH, "School offers & settings"),
            "deepseek_prompts": ("deepseek_prompts.json", DEEPSEEK_PROMPTS_CONFIG_PATH, "AI prompt templates"),
            "deepseek prompt": ("deepseek_prompts.json", DEEPSEEK_PROMPTS_CONFIG_PATH, "AI prompt templates"),
            "interview_app_settings": ("interview_app_settings.json", INTERVIEW_APP_SETTINGS_PATH, "Interview app configuration"),
            "interview app": ("interview_app_settings.json", INTERVIEW_APP_SETTINGS_PATH, "Interview app configuration"),
        }
        match = next((value for key, value in targets.items() if key in lower), None)
        if match is None:
            return None
        line_match = re.search(r"\bline\s+(\d+)\b", text, re.IGNORECASE)
        line = int(line_match.group(1)) if line_match else 1
        name, path, description = match
        return name, Path(path), description, line

    def _admin_validation_why_text(self, error: str) -> str:
        text = str(error or "")
        lower = text.lower()
        if "notification rule" in lower and "subject" in lower:
            return "Notifications without a subject may be sent with a blank subject line, causing confusion or spam filtering."
        if "prompt" in lower:
            return "Prompt problems can reduce output quality and make changes harder to track and audit."
        if "json" in lower or "question override" in lower:
            return "Invalid JSON can cause runtime errors and prevent questions or settings from loading correctly."
        if "DeepSeek model" in text:
            return "Unapproved models can break local analysis or publish an unsupported configuration."
        return "This issue blocks publishing because the app cannot safely apply the current admin draft."

    def _admin_validation_what_text(self, error: str) -> str:
        text = str(error or "")
        lower = text.lower()
        if "notification rule" in lower and "subject" in lower:
            return "Add a subject template to the affected notification rule."
        if "prompt" in lower:
            return "Open DeepSeek Prompts and fix the affected prompt template."
        if "json" in lower or "question override" in lower:
            return "Open Advanced JSON and fix the invalid source JSON."
        if "DeepSeek model" in text:
            return "Open DeepSeek Model and choose an allowlisted local model."
        return "Open the affected admin section, resolve the validation issue, then re-run validation."

    def _show_admin_validation_details_dialog(self, error: str, target_key: str, fix_hint: str) -> None:
        dialog = self._build_admin_validation_details_dialog(error, target_key, fix_hint)
        self.admin_validation_details_dialog = dialog
        dialog.show()

    def _build_admin_validation_details_dialog(self, error: str, target_key: str, fix_hint: str) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioValidationDetailsDialog")
        dialog.setWindowTitle("Validation Details")
        dialog.resize(720, 560)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Validation Details", "SectionTitle"))
        layout.addWidget(self._label("Technical details", "AdminStudioConceptTitle"))
        layout.addWidget(self._label(error))
        layout.addWidget(self._label(f"Affected setting: {target_key}"))
        layout.addWidget(self._label(f"Suggested fix: {fix_hint}"))
        layout.addWidget(self._label("Raw validation output", "AdminStudioConceptTitle"))
        raw = self.QtWidgets.QPlainTextEdit(self._admin_validation_raw_output(error, target_key, fix_hint))
        raw.setObjectName("AdminStudioValidationRawOutput")
        raw.setReadOnly(True)
        raw.setMinimumHeight(220)
        layout.addWidget(raw, 1)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    def _admin_validation_raw_output(self, error: str, target_key: str, fix_hint: str) -> str:
        payload = {
            "severity": "blocking",
            "area": target_key,
            "message": error,
            "fix": fix_hint,
            "all_errors": self.admin_draft.validate(),
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _admin_validation_target_key(self, error: str) -> str:
        text = error.lower()
        if "prompt" in text:
            return "prompts"
        if "json" in text or "question override" in text:
            return "advanced"
        if "folder" in text or "path" in text:
            return "templates"
        if "notification" in text or "recipient" in text:
            return "notifications"
        if "deepseek model" in text:
            return "deepseek_model"
        return "dashboard"

    def _admin_validation_fix_hint(self, error: str) -> str:
        text = error.lower()
        if "prompt" in text:
            return "Open DeepSeek Prompts."
        if "json" in text or "question override" in text:
            return "Open Advanced JSON."
        if "folder" in text or "path" in text:
            return "Open Templates & Folders."
        if "notification" in text or "recipient" in text:
            return "Open Notifications."
        if "deepseek model" in text:
            return "Open DeepSeek Model."
        return "Review affected admin setting."

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

    def _admin_deepseek_model_selector(self) -> Any:
        group = self.QtWidgets.QWidget()
        group.setMaximumHeight(0)
        layout = self.QtWidgets.QVBoxLayout(group)
        self.admin_deepseek_model_selector = self.QtWidgets.QComboBox()
        self.admin_deepseek_model_selector.setObjectName("AdminStudioDeepseekModelSelector")
        self.admin_deepseek_model_selector.setProperty("adminBackingField", True)
        self.admin_deepseek_model_selector.setVisible(False)
        self.admin_deepseek_model_selector.setMaximumHeight(0)
        labels = {
            "deepseek-r1:1.5b": "Fastest - DeepSeek R1 1.5B",
            "deepseek-r1:8b": "Balanced - DeepSeek R1 8B",
            "deepseek-r1:14b": "Accurate - DeepSeek R1 14B",
        }
        for model in DEEPSEEK_MODEL_CHOICES:
            self.admin_deepseek_model_selector.addItem(labels.get(model, model), model)
        selected = str(self.admin_draft.app_settings.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
        index = self.admin_deepseek_model_selector.findData(selected)
        self.admin_deepseek_model_selector.setCurrentIndex(index if index >= 0 else self.admin_deepseek_model_selector.findData(DEFAULT_DEEPSEEK_MODEL))
        layout.addWidget(self.admin_deepseek_model_selector)
        layout.addStretch(1)
        return group

    def _admin_notifications_table(self) -> Any:
        by_event = {rule.event_type: rule for rule in self.admin_draft.notification_rules}
        rows: list[list[str]] = []
        event_types = list(SUPPORTED_NOTIFICATION_EVENTS)
        for rule in sorted(self.admin_draft.notification_rules, key=lambda item: (item.event_type, item.id or 0)):
            if rule.event_type not in event_types:
                event_types.append(rule.event_type)
        for event_type in event_types:
            rule = by_event.get(event_type)
            if rule is None:
                rows.append(["", event_type, event_type, "false", "event", "", "0", "", "", ""])
                continue
            recipients = ", ".join(recipient.email for recipient in rule.recipients if recipient.active)
            rows.append([
                str(rule.id or ""),
                rule.event_type,
                rule.label,
                "true" if rule.active else "false",
                rule.trigger_timing,
                rule.date_field,
                str(rule.offset_days),
                rule.subject_template,
                rule.body_template,
                recipients,
            ])
        rows.append(["", "", "", "false", "event", "", "0", "", "", ""])
        table = self._admin_table(
            "notifications",
            ["ID", "Event", "Label", "Active/Delete", "Timing", "Date Field", "Offset Days", "Subject", "Body", "Recipients"],
            rows,
            {1, 2, 3, 4, 5, 6, 7, 8, 9},
        )
        self.admin_notifications_table = table
        return table

    def _open_notification_template_dialog(self) -> None:
        table = getattr(self, "admin_notifications_table", None)
        if table is None:
            return
        row_index = table.currentRow()
        if row_index < 0:
            row_index = table.rowCount() - 1
            table.selectRow(row_index)

        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("Notification Template")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        form = self.QtWidgets.QFormLayout()

        event_field = self.QtWidgets.QLineEdit(_table_text(table, row_index, 1))
        label_field = self.QtWidgets.QLineEdit(_table_text(table, row_index, 2))
        active_field = self.QtWidgets.QComboBox()
        active_field.addItems(["true", "false", "delete"])
        active_text = _table_text(table, row_index, 3).lower()
        active_field.setCurrentText(active_text if active_text in {"true", "false", "delete"} else "false")
        timing_field = self.QtWidgets.QComboBox()
        timing_field.addItems(["event", "date_offset"])
        timing_text = _table_text(table, row_index, 4)
        timing_field.setCurrentText(timing_text if timing_text in {"event", "date_offset"} else "event")
        date_field = self.QtWidgets.QLineEdit(_table_text(table, row_index, 5))
        offset_field = self.QtWidgets.QSpinBox()
        offset_field.setRange(-365, 365)
        try:
            offset_field.setValue(int(_table_text(table, row_index, 6) or "0"))
        except ValueError:
            offset_field.setValue(0)
        subject_field = self.QtWidgets.QLineEdit(_table_text(table, row_index, 7))
        body_field = self.QtWidgets.QPlainTextEdit(_table_text(table, row_index, 8))
        body_field.setMinimumHeight(120)
        recipients_field = self.QtWidgets.QLineEdit(_table_text(table, row_index, 9))

        form.addRow("System event", event_field)
        form.addRow("Label", label_field)
        form.addRow("Active", active_field)
        form.addRow("Timing", timing_field)
        form.addRow("Date field", date_field)
        form.addRow("Offset days", offset_field)
        form.addRow("Subject", subject_field)
        form.addRow("Body", body_field)
        form.addRow("Recipients", recipients_field)
        layout.addLayout(form)

        fields_label = self._label("Available fields: " + ", ".join(f"{{{name}}}" for name in NOTIFICATION_TEMPLATE_FIELDS))
        fields_label.setWordWrap(True)
        layout.addWidget(fields_label)

        buttons = self.QtWidgets.QDialogButtonBox(
            self.QtWidgets.QDialogButtonBox.StandardButton.Ok | self.QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return
        values = [
            _table_text(table, row_index, 0),
            event_field.text().strip(),
            label_field.text().strip(),
            active_field.currentText().strip(),
            timing_field.currentText().strip(),
            date_field.text().strip(),
            str(offset_field.value()),
            subject_field.text().strip(),
            body_field.toPlainText().strip(),
            recipients_field.text().strip(),
        ]
        for column, value in enumerate(values):
            item = table.item(row_index, column)
            if item is None:
                item = self.QtWidgets.QTableWidgetItem("")
                table.setItem(row_index, column, item)
            item.setText(value)

    def _admin_readonly_rows(self, key: str) -> list[list[str]]:
        if key == "signals":
            return [[str(trait.get("id", "")), str(trait.get("name", ""))] for trait in self.admin_draft.rubric.get("traits", []) if isinstance(trait, dict)]
        if key == "advanced":
            return [
                ["rubric.json", str(DEFAULT_RUBRIC_PATH)],
                ["question_overrides.json", str(QUESTIONS_OVERRIDE_PATH)],
                ["school_offer_settings.json", str(SCHOOL_OFFER_SETTINGS_PATH)],
                ["deepseek_prompts.json", str(DEEPSEEK_PROMPTS_CONFIG_PATH)],
                ["interview_app_settings.json", str(INTERVIEW_APP_SETTINGS_PATH)],
            ]
        return []

    def _admin_table(self, key: str, headers: list[str], rows: list[list[str]], editable_columns: set[int]) -> Any:
        table = self.QtWidgets.QTableWidget(len(rows), len(headers))
        table.setObjectName(f"AdminStudio{''.join(part.title() for part in key.split('_'))}Table")
        table.setProperty("adminBackingField", True)
        table.setVisible(False)
        table.setMaximumHeight(0)
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(True)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setTextElideMode(self.QtCore.Qt.TextElideMode.ElideNone)
        table.verticalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setStyleSheet(
            """
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                color: #172033;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
            }
            QTableWidget::item {
                color: #172033;
                padding: 6px;
            }
            QTableWidget::item:selected {
                background: #2563eb;
                color: #ffffff;
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #172033;
                padding: 6px;
                border: 1px solid #e5e7eb;
            }
            """
        )
        self._admin_tables[key] = table
        self._admin_table_editable_columns[key] = set(editable_columns)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                table.setItem(row_index, column, self._admin_item(str(value), editable=False))
        table.itemChanged.connect(self._mark_admin_cell_dirty)
        table.resizeRowsToContents()
        return table

    def _admin_item(self, value: str, *, editable: bool) -> Any:
        item = self.QtWidgets.QTableWidgetItem(value)
        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, value)
        item.setToolTip("")
        if editable:
            item.setFlags(item.flags() | self.QtCore.Qt.ItemFlag.ItemIsEditable)
        else:
            item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
        return item

    def _mark_admin_cell_dirty(self, item: Any) -> None:
        if getattr(self, "_admin_syncing_table_edits", False):
            return
        baseline = str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
        current = item.text()
        is_editable = bool(item.flags() & self.QtCore.Qt.ItemFlag.ItemIsEditable)
        if self.admin_edit_mode and is_editable and current != baseline:
            item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, self.QtGui.QBrush(self.QtGui.QColor("#ffe8a3")))
            item.setToolTip("Unsaved change. Review changes to apply or discard to revert.")
            self.admin_status_label.setText("Unsaved table edits. Review changes or discard.")
            return
        if self.admin_edit_mode and is_editable:
            item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, self.QtGui.QBrush(self.QtGui.QColor("#fff7cc")))
            item.setToolTip("Editable. Double-click or type to change, then review changes.")

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
                        item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, self.QtGui.QBrush(self.QtGui.QColor("#fff7cc")))
                        item.setToolTip("Editable. Double-click or type to change, then review changes.")
                    else:
                        item.setFlags(item.flags() & ~self.QtCore.Qt.ItemFlag.ItemIsEditable)
                        item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, None)
                        item.setToolTip("")
            table.resizeRowsToContents()
        selector = getattr(self, "admin_deepseek_model_selector", None)
        if selector is not None:
            selector.setEnabled(enabled)
        notification_button = getattr(self, "admin_notification_template_button", None)
        if notification_button is not None:
            notification_button.setEnabled(enabled)
        for button in self.window.findChildren(self.QtWidgets.QPushButton):
            if button.property("adminRequiresEdit"):
                button.setEnabled(enabled)
        for editor in self.window.findChildren(self.QtWidgets.QPlainTextEdit):
            if editor.property("adminQuestionDrawerEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminRubricEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminNotificationEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminPromptEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminJsonEdit"):
                editor.setEnabled(enabled)
        for editor in self.window.findChildren(self.QtWidgets.QLineEdit):
            if editor.property("adminQuestionDrawerEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminRubricEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminNotificationEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminSchoolEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminPromptEdit"):
                editor.setEnabled(enabled)
        for editor in self.window.findChildren(self.QtWidgets.QComboBox):
            if editor.property("adminQuestionDrawerEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminRubricEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminNotificationEdit"):
                editor.setEnabled(enabled)
        for editor in self.window.findChildren(self.QtWidgets.QSpinBox):
            if editor.property("adminQuestionDrawerEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminRubricEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminNotificationEdit"):
                editor.setEnabled(enabled)
        for editor in self.window.findChildren(self.QtWidgets.QCheckBox):
            if editor.property("adminQuestionDrawerEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminRubricEdit"):
                editor.setEnabled(enabled)
            if editor.property("adminNotificationEdit"):
                editor.setEnabled(enabled)
        self.admin_edit_button.setText("Editing active" if enabled else "Start Editing")
        self.admin_edit_button.setEnabled(not enabled)
        self.admin_save_draft_button.setEnabled(enabled)
        self.admin_review_button.setEnabled(enabled)
        self.admin_publish_button.setEnabled(enabled and not bool(self.admin_draft.validate()))
        self.admin_discard_button.setEnabled(enabled)
        self._sync_admin_status()

    def _save_admin_draft(self) -> None:
        try:
            self._capture_admin_table_edits()
        except Exception as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Admin Studio", f"Admin changes are invalid: {exc}")
            return
        self._sync_admin_status()

    def _sync_admin_status(self) -> None:
        summary = self.admin_studio.summary(self.admin_draft)
        self.admin_tracks_pill.setText(f"Tracks: {summary.track_count}")
        self.admin_questions_pill.setText(f"Questions: {summary.question_count}")
        self.admin_unsaved_pill.setText(f"Unsaved changes: {summary.dirty_count}")
        self.admin_unsaved_pill.setProperty("adminStatus", "dirty" if summary.dirty_count else "clean")
        if summary.validation_errors:
            self.admin_validation_pill.setText(f"Validation blocked: {len(summary.validation_errors)} issues")
            self.admin_validation_pill.setProperty("adminStatus", "blocked")
        else:
            self.admin_validation_pill.setText("Validation: ready")
            self.admin_validation_pill.setProperty("adminStatus", "ready")
        for pill in (self.admin_tracks_pill, self.admin_questions_pill, self.admin_unsaved_pill, self.admin_validation_pill):
            self._refresh_widget_style(pill)
        status = f"Tracks: {summary.track_count}    Questions: {summary.question_count}    Unsaved changes: {summary.dirty_count}"
        if summary.validation_errors:
            status = f"{status}    Validation blocked: {len(summary.validation_errors)} issues"
        else:
            status = f"{status}    Validation: ready"
        if self.admin_edit_mode:
            status = f"Edit mode    {status}"
        self.admin_status_label.setText(status)
        self.admin_publish_button.setEnabled(self.admin_edit_mode and not bool(summary.validation_errors))

    def _has_admin_table_edits(self) -> bool:
        for table in self._admin_tables.values():
            for row_index in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row_index, column)
                    if item is not None and item.text() != str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or ""):
                        return True
        return False

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
        selector = getattr(self, "admin_deepseek_model_selector", None)
        if selector is not None:
            selected_model = selector.currentData() or selector.currentText()
            self.admin_draft.update_deepseek_model(str(selected_model))
        notifications = self._admin_tables.get("notifications")
        if notifications is not None:
            for row_index in range(notifications.rowCount()):
                id_item = notifications.item(row_index, 0)
                event_item = notifications.item(row_index, 1)
                if event_item is None:
                    continue
                event_type = event_item.text().strip()
                active_text = notifications.item(row_index, 3).text() if notifications.item(row_index, 3) else "true"
                rule_id = id_item.text().strip() if id_item else ""
                if str(active_text or "").strip().lower() in {"delete", "remove"}:
                    if rule_id:
                        self.admin_draft.delete_notification_rule(int(rule_id))
                    continue
                if not event_type:
                    continue
                self.admin_draft.update_notification_rule(
                    event_type,
                    {
                        "id": rule_id,
                        "label": notifications.item(row_index, 2).text() if notifications.item(row_index, 2) else "",
                        "active": active_text,
                        "trigger_timing": notifications.item(row_index, 4).text() if notifications.item(row_index, 4) else "event",
                        "date_field": notifications.item(row_index, 5).text() if notifications.item(row_index, 5) else "",
                        "offset_days": notifications.item(row_index, 6).text() if notifications.item(row_index, 6) else "0",
                        "subject_template": notifications.item(row_index, 7).text() if notifications.item(row_index, 7) else "",
                        "body_template": notifications.item(row_index, 8).text() if notifications.item(row_index, 8) else "",
                        "recipients": notifications.item(row_index, 9).text() if notifications.item(row_index, 9) else "",
                    },
                )
        self._sync_admin_status()

    def _show_admin_review_changes_dialog(self) -> None:
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
            self.admin_status_label.setText("No admin changes to review.")
            return
        dialog = self._build_admin_review_changes_dialog(summary)
        self.admin_review_changes_dialog = dialog
        dialog.show()

    def _build_admin_review_changes_dialog(self, summary: Any) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioReviewChangesDialog")
        dialog.setWindowTitle("Review Changes")
        dialog.resize(860, 680)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Review Changes", "SectionTitle"))
        layout.addWidget(self._label("Review draft changes by section before publishing. Backups are created before writing."))
        scroll = self.QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = self.QtWidgets.QWidget()
        content_layout = self.QtWidgets.QVBoxLayout(content)
        changed_payloads = self.admin_draft.changed_payloads()
        grouped_files: dict[str, list[str]] = {}
        for filename in summary.changed_files:
            grouped_files.setdefault(self._admin_review_section_title(filename), []).append(filename)
        for section_title, filenames in grouped_files.items():
            section_card, section_layout = self._surface()
            section_card.setObjectName("AdminStudioReviewChangedSectionCard")
            section_layout.addWidget(self._label(section_title, "AdminStudioConceptTitle"))
            for filename in filenames:
                card, card_layout = self._surface()
                card.setObjectName("AdminStudioReviewChangedFileCard")
                card_layout.addWidget(self._label(filename, "AdminStudioChip"))
                matching_lines = [line for line in summary.lines if self._admin_review_line_matches_file(filename, line)]
                if not matching_lines and summary.lines:
                    matching_lines = summary.lines[:6]
                for line in matching_lines[:8]:
                    card_layout.addWidget(self._label(line))
                before_after = changed_payloads.get(filename)
                if before_after is not None:
                    for line in self._admin_review_payload_diff_lines(filename, before_after[0], before_after[1])[:8]:
                        card_layout.addWidget(self._label(line))
                section_layout.addWidget(card)
            content_layout.addWidget(section_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        final_confirmation = self.QtWidgets.QCheckBox("I reviewed these draft changes and am ready to continue to publish.")
        final_confirmation.setObjectName("AdminStudioReviewFinalConfirmation")
        layout.addWidget(final_confirmation)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        actions.addWidget(cancel)
        publish = self._primary_button("Publish Changes")
        publish.setObjectName("AdminStudioReviewPublishButton")
        publish.setEnabled(False)
        final_confirmation.toggled.connect(publish.setEnabled)
        publish.clicked.connect(lambda _checked=False: (dialog.close(), self._show_admin_publish_confirmation_dialog()))
        actions.addWidget(publish)
        layout.addLayout(actions)
        return dialog

    def _admin_review_section_title(self, filename: str) -> str:
        return {
            "rubric.json": "Rubrics",
            "question_overrides.json": "Questions & Flow",
            "school_offer_settings.json": "Templates & Folders",
            "deepseek_prompts.json": "DeepSeek Prompts",
            "interview_app_settings.json": "DeepSeek Model",
            "notification_rules.sqlite3": "Notifications",
        }.get(str(filename or "").strip(), "Advanced JSON")

    def _show_admin_publish_confirmation_dialog(self) -> None:
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
            self.admin_status_label.setText("No admin changes to publish.")
            return
        dialog = self._build_admin_publish_confirmation_dialog(summary)
        self.admin_publish_confirmation_dialog = dialog
        dialog.show()

    def _build_admin_publish_confirmation_dialog(self, summary: Any) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioPublishConfirmationDialog")
        dialog.setWindowTitle("Publish Confirmation")
        dialog.resize(760, 560)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Publish Confirmation", "SectionTitle"))
        layout.addWidget(self._label("Validation: ready", "AdminStudioChip"))
        layout.addWidget(self._label("These draft changes will be published after final confirmation. Backups are created first."))
        scroll = self.QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = self.QtWidgets.QWidget()
        content_layout = self.QtWidgets.QVBoxLayout(content)
        changed_payloads = self.admin_draft.changed_payloads()
        grouped_files: dict[str, list[str]] = {}
        for filename in summary.changed_files:
            grouped_files.setdefault(self._admin_review_section_title(filename), []).append(filename)
        for section_title, filenames in grouped_files.items():
            section_card, section_layout = self._surface()
            section_card.setObjectName("AdminStudioPublishSectionSummaryCard")
            section_layout.addWidget(self._label(section_title, "AdminStudioConceptTitle"))
            for filename in filenames:
                card, card_layout = self._surface()
                card.setObjectName("AdminStudioPublishFileSummaryCard")
                card_layout.addWidget(self._label(filename, "AdminStudioChip"))
                for line in [line for line in summary.lines if self._admin_review_line_matches_file(filename, line)][:6]:
                    card_layout.addWidget(self._label(line))
                before_after = changed_payloads.get(filename)
                if before_after is not None:
                    for line in self._admin_review_payload_diff_lines(filename, before_after[0], before_after[1])[:6]:
                        card_layout.addWidget(self._label(line))
                section_layout.addWidget(card)
            content_layout.addWidget(section_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        final_confirmation = self.QtWidgets.QCheckBox("I reviewed these changes and want to publish them.")
        final_confirmation.setObjectName("AdminStudioPublishFinalConfirmation")
        layout.addWidget(final_confirmation)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        actions.addWidget(cancel)
        confirm = self._primary_button("Confirm Publish")
        confirm.setObjectName("AdminStudioConfirmPublishButton")
        confirm.setEnabled(False)
        final_confirmation.toggled.connect(confirm.setEnabled)
        confirm.clicked.connect(lambda _checked=False: (dialog.close(), self._publish_admin_draft_confirmed()))
        actions.addWidget(confirm)
        layout.addLayout(actions)
        return dialog

    def _admin_review_payload_diff_lines(self, filename: str, before: Any, after: Any) -> list[str]:
        if filename == "deepseek_prompts.json" and isinstance(before, dict) and isinstance(after, dict):
            lines: list[str] = []
            for key in sorted(set(before) | set(after)):
                if before.get(key) == after.get(key):
                    continue
                lines.append(f"Before {key}: {before.get(key, '')}")
                lines.append(f"After {key}: {after.get(key, '')}")
            return lines
        if isinstance(before, dict) and isinstance(after, dict):
            changed_keys = [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]
            return [f"Changed keys: {', '.join(str(key) for key in changed_keys[:8])}"] if changed_keys else []
        return ["Before/after changes available for review."]

    def _admin_review_line_matches_file(self, filename: str, line: str) -> bool:
        lowered = line.lower()
        if filename == "deepseek_prompts.json":
            return "prompt" in lowered
        if filename == "rubric.json":
            return "trait" in lowered or "rubric" in lowered
        if filename == "school_offer_settings.json":
            return "school" in lowered or "folder" in lowered
        if filename == "notification_rules.sqlite3":
            return "notification" in lowered
        if filename == "interview_app_settings.json":
            return "deepseek model" in lowered or "model" in lowered
        if filename == "question_overrides.json":
            return "question" in lowered or "flow" in lowered
        return True

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
        file_lines = "\n".join(f"- {filename}" for filename in summary.changed_files)
        change_lines = "\n".join(summary.lines[:12]) or "Admin settings changed."
        message = (
            "Apply these admin changes?\n\n"
            "Changed files:\n"
            f"{file_lines}\n\n"
            "Review:\n"
            f"{change_lines}\n\n"
            "Backups will be created before writing."
        )
        result = self.QtWidgets.QMessageBox.question(
            self.window,
            "Review Admin Changes",
            message,
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if result != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._publish_admin_draft_confirmed()

    def _publish_admin_draft_confirmed(self) -> None:
        applied = self.admin_studio.apply_draft(self.admin_draft, confirm=True)
        if not applied.applied:
            self.QtWidgets.QMessageBox.warning(self.window, "Admin Studio", "\n".join(applied.validation_errors or ["Admin changes were not applied."]))
            return
        self._commit_admin_table_baselines()
        self.admin_draft = self.admin_studio.create_draft()
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()
        self.admin_status_label.setText("Admin changes applied.")

    def _show_admin_discard_confirmation_dialog(self) -> None:
        if not (self.admin_draft.is_dirty or self._has_admin_table_edits()):
            self._discard_admin_changes_confirmed()
            return
        try:
            self._capture_admin_table_edits()
        except Exception:
            pass
        dialog = self._build_admin_discard_confirmation_dialog(self.admin_draft.change_summary())
        self.admin_discard_confirmation_dialog = dialog
        dialog.show()

    def _build_admin_discard_confirmation_dialog(self, summary: Any) -> Any:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("AdminStudioDiscardConfirmationDialog")
        dialog.setWindowTitle("Discard Changes")
        dialog.resize(720, 520)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Discard Changes", "SectionTitle"))
        layout.addWidget(self._label("These draft changes will be discarded and cannot be recovered from this workspace."))
        scroll = self.QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content = self.QtWidgets.QWidget()
        content_layout = self.QtWidgets.QVBoxLayout(content)
        changed_payloads = self.admin_draft.changed_payloads()
        grouped_files: dict[str, list[str]] = {}
        for filename in summary.changed_files:
            grouped_files.setdefault(self._admin_review_section_title(filename), []).append(filename)
        for section_title, filenames in grouped_files.items():
            section_card, section_layout = self._surface()
            section_card.setObjectName("AdminStudioDiscardSectionSummaryCard")
            section_layout.addWidget(self._label(section_title, "AdminStudioConceptTitle"))
            for filename in filenames:
                card, card_layout = self._surface()
                card.setObjectName("AdminStudioDiscardFileSummaryCard")
                card_layout.addWidget(self._label(filename, "AdminStudioChip"))
                for line in [line for line in summary.lines if self._admin_review_line_matches_file(filename, line)][:6]:
                    card_layout.addWidget(self._label(line))
                before_after = changed_payloads.get(filename)
                if before_after is not None:
                    for line in self._admin_review_payload_diff_lines(filename, before_after[0], before_after[1])[:6]:
                        card_layout.addWidget(self._label(line))
                section_layout.addWidget(card)
            content_layout.addWidget(section_card)
        if not summary.changed_files:
            content_layout.addWidget(self._label("No saved draft changes."))
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        actions.addWidget(cancel)
        confirm = self.QtWidgets.QPushButton("Discard Changes")
        confirm.setObjectName("AdminStudioConfirmDiscardButton")
        confirm.clicked.connect(lambda _checked=False: (dialog.close(), self._discard_admin_changes_confirmed()))
        actions.addWidget(confirm)
        layout.addLayout(actions)
        return dialog

    def _discard_admin_changes_confirmed(self) -> None:
        self.admin_draft = self.admin_draft.discard()
        self._revert_admin_table_edits()
        selector = getattr(self, "admin_deepseek_model_selector", None)
        if selector is not None:
            selected = str(self.admin_draft.app_settings.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
            index = selector.findData(selected)
            selector.setCurrentIndex(index if index >= 0 else selector.findData(DEFAULT_DEEPSEEK_MODEL))
        self._set_admin_editing_enabled(False)
        self._sync_admin_status()

    def _discard_admin_changes(self) -> None:
        if self.admin_draft.is_dirty or self._has_admin_table_edits():
            result = self.QtWidgets.QMessageBox.question(
                self.window,
                "Discard Admin Changes",
                "Discard unsaved Admin Studio edits and restore the last saved settings?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if result != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._discard_admin_changes_confirmed()

    def _revert_admin_table_edits(self) -> None:
        self._admin_syncing_table_edits = True
        try:
            for table in self._admin_tables.values():
                for row_index in range(table.rowCount()):
                    for column in range(table.columnCount()):
                        item = table.item(row_index, column)
                        if item is None:
                            continue
                        baseline = str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or "")
                        item.setText(baseline)
                        item.setData(self.QtCore.Qt.ItemDataRole.BackgroundRole, None)
                        item.setToolTip("")
        finally:
            self._admin_syncing_table_edits = False

    def _commit_admin_table_baselines(self) -> None:
        self._admin_syncing_table_edits = True
        try:
            for table in self._admin_tables.values():
                for row_index in range(table.rowCount()):
                    for column in range(table.columnCount()):
                        item = table.item(row_index, column)
                        if item is None:
                            continue
                        item.setData(self.QtCore.Qt.ItemDataRole.UserRole, item.text())
        finally:
            self._admin_syncing_table_edits = False

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

    def _staffing_page(self) -> Any:
        page, layout = self._scrollable_page()
        header = self.QtWidgets.QHBoxLayout()
        header.addWidget(self._label("Classroom Detail", "Title"), 1)
        updated = self._label("Last updated: May 8, 2025 9:41 AM", "PySideStaffingUpdatedLabel")
        updated.setWordWrap(False)
        header.addWidget(updated)
        filters = self.QtWidgets.QPushButton("Filters")
        filters.setObjectName("PySideStaffingFiltersButton")
        header.addWidget(filters)
        layout.addLayout(header)
        self.staffing_status_label = self._label("")
        layout.addWidget(self.staffing_status_label)
        self.staffing_metrics_label = self._label("")
        self.staffing_metrics_label.setObjectName("PySideStaffingMetricsLabel")
        self.staffing_metrics_label.hide()
        layout.addWidget(self.staffing_metrics_label)

        selector_row = self.QtWidgets.QHBoxLayout()
        self.staffing_school_selector = self.QtWidgets.QComboBox()
        self.staffing_school_selector.setObjectName("PySideStaffingSchoolSelector")
        self.staffing_school_selector.currentIndexChanged.connect(self._select_staffing_school_index)
        selector_row.addWidget(self.staffing_school_selector)
        self.staffing_classroom_selector = self.QtWidgets.QComboBox()
        self.staffing_classroom_selector.setObjectName("PySideStaffingClassroomSelector")
        self.staffing_classroom_selector.currentIndexChanged.connect(self._select_staffing_classroom_index)
        selector_row.addWidget(self.staffing_classroom_selector)
        selector_row.addStretch(1)
        layout.addLayout(selector_row)

        main = self.QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        main.setObjectName("PySideStaffingSectionSplitter")
        main.setChildrenCollapsible(False)
        list_frame, list_layout = self._surface()
        list_frame.setMinimumWidth(220)
        self.staffing_classroom_list = self.QtWidgets.QListWidget()
        self.staffing_classroom_list.setObjectName("PySideStaffingClassroomList")
        self.staffing_classroom_list.currentRowChanged.connect(self._select_staffing_classroom_index)
        list_layout.addWidget(self._label("Classrooms", "SectionTitle"))
        list_layout.addWidget(self.staffing_classroom_list, 1)
        main.addWidget(list_frame)

        detail_frame, detail_layout = self._surface()
        detail_frame.setMinimumWidth(420)
        detail_header = self.QtWidgets.QHBoxLayout()
        title_column = self.QtWidgets.QVBoxLayout()
        self.staffing_classroom_title = self._label("", "PySideStaffingClassroomTitle")
        self.staffing_classroom_subtitle = self._label("")
        title_column.addWidget(self.staffing_classroom_title)
        title_column.addWidget(self.staffing_classroom_subtitle)
        detail_header.addLayout(title_column, 1)
        detail_header.addWidget(self._label("Priority Status"))
        self.staffing_priority_badge = self._label("", "PySideStaffingPriorityBadge")
        detail_header.addWidget(self.staffing_priority_badge)
        detail_layout.addLayout(detail_header)

        card_row = self.QtWidgets.QHBoxLayout()
        card_row.setSpacing(10)
        self.staffing_metric_cards_layout = card_row
        detail_layout.addLayout(card_row)

        detail_layout.addWidget(self._label("Positions", "SectionTitle"))
        self.staffing_positions_table = self.QtWidgets.QTableWidget(0, 7)
        self.staffing_positions_table.setObjectName("PySideStaffingPositionsTable")
        self.staffing_positions_table.setHorizontalHeaderLabels(
            ["Position", "Person", "Status", "Start Date", "Days Open", "Permit Status", "Action"]
        )
        self.staffing_positions_table.horizontalHeader().setStretchLastSection(True)
        self.staffing_positions_table.horizontalHeader().setSectionResizeMode(self.QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.staffing_positions_table.cellClicked.connect(
            lambda row, column, widget=self.staffing_positions_table: self._open_staffing_assignment_details_from_table(widget, row, column)
        )
        detail_layout.addWidget(self.staffing_positions_table, 1)
        add_position = self.QtWidgets.QPushButton("+  Add Position")
        add_position.setObjectName("PySideStaffingAddPositionButton")
        detail_layout.addWidget(add_position)
        main.addWidget(detail_frame)
        self.staffing_detail_drawer = self._staffing_detail_drawer()
        main.addWidget(self.staffing_detail_drawer)
        main.setSizes([360, 900, 380])
        layout.addWidget(main, 1)

        tabs = self.QtWidgets.QTabWidget()
        tabs.setObjectName("PySideStaffingSchoolTabs")
        tabs.hide()
        self.staffing_tabs = tabs
        layout.addWidget(tabs)
        self._refresh_staffing_dashboard()
        return page

    def _staffing_v2_page(self) -> Any:
        self.staffing_store.initialize()
        existing_assignments = self.staffing_store.list_assignments()
        if STAFFING_SEED_PATH.exists() and not existing_assignments:
            self.staffing_store.import_seed_file(STAFFING_SEED_PATH)
        elif STAFFING_SEED_PATH.exists():
            seed_data = json.loads(STAFFING_SEED_PATH.read_text(encoding="utf-8"))
            seed_assignment_count = 0
            for school in seed_data.get("schools", []):
                for classroom in school.get("classrooms", []):
                    seed_assignment_count += len(classroom.get("slots", classroom.get("positions", [])))
                for support_row in school.get("support_rows", []):
                    seed_assignment_count += len(support_row.get("slots", support_row.get("positions", [])))
            if len(existing_assignments) < seed_assignment_count:
                self.staffing_store.import_seed_file(STAFFING_SEED_PATH)
        self._import_queued_staffing_director_referrals()
        self._sync_staffing_director_referrals_from_history()
        dashboard = StaffingDashboardV2Page(
            QtCore=self.QtCore,
            QtGui=self.QtGui,
            QtWidgets=self.QtWidgets,
            store=self.staffing_store,
            service_factory=lambda: StaffingService(self.staffing_store, notification_service=self._notification_service()),
            actions={
                "open_position": self._open_staffing_position,
                "mark_coming": self._mark_staffing_coming,
                "mark_filled": self._mark_staffing_filled,
                "mark_dont_need": self._mark_staffing_not_needed,
                "revert_coming": self._revert_staffing_coming,
                "clear_replacement": self._clear_staffing_replacement,
                "update_permit": self._update_staffing_permit,
                "replace_employee": self._mark_staffing_replacing,
                "view_details": self._open_staffing_assignment_details,
            },
            school_filter=self.director_staffing_school,
            notification_store_path=NOTIFICATION_RULES_PATH,
            notification_service_factory=self._notification_service,
        )
        self.staffing_v2_dashboard = dashboard
        self._start_staffing_referral_queue_polling()
        return dashboard.widget

    def _start_staffing_referral_queue_polling(self) -> None:
        if self._staffing_referral_queue_timer is not None:
            return
        timer = self.QtCore.QTimer(self.window)
        timer.setInterval(5000)
        timer.timeout.connect(self._poll_staffing_referral_queue)
        timer.start()
        self._staffing_referral_queue_timer = timer

    def _poll_staffing_referral_queue(self) -> None:
        imported = self._import_queued_staffing_director_referrals()
        if imported and getattr(self, "staffing_v2_dashboard", None) is not None:
            self.staffing_v2_dashboard.refresh()

    def _import_queued_staffing_director_referrals(self) -> int:
        if not hasattr(self, "staffing_store"):
            return 0
        try:
            payloads = _pop_staffing_referral_queue_for_school(self.director_staffing_school)
        except OSError:
            return 0
        if not payloads:
            return 0
        service = StaffingService(self.staffing_store, notification_service=self._notification_service())
        imported = 0
        for payload in payloads:
            try:
                service.upsert_director_candidate_referral(
                    history_id=str(payload["history_id"]),
                    candidate_name=str(payload["candidate_name"]),
                    school=str(payload["school"]),
                    position=str(payload.get("position", "")),
                    interviewer_rating=payload.get("interviewer_rating"),
                    interviewer_outcome=str(payload["interviewer_outcome"]),
                    interview_date=str(payload.get("interview_date", "")),
                    candidate_email=str(payload.get("candidate_email", "")),
                    referral_date=str(payload.get("referral_date", "")),
                    queue_on_lock=True,
                )
            except (OSError, ValueError, StaffingEditLock, KeyError):
                continue
            imported += 1
        return imported

    def _sync_staffing_director_referrals_from_history(self) -> None:
        if not hasattr(self, "staffing_store"):
            return
        school_filter = str(self.director_staffing_school or "").strip()
        service = StaffingService(self.staffing_store, notification_service=self._notification_service())
        dismissed_history_ids = self.staffing_store.list_dismissed_director_referral_history_ids()
        for row in self.model.home.history_rows:
            outcome = _director_referral_outcome(row.status)
            if not outcome:
                continue
            if school_filter and row.school != school_filter:
                continue
            history_id = row.row_key or f"{row.candidate}:{row.interview_date}"
            if history_id in dismissed_history_ids:
                continue
            try:
                service.upsert_director_candidate_referral(
                    history_id=history_id,
                    candidate_name=row.candidate,
                    school=row.school,
                    position=row.position,
                    interviewer_rating=_director_referral_rating(row.score),
                    interviewer_outcome=outcome,
                    interview_date=row.interview_date,
                    candidate_email=row.candidate_email,
                    queue_on_lock=True,
                )
            except (OSError, ValueError, StaffingEditLock):
                continue

    def _record_staffing_director_referral_from_finalize_result(self, result: dict[str, Any]) -> None:
        if not hasattr(self, "staffing_store") or self.session is None or not isinstance(result, dict):
            return
        scoring = result.get("scoring", {})
        if not isinstance(scoring, dict):
            return
        outcome = _director_referral_outcome(str(scoring.get("outcome", "") or ""))
        if not outcome:
            return
        rating_source = scoring.get("interviewer_rating", scoring.get("rating", scoring.get("percent_of_max", "")))
        try:
            _append_staffing_referral_queue(
                {
                    "history_id": str(result.get("history_id", "") or f"{self.session.candidate_name}:{self.session.interview_date}"),
                    "candidate_name": self.session.candidate_name,
                    "school": self.session.school,
                    "position": self.session.position,
                    "interviewer_rating": _director_referral_rating(str(rating_source)),
                    "interviewer_outcome": outcome,
                    "interview_date": self.session.interview_date,
                    "candidate_email": "",
                    "referral_date": self.session.interview_date,
                }
            )
        except OSError:
            return

    def _staffing_detail_drawer(self) -> Any:
        drawer = self.QtWidgets.QFrame()
        drawer.setObjectName("PySideStaffingDetailDrawer")
        drawer.setMinimumWidth(300)
        drawer.hide()
        layout = self.QtWidgets.QVBoxLayout(drawer)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.staffing_detail_drawer_layout = layout
        return drawer

    def _refresh_staffing_dashboard(self) -> None:
        self.staffing_store.initialize()
        existing_assignments = self.staffing_store.list_assignments()
        if STAFFING_SEED_PATH.exists() and not existing_assignments:
            self.staffing_store.import_seed_file(STAFFING_SEED_PATH)
        elif STAFFING_SEED_PATH.exists():
            seed_data = json.loads(STAFFING_SEED_PATH.read_text(encoding="utf-8"))
            seed_assignment_count = 0
            for school in seed_data.get("schools", []):
                for classroom in school.get("classrooms", []):
                    seed_assignment_count += len(classroom.get("slots", classroom.get("positions", [])))
                for support_row in school.get("support_rows", []):
                    seed_assignment_count += len(support_row.get("slots", support_row.get("positions", [])))
            if len(existing_assignments) < seed_assignment_count:
                self.staffing_store.import_seed_file(STAFFING_SEED_PATH)
        service = StaffingService(self.staffing_store, notification_service=self._notification_service())
        metrics = service.staffing_metrics(today=date.today(), school=self.director_staffing_school)
        self._staffing_rows_by_school = {}
        for row in metrics.rows:
            self._staffing_rows_by_school.setdefault(row.school or "Unassigned", []).append(row)
        if self.staffing_metrics_label is not None:
            self.staffing_metrics_label.setText(
                f"Open positions: {metrics.open_count}    "
                f"Average days to fill: {metrics.avg_days_to_fill:.1f}    "
                f"Open > 7 days: {metrics.open_over_7_days}"
            )
        tabs = self.staffing_tabs
        if tabs is None:
            return
        current_school = tabs.tabText(tabs.currentIndex()) if tabs.count() else ""
        while tabs.count():
            widget = tabs.widget(0)
            for table in widget.findChildren(self.QtWidgets.QTableWidget):
                table.setObjectName("")
            tabs.removeTab(0)
            widget.deleteLater()
        self.staffing_table = None
        for school, rows in self._staffing_rows_by_school.items():
            tab = self.QtWidgets.QWidget()
            tab_layout = self.QtWidgets.QVBoxLayout(tab)
            summary = self._label(_staffing_school_summary(rows))
            summary.setObjectName("PySideStaffingSummary")
            tab_layout.addWidget(summary)
            ratio_summary = _staffing_ratio_summary(rows)
            if ratio_summary:
                tab_layout.addWidget(self._label(ratio_summary))
            tab_layout.addWidget(self._staffing_workbook_board(rows), 2)
            tabs.addTab(tab, school)
            tabs.tabBar().setTabTextColor(tabs.count() - 1, self.QtGui.QColor(_staffing_school_tab_color(tabs.count() - 1)))
        selector = getattr(self, "staffing_school_selector", None)
        if selector is not None:
            selector.blockSignals(True)
            selector.clear()
            for school in self._staffing_rows_by_school:
                selector.addItem(school)
            selector.setCurrentIndex(max(0, tabs.currentIndex()))
            selector.blockSignals(False)
        if current_school:
            for index in range(tabs.count()):
                if tabs.tabText(index) == current_school:
                    tabs.setCurrentIndex(index)
                    if selector is not None:
                        selector.setCurrentIndex(index)
                    break
        self._refresh_staffing_classroom_choices()
        self._refresh_staffing_classroom_detail()

    def _select_staffing_school_index(self, index: int) -> None:
        tabs = getattr(self, "staffing_tabs", None)
        if tabs is not None and 0 <= index < tabs.count():
            tabs.setCurrentIndex(index)
        self._refresh_staffing_classroom_choices()
        self._refresh_staffing_classroom_detail()

    def _select_staffing_classroom_index(self, index: int) -> None:
        classroom_selector = getattr(self, "staffing_classroom_selector", None)
        classroom_list = getattr(self, "staffing_classroom_list", None)
        if classroom_selector is not None and 0 <= index < classroom_selector.count() and classroom_selector.currentIndex() != index:
            classroom_selector.blockSignals(True)
            classroom_selector.setCurrentIndex(index)
            classroom_selector.blockSignals(False)
        if classroom_list is not None and 0 <= index < classroom_list.count() and classroom_list.currentRow() != index:
            classroom_list.blockSignals(True)
            classroom_list.setCurrentRow(index)
            classroom_list.blockSignals(False)
        self._refresh_staffing_classroom_detail()

    def _refresh_staffing_classroom_choices(self) -> None:
        school = self._selected_staffing_school()
        rows = self._staffing_rows_by_school.get(school, [])
        classrooms: list[str] = []
        for row in rows:
            if row.classroom not in classrooms:
                classrooms.append(row.classroom)
        classroom_rows = {classroom: [row for row in rows if row.classroom == classroom] for classroom in classrooms}
        selector = getattr(self, "staffing_classroom_selector", None)
        if selector is not None:
            current = selector.currentText()
            selector.blockSignals(True)
            selector.clear()
            selector.addItems(classrooms)
            if current in classrooms:
                selector.setCurrentIndex(classrooms.index(current))
            elif classrooms:
                selector.setCurrentIndex(0)
            selector.blockSignals(False)
        classroom_list = getattr(self, "staffing_classroom_list", None)
        if classroom_list is not None:
            classroom_list.setMinimumWidth(360)
            classroom_list.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            classroom_list.setWordWrap(True)
            current_row = classroom_list.currentRow()
            classroom_list.blockSignals(True)
            classroom_list.clear()
            for classroom in classrooms:
                item = self.QtWidgets.QListWidgetItem(_staffing_classroom_list_label(classroom, classroom_rows[classroom]))
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, classroom)
                item.setToolTip(classroom)
                item.setBackground(self.QtGui.QColor(_staffing_classroom_list_color(classroom_rows[classroom])))
                classroom_list.addItem(item)
            if 0 <= current_row < classroom_list.count():
                classroom_list.setCurrentRow(current_row)
            elif classrooms:
                classroom_list.setCurrentRow(0)
            classroom_list.blockSignals(False)

    def _selected_staffing_school(self) -> str:
        selector = getattr(self, "staffing_school_selector", None)
        if selector is not None and selector.currentText():
            return selector.currentText()
        return next(iter(self._staffing_rows_by_school), "")

    def _selected_staffing_classroom(self) -> str:
        selector = getattr(self, "staffing_classroom_selector", None)
        if selector is not None and selector.currentText():
            return selector.currentText()
        rows = self._staffing_rows_by_school.get(self._selected_staffing_school(), [])
        return rows[0].classroom if rows else ""

    def _selected_staffing_classroom_rows(self) -> list[Any]:
        school = self._selected_staffing_school()
        classroom = self._selected_staffing_classroom()
        return [row for row in self._staffing_rows_by_school.get(school, []) if row.classroom == classroom]

    def _refresh_staffing_classroom_detail(self) -> None:
        rows = self._selected_staffing_classroom_rows()
        school = self._selected_staffing_school()
        classroom = self._selected_staffing_classroom()
        if self.staffing_classroom_title is not None:
            self.staffing_classroom_title.setText(classroom)
        if self.staffing_classroom_subtitle is not None:
            self.staffing_classroom_subtitle.setText(school)
        if self.staffing_priority_badge is not None:
            self.staffing_priority_badge.setText(_staffing_priority_status(rows))
        self._refresh_staffing_metric_cards(rows)
        self._refresh_staffing_positions_table(rows)

    def _refresh_staffing_metric_cards(self, rows: list[Any]) -> None:
        layout = getattr(self, "staffing_metric_cards_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        total = len(rows)
        filled = sum(1 for row in rows if row.status == "filled")
        open_rows = [row for row in rows if row.status in {"need_now", "replace"}]
        avg_open = sum((row.days_open or 0) for row in open_rows) / len(open_rows) if open_rows else 0.0
        capacity = next((row.classroom_capacity for row in rows if row.classroom_capacity is not None), None)
        program = next((row.classroom_program for row in rows if getattr(row, "classroom_program", "")), "")
        if not program:
            program = next((row.ratio_group for row in rows if row.ratio_group), "Preschool")
        card_values = [
            ("Program", program),
            ("Licensed Capacity", "" if capacity is None else str(capacity)),
            ("Total Positions", str(total)),
            ("Filled", f"{filled}\n{round((filled / total) * 100) if total else 0}%"),
            ("Open", str(len(open_rows))),
            ("Avg Days to Fill", f"{avg_open:.1f}"),
        ]
        for label, value in card_values:
            layout.addWidget(self._staffing_metric_card(label, value))

    def _staffing_metric_card(self, label: str, value: str) -> Any:
        frame, layout = self._surface()
        frame.setObjectName("PySideStaffingMetricCard")
        frame.setMinimumWidth(120)
        layout.addWidget(self._label(label))
        metric = self._label(value)
        metric.setObjectName("PySideStaffingMetricValue")
        layout.addWidget(metric)
        return frame

    def _refresh_staffing_positions_table(self, rows: list[Any]) -> None:
        table = getattr(self, "staffing_positions_table", None)
        if table is None:
            return
        table.setRowCount(len(rows))
        for row_index, assignment in enumerate(rows):
            values = [
                assignment.position_name,
                assignment.person_name or "OPEN POSITION",
                _staffing_display_status(assignment.status),
                assignment.start_date or "",
                "-" if assignment.days_open is None else str(assignment.days_open),
                _staffing_display_permit(assignment.permit_status or "unknown"),
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, assignment.assignment_id)
                item.setToolTip(assignment.position_name)
                if column == 2:
                    item.setBackground(self.QtGui.QColor(_staffing_status_color(assignment.status)))
                table.setItem(row_index, column, item)
            table.setCellWidget(row_index, 6, self._staffing_action_button(assignment.assignment_id, assignment.status))
        table.resizeColumnsToContents()

    def _staffing_color_key(self) -> Any:
        frame, layout = self._surface()
        layout.addWidget(self._label("Color Code Key", "SectionTitle"))
        key = self.QtWidgets.QGridLayout()
        entries = [
            ("Don't need now due to low enrollment / ratios", _staffing_status_color("dont_need_now")),
            ("Replace - employee gave notice they will be leaving", _staffing_status_color("replace")),
            ("Need Now - Job Opening", _staffing_status_color("need_now")),
            ("Coming (offer accepted)", _staffing_status_color("coming")),
            ("No Permit or Application Yet", _staffing_permit_color("no_permit_or_application")),
            ("Permit in Process (application and fingerprints submitted)", _staffing_permit_color("permit_in_process")),
            ("Teacher Permit Approved", _staffing_permit_color("teacher_permit_approved")),
            ("No units needed", _staffing_permit_color("no_units_needed")),
        ]
        for index, (label, color) in enumerate(entries):
            item = self._label(label)
            item.setStyleSheet(f"background-color: {color}; padding: 5px;")
            key.addWidget(item, index // 2, index % 2)
        layout.addLayout(key)
        return frame

    def _staffing_workbook_board(self, rows: list[Any]) -> Any:
        window = self

        class StaffingWorkbookTable(self.QtWidgets.QTableWidget):
            def dropEvent(table_self, event: Any) -> None:  # noqa: N802
                source = event.source()
                if source is None or not hasattr(source, "currentRow"):
                    event.ignore()
                    return
                target_row = table_self.rowAt(event.position().toPoint().y() if hasattr(event, "position") else event.pos().y())
                source_row = source.currentRow()
                if target_row < 0 or source_row < 0:
                    event.ignore()
                    return
                source_id = _table_assignment_id(source, source_row)
                target_id = _table_assignment_id(table_self, target_row)
                if source_id is None or target_id is None:
                    event.ignore()
                    return
                if window._confirm_staffing_move(source_id, target_id):
                    event.accept()
                    return
                event.ignore()

        table = StaffingWorkbookTable(0, 8)
        table.setObjectName("PySideStaffingWorkbookBoard")
        table.setHorizontalHeaderLabels(["Ratio", "Classroom", "Person", "Status", "Capacity", "Permit Status", "Details", "Action"])
        table.setDragEnabled(True)
        table.setAcceptDrops(True)
        table.setDragDropMode(self.QtWidgets.QAbstractItemView.DragDropMode.DragDrop)
        table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(self.QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in rows:
            grouped.setdefault((row.ratio_group or "", row.classroom), []).append(row)
        board_rows: list[tuple[str, str, Any | None]] = []
        current_ratio = object()
        for (ratio, classroom), assignments in grouped.items():
            if ratio and ratio != current_ratio:
                board_rows.append((ratio, "", None))
                current_ratio = ratio
            for assignment in assignments:
                board_rows.append((ratio, classroom, assignment))
        table.setRowCount(len(board_rows))
        for row_index, (ratio, classroom, assignment) in enumerate(board_rows):
            if assignment is None:
                item = self.QtWidgets.QTableWidgetItem(ratio)
                item.setBackground(self.QtGui.QColor("#b7d0ec"))
                table.setItem(row_index, 0, item)
                continue
            values = [
                "",
                classroom,
                assignment.person_name or "OPEN POSITION",
                assignment.status,
                "" if assignment.classroom_capacity is None else str(assignment.classroom_capacity),
                "",
                assignment.notes,
            ]
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem(value)
                item.setData(self.QtCore.Qt.ItemDataRole.UserRole, assignment.assignment_id)
                item.setToolTip(assignment.position_name)
                item.setFlags(item.flags() | self.QtCore.Qt.ItemFlag.ItemIsDragEnabled | self.QtCore.Qt.ItemFlag.ItemIsDropEnabled)
                if column == 2:
                    color = _staffing_permit_color(assignment.permit_status) if assignment.permit_status else _staffing_status_color(assignment.status)
                    item.setBackground(self.QtGui.QColor(color))
                if column == 3:
                    item.setBackground(self.QtGui.QColor(_staffing_status_color(assignment.status)))
                table.setItem(row_index, column, item)
            table.setCellWidget(
                row_index,
                5,
                self._staffing_permit_combo(assignment.assignment_id, assignment.permit_status, bool(assignment.person_name)),
            )
            table.setCellWidget(row_index, 7, self._staffing_action_button(assignment.assignment_id, assignment.status))
        table.cellClicked.connect(lambda row, column, widget=table: self._open_staffing_assignment_details_from_table(widget, row, column))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _staffing_permit_combo(self, assignment_id: int, permit_status: str, has_person: bool) -> Any:
        combo = self.QtWidgets.QComboBox()
        combo.addItems(STAFFING_PERMIT_VALUES)
        combo.setCurrentText(permit_status if permit_status in STAFFING_PERMIT_VALUES else "unknown")
        combo.setEnabled(has_person)
        combo.currentTextChanged.connect(lambda value, item=assignment_id: self._update_staffing_permit_from_table(item, value))
        return combo

    def _staffing_action_button(self, assignment_id: int, status: str) -> Any:
        menu_actions: list[tuple[str, Any]] = []
        if status == "dont_need_now":
            label = "Open"
            callback = lambda _checked=False, item=assignment_id: self._open_staffing_position(item)
            menu_actions.append(("Mark Not Needed", lambda item=assignment_id: self._mark_staffing_not_needed(item)))
        elif status in {"need_now", "replace"}:
            label = "Mark Coming"
            callback = lambda _checked=False, item=assignment_id: self._mark_staffing_coming(item)
            if status == "replace":
                menu_actions.append(("Clear Replacement", lambda item=assignment_id: self._clear_staffing_replacement(item)))
            menu_actions.append(("Mark Not Needed", lambda item=assignment_id: self._mark_staffing_not_needed(item)))
        elif status == "coming":
            label = "Mark Filled"
            callback = lambda _checked=False, item=assignment_id: self._mark_staffing_filled(item)
            menu_actions.append(("Revert Coming", lambda item=assignment_id: self._revert_staffing_coming(item)))
            menu_actions.append(("Mark Not Needed", lambda item=assignment_id: self._mark_staffing_not_needed(item)))
        elif status == "filled":
            label = "Replace"
            callback = lambda _checked=False, item=assignment_id: self._mark_staffing_replacing(item)
            menu_actions.append(("Update Permit", lambda item=assignment_id: self._update_staffing_permit(item)))
            menu_actions.append(("Mark Not Needed", lambda item=assignment_id: self._mark_staffing_not_needed(item)))
        else:
            label = "Review"
            callback = lambda _checked=False: None
        button = self.QtWidgets.QToolButton()
        button.setText(label)
        button.setProperty("staffing_assignment_id", assignment_id)
        button.clicked.connect(callback)
        if menu_actions:
            menu = self.QtWidgets.QMenu(button)
            for action_label, action_callback in menu_actions:
                menu.addAction(action_label, action_callback)
            button.setMenu(menu)
            button.setPopupMode(self.QtWidgets.QToolButton.ToolButtonPopupMode.DelayedPopup)
        return button

    def _open_staffing_assignment_details_from_table(self, table: Any, row: int, column: int) -> None:
        if column in {5, 7}:
            return
        assignment_id = _table_assignment_id(table, row)
        if assignment_id is None:
            return
        self._open_staffing_assignment_details(assignment_id)

    def _open_staffing_assignment_details(self, assignment_id: int) -> None:
        try:
            assignment = self.staffing_store.get_assignment(assignment_id)
        except Exception as exc:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText(str(exc) or "Staffing assignment not found.")
            return
        self._show_staffing_detail_drawer(assignment)

    def _show_staffing_detail_drawer(self, assignment: Any) -> None:
        drawer = getattr(self, "staffing_detail_drawer", None)
        layout = getattr(self, "staffing_detail_drawer_layout", None)
        if drawer is None or layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            if child_layout is not None:
                while child_layout.count():
                    child = child_layout.takeAt(0).widget()
                    if child is not None:
                        child.setParent(None)
                        child.deleteLater()
        title = "Person Details" if assignment.person_name else "Position Details"
        layout.addWidget(self._label(title, "PySideStaffingDrawerTitle"))
        layout.addWidget(self._label(assignment.person_name or "OPEN POSITION", "PySideStaffingDrawerName"))
        layout.addWidget(self._label(assignment.position_name))
        layout.addWidget(self._label(f"{assignment.classroom}  |  {assignment.school}"))
        layout.addWidget(self._label(_staffing_display_status(assignment.status)))
        layout.addWidget(self._label(_staffing_display_permit(assignment.permit_status or "unknown")))
        layout.addSpacing(8)

        form = self.QtWidgets.QFormLayout()
        person_field = self.QtWidgets.QLineEdit(assignment.person_name)
        person_field.setObjectName("PySideStaffingDetailPersonName")
        position_field = self.QtWidgets.QLineEdit(assignment.position_name)
        position_field.setObjectName("PySideStaffingDetailPositionName")
        type_field = self.QtWidgets.QComboBox()
        type_field.setObjectName("PySideStaffingDetailPositionType")
        type_values = [assignment.position_type, *STAFFING_POSITION_TYPES]
        type_field.addItems([value for index, value in enumerate(type_values) if value and value not in type_values[:index]])
        type_field.setEditable(True)
        type_field.setCurrentText(assignment.position_type)
        status_field = self.QtWidgets.QComboBox()
        status_field.setObjectName("PySideStaffingDetailStatus")
        for status in STAFFING_STATUS_VALUES:
            status_field.addItem(_staffing_display_status(status), status)
        status_index = status_field.findData(assignment.status)
        status_field.setCurrentIndex(max(0, status_index))
        classroom_field = self.QtWidgets.QComboBox()
        classroom_field.setObjectName("PySideStaffingDetailClassroom")
        classrooms = []
        for row in self._staffing_rows_by_school.get(assignment.school, []):
            if row.classroom not in classrooms:
                classrooms.append(row.classroom)
        classroom_field.addItems(classrooms or [assignment.classroom])
        classroom_field.setEditable(True)
        classroom_field.setCurrentText(assignment.classroom)
        program_field = self.QtWidgets.QComboBox()
        program_field.setObjectName("PySideStaffingDetailProgram")
        program_values = [assignment.classroom_program, *STAFFING_PROGRAM_VALUES]
        program_field.addItems([value for index, value in enumerate(program_values) if value and value not in program_values[:index]])
        program_field.setEditable(True)
        program_field.setCurrentText(assignment.classroom_program or "Preschool")
        start_field = self.QtWidgets.QDateEdit()
        start_field.setObjectName("PySideStaffingDetailStartDate")
        start_field.setCalendarPopup(True)
        start_field.setDisplayFormat("MMM d, yyyy")
        blank_date = self.QtCore.QDate(1900, 1, 1)
        start_field.setMinimumDate(blank_date)
        start_field.setSpecialValueText("-")
        if assignment.start_date:
            parsed = self.QtCore.QDate.fromString(assignment.start_date, "yyyy-MM-dd")
            start_field.setDate(parsed if parsed.isValid() else blank_date)
        else:
            start_field.setDate(blank_date)
        shift_start_field = self.QtWidgets.QTimeEdit()
        shift_start_field.setObjectName("PySideStaffingDetailShiftStart")
        shift_start_field.setDisplayFormat("h:mm AP")
        shift_end_field = self.QtWidgets.QTimeEdit()
        shift_end_field.setObjectName("PySideStaffingDetailShiftEnd")
        shift_end_field.setDisplayFormat("h:mm AP")
        for field, value in ((shift_start_field, assignment.shift_start), (shift_end_field, assignment.shift_end)):
            parsed = self.QtCore.QTime.fromString(value, "HH:mm")
            field.setTime(parsed if parsed.isValid() else self.QtCore.QTime(0, 0))
        permit_field = self.QtWidgets.QComboBox()
        permit_field.setObjectName("PySideStaffingDetailPermitStatus")
        permit_field.addItems(STAFFING_PERMIT_VALUES)
        permit_field.setCurrentText(assignment.permit_status if assignment.permit_status in STAFFING_PERMIT_VALUES else "unknown")
        days_open = "-" if not assignment.current_opened_date else _staffing_days_open_text(assignment.current_opened_date)
        notes_field = self.QtWidgets.QTextEdit()
        notes_field.setObjectName("PySideStaffingDetailNotes")
        notes_field.setPlainText(assignment.notes)
        notes_field.setFixedHeight(90)
        form.addRow("Person", person_field)
        form.addRow("Position", position_field)
        form.addRow("Position Type", type_field)
        form.addRow("Status", status_field)
        form.addRow("Classroom", classroom_field)
        form.addRow("Program", program_field)
        form.addRow("Days Open", self._label(days_open))
        form.addRow("Start Date", start_field)
        form.addRow("Permit Status", permit_field)
        form.addRow("Shift Start", shift_start_field)
        form.addRow("Shift End", shift_end_field)
        form.addRow("Notes", notes_field)
        layout.addLayout(form)
        layout.addStretch(1)
        actions = self.QtWidgets.QHBoxLayout()
        edit_button = self.QtWidgets.QPushButton("Edit")
        edit_button.clicked.connect(position_field.setFocus)
        actions.addWidget(edit_button)
        save_button = self._primary_button("Save")
        save_button.setObjectName("PySideStaffingDetailSave")

        def save_details() -> None:
            start_date = "" if start_field.date() == blank_date else start_field.date().toString("yyyy-MM-dd")
            shift_start = "" if not assignment.shift_start and shift_start_field.time() == self.QtCore.QTime(0, 0) else shift_start_field.time().toString("HH:mm")
            shift_end = "" if not assignment.shift_end and shift_end_field.time() == self.QtCore.QTime(0, 0) else shift_end_field.time().toString("HH:mm")
            self._run_staffing_action(
                lambda service: service.update_assignment_details(
                    assignment.id,
                    classroom=classroom_field.currentText(),
                    classroom_program=program_field.currentText(),
                    position_name=position_field.text(),
                    position_type=type_field.currentText(),
                    status=str(status_field.currentData() or assignment.status),
                    person_name=person_field.text(),
                    start_date=start_date,
                    shift_start=shift_start,
                    shift_end=shift_end,
                    permit_status=permit_field.currentText(),
                    notes=notes_field.toPlainText(),
                ),
                "Assignment details updated.",
            )
            if self.staffing_status_label is None or self.staffing_status_label.text() == "Assignment details updated.":
                self.QtCore.QTimer.singleShot(0, lambda item=assignment.id: self._open_staffing_assignment_details(item))

        save_button.clicked.connect(save_details)
        actions.addWidget(save_button)
        if assignment.person_name:
            replace_button = self.QtWidgets.QPushButton("Replace")
            replace_button.clicked.connect(lambda _checked=False, item=assignment.id: self._mark_staffing_replacing(item))
            actions.addWidget(replace_button)
        else:
            coming_button = self._primary_button("Mark Coming")
            coming_button.clicked.connect(lambda _checked=False, item=assignment.id: self._mark_staffing_coming(item))
            actions.addWidget(coming_button)
            not_needed_button = self.QtWidgets.QPushButton("Don't Need Now")
            not_needed_button.clicked.connect(lambda _checked=False, item=assignment.id: self._mark_staffing_not_needed(item))
            actions.addWidget(not_needed_button)
        close_button = self.QtWidgets.QPushButton("Close")
        close_button.clicked.connect(drawer.hide)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        drawer.show()

    def _confirm_staffing_move(self, source_assignment_id: int, target_assignment_id: int) -> bool:
        if source_assignment_id == target_assignment_id:
            return False
        try:
            source = self.staffing_store.get_assignment(source_assignment_id)
            target = self.staffing_store.get_assignment(target_assignment_id)
        except Exception as exc:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText(str(exc) or "Staffing move failed.")
            return False
        confirmed = self.QtWidgets.QMessageBox.question(
            self.window,
            "Move Teacher",
            f"Move {source.person_name or 'this person'} from {source.classroom} {source.position_name} "
            f"to {target.classroom} {target.position_name}?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirmed != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return False
        self._run_staffing_action(
            lambda service: service.move_person(source_assignment_id, target_assignment_id, confirmed=True),
            "Teacher moved.",
        )
        return self.staffing_status_label is None or self.staffing_status_label.text() == "Teacher moved."

    def _open_staffing_position(self, assignment_id: int) -> None:
        self._run_staffing_action(lambda service: service.open_position(assignment_id), "Position opened.")

    def _mark_staffing_filled(self, assignment_id: int) -> None:
        self._run_staffing_action(lambda service: service.mark_filled(assignment_id), "Position filled.")

    def _revert_staffing_coming(self, assignment_id: int) -> None:
        self._run_staffing_action(lambda service: service.revert_coming(assignment_id), "Incoming person reverted.")

    def _clear_staffing_replacement(self, assignment_id: int) -> None:
        self._run_staffing_action(lambda service: service.clear_replacement(assignment_id), "Replacement cleared.")

    def _mark_staffing_not_needed(self, assignment_id: int) -> None:
        confirmed = self.QtWidgets.QMessageBox.question(
            self.window,
            "Staffing",
            "Mark this position not needed?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirmed != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._run_staffing_action(lambda service: service.mark_not_needed(assignment_id, confirmed=True), "Position marked not needed.")

    def _update_staffing_permit(self, assignment_id: int) -> None:
        assignment = self.staffing_store.get_assignment(assignment_id)
        if assignment.person_id is None:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText("No person assigned to update.")
            return
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("PySideStaffingPermitDialog")
        dialog.setWindowTitle("Update Permit Status")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(self._label("Update Permit Status", "PySideStaffingDrawerTitle"))
        layout.addWidget(self._label(f"{assignment.school} / {assignment.classroom} / {assignment.person_name}"))
        form = self.QtWidgets.QFormLayout()
        permit_field = self.QtWidgets.QComboBox()
        permit_field.setObjectName("PySideStaffingPermitStatus")
        permit_field.addItems(STAFFING_PERMIT_VALUES)
        permit_field.setCurrentText(assignment.permit_status if assignment.permit_status in STAFFING_PERMIT_VALUES else "unknown")
        units_field = self.QtWidgets.QLineEdit("12" if assignment.permit_status == "teacher_permit_approved" else "")
        effective_date_field = self.QtWidgets.QLineEdit(date.today().isoformat())
        form.addRow("Permit Status", permit_field)
        form.addRow("Units", units_field)
        form.addRow("Effective Date", effective_date_field)
        layout.addLayout(form)
        layout.addWidget(self._label("This action updates the People table only. Position data will remain unchanged."))
        buttons = self.QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        buttons.addWidget(cancel)
        save = self._primary_button("Save")
        save.setObjectName("PySideStaffingPermitSave")

        def save_permit() -> None:
            self._run_staffing_action(
                lambda service: service.update_assignment_details(
                    assignment_id,
                    classroom=assignment.classroom,
                    shift_start=assignment.shift_start,
                    shift_end=assignment.shift_end,
                    permit_status=permit_field.currentText(),
                ),
                "Permit status updated.",
            )
            if self.staffing_status_label is None or self.staffing_status_label.text() == "Permit status updated.":
                dialog.close()

        save.clicked.connect(save_permit)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        dialog.show()

    def _update_staffing_permit_from_table(self, assignment_id: int, permit_status: str) -> None:
        assignment = self.staffing_store.get_assignment(assignment_id)
        if assignment.person_id is None:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText("No person assigned to update.")
            return
        self._run_staffing_action(
            lambda service: service.update_permit_status(assignment.person_id or 0, permit_status),
            "Permit status updated.",
        )

    def _mark_staffing_coming(self, assignment_id: int) -> None:
        try:
            assignment = self.staffing_store.get_assignment(assignment_id)
        except Exception as exc:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText(str(exc) or "Staffing assignment not found.")
            return
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("PySideStaffingMarkComingDialog")
        dialog.setWindowTitle("Mark Coming")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(self._label("Mark Coming", "PySideStaffingDrawerTitle"))
        layout.addWidget(self._label(f"{assignment.school} / {assignment.classroom} / {assignment.position_name}"))

        form = self.QtWidgets.QFormLayout()
        name_field = self.QtWidgets.QLineEdit()
        name_field.setObjectName("PySideStaffingComingName")
        start_field = self.QtWidgets.QLineEdit(date.today().isoformat())
        start_field.setObjectName("PySideStaffingComingStartDate")
        role_field = self.QtWidgets.QComboBox()
        role_field.addItems([assignment.position_type or "Teacher", "Teacher", "Aide", "Assistant"])
        role_field.setCurrentText(assignment.position_type or "Teacher")
        permit_field = self.QtWidgets.QComboBox()
        permit_field.addItems(STAFFING_PERMIT_VALUES)
        permit_field.setCurrentText("unknown")
        units_field = self.QtWidgets.QLineEdit("0")
        form.addRow("Teacher Name *", name_field)
        form.addRow("Start Date *", start_field)
        form.addRow("Role *", role_field)
        form.addRow("Permit Status *", permit_field)
        form.addRow("Units", units_field)
        layout.addLayout(form)

        note = self._label(
            f"This action will update the position status to Coming and create a People record for {assignment.position_name}."
        )
        note.setObjectName("PySideStaffingActionNote")
        layout.addWidget(note)
        buttons = self.QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        buttons.addWidget(cancel)
        save = self._primary_button("Save")
        save.setObjectName("PySideStaffingComingSave")

        def save_coming() -> None:
            person_name = name_field.text().strip()
            start_date = start_field.text().strip()
            permit_status = permit_field.currentText()

            def action(service: StaffingService) -> Any:
                result = service.mark_coming(assignment_id, person_name=person_name, start_date=start_date)
                if permit_status != "unknown" and result.person_id is not None:
                    service.update_permit_status(result.person_id, permit_status)
                return result

            self._run_staffing_action(action, "Incoming person saved.")
            if self.staffing_status_label is None or self.staffing_status_label.text() == "Incoming person saved.":
                dialog.close()

        save.clicked.connect(save_coming)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        dialog.show()

    def _mark_staffing_replacing(self, assignment_id: int) -> None:
        try:
            assignment = self.staffing_store.get_assignment(assignment_id)
        except Exception as exc:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText(str(exc) or "Staffing assignment not found.")
            return
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("PySideStaffingReplaceDialog")
        dialog.setWindowTitle("Replace Employee")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(self._label("Replace Employee", "PySideStaffingDrawerTitle"))
        layout.addWidget(self._label(f"{assignment.school} / {assignment.classroom} / {assignment.position_name}"))
        layout.addWidget(self._label("Current Employee"))
        layout.addWidget(self._label(assignment.person_name or "Unassigned", "PySideStaffingDrawerName"))

        form = self.QtWidgets.QFormLayout()

        class ReplaceDateEdit(self.QtWidgets.QDateEdit):
            def _open_calendar(date_self: Any) -> None:
                calendar = date_self.calendarWidget()
                calendar.setSelectedDate(date_self.date())
                popup = calendar.parentWidget()
                target = popup or calendar
                target.move(date_self.mapToGlobal(self.QtCore.QPoint(0, date_self.height())))
                target.show()
                target.raise_()

            def mousePressEvent(date_self: Any, event: Any) -> None:  # noqa: N802 - Qt override.
                super(ReplaceDateEdit, date_self).mousePressEvent(event)
                if event.button() != self.QtCore.Qt.MouseButton.LeftButton or not date_self.calendarPopup():
                    return
                self.QtCore.QTimer.singleShot(0, date_self._open_calendar)

        today = self.QtCore.QDate.currentDate()
        notice_field = ReplaceDateEdit()
        notice_field.setObjectName("PySideStaffingReplaceNotice")
        notice_field.setCalendarPopup(True)
        notice_field.setDisplayFormat("MMM d, yyyy")
        notice_field.setDate(today)
        final_day_field = ReplaceDateEdit()
        final_day_field.setObjectName("PySideStaffingReplaceFinalDay")
        final_day_field.setCalendarPopup(True)
        final_day_field.setDisplayFormat("MMM d, yyyy")
        final_day_field.setDate(today)
        reason_field = self.QtWidgets.QComboBox()
        reason_field.setObjectName("PySideStaffingReplaceReason")
        reason_field.addItems(["Resignation", "Termination", "Leave of absence", "Transfer", "Other"])
        form.addRow("Notice Given *", notice_field)
        form.addRow("Final Working Day *", final_day_field)
        form.addRow("Reason (optional)", reason_field)
        layout.addLayout(form)
        error_label = self._label("")
        error_label.setObjectName("PySideStaffingReplaceError")
        layout.addWidget(error_label)
        note = self._label(
            "Confirming this action will update the People record and move the current assignment to Replace."
        )
        note.setObjectName("PySideStaffingActionNote")
        layout.addWidget(note)
        buttons = self.QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.close)
        buttons.addWidget(cancel)
        save = self._primary_button("Confirm Replace")
        save.setObjectName("PySideStaffingReplaceSave")

        def save_replace() -> None:
            error_label.setText("")
            self._run_staffing_action(
                lambda service: service.mark_replacing(
                    assignment_id,
                    notice_given=notice_field.date().toString("yyyy-MM-dd"),
                    final_working_day=final_day_field.date().toString("yyyy-MM-dd"),
                ),
                "Replacement need opened.",
            )
            if self.staffing_status_label is None or self.staffing_status_label.text() == "Replacement need opened.":
                dialog.close()
            elif self.staffing_status_label is not None:
                error_label.setText(self.staffing_status_label.text())

        save.clicked.connect(save_replace)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        dialog.show()

    def _run_staffing_action(self, action: Any, success_message: str) -> None:
        try:
            result = action(StaffingService(self.staffing_store, notification_service=self._notification_service()))
        except Exception as exc:
            if self.staffing_status_label is not None:
                self.staffing_status_label.setText(str(exc) or "Staffing action failed.")
            return
        if self.staffing_status_label is not None:
            if getattr(result, "status", "") == "queued":
                self.staffing_status_label.setText("DB busy. Change saved to queue and will apply when unlocked.")
                self.QtCore.QTimer.singleShot(5000, self._refresh_staffing_dashboard)
            else:
                self.staffing_status_label.setText(success_message)
        self._refresh_staffing_dashboard()

    def _notification_service(self) -> Any:
        service = getattr(self, "notification_service", None)
        if service is not None:
            return service
        service = notification_service_from_email_account_settings(
            settings_path=EMAIL_ACCOUNT_SETTINGS_PATH,
            store_path=NOTIFICATION_RULES_PATH,
        )
        self.notification_service = service
        return service

    def _run_due_notifications_safely(self) -> None:
        try:
            service = self._notification_service()
            settings = getattr(service, "email_settings", None)
            if settings is not None and (not getattr(settings, "smtp_host", "") or not getattr(settings, "sender_email", "")):
                return
            runner = getattr(service, "run_due_notifications", None)
            if callable(runner):
                runner()
        except Exception:
            return

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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the PySide interview assistant.")
    parser.add_argument("--director-staffing", action="store_true", help="Open only the Staffing dashboard for directors.")
    parser.add_argument("--director-school", default="", help="Limit director Staffing dashboard to one school.")
    return parser.parse_args(list(argv) if argv is not None else None)


def launch_pyside_interview_app(
    model: InterviewRedesignModel | None = None,
    *,
    director_staffing: bool = False,
    director_school: str = "",
) -> int:
    _QtCore, _QtGui, QtWidgets = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _apply_styles(app)
    active_model = model or build_interview_redesign_model()
    if director_staffing:
        active_model = build_director_staffing_model(active_model, school=director_school)
    window = PySideInterviewWindow(active_model, defer_secondary_pages=True)
    window.show()
    return app.exec()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return launch_pyside_interview_app(
        director_staffing=bool(args.director_staffing),
        director_school=str(args.director_school or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
