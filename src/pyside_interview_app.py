from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from uuid import uuid4
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from datetime import date
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from admin_studio import AdminStudio, AdminStudioPaths
from app_branding import apply_staffing_app_icon
from candidate_report import CandidateReportRepository, build_candidate_report_snapshot
from data_store import (
    InterviewHistoryStore,
    InterviewAppSettingsStore,
    QuestionOverridesStore,
    RubricLoader,
    SchoolOfferSettingsStore,
    resolve_interview_notes_output_dir,
    resolve_offer_output_dir,
    resolve_offer_template_path,
)
from interview_runtime import (
    FinalizeGateways,
    IndeedTranscriptImportResult,
    InterviewSessionStore,
    build_finalize_progress_tasks,
    build_finalize_context,
    build_flow_time_windows,
    format_finalize_progress_tasks,
    map_indeed_transcript_to_questions,
    load_candidate_segments,
    map_segments_to_flow_indices,
    list_windows_dshow_audio_devices,
    parse_indeed_transcript_text,
    resolve_default_windows_microphone_device,
    resolve_default_windows_system_device,
    resolve_runtime,
)
from interview_audio_preflight import AudioPreflightResult, evaluate_audio_preflight, recent_wav_signal_level
from pyside_live_interview import (
    LiveInterviewCallbacks,
    LiveInterviewPage,
    LiveInterviewViewModel,
    LiveQuestionSpec,
    LiveRatingOption,
    derive_live_stages,
)
from pyside_completed_interview import (
    CompletedInterviewCallbacks,
    CompletedInterviewPage,
    CompletionState,
    build_completed_interview_view_model,
    build_completed_transcript_export,
)
from pyside_interview_components import CandidateIdentityEditor, CandidateQualificationEditor
from hiring_pipeline import (
    HiringOfferNotificationAdapter,
    HiringPipelineStore,
    HiringWorkflowService,
    calculate_offer_approval_dates,
    normalize_candidate_phone,
)
from hiring_workspace_v2 import HiringInterviewGuidePage, HiringOfferApprovalDialog, HiringWorkspaceV2Page
from dashboard_v2_ui import SEMANTIC_COLORS
from notification_service import (
    EMAIL_ACCOUNT_SETTINGS_PATH,
    NOTIFICATION_RULES_PATH,
    StaffingNotificationScheduler,
    notification_service_from_email_account_settings,
)
from notification_templates import notification_payload_from_mapping
from onboarding_operations import build_dashboard_today_summary, filtered_tasks, task_status
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
from scoring_reporting import (
    DocxExporter,
    OfferInput,
    OfferLetterService,
    ScoringEngine,
    build_approval_offer_input,
    build_school_offer_filename,
    next_available_offer_path,
    derive_offer_schedule,
)
from scoring_reporting import build_integration_payload, serialize_integration_payload
from scoring_reporting import CandidateQualification
from staffing_dashboard_host import StaffingDashboardAccess, StaffingDashboardHost
from staffing_dashboard_v2 import apply_staffing_v2_light_theme
from staffing_settings_v2 import StaffingSettingsV2Page
from staffing_referral_queue import StaffingReferralQueueStore
from staffing_change_stage import StaffingChangeStage
from staffing_service import StaffingChangeConflict, StaffingService, staffing_change_conflict_message
from staffing_store import StaffingEditLock, StaffingStore
from source_update_monitor import SourceUpdateDetector, build_source_update_banner, relaunch_application
from starting_pay_calculator import (
    POSITION_LABELS,
    calculate_offer_pay,
    load_starting_pay_settings,
    qualification_input_from_mapping,
)


APP_TITLE = "Interview Assistant"
LOGGER = logging.getLogger(__name__)
NAVIGATION = ["Staffing v2"]
DIRECTOR_STAFFING_NAVIGATION = ["Staffing v2"]
SETUP_STEPS = ["Candidate", "Interview Plan", "Ready"]
_INTERVIEW_HOME_TAB_INDEX = 0
_INTERVIEW_LIVE_TAB_INDEX = 1
_INTERVIEW_REVIEW_TAB_INDEX = 2
STAFFING_DB_PATH = DEFAULT_BASE_DIR / "staffing_dashboard.sqlite3"
STAFFING_REFERRAL_QUEUE_PATH = DEFAULT_BASE_DIR / "staffing_referrals.pending.jsonl"
STAFFING_REFERRAL_QUEUE_DB_PATH = DEFAULT_BASE_DIR / "staffing_referrals.sqlite3"
SOURCE_VERSION_PATH = Path(__file__).resolve().parents[1] / "config" / "source_version.txt"
SOURCE_UPDATE_ROOT = Path(__file__).resolve().parent
STAFFING_SEED_PATH = CONFIG_DIR / "staffing_seed.json"
QUICK_ACTIONS = [
    "Needs follow-up",
    "Candidate gave no example",
    "Evidence captured",
    "Disqualifier observed",
]
PYSIDE_CORE_FINALIZE_PROGRESS_TASKS = (
    "Stopping recording and transcribing",
    "Building interview notes",
    "Saving interview artifacts",
)
PYSIDE_INTRO_AUDIO_CHECK_DELAY_MS = 15000
LIVE_TRANSCRIPT_INTERVAL_MS = 10000
LIVE_AUDIO_INTERVAL_MS = 500


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
    candidate_email: str = ""
    candidate_phone: str = ""
    offer_path: str = ""


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
    sample_answer: str = ""


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
    stage: str = ""
    priority: str = ""
    weight: float = 0.0


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
    director_staffing_only: bool = False


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
    honorific: str = "Ms."
    interview_date: str = ""
    school: str = ""
    track_key: str = ""
    current_index: int = 0
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    qualification: dict[str, Any] = field(default_factory=dict)
    flow_time_marks: list[dict[str, Any]] = field(default_factory=list)
    flow_candidate_transcripts: dict[int, str] = field(default_factory=dict)
    flow_live_transcripts: dict[int, str] = field(default_factory=dict)
    flow_transcript_overrides: dict[int, str] = field(default_factory=dict)
    flow_recordings: dict[int, dict[str, Any]] = field(default_factory=dict)
    application_id: str = ""

    def start(self, *, candidate_name: str, school: str, track_key: str, honorific: str = "Ms.") -> None:
        if track_key not in self.model.flows:
            raise ValueError(f"Unknown track: {track_key}")
        clean_honorific = str(honorific or "").strip()
        if clean_honorific not in {"Mr.", "Ms."}:
            raise ValueError("Honorific must be Mr. or Ms.")
        self.candidate_name = candidate_name.strip()
        self.honorific = clean_honorific
        self.interview_date = date.today().isoformat()
        self.school = school.strip()
        self.track_key = track_key
        self.current_index = 0
        self.answers = {}
        self.qualification = {}
        self.flow_time_marks = []
        self.flow_candidate_transcripts = {}
        self.flow_live_transcripts = {}
        self.flow_transcript_overrides = {}
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

    def append_live_transcript(self, flow_index: int, text: str) -> None:
        index = int(flow_index)
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return
        target = self.flow_transcript_overrides if index in self.flow_transcript_overrides else self.flow_live_transcripts
        existing = str(target.get(index, "") or "").strip()
        target[index] = f"{existing} {clean}".strip()
        self.save_draft()

    def replace_live_transcript(self, flow_index: int, text: str) -> None:
        index = int(flow_index)
        self.flow_transcript_overrides[index] = " ".join(str(text or "").split()).strip()
        self.save_draft()

    def live_transcript(self, flow_index: int) -> str:
        index = int(flow_index)
        if index in self.flow_transcript_overrides:
            return str(self.flow_transcript_overrides[index] or "").strip()
        return str(self.flow_live_transcripts.get(index, "") or "").strip()

    def apply_canonical_transcripts(self, canonical: dict[int, str]) -> None:
        indices = set(canonical) | set(self.flow_live_transcripts) | set(self.flow_transcript_overrides)
        resolved: dict[int, str] = {}
        for index in indices:
            if index in self.flow_transcript_overrides:
                text = self.flow_transcript_overrides[index]
            else:
                text = str(canonical.get(index, "") or "").strip() or self.flow_live_transcripts.get(index, "")
            clean = str(text or "").strip()
            if clean:
                resolved[int(index)] = clean
        self.flow_candidate_transcripts = resolved
        self.save_draft()

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
            previous = dict(self.answers.get(item.question_id, {}) or {})
            previous_score = str(previous.get("score") or "").strip()
            previous_notes = str(previous.get("notes") or "").strip()
            preserve_manual_notes = (
                item.kind in {"custom", "qualification"}
                and bool(previous_notes)
                and not bool(previous.get("imported_transcript"))
            )
            self.answers[item.question_id] = {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": previous_notes if preserve_manual_notes else match.candidate_transcript,
                "score": previous_score,
                "quick_actions": list(previous.get("quick_actions", []) or []),
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
            previous = dict(self.answers.get(question_id, {}) or {})
            previous_score = str(previous.get("score") or "").strip()
            previous_notes = str(previous.get("notes") or "")
            answer = {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": previous_notes if previous_score else "",
                "score": previous_score,
                "quick_actions": list(previous.get("quick_actions", []) or []),
                "imported_transcript": True,
            }
            if not previous_score:
                answer.update(
                    {
                        "skipped": True,
                        "skip_reason": "not_found_in_indeed_transcript",
                    }
                )
            self.answers[question_id] = answer
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
        title: str = "",
        position: str = "",
    ) -> Path:
        first_name, last_name = _split_candidate_name(self.candidate_name)
        defaults = self.offer_review_defaults()
        output_path = next_available_offer_path(
            Path(output_dir),
            build_school_offer_filename(self.school, self.candidate_name),
        )
        data = OfferInput(
            first_name=first_name,
            last_name=last_name,
            city=self.school,
            position=str(position or defaults["position"]).strip(),
            start_date=start_date,
            start_time_12h=start_time_12h,
            end_time_12h=end_time_12h,
            hourly_pay=float(hourly_pay),
            hours=int(hours),
            created_on=created_on,
            title=title,
        )
        return OfferLetterService.render_offer(Path(template_path), output_path, data)

    def generate_interview_notes_document(
        self,
        *,
        output_dir: Path,
        history_path: Path = INTERVIEW_HISTORY_PATH,
    ) -> Path:
        result = self.finalize_interview(
            base_dir=Path(output_dir),
            history_path=Path(history_path),
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
        context = build_finalize_context(adapter, scoring, warnings, transcript_metadata)
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
        report_repository = CandidateReportRepository(Path(history_path))
        if report_repository.exists(history_id):
            report_repository.sync_report_path(history_id, Path(out_path))
        history_row = next(
            (
                row
                for row in adapter.history_store.load()
                if adapter.history_store.build_row_key(row) == history_id
            ),
            {},
        )
        track = self.model.flows.get(self.track_key)
        hiring_service = HiringWorkflowService(HiringPipelineStore(Path(history_path)))
        completion = {
            "history_id": history_id,
            "score": float(scoring.get("percent_of_max", 0.0) or 0.0),
            "outcome": str(scoring.get("outcome") or ""),
        }
        if self.application_id:
            hiring_service.finalize_initial_interview(
                self.application_id,
                **completion,
                actor="Admin User",
            )
        else:
            hiring_service.record_initial_interview(
                **completion,
                legal_name=self.candidate_name,
                email=str(history_row.get("candidate_email") or history_row.get("email") or ""),
                phone=str(history_row.get("candidate_phone") or history_row.get("phone") or ""),
                school=self.school,
                position=track.label if track is not None else self.track_key,
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
            "history_id": history_id,
        }

    def update_completed_artifacts(
        self,
        *,
        history_id: str,
        base_dir: Path = DEFAULT_BASE_DIR,
        history_path: Path = INTERVIEW_HISTORY_PATH,
    ) -> dict[str, Any]:
        key = str(history_id or "").strip()
        if not key:
            raise ValueError("Completed interview history id is required.")
        adapter = _PySideFinalizeAdapter(self, base_dir=Path(base_dir), history_path=Path(history_path))
        scoring = ScoringEngine.evaluate(
            adapter._rubric_with_question_overrides(), adapter.state.track, adapter.state.trait_inputs
        )
        context = build_finalize_context(adapter, scoring, [], self._transcript_metadata())
        existing = next(
            (row for row in adapter.history_store.load() if adapter.history_store.build_row_key(row) == key),
            None,
        )
        if existing is None:
            raise ValueError("Completed interview history row was not found.")
        existing_path = Path(
            str(existing.get("saved_report_path") or existing.get("report_path") or existing.get("interview_notes_path") or "")
        )
        output_dir = existing_path.parent if existing_path.suffix.casefold() == ".docx" else adapter._interview_notes_output_dir()
        out_path = DocxExporter(output_dir).export_basic_interview_notes(
            adapter._rubric_with_question_overrides(), context.payload, scoring
        )
        percent = float(scoring.get("percent_of_max", 0.0) or 0.0)
        percent_label = str(scoring.get("percent_of_max_label") or f"{percent}%")
        outcome = str(scoring.get("outcome", "Incomplete") or "Incomplete")
        updates = {
            "answers": self.answers,
            "review_scores": {
                question_id: str(answer.get("score") or "")
                for question_id, answer in self.answers.items()
                if isinstance(answer, dict) and str(answer.get("kind") or "") == "trait"
            },
            "flow_candidate_transcripts": {
                str(index): text for index, text in sorted(self.flow_candidate_transcripts.items())
            },
            "scoring": scoring,
            "interview_score": percent,
            "score": percent_label,
            "percent_of_max": percent,
            "percent_of_max_label": percent_label,
            "determination": outcome,
            "outcome": outcome,
            "status": outcome,
            "interview_status": outcome,
            "next_action": _next_action_for_outcome(outcome),
            "saved_report_path": str(out_path),
            "interview_notes_path": str(out_path),
            "notes_path": str(out_path),
            "report_path": str(out_path),
            "completed_overview_updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
        if not adapter.history_store.update_row(key, updates):
            raise ValueError("Completed interview history row was not found.")
        merged = {**existing, **updates}
        snapshot = build_candidate_report_snapshot(context.payload, scoring, merged, report_path=str(out_path))
        repository = CandidateReportRepository(Path(history_path))
        if repository.exists(key):
            record = repository.load_visible_version(key, role="admin")
            if record.snapshot != snapshot:
                repository.finalize(
                    key,
                    snapshot,
                    expected_row_version=record.row_version,
                    actor="Admin User",
                    role="admin",
                    reason="Completed interview overview update",
                    force=True,
                )
            repository.sync_report_path(key, Path(out_path))
        self.save_draft()
        return {"history_id": key, "out_path": str(out_path), "scoring": scoring}

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
            "application_id": self.application_id,
            "candidate_name": self.candidate_name,
            "honorific": self.honorific,
            "interview_date": self.interview_date,
            "school": self.school,
            "track_key": self.track_key,
            "current_index": self.current_index,
            "qualification": self.qualification,
            "answers": self.answers,
            "flow_time_marks": self.flow_time_marks,
            "flow_candidate_transcripts": {str(key): value for key, value in self.flow_candidate_transcripts.items()},
            "flow_live_transcripts": {str(key): value for key, value in self.flow_live_transcripts.items()},
            "flow_transcript_overrides": {str(key): value for key, value in self.flow_transcript_overrides.items()},
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
        flow_live_transcripts = payload.get("flow_live_transcripts", {})
        if not isinstance(flow_live_transcripts, dict):
            flow_live_transcripts = {}
        flow_transcript_overrides = payload.get("flow_transcript_overrides", {})
        if not isinstance(flow_transcript_overrides, dict):
            flow_transcript_overrides = {}
        flow_recordings = payload.get("flow_recordings", {})
        if not isinstance(flow_recordings, dict):
            flow_recordings = {}
        return cls(
            model=model,
            draft_path=Path(draft_path),
            application_id=str(payload.get("application_id", "")).strip(),
            candidate_name=str(payload.get("candidate_name", "")).strip(),
            honorific=str(payload.get("honorific", "Ms.") or "Ms.").strip(),
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
            flow_live_transcripts={
                int(key): str(value)
                for key, value in flow_live_transcripts.items()
                if str(key).lstrip("-").isdigit()
            },
            flow_transcript_overrides={
                int(key): str(value)
                for key, value in flow_transcript_overrides.items()
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
    if "automatic no-hire signal" in locked_rule:
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


def should_prompt_candidate_contact_handoff(score: Any) -> bool:
    """Return whether finalized initial-interview score requires contact prompt."""
    value = _coerce_history_percent(score)
    return value is not None and value > 65


def _format_offer_number(value: Any) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


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


def staffing_referral_queue_db_path(*, queue_path: Path | None = None) -> Path:
    if queue_path is None:
        return Path(STAFFING_REFERRAL_QUEUE_DB_PATH)
    legacy_path = Path(queue_path)
    return legacy_path.with_suffix("").with_suffix(".sqlite3")


def _staffing_referral_queue_store(*, queue_path: Path | None = None) -> StaffingReferralQueueStore:
    legacy_path = Path(queue_path or STAFFING_REFERRAL_QUEUE_PATH)
    return StaffingReferralQueueStore(
        staffing_referral_queue_db_path(queue_path=legacy_path),
        legacy_jsonl_path=legacy_path,
    )


def _append_staffing_referral_queue(
    payload: dict[str, Any],
    *,
    queue_path: Path | None = None,
    operation: str = "director_candidate_referral",
) -> None:
    _staffing_referral_queue_store(queue_path=queue_path).append(payload, operation=operation)


def _pop_staffing_referral_queue_for_school(school: str, *, queue_path: Path | None = None) -> list[dict[str, Any]]:
    return _staffing_referral_queue_store(queue_path=queue_path).pop_for_school(school)


def _append_staffing_referral_dismissal_queue(payload: dict[str, Any], *, queue_path: Path | None = None) -> None:
    _append_staffing_referral_queue(
        payload,
        queue_path=queue_path,
        operation="director_candidate_referral_dismissal",
    )


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


def _qualification_notification_payload(qualification: Any) -> dict[str, str]:
    payload = qualification.to_dict() if hasattr(qualification, "to_dict") else {}
    if not isinstance(payload, dict):
        payload = {}
    has_degree = bool(payload.get("has_degree"))
    degree_type = _notification_text(payload.get("degree_type"))
    degree_in_ece = _notification_text(payload.get("degree_in_ece"))
    experience = _notification_text(payload.get("years_experience"))
    return {
        "has_degree": _notification_text(payload.get("has_degree")),
        "degree_type": _notification_text(payload.get("degree_type")),
        "degree_in_ece": _notification_text(payload.get("degree_in_ece")),
        "ece_units_completed": _notification_text(payload.get("ece_units_completed")),
        "total_units_completed": _notification_text(payload.get("total_units_completed")),
        "infant_toddler_class_completed": _notification_text(payload.get("infant_toddler_class_completed")),
        "years_experience": _notification_text(payload.get("years_experience")),
        "degree_display": degree_type if has_degree and degree_type else "No",
        "degree_in_ece_display": f"\nDegree in ECE: {degree_in_ece}" if has_degree else "",
        "experience": experience,
    }


def _convert_offer_docx_to_pdf_path(offer_path: str) -> tuple[str, str]:
    path_text = str(offer_path or "").strip()
    if not path_text:
        return "", "Offer document path is blank."
    source = Path(path_text)
    if source.suffix.casefold() == ".pdf":
        if source.is_file() and source.stat().st_size > 0:
            return str(source), ""
        return "", f"PDF file was not found or is empty: {source}"
    if source.suffix.casefold() != ".docx" or not source.is_file():
        return "", f"Offer DOCX was not found: {source}"
    pdf_path = source.with_suffix(".pdf")
    if pdf_path.is_file() and pdf_path.stat().st_size > 0:
        return str(pdf_path), ""
    word = None
    document = None
    try:
        import win32com.client  # type: ignore[import-not-found]

        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(source))
        document.ExportAsFixedFormat(str(pdf_path), 17)
        for _attempt in range(20):
            if pdf_path.is_file() and pdf_path.stat().st_size > 0:
                return str(pdf_path), ""
            time.sleep(0.25)
    except Exception as exc:
        return "", f"Microsoft Word could not convert the approved DOCX to PDF: {exc}"
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
    if pdf_path.is_file():
        return "", f"Microsoft Word created an empty PDF: {pdf_path}"
    return "", f"Microsoft Word did not create the expected PDF: {pdf_path}"


def _ensure_offer_pdf_path(offer_path: str) -> str:
    pdf_path, _error = _convert_offer_docx_to_pdf_path(offer_path)
    return pdf_path


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
                candidate_email=_history_text(row, "candidate_email", "email", "candidateEmail", default=""),
                candidate_phone=_history_text(row, "candidate_phone", "phone", "candidatePhone", default=""),
                offer_path=_history_text(row, "offer_letter_path", "offer_path", default=""),
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
    sample_answers = trait.get("sample_answers", {}) or {}
    cards: list[ScoreCard] = []
    for score in ["1", "2", "3", "4", "5"]:
        description = str(descriptors.get(score, "")).strip()
        if not description:
            description = f"Score {score}"
        cards.append(
            ScoreCard(
                label=score,
                description=description,
                sample_answer=str(sample_answers.get(score, "") or "").strip(),
            )
        )
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
        stage="scored",
        priority=str(trait.get("priority", "") or "").strip(),
        weight=float(trait.get("weight", 0) or 0),
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
        director_staffing_only=True,
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


def _normalize_history_search(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return " ".join(text.split())


class PySide6UnavailableError(RuntimeError):
    pass


def _import_qt() -> Any:
    try:
        from PySide6 import QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets
    except ImportError as exc:
        raise PySide6UnavailableError(
            "PySide6 is not installed. Install requirements, then launch this redesign."
        ) from exc
    return QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets


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
        QWidget#HiringV2InterviewGuide {
            background: #f8fafc;
        }
        QScrollArea#HiringV2NewInterviewSetup,
        QScrollArea#HiringV2NewInterviewSetup > QWidget > QWidget {
            background: #f8fafc;
        }
        QLabel#HiringV2SetupSubtitle,
        QLabel#HiringV2SetupAudioNote {
            color: #64748b;
        }
        QFrame#HiringV2SetupProgress {
            background: transparent;
            border: 0;
        }
        QLabel#HiringV2SetupProgressBadge {
            color: #ffffff;
            background: #8792a2;
            border-radius: 14px;
            font-weight: 700;
        }
        QLabel#HiringV2SetupProgressBadge[activeStep="true"] {
            background: #2563eb;
        }
        QLabel#HiringV2SetupProgressLabel {
            color: #64748b;
            font-weight: 500;
        }
        QLabel#HiringV2SetupProgressLabel[activeStep="true"] {
            color: #2563eb;
            font-weight: 700;
        }
        QFrame#HiringV2SetupProgressConnector,
        QFrame#HiringV2SetupDivider {
            background: #d8dee8;
            border: 0;
        }
        QFrame#HiringV2CandidateSetupCard,
        QFrame#HiringV2CapturePreflight,
        QFrame#HiringV2StructuredResponseCard,
        QFrame#HiringV2RubricCard {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
        }
        QFrame#HiringV2CandidateSetupCard {
            border-radius: 11px;
        }
        QLabel#HiringV2SetupFieldLabel,
        QLabel#HiringV2SetupStatusName {
            color: #23324a;
        }
        QLabel#HiringV2SetupMicrophoneStatus,
        QLabel#HiringV2SetupSystemAudioStatus,
        QLabel#HiringV2SetupTranscriptStatus {
            color: #b45309;
        }
        QLabel#HiringV2SetupMicrophoneStatus[readinessState="ready"],
        QLabel#HiringV2SetupSystemAudioStatus[readinessState="ready"],
        QLabel#HiringV2SetupTranscriptStatus[readinessState="ready"] {
            color: #07913f;
        }
        QLabel#HiringV2SetupMicrophoneStatus[readinessState="failed"],
        QLabel#HiringV2SetupSystemAudioStatus[readinessState="failed"],
        QLabel#HiringV2SetupTranscriptStatus[readinessState="failed"] {
            color: #b91c1c;
        }
        QLabel#HiringV2SetupValidation {
            color: #991b1b;
            background: #fee2e2;
            border: 1px solid #fecaca;
            border-radius: 7px;
            padding: 8px;
        }
        QPushButton#HiringV2SetupCancel,
        QPushButton#HiringV2SetupBegin {
            font-size: 15px;
            font-weight: 650;
        }
        QPushButton#HiringV2SetupBegin {
            color: #ffffff;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #275eea, stop:1 #0b63f6);
            border: 1px solid #2563eb;
            border-radius: 8px;
        }
        QFrame#HiringV2RubricCard:focus-within {
            border: 2px solid #2563eb;
            background: #eff6ff;
        }
        QListWidget#HiringV2QuestionRail {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 6px;
        }
        QListWidget#HiringV2QuestionRail::item {
            padding: 8px;
            border-radius: 6px;
        }
        QListWidget#HiringV2QuestionRail::item:selected {
            background: #dbeafe;
            color: #1d4ed8;
        }
        QLabel#PySideRecordingWarning {
            color: #991b1b;
            background: #fee2e2;
            border: 1px solid #fecaca;
            border-radius: 8px;
            padding: 8px;
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
        QtCore, QtGui, QtPdf, QtPdfWidgets, QtWidgets = _import_qt()
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtPdf = QtPdf
        self.QtPdfWidgets = QtPdfWidgets
        self.QtWidgets = QtWidgets
        self.model = model
        self.director_staffing_mode = bool(model.director_staffing_only)
        self.director_staffing_school = str(getattr(model, "director_staffing_school", "") or "").strip()
        self.session_track_key = next(iter(model.flows), "")
        self.session_index = 0
        self.session_answers: dict[str, dict[str, Any]] = {}
        self.session: PySideInterviewSession | None = None
        self.history_store = InterviewHistoryStore(model.history_path)
        self.school_offer_store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
        staffing_db_path = (
            staffing_db_path_for_school(self.director_staffing_school)
            if self.director_staffing_mode
            else STAFFING_DB_PATH
        )
        _bootstrap_school_staffing_db_from_base(self.director_staffing_school, staffing_db_path)
        self.staffing_store = StaffingStore(staffing_db_path)
        self.staffing_change_stage = StaffingChangeStage(Path(STAFFING_DB_PATH).with_name("staffing_change_events"))
        self.source_update_detector: SourceUpdateDetector | None = None
        self.source_update_timer: Any | None = None
        self._staffing_referral_queue_timer: Any | None = None
        self._staffing_v2_director_referrals_sync_started = False
        self.history_search_text = ""
        self.history_school_filter_text = ""
        self.history_outcome_filter_text = ""
        self.recording_session: Any | None = None
        self.recording_base_name = ""
        self.recording_started_monotonic: float | None = None
        self.recording_candidate_label = "CANDIDATE"
        self.recording_system_device = ""
        self.recording_warning = ""
        self._pyside_intro_audio_check_queue: queue.Queue[dict[str, Any]] | None = None
        self._pyside_intro_audio_check_timer: Any | None = None
        self._pyside_finalize_running = False
        self._completed_finalize_error = ""
        self._completed_artifacts_dirty = False
        self._pyside_finalize_progress_step = ""
        self._pyside_finalize_progress_tasks: list[dict[str, str]] = []
        self.pyside_finalize_progress_dialog: Any | None = None
        self.pyside_finalize_progress_label: Any | None = None
        self.pyside_finalize_progress_bar: Any | None = None
        self._pyside_finalize_progress_queue: queue.Queue[str] | None = None
        self._pyside_finalize_progress_refresh_timer: Any | None = None
        self._review_score_dirty = False
        self._review_history_id = ""
        self.review_question_table: Any | None = None
        self.review_apply_scores_button: Any | None = None
        self._history_table_widgets: dict[str, Any] = {}
        self._overwrite_next_live_timestamp = False
        self._overwrite_next_live_boundary_timestamp = False
        self._startup_notifications_scheduled = False
        self._recording_interface_preload_started = False
        self._live_transcription_lock = threading.Lock()
        self._live_transcript_queue: queue.Queue[dict[str, Any]] | None = None
        self._live_transcript_timer: Any | None = None
        self._live_transcript_poll_timer: Any | None = None
        self._live_audio_timer: Any | None = None
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

            def closeEvent(inner_self, event: Any) -> None:
                callback = getattr(inner_self, "_close_callback", None)
                if callback is not None and not callback():
                    event.ignore()
                    return
                super().closeEvent(event)

        self.window = ResponsiveMainWindow()
        self.window._responsive_callback = self._apply_responsive_layout
        self.window._close_callback = self._request_window_close
        self.window.setWindowFlags(standard_window_control_flags(QtCore))
        self.window.setWindowTitle(model.app_title)
        self.window.resize(*self._initial_window_size())
        self._fit_window_to_available_screen()
        self.stack = QtWidgets.QStackedWidget()

        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.source_update_banner, self.source_update_restart_button = build_source_update_banner(
            QtWidgets,
            self._restart_after_source_update,
        )
        layout.addWidget(self.source_update_banner)
        layout.addWidget(self.stack, 1)
        self.window.setCentralWidget(root)
        self.stack.addWidget(self._staffing_v2_page())
        self._apply_responsive_layout()

    def _register_hiring_v2_pages(self, dashboard: Any) -> None:
        notification_adapter = HiringOfferNotificationAdapter(self._notification_service())
        service = HiringWorkflowService(
            HiringPipelineStore(self.model.history_path),
            send_offer=notification_adapter,
            notify_offer_accepted=notification_adapter.offer_accepted,
            onboarding_accept_offer=self._accept_hiring_offer_into_onboarding,
            prepare_offer_artifacts=self._generate_hiring_offer_artifacts,
        )
        service.backfill_history()
        service.retry_pending_accepted_notifications()
        self._sync_hiring_v2_director_decisions(service)
        guide_widget = self._hiring_interview_guide_widget()

        def new_interview() -> None:
            dashboard.show_external_page("interviews")
            dashboard.set_navigation_locked(False)
            dashboard.set_navigation_mode("full")
            self.hiring_v2_router.show_interview()
            self.interview_tabs.setCurrentIndex(_INTERVIEW_HOME_TAB_INDEX)
            if self.session is None:
                self._reset_new_interview_setup()

        def resume_interview(application: Any) -> None:
            new_interview()
            self._resume_hiring_application(application.application_id)

        def review_approval(application: Any) -> None:
            self._review_hiring_offer_approval(service, application)

        def director_review(_application: Any) -> None:
            dashboard.dashboard_nav_button.click()

        def view_closeout(application: Any) -> None:
            row = next(
                (item for item in self.model.home.history_rows if item.row_key == application.history_id),
                None,
            )
            if row is None:
                self.QtWidgets.QMessageBox.warning(self.window, "Closeout", "Interview history record not found.")
                return
            new_interview()
            self.session = self._session_from_history_row(row)
            self._review_history_id = row.row_key
            self._render_review_page()
            self.interview_tabs.setCurrentIndex(_INTERVIEW_REVIEW_TAB_INDEX)
            self._show_hiring_closeout()

        def history_row(application: Any) -> PySideHistoryRow | None:
            return next(
                (item for item in self.model.home.history_rows if item.row_key == application.history_id),
                None,
            )

        def open_notes(application: Any) -> None:
            row = history_row(application)
            if row is not None:
                self._open_history_notes(row)

        def regenerate_notes(application: Any) -> None:
            row = history_row(application)
            if row is not None:
                self._regenerate_history_notes(row)

        def import_transcript(application: Any) -> None:
            row = history_row(application)
            if row is not None:
                self._import_indeed_transcript_for_history_row(row)

        self.hiring_v2_page = HiringWorkspaceV2Page(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            service=service,
            school_options=self.model.school_options,
            actions={
                "new_interview": new_interview,
                "resume_interview": resume_interview,
                "review_approval": review_approval,
                "approve_revision": review_approval,
                "send_offer": lambda application, version: self._send_hiring_offer_with_email(
                    service, application, version
                ),
                "director_review": director_review,
                "view_closeout": view_closeout,
                "view_acceptance": lambda _application: None,
                "open_notes": open_notes,
                "regenerate_notes": regenerate_notes,
                "import_transcript": import_transcript,
            },
        )
        candidates_workspace = self._hiring_candidates_workspace()
        self.hiring_v2_router = HiringInterviewGuidePage(
            QtWidgets=self.QtWidgets,
            pipeline_widget=None,
            interview_widget=guide_widget,
            initial_route="interview",
        )
        dashboard.register_external_section("hiring", "HIRING")
        interviews_nav = dashboard.register_external_page(
            "hiring", "interviews", "Interviews", self.hiring_v2_router.widget, icon_key="people"
        )
        interviews_nav.clicked.connect(lambda _checked=False: new_interview())
        dashboard.register_external_page(
            "hiring", "candidates", "Candidates", candidates_workspace, icon_key="people"
        )
        dashboard.register_external_page(
            "hiring", "offers", "Offers", self.hiring_v2_page.offers_widget, icon_key="history"
        )

    def _hiring_candidates_workspace(self) -> Any:
        workspace = self.QtWidgets.QWidget()
        workspace.setObjectName("HiringV2CandidatesWorkspace")
        layout = self.QtWidgets.QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        tabs = self.QtWidgets.QTabWidget()
        tabs.setObjectName("HiringV2CandidatesTabs")
        tabs.addTab(self.hiring_v2_page.candidates_widget, "Candidate Roster")

        pipeline_tab = self.QtWidgets.QWidget()
        pipeline_tab.setObjectName("HiringV2CandidatesPipelineHistory")
        pipeline_layout = self.QtWidgets.QVBoxLayout(pipeline_tab)
        pipeline_layout.setContentsMargins(0, 0, 0, 0)
        pipeline_layout.setSpacing(10)
        tools = self.QtWidgets.QFrame()
        tools.setObjectName("HiringV2CandidateHistoryTools")
        tools_layout = self.QtWidgets.QHBoxLayout(tools)
        tools_layout.setContentsMargins(12, 10, 12, 10)
        self.candidate_draft_label = self._label("No saved draft available.", "HiringV2CandidateDraftStatus")
        tools_layout.addWidget(self.candidate_draft_label, 1)
        import_button = self.QtWidgets.QPushButton("Import Indeed Transcript")
        import_button.setObjectName("ImportIndeedTranscriptButton")
        import_button.clicked.connect(self._import_indeed_transcript_from_home)
        tools_layout.addWidget(import_button)
        continue_button = self.QtWidgets.QPushButton("Continue Saved Draft")
        continue_button.setObjectName("HiringV2CandidateContinueDraft")
        continue_button.clicked.connect(self._continue_latest_draft)
        tools_layout.addWidget(continue_button)
        delete_button = self.QtWidgets.QPushButton("Delete Saved Draft")
        delete_button.setObjectName("HiringV2CandidateDeleteDraft")
        delete_button.clicked.connect(self._delete_latest_draft)
        tools_layout.addWidget(delete_button)
        pipeline_layout.addWidget(tools)
        pipeline_layout.addWidget(self.hiring_v2_page.widget, 1)
        tabs.addTab(pipeline_tab, "Pipeline / History")
        layout.addWidget(tabs, 1)
        self.hiring_v2_candidates_tabs = tabs
        self.candidate_continue_draft_button = continue_button
        self.candidate_delete_draft_button = delete_button
        self.home_continue_button = continue_button
        self.home_delete_draft_button = delete_button
        self._refresh_home_draft_panel()
        return workspace

    def _generate_hiring_offer_artifacts(
        self,
        application: Any,
        candidate: Any,
        version: Any,
    ) -> tuple[Path, Path]:
        terms = version.terms
        template_path = Path(str(terms.get("template_path") or "")).expanduser().resolve()
        output_dir = Path(str(terms.get("output_dir") or "")).expanduser().resolve()
        if not template_path.is_file():
            raise ValueError("Validated offer template is required before submission.")
        output_dir.mkdir(parents=True, exist_ok=True)
        first_name, last_name = _split_candidate_name(candidate.legal_name)
        generated_on = date.today()
        output_path = next_available_offer_path(
            output_dir,
            build_school_offer_filename(application.school, candidate.legal_name),
        )
        data = build_approval_offer_input(
            first_name=first_name,
            last_name=last_name,
            city=application.school,
            position=application.position,
            approval_date=generated_on,
            terms=terms,
        )
        OfferLetterService.render_approved_offer(
            template_path,
            output_path,
            data,
            approval_date=generated_on,
        )
        pdf_text, pdf_error = _convert_offer_docx_to_pdf_path(str(output_path))
        if not pdf_text:
            detail = pdf_error or "Confirm Microsoft Word is installed and can export PDFs."
            raise ValueError(
                f"Approved PDF could not be rendered and validated. "
                f"DOCX saved for records: {output_path}. {detail}"
            )
        return output_path.resolve(), Path(pdf_text).resolve()

    def _review_hiring_offer_approval(
        self,
        service: HiringWorkflowService,
        application: Any,
    ) -> None:
        if getattr(self, "hiring_v2_page", None) is not None:
            self.hiring_v2_page._set_action_state("working", "Preparing approval review...")
        pending = [
            version
            for version in service.store.list_offer_versions(application.application_id)
            if version.status == "pending_approval"
        ]
        if not pending:
            if getattr(self, "hiring_v2_page", None) is not None:
                self.hiring_v2_page._set_action_state("error", "No pending offer version found.")
            self.QtWidgets.QMessageBox.warning(self.window, "Executive approval", "No pending offer version found.")
            return
        version = max(pending, key=lambda item: item.version_number)
        candidate = service.store.get_candidate(application.candidate_id)
        terms = version.terms
        try:
            approval_date = date.today()
            docx_path = Path(str(getattr(version, "docx_path", "") or "")).expanduser().resolve()
            pdf_path = Path(str(getattr(version, "pdf_path", "") or "")).expanduser().resolve()
            if (
                docx_path.suffix.casefold() != ".docx"
                or not docx_path.is_file()
                or docx_path.stat().st_size <= 0
                or pdf_path.suffix.casefold() != ".pdf"
                or not pdf_path.is_file()
                or pdf_path.stat().st_size <= 0
            ):
                docx_path, pdf_path = self._generate_hiring_offer_artifacts(
                    application, candidate, version
                )
                version = service.store.record_offer_artifacts(
                    version.version_id,
                    docx_path=docx_path,
                    pdf_path=pdf_path,
                )
            approval_dates = calculate_offer_approval_dates(approval_date)
            payload = HiringOfferNotificationAdapter.payload(candidate, version, pdf_path)
            payload.update(
                {
                    "offer_date": approval_dates.offer_date.isoformat(),
                    "reply_by_date": approval_dates.reply_by_date.isoformat(),
                    "start_date": approval_dates.start_date.isoformat(),
                }
            )
            rendered_email = self._notification_service().render_candidate_event_preview(
                "offer.approved", payload
            )
        except (OSError, TypeError, ValueError) as exc:
            if getattr(self, "hiring_v2_page", None) is not None:
                self.hiring_v2_page._set_action_state("error", f"Approval review failed: {exc}")
            self.QtWidgets.QMessageBox.warning(self.window, "Executive approval", str(exc))
            return
        interview_score = "—"
        for event in reversed(service.store.list_events(application.application_id)):
            if event.event_type != "initial_interview_completed":
                continue
            score = float(event.payload.get("score", 0))
            interview_score = f"{score:g}%"
            break
        qualification = terms.get("qualification_snapshot", {})
        if not isinstance(qualification, dict):
            qualification = {}
        has_degree = bool(qualification.get("has_degree"))
        degree = str(qualification.get("degree_type") or ("Yes" if has_degree else "No"))
        review_details = {
            "Name": candidate.legal_name or "—",
            "Initial Interview Score": interview_score,
            "Director Rating": str(terms.get("director_rating") or "—"),
            "Degree": degree,
            "Years of Experience": str(qualification.get("years_experience", "—")),
            "Requested Pay": str(terms.get("requested_pay_raw") or "—"),
            "Offer Amount": f"${float(terms.get('hourly_pay') or 0):.2f} per hour",
            "Classroom": str(terms.get("proposed_classroom") or "—"),
            "Hours": f"{terms.get('weekly_hours') or terms.get('hours_week') or '—'} weekly",
        }
        approval_dialog = HiringOfferApprovalDialog(
            QtCore=self.QtCore,
            QtPdf=self.QtPdf,
            QtPdfWidgets=self.QtPdfWidgets,
            QtWidgets=self.QtWidgets,
            parent=self.window,
            title=f"Approve offer v{version.version_number}",
            summary="",
            review_details=review_details,
            rendered_email=rendered_email,
            pdf_path=pdf_path,
            hourly_pay=str(terms.get("hourly_pay") or ""),
            approve_label=(
                "Approve and send"
                if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate.email)
                else "Approve"
            ),
        )
        if not approval_dialog.exec():
            approval_dialog.close()
            if getattr(self, "hiring_v2_page", None) is not None:
                self.hiring_v2_page._set_action_state("ready", "Approval review cancelled.")
            return
        approver_name = approval_dialog.approver_name()
        changed_pay = approval_dialog.hourly_pay()
        change_pay_requested = approval_dialog.change_pay_requested()
        approval_dialog.close()
        try:
            if change_pay_requested:
                version = service.revise_pending_offer_pay(
                    application.application_id,
                    version.version_id,
                    hourly_pay=changed_pay,
                    actor=approver_name,
                )
                terms = version.terms
                docx_path = Path(version.docx_path).expanduser().resolve()
                pdf_path = Path(version.pdf_path).expanduser().resolve()
                payload = HiringOfferNotificationAdapter.payload(candidate, version, pdf_path)
                payload.update(
                    {
                        "offer_date": approval_dates.offer_date.isoformat(),
                        "reply_by_date": approval_dates.reply_by_date.isoformat(),
                        "start_date": approval_dates.start_date.isoformat(),
                    }
                )
                rendered_email = self._notification_service().render_candidate_event_preview(
                    "offer.approved", payload
                )
            if application.stage.value == "offer_sent":
                service.approve_compensation_revision(
                    application.application_id,
                    version.version_id,
                    admin_name=approver_name,
                    approval_date=approval_date,
                    docx_path=docx_path,
                    pdf_path=pdf_path,
                    rendered_email=rendered_email,
                )
            else:
                service.approve_offer(
                    application.application_id,
                    version.version_id,
                    approver_name=approver_name,
                    approver_role="Executive Director",
                    approval_date=approval_date,
                    docx_path=docx_path,
                    pdf_path=pdf_path,
                    rendered_email=rendered_email,
                )
            self.hiring_v2_page.refresh()
            if getattr(self, "hiring_v2_page", None) is not None:
                message = (
                    "Offer approved and send attempted."
                    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate.email)
                    else "Offer approved. Candidate email required before sending."
                )
                self.hiring_v2_page._set_action_state("success", message)
        except ValueError as exc:
            if getattr(self, "hiring_v2_page", None) is not None:
                self.hiring_v2_page._set_action_state("error", f"Approval failed: {exc}")
            self.QtWidgets.QMessageBox.warning(self.window, "Executive approval", str(exc))

    def _send_hiring_offer_with_email(
        self,
        service: HiringWorkflowService,
        application: Any,
        version: Any,
    ) -> None:
        email, accepted = self.QtWidgets.QInputDialog.getText(
            self.window,
            "Send offer",
            "Candidate email:",
        )
        if not accepted:
            return
        try:
            delivered = service.send_approved_offer(
                application.application_id,
                version.version_id,
                candidate_email=email,
                actor="Admin User",
            )
        except ValueError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Send offer", str(exc))
            return
        self.hiring_v2_page.refresh()
        if delivered.status == "sent":
            self.hiring_v2_page._set_action_state("success", "Offer sent.")
            return
        self.hiring_v2_page._set_action_state(
            "error", "Offer remains approved, but delivery failed."
        )

    def _resume_hiring_application(self, application_id: str) -> None:
        clean_id = str(application_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("Valid application ID is required.")
        draft_path = self._drafts_dir() / f"{clean_id}.json"
        self.session = PySideInterviewSession.load(model=self.model, draft_path=draft_path)
        if self.session.application_id != clean_id:
            raise ValueError("Interview draft does not match the selected application.")
        self.session_track_key = self.session.track_key
        self.session_index = self.session.current_index
        self.session_answers = dict(self.session.answers)
        self._render_live_question_page()
        self._render_review_page()
        self._start_pyside_interview_recording()
        self.interview_tabs.setCurrentIndex(
            _INTERVIEW_LIVE_TAB_INDEX if self.session.active_question() is not None else _INTERVIEW_REVIEW_TAB_INDEX
        )
        self._set_hiring_focus_mode(self.session.active_question() is not None)

    def _sync_hiring_v2_director_decisions(self, service: HiringWorkflowService) -> None:
        shared_path = Path(STAFFING_DB_PATH)
        for application in service.store.list_applications():
            if application.stage.value not in {"director_review", "offer_draft"}:
                continue
            school_path = staffing_db_path_for_school(application.school, base_path=shared_path)
            for path in dict.fromkeys((school_path, shared_path)):
                if not path.exists():
                    continue
                staffing_store = StaffingStore(path)
                staffing_store.initialize()
                staffing_service = StaffingService(staffing_store)
                interview = staffing_service.find_any_completed_director_interview(
                    history_id=application.history_id,
                    school=application.school,
                )
                if interview is None:
                    continue
                actor = interview.director_name or "Staffing v2"
                if application.stage.value == "director_review":
                    application = service.synchronize_director_outcome(
                        application.history_id,
                        decision=interview.decision,
                        actor=actor,
                        actor_id=f"staffing-director-interview:{interview.id}",
                    )
                if interview.decision == "hire":
                    try:
                        referral = staffing_store.list_director_candidate_referrals(
                            school=application.school,
                            include_completed=True,
                        )
                        contact = next((item for item in referral if item.id == interview.referral_id), None)
                        candidate = service.store.get_candidate(application.candidate_id)
                        if contact is not None and (contact.candidate_email or contact.candidate_phone):
                            candidate = service.update_candidate_profile(
                                candidate.candidate_id,
                                legal_name=candidate.legal_name,
                                preferred_name=candidate.preferred_name,
                                email=contact.candidate_email or candidate.email,
                                phone=contact.candidate_phone or candidate.phone,
                            )
                        terms = self._director_offer_terms(application, candidate, interview)
                        offer = service.ensure_director_offer_submitted(
                            application.application_id,
                            source_key=f"director-interview:{interview.id}:v{interview.version_number}",
                            terms=terms,
                            actor=actor,
                        )
                        notification_factory = getattr(self, "_notification_service", None)
                        if callable(notification_factory):
                            history_store = InterviewHistoryStore(self.model.history_path)
                            history_row = next(
                                (
                                    row
                                    for row in history_store.load()
                                    if history_store.build_row_key(row) == application.history_id
                                ),
                                {},
                            )
                            notification_source = dict(history_row)
                            report_repository = CandidateReportRepository(self.model.history_path)
                            if report_repository.exists(application.history_id):
                                report = report_repository.load_visible_version(
                                    application.history_id,
                                    role="admin",
                                )
                                notification_source.update(report.snapshot)
                            interview_payload = notification_payload_from_mapping(notification_source)
                            payload = {
                                "candidate_name": candidate.legal_name,
                                "candidate_email": candidate.email,
                                "school": application.school,
                                "position": application.position,
                                "director_name": actor,
                                "interview_score": interview_payload.get("interview_score", ""),
                                "director_interview_score": f"{interview.rating:g}",
                                "degree_display": interview_payload.get("degree_display", "No"),
                                "degree_in_ece_display": interview_payload.get("degree_in_ece_display", ""),
                                "experience": interview_payload.get("experience", ""),
                                "requested_pay": terms["requested_pay_raw"],
                                "offer_amount": terms["hourly_pay"],
                                "proposed_classroom": terms["proposed_classroom"],
                                "shift_start": terms["start_time"],
                                "shift_end": terms["end_time"],
                            }
                            try:
                                notification_factory().emit_event(
                                    "director.interview.hire",
                                    payload,
                                    f"offer-version:{offer.version_id}:submitted",
                                )
                            except Exception:
                                pass
                    except (OSError, TypeError, ValueError):
                        service.store.update_application_stage(
                            application.application_id,
                            application.stage,
                            attention_code="offer_draft_pending",
                        )
                break

    def _accept_hiring_offer_into_onboarding(
        self,
        application: Any,
        candidate: Any,
        version: Any,
        director_id: str,
        director_name: str,
    ) -> Any:
        host = getattr(self, "staffing_v2_host", None)
        workspace = getattr(host, "onboarding_workspace", None)
        if workspace is None:
            raise ValueError("Onboarding workspace is unavailable for accepted-offer handoff.")
        start_date = str(version.start_date or version.terms.get("start_date") or "").strip()
        if not start_date:
            raise ValueError("Accepted offer start date is required for onboarding handoff.")
        employee = workspace.service.accept_offer(
            application_id=application.application_id,
            legal_name=candidate.legal_name,
            school=application.school,
            role=application.position,
            acceptance_date=date.today().isoformat(),
            start_date=start_date,
            email=candidate.email,
            phone=candidate.phone,
            hiring_director_id=director_id,
            hiring_director_name=director_name,
        )
        workspace.refresh_after_handoff()
        return employee

    def _director_offer_terms(self, application: Any, candidate: Any, interview: Any) -> dict[str, Any]:
        schedule = derive_offer_schedule(interview.proposed_shift_start, interview.proposed_shift_end)
        settings = self.school_offer_store.load()
        template_path = resolve_offer_template_path(
            DEFAULT_BASE_DIR,
            application.school,
            int(schedule.weekly_hours),
            settings,
        )
        output_dir = resolve_offer_output_dir(DEFAULT_BASE_DIR, application.school, settings)
        requested_pay = self._requested_pay_answer(application.history_id)
        qualification = self._offer_qualification(application.history_id)
        pay_input = qualification_input_from_mapping(interview.offer_position_id, qualification)
        pay_result = calculate_offer_pay(pay_input, load_starting_pay_settings())
        if pay_result.status != "calculated" or pay_result.starting_hourly_pay is None:
            raise ValueError(pay_result.qualification_explanation)
        return {
            "honorific": candidate.honorific,
            "candidate_name": candidate.legal_name,
            "candidate_email": candidate.email,
            "position_id": interview.offer_position_id,
            "position": POSITION_LABELS[interview.offer_position_id],
            "school": application.school,
            "start_time": interview.proposed_shift_start,
            "end_time": interview.proposed_shift_end,
            "gross_daily_hours": _format_offer_number(schedule.gross_daily_hours),
            "net_daily_hours": _format_offer_number(schedule.net_daily_hours),
            "weekly_hours": _format_offer_number(schedule.weekly_hours),
            "employment_type": schedule.employment_type,
            "proposed_classroom": interview.proposed_classroom,
            "requested_pay_raw": requested_pay,
            "director_rating": f"{interview.rating:g}",
            "qualification_snapshot": dict(qualification),
            "hourly_pay": format(pay_result.starting_hourly_pay, ".2f"),
            "compensation_review_required": False,
            "pay_calculation": pay_result.to_dict(),
            "pto": _format_offer_number(schedule.weekly_hours * 2),
            "pto2": _format_offer_number(schedule.weekly_hours * 4),
            "template_path": str(template_path),
            "output_dir": str(output_dir),
        }

    def _offer_qualification(self, history_id: str) -> dict[str, Any]:
        store = InterviewHistoryStore(self.model.history_path)
        row = next((item for item in store.load() if store.build_row_key(item) == history_id), {})
        qualification = row.get("qualification")
        report_repository = CandidateReportRepository(self.model.history_path)
        if report_repository.exists(history_id):
            report = report_repository.load_visible_version(history_id, role="admin")
            candidate = report.snapshot.get("candidate")
            if isinstance(candidate, dict):
                qualification = candidate.get("qualification", qualification)
        if not isinstance(qualification, dict):
            raise ValueError("Candidate qualification details are required before generating an offer.")
        return qualification

    def _requested_pay_answer(self, history_id: str) -> str:
        store = InterviewHistoryStore(self.model.history_path)
        row = next((item for item in store.load() if store.build_row_key(item) == history_id), {})
        report_repository = CandidateReportRepository(self.model.history_path)
        if report_repository.exists(history_id):
            report = report_repository.load_visible_version(history_id, role="admin")
            questions = report.snapshot.get("questions", [])
            for question in questions if isinstance(questions, list) else []:
                if not isinstance(question, dict):
                    continue
                question_id = str(question.get("question_id") or question.get("id") or "").strip()
                if question_id.casefold() != "pay":
                    continue
                return str(question.get("interviewer_notes") or "").strip()
        answers = row.get("answers", {}) if isinstance(row, dict) else {}
        if isinstance(answers, dict):
            pay = answers.get("Pay", {})
            if isinstance(pay, dict):
                value = str(pay.get("notes") or pay.get("answer") or "").strip()
                if value:
                    return value
        custom_answers = row.get("custom_answers", []) if isinstance(row, dict) else []
        for answer in custom_answers if isinstance(custom_answers, list) else []:
            if isinstance(answer, dict) and str(answer.get("id") or "").casefold() == "pay":
                return str(answer.get("answer") or answer.get("notes") or "").strip()
        return ""

    def show(self) -> None:
        self._fit_window_to_available_screen(fill_available=True)
        self.window.showMaximized()
        self._start_source_update_monitoring()
        if getattr(self, "director_staffing_mode", False):
            return
        self._schedule_startup_notifications()
        self._schedule_recording_interface_preload()

    def _start_source_update_monitoring(self) -> None:
        if not hasattr(self, "QtCore") or getattr(self, "source_update_timer", None) is not None:
            return
        self.source_update_detector = SourceUpdateDetector(
            SOURCE_VERSION_PATH,
            source_root=SOURCE_UPDATE_ROOT,
        )
        timer = self.QtCore.QTimer(self.window)
        timer.setInterval(5000)
        timer.timeout.connect(self._poll_source_updates)
        timer.start()
        self.source_update_timer = timer

    def _poll_source_updates(self) -> None:
        detector = self.source_update_detector
        if detector is not None and detector.poll():
            self.source_update_banner.show()

    def _restart_after_source_update(self) -> None:
        if not self._request_window_close():
            return
        self.window._close_callback = None
        started = relaunch_application(
            self.QtCore,
            self.window.close,
            cwd=SOURCE_VERSION_PATH.parent.parent,
        )
        if started:
            return
        self.window._close_callback = self._request_window_close
        staffing_host = getattr(self, "staffing_v2_host", None)
        resume_onboarding = getattr(staffing_host, "resume_onboarding", None)
        if callable(resume_onboarding):
            resume_onboarding()
        self.QtWidgets.QMessageBox.warning(
            self.window,
            "Restart Failed",
            "Could not start the updated app. Please close and reopen it manually.",
        )

    def _schedule_startup_notifications(self) -> None:
        if self._startup_notifications_scheduled:
            return
        self._startup_notifications_scheduled = True
        self.QtCore.QTimer.singleShot(0, self._run_due_notifications_safely)
        timer = self.QtCore.QTimer(self.window)
        timer.setInterval(5 * 60 * 1000)
        timer.timeout.connect(self._run_due_notifications_safely)
        timer.start()
        self._notification_scheduler_timer = timer

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
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        self._recording_preload_queue = results

        def worker() -> None:
            try:
                results.put({"ok": True, "state": self._probe_recording_interface()})
            except Exception as exc:
                self.recording_warning = f"Recording preload unavailable: {exc}"
                results.put({"ok": False})

        threading.Thread(target=worker, daemon=True).start()
        timer = self.QtCore.QTimer(self.window)
        self._recording_preload_timer = timer
        timer.timeout.connect(lambda: self._poll_recording_interface_preload(results, timer))
        timer.start(50)

    def _probe_recording_interface(self) -> dict[str, Any]:
        resolve_runtime(self._recording_runtime_settings())
        if not sys.platform.startswith("win"):
            return {
                "available_devices": [],
                "microphone_device": "",
                "system_device": "",
                "transcription_ready": True,
            }
        available_devices = list_windows_dshow_audio_devices()
        return {
            "available_devices": available_devices,
            "microphone_device": resolve_default_windows_microphone_device(),
            "system_device": resolve_default_windows_system_device(),
            "transcription_ready": True,
        }

    def _poll_recording_interface_preload(
        self,
        results: queue.Queue[dict[str, Any]],
        timer: Any,
    ) -> None:
        try:
            message = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        timer.deleteLater()
        if getattr(self, "_recording_preload_queue", None) is results:
            self._recording_preload_queue = None
            self._recording_preload_timer = None
        if message.get("ok") and isinstance(message.get("state"), dict):
            self._apply_setup_audio_probe(**message["state"])
            return
        self._apply_setup_audio_probe(
            available_devices=[],
            microphone_device="",
            system_device="",
            transcription_ready=False,
        )

    def _apply_setup_audio_probe(
        self,
        *,
        available_devices: Sequence[str],
        microphone_device: str,
        system_device: str,
        transcription_ready: bool,
    ) -> None:
        devices = list(dict.fromkeys(str(item or "").strip() for item in available_devices if str(item or "").strip()))
        folded = {item.casefold(): item for item in devices}
        resolved_system = folded.get(str(system_device or "").strip().casefold(), "")
        resolved_microphone = folded.get(str(microphone_device or "").strip().casefold(), "")
        combo = getattr(self, "home_audio_source_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.clear()
            ordered = ([resolved_system] if resolved_system else []) + [
                item for item in devices if item != resolved_system
            ]
            if ordered:
                for item in ordered:
                    combo.addItem(item, item)
                combo.setCurrentIndex(0)
            else:
                combo.addItem("No system audio device detected", "")
            combo.blockSignals(False)
        self._set_setup_probe_status(
            getattr(self, "home_microphone_status", None),
            ready=bool(resolved_microphone),
            ready_text="Microphone connected",
            failed_text="Microphone not detected",
        )
        self._set_setup_probe_status(
            getattr(self, "home_system_audio_status", None),
            ready=bool(resolved_system),
            ready_text="System audio connected",
            failed_text="System audio not detected",
        )
        self._set_setup_probe_status(
            getattr(self, "home_transcript_status", None),
            ready=bool(transcription_ready),
            ready_text="Live transcription ready",
            failed_text="Live transcription unavailable",
        )
        button = getattr(self, "home_test_audio_button", None)
        if button is not None and getattr(self, "_manual_audio_preflight_queue", None) is None:
            button.setEnabled(bool(resolved_system))

    def _set_setup_probe_status(
        self,
        widget: Any,
        *,
        ready: bool,
        ready_text: str,
        failed_text: str,
    ) -> None:
        if widget is None:
            return
        widget.setText(ready_text if ready else failed_text)
        widget.setProperty("readinessState", "ready" if ready else "failed")
        self._refresh_widget_style(widget)

    def _recording_warning_text(self) -> str:
        warning = str(self.recording_warning or "").strip()
        if not warning:
            return ""
        device = str(self.recording_system_device or "").strip()
        if device:
            return f"{warning} System audio capture device: {device}. Check Windows/meeting output before continuing."
        return f"{warning} Check Windows/meeting output before continuing."

    def _schedule_pyside_intro_audio_transcription_check(self) -> None:
        if self.recording_session is None:
            return
        self.QtCore.QTimer.singleShot(
            PYSIDE_INTRO_AUDIO_CHECK_DELAY_MS,
            self._run_pyside_intro_audio_transcription_check_async,
        )

    def _run_pyside_intro_audio_transcription_check_async(self) -> None:
        session = self.recording_session
        if session is None:
            return
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pyside_intro_audio_check_queue = results

        def _worker() -> None:
            try:
                result = self._evaluate_pyside_intro_audio_preflight(session)
                results.put({"ok": True, "result": result})
            except Exception as exc:  # noqa: BLE001
                results.put({"ok": False, "error": exc})

        threading.Thread(target=_worker, daemon=True).start()
        timer = self.QtCore.QTimer(self.window)
        self._pyside_intro_audio_check_timer = timer
        timer.timeout.connect(lambda: self._poll_pyside_intro_audio_check(results, timer))
        timer.start(100)

    def _transcribe_pyside_intro_audio_sample(self, session: Any) -> str:
        transcribe = getattr(session, "transcribe_new_segments", None)
        if not callable(transcribe):
            return ""
        with self._live_transcription_lock:
            segments = transcribe(language="en")
        chunks: list[str] = []
        for segment in segments or []:
            speaker = str(getattr(segment, "speaker", "") or "").strip()
            if speaker and speaker != self.recording_candidate_label:
                continue
            text = str(getattr(segment, "text", "") or "").strip()
            if text:
                chunks.append(text)
        return " ".join(chunks).strip()

    def _poll_pyside_intro_audio_check(self, results: queue.Queue[dict[str, Any]], timer: Any) -> None:
        try:
            message = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        timer.deleteLater()
        if self._pyside_intro_audio_check_queue is results:
            self._pyside_intro_audio_check_queue = None
            self._pyside_intro_audio_check_timer = None
        if message.get("ok"):
            result = message.get("result")
            if isinstance(result, AudioPreflightResult):
                self._apply_pyside_intro_audio_preflight_result(result)
            return
        self.recording_warning = (
            "Audio transcription check failed. Check audio settings. "
            "Record the interview in Zoom as a backup so transcripts can be generated outside this app."
        )
        LOGGER.error("pyside_intro_audio_check_failed")
        if getattr(self, "live_page", None) is not None:
            self.live_page.update_warning(self._recording_warning_text())

    def _evaluate_pyside_intro_audio_preflight(self, session: Any) -> AudioPreflightResult:
        with self._live_transcription_lock:
            segments = session.transcribe_new_segments(language="en")
        return evaluate_audio_preflight(
            microphone_wav=Path(str(getattr(session, "mic_wav", "") or "")),
            system_audio_wav=Path(str(getattr(session, "sys_wav", "") or "")),
            transcript_segments=segments or [],
            candidate_label=str(getattr(session, "sys_label", "") or self.recording_candidate_label),
        )

    def _start_live_capture_monitor(self) -> None:
        self._stop_live_capture_monitor()
        if self.recording_session is None or self.session is None:
            return
        transcript_timer = self.QtCore.QTimer(self.window)
        transcript_timer.timeout.connect(self._run_live_transcript_async)
        transcript_timer.start(max(1, int(LIVE_TRANSCRIPT_INTERVAL_MS)))
        self._live_transcript_timer = transcript_timer
        audio_timer = self.QtCore.QTimer(self.window)
        audio_timer.timeout.connect(self._update_live_candidate_audio)
        audio_timer.start(max(50, int(LIVE_AUDIO_INTERVAL_MS)))
        self._live_audio_timer = audio_timer

    def _stop_live_capture_monitor(self) -> None:
        for name in ("_live_transcript_timer", "_live_transcript_poll_timer", "_live_audio_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            setattr(self, name, None)
        self._live_transcript_queue = None

    def _stop_manual_audio_preflight(self) -> None:
        cancel_event = getattr(self, "_manual_audio_preflight_cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        self._manual_audio_preflight_cancel_event = None
        timer = getattr(self, "_manual_audio_preflight_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._manual_audio_preflight_timer = None
        self._manual_audio_preflight_queue = None
        session = getattr(self, "_manual_audio_preflight_session", None)
        self._manual_audio_preflight_session = None
        if session is not None:
            threading.Thread(target=session.stop, daemon=True).start()

    def _run_live_transcript_async(self) -> None:
        recorder = self.recording_session
        interview_session = self.session
        if recorder is None or interview_session is None or self._live_transcript_queue is not None:
            return
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        self._live_transcript_queue = results

        def _worker() -> None:
            try:
                with self._live_transcription_lock:
                    segments = recorder.transcribe_new_segments(language="en")
                results.put({"ok": True, "segments": list(segments or []), "recorder": recorder, "session": interview_session})
            except Exception as exc:  # noqa: BLE001
                results.put({"ok": False, "error": exc, "recorder": recorder, "session": interview_session})

        threading.Thread(target=_worker, daemon=True).start()
        poll = self.QtCore.QTimer(self.window)
        poll.timeout.connect(lambda: self._poll_live_transcript(results, poll))
        poll.start(50)
        self._live_transcript_poll_timer = poll

    def _poll_live_transcript(self, results: queue.Queue[dict[str, Any]], timer: Any) -> None:
        try:
            message = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        timer.deleteLater()
        if self._live_transcript_queue is results:
            self._live_transcript_queue = None
            self._live_transcript_poll_timer = None
        if message.get("recorder") is not self.recording_session or message.get("session") is not self.session:
            return
        if not message.get("ok"):
            self.recording_warning = (
                "Live transcription temporarily unavailable. "
                "Recording continues and final transcription will retry from the saved audio."
            )
            LOGGER.error("pyside_live_transcription_failed")
            if getattr(self, "live_page", None) is not None:
                self.live_page.update_warning(self._recording_warning_text())
            return
        candidate_segments: list[dict[str, Any]] = []
        for segment in message.get("segments", []) or []:
            speaker = str(getattr(segment, "speaker", "") or "").strip()
            if speaker.casefold() != self.recording_candidate_label.casefold():
                continue
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            candidate_segments.append(
                {
                    "start": float(getattr(segment, "start", 0.0) or 0.0),
                    "end": float(getattr(segment, "end", 0.0) or 0.0),
                    "text": text,
                }
            )
        windows = build_flow_time_windows(self.session.flow_time_marks)
        mapped = map_segments_to_flow_indices(candidate_segments, windows) if windows else {}
        if candidate_segments and not mapped:
            mapped = {self.session.current_index: " ".join(item["text"] for item in candidate_segments)}
        for flow_index, text in mapped.items():
            self.session.append_live_transcript(flow_index, text)
        if getattr(self, "live_page", None) is not None:
            self.live_page.update_transcript(self.session.live_transcript(self.session.current_index))

    def _update_live_candidate_audio(self) -> None:
        recorder = self.recording_session
        page = getattr(self, "live_page", None)
        if recorder is None or page is None:
            return
        sys_wav_text = str(getattr(recorder, "sys_wav", "") or "").strip()
        level = recent_wav_signal_level(Path(sys_wav_text)) if sys_wav_text else 0.0
        page.update_audio(level, level >= (8.0 / 32768.0))

    def _apply_pyside_intro_audio_preflight_result(self, result: AudioPreflightResult) -> None:
        self._apply_setup_audio_preflight_result(result)
        self.recording_warning = result.warning
        if getattr(self, "live_page", None) is not None:
            self.live_page.update_warning(self._recording_warning_text())
        if hasattr(self, "review_layout"):
            self._render_review_page()

    def _apply_pyside_intro_audio_check_result(self, transcript_text: str) -> None:
        if str(transcript_text or "").strip():
            return
        self.recording_warning = (
            "No speech was transcribed from the first 15 seconds. "
            "Check audio settings and confirm Zoom/Windows output is routed to VB-CABLE. "
            "Record the interview in Zoom as a backup so transcripts can be generated outside this app."
        )
        LOGGER.warning("pyside_intro_audio_check_blank")
        if getattr(self, "live_page", None) is not None:
            self.live_page.update_warning(self._recording_warning_text())

    def _wav_has_detectable_signal(self, wav_path: Path, *, min_average_abs: float = 8.0) -> bool:
        try:
            with wave.open(str(wav_path), "rb") as wav_file:
                sample_width = wav_file.getsampwidth()
                frame_count = min(wav_file.getnframes(), max(wav_file.getframerate(), 8000))
                raw = wav_file.readframes(frame_count)
        except (OSError, wave.Error):
            return False
        if sample_width != 2 or not raw:
            return bool(raw)
        sample_count = len(raw) // 2
        if sample_count <= 0:
            return False
        total = 0
        for index in range(0, len(raw) - 1, 2):
            value = int.from_bytes(raw[index : index + 2], byteorder="little", signed=True)
            total += abs(value)
        return (total / sample_count) >= min_average_abs

    def _check_pyside_system_audio_capture(self) -> None:
        session = self.recording_session
        if session is None:
            return
        sys_wav = Path(str(getattr(session, "sys_wav", "") or ""))
        if not sys_wav or self._wav_has_detectable_signal(sys_wav):
            return
        device = str(self.recording_system_device or "").strip()
        detail = f" on {device}" if device else ""
        self.recording_warning = (
            f"No meeting/system audio detected yet{detail}. "
            "If candidate audio is playing, switch Zoom/Windows output to VB-CABLE."
        )
        LOGGER.warning("pyside_system_audio_route_no_signal")
        if hasattr(self, "live_question_layout"):
            self._render_live_question_page()

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

    def _fit_window_to_available_screen(self, *, fill_available: bool = False) -> None:
        if self.window.isMaximized() or self.window.isFullScreen():
            return
        screen = self.window.screen() or self.QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_width = max(640, int(available.width()))
        max_height = max(480, int(available.height()))
        if fill_available:
            self.window.setGeometry(available)
            return
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
        width = int(self.window.width())
        if width < 900:
            admin_min = 132
            admin_max = 184
        else:
            admin_min = 170
            admin_max = 260
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
        live_page = getattr(self, "live_page", None)
        if live_page is not None and hasattr(self, "interview_tabs"):
            staffing_sidebar = getattr(getattr(self, "staffing_v2_dashboard", None), "staffing_sidebar", None)
            sidebar_width = int(staffing_sidebar.width()) if staffing_sidebar is not None else 0
            live_page.set_narrow(max(0, int(self.window.width()) - sidebar_width - 40) < 1180)
            live_scroll = self.interview_tabs.widget(_INTERVIEW_LIVE_TAB_INDEX).findChild(
                self.QtWidgets.QScrollArea,
                "LiveInterviewScroll",
            )
            if live_scroll is not None and live_scroll.widget() is not None:
                available = max(0, int(live_scroll.viewport().width()) - 12)
                live_scroll.widget().setMaximumWidth(available)
                live_scroll.widget().resize(available, live_scroll.widget().height())
                live_page.set_available_width(max(0, available - 48))
        completed_page = getattr(self, "completed_interview_page", None)
        if completed_page is not None and hasattr(self, "interview_tabs"):
            staffing_sidebar = getattr(getattr(self, "staffing_v2_dashboard", None), "staffing_sidebar", None)
            completed_sidebar_width = int(staffing_sidebar.width()) if staffing_sidebar is not None else 0
            completed_page.set_narrow(max(0, int(self.window.width()) - completed_sidebar_width - 40) < 1180)
            completed_scroll = self.interview_tabs.widget(_INTERVIEW_REVIEW_TAB_INDEX).findChild(
                self.QtWidgets.QScrollArea,
                "CompletedInterviewScroll",
            )
            if completed_scroll is not None and completed_scroll.widget() is not None:
                content = completed_scroll.widget()
                if completed_scroll.isVisible():
                    available = max(0, int(completed_scroll.viewport().width()))
                    content.setMaximumWidth(available)
                    content.resize(available, content.height())
                else:
                    content.setMaximumWidth(16777215)

    def _page(self) -> Any:
        QtWidgets = self.QtWidgets
        QtCore = self.QtCore

        class ResponsivePageScrollArea(QtWidgets.QScrollArea):
            def resizeEvent(inner_self, event: Any) -> None:
                super().resizeEvent(event)
                inner_self._clamp_child_width()
                QtCore.QTimer.singleShot(0, inner_self._clamp_child_width)

            def _clamp_child_width(inner_self) -> None:
                child = inner_self.widget()
                if child is not None:
                    width = max(0, inner_self.viewport().width())
                    child.setMaximumWidth(width)
                    if child.width() > width:
                        child.resize(width, child.height())

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
        page.verticalScrollBar().rangeChanged.connect(lambda _minimum, _maximum: page._clamp_child_width())
        page.horizontalScrollBar().rangeChanged.connect(lambda _minimum, _maximum: page._clamp_child_width())
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

    def _hiring_interview_guide_widget(self) -> Any:
        page, layout = self._page()
        page.setObjectName("HiringV2InterviewGuide")
        page.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.interview_tabs = self.QtWidgets.QTabWidget()
        self.interview_tabs.setObjectName("HiringV2InterviewRouteStack")
        self.interview_tabs.addTab(self._home_tab(), "Home")
        self.interview_tabs.addTab(self._live_question_tab(), "Live Interview")
        self.interview_tabs.addTab(self._review_tab(), "Review")
        self.interview_tabs.tabBar().hide()
        layout.addWidget(self.interview_tabs, 1)
        return page

    def _home_tab(self) -> Any:
        page, layout = self._page()
        page.setObjectName("HiringV2NewInterviewSetup")
        page.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.setContentsMargins(34, 22, 34, 24)
        layout.setSpacing(16)

        header = self.QtWidgets.QWidget()
        header.setObjectName("HiringV2SetupHeader")
        header_layout = self.QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(3)
        header_layout.addWidget(self._label("New Interview", "Title"))
        header_layout.addWidget(
            self._label(
                "Enter the candidate details and confirm audio capture before beginning.",
                "HiringV2SetupSubtitle",
            )
        )
        layout.addWidget(header)

        progress = self.QtWidgets.QFrame()
        progress.setObjectName("HiringV2SetupProgress")
        progress_layout = self.QtWidgets.QHBoxLayout(progress)
        progress_layout.setContentsMargins(120, 4, 120, 2)
        progress_layout.setSpacing(10)
        for index, step in enumerate(("Setup", "Introduction", "Questions", "Review"), start=1):
            step_box = self.QtWidgets.QWidget()
            step_layout = self.QtWidgets.QVBoxLayout(step_box)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(4)
            badge = self._label(str(index), "HiringV2SetupProgressBadge")
            badge.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
            badge.setProperty("activeStep", index == 1)
            badge.setFixedSize(28, 28)
            step_layout.addWidget(badge, 0, self.QtCore.Qt.AlignmentFlag.AlignHCenter)
            label = self._label(step, "HiringV2SetupProgressLabel")
            label.setAlignment(self.QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setProperty("activeStep", index == 1)
            step_layout.addWidget(label)
            progress_layout.addWidget(step_box, 0)
            if index < 4:
                connector = self.QtWidgets.QFrame()
                connector.setObjectName("HiringV2SetupProgressConnector")
                connector.setFixedHeight(1)
                connector.setSizePolicy(
                    self.QtWidgets.QSizePolicy.Policy.Expanding,
                    self.QtWidgets.QSizePolicy.Policy.Fixed,
                )
                progress_layout.addWidget(connector, 1)
        layout.addWidget(progress)

        setup = self.QtWidgets.QFrame()
        setup.setObjectName("HiringV2CandidateSetupCard")
        setup.setMinimumWidth(860)
        setup.setMaximumWidth(940)
        setup.setSizePolicy(
            self.QtWidgets.QSizePolicy.Policy.Expanding,
            self.QtWidgets.QSizePolicy.Policy.Preferred,
        )
        setup_layout = self.QtWidgets.QVBoxLayout(setup)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(0)

        candidate_section = self.QtWidgets.QWidget()
        candidate_layout = self.QtWidgets.QVBoxLayout(candidate_section)
        candidate_layout.setContentsMargins(24, 22, 24, 22)
        candidate_layout.setSpacing(14)
        candidate_layout.addWidget(self._label("1. Candidate & Interview", "SectionTitle"))
        identity_editor = CandidateIdentityEditor(
            QtWidgets=self.QtWidgets,
            object_prefix="HiringV2Setup",
            school_options=self.model.school_options,
            position_options=list(self.model.track_labels.items()),
            allow_empty_selection=False,
        )
        candidate = identity_editor.candidate_name
        honorific = identity_editor.honorific
        school = identity_editor.school
        role = identity_editor.position
        role.setObjectName("HiringV2SetupTrack")
        candidate_layout.addWidget(identity_editor.widget)
        interview_type = self.QtWidgets.QComboBox()
        interview_type.setObjectName("HiringV2SetupInterviewType")
        interview_type.addItem("First Interview")
        interview_type_form = self.QtWidgets.QFormLayout()
        interview_type_form.addRow("Interview Type", interview_type)
        candidate_layout.addLayout(interview_type_form)
        for field in (candidate, honorific, school, role, interview_type):
            field.setMinimumHeight(40)
        setup_layout.addWidget(candidate_section)

        divider = self.QtWidgets.QFrame()
        divider.setObjectName("HiringV2SetupDivider")
        divider.setFixedHeight(1)
        setup_layout.addWidget(divider)

        audio_section = self.QtWidgets.QWidget()
        audio_layout = self.QtWidgets.QVBoxLayout(audio_section)
        audio_layout.setContentsMargins(24, 20, 24, 22)
        audio_layout.setSpacing(12)
        audio_layout.addWidget(self._label("2. Audio & Transcript Check", "SectionTitle"))
        status_grid = self.QtWidgets.QGridLayout()
        status_grid.setHorizontalSpacing(18)
        status_grid.setVerticalSpacing(10)
        status_grid.setColumnMinimumWidth(0, 150)
        statuses = (
            ("Microphone", "HiringV2SetupMicrophoneStatus"),
            ("System audio", "HiringV2SetupSystemAudioStatus"),
            ("Transcript", "HiringV2SetupTranscriptStatus"),
        )
        status_widgets: list[Any] = []
        for row, (label_text, object_name) in enumerate(statuses):
            status_grid.addWidget(self._label(label_text, "HiringV2SetupStatusName"), row, 0)
            status = self._label("Checking...", object_name)
            status.setProperty("readinessState", "checking")
            status_grid.addWidget(status, row, 1)
            status_widgets.append(status)
        audio_layout.addLayout(status_grid)

        source_row = self.QtWidgets.QGridLayout()
        source_row.setHorizontalSpacing(16)
        source_row.setColumnMinimumWidth(0, 150)
        source_row.addWidget(self._label("Audio source", "HiringV2SetupFieldLabel"), 0, 0)
        audio_source = self.QtWidgets.QComboBox()
        audio_source.setObjectName("HiringV2SetupAudioSource")
        audio_source.addItem("Detecting system audio...", "")
        audio_source.setMinimumHeight(40)
        source_row.addWidget(audio_source, 0, 1)
        test_audio = self.QtWidgets.QPushButton("Test Audio")
        test_audio.setObjectName("HiringV2SetupTestAudio")
        test_audio.setMinimumHeight(40)
        test_audio.clicked.connect(self._start_manual_audio_preflight)
        source_row.addWidget(test_audio, 0, 2)
        source_row.setColumnStretch(1, 1)
        audio_layout.addLayout(source_row)
        note = self._label(
            "Note: Recording and autosave begin when the interview starts.",
            "HiringV2SetupAudioNote",
        )
        audio_layout.addWidget(note)

        validation = self._label("", "HiringV2SetupValidation")
        validation.hide()
        audio_layout.addWidget(validation)

        actions = self.QtWidgets.QHBoxLayout()
        actions.setSpacing(16)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.setObjectName("LiveTranscriptEditorCancel")
        cancel.setObjectName("HiringV2SetupCancel")
        cancel.setMinimumHeight(50)
        cancel.clicked.connect(self._cancel_new_interview_setup)
        actions.addWidget(cancel, 1)
        begin = self._primary_button("Begin Interview")
        begin.setObjectName("HiringV2SetupBegin")
        begin.setMinimumHeight(50)
        begin.clicked.connect(self._begin_selected_interview)
        actions.addWidget(begin, 1)
        audio_layout.addSpacing(18)
        audio_layout.addLayout(actions)
        setup_layout.addWidget(audio_section)

        card_row = self.QtWidgets.QHBoxLayout()
        card_row.addWidget(setup, 1, self.QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(card_row)
        layout.addStretch(1)

        self.home_candidate_input = candidate
        self.home_identity_editor = identity_editor
        self.home_honorific_combo = honorific
        self.home_school_combo = school
        self.home_role_combo = role
        self.home_interview_type_combo = interview_type
        self.home_audio_source_combo = audio_source
        self.home_microphone_status = status_widgets[0]
        self.home_system_audio_status = status_widgets[1]
        self.home_transcript_status = status_widgets[2]
        self.home_setup_validation = validation
        self.home_test_audio_button = test_audio
        self.home_begin_button = begin
        self._setup_form_dirty = False
        candidate.textChanged.connect(self._mark_new_interview_setup_dirty)
        honorific.currentIndexChanged.connect(self._mark_new_interview_setup_dirty)
        school.currentIndexChanged.connect(self._mark_new_interview_setup_dirty)
        role.currentIndexChanged.connect(self._mark_new_interview_setup_dirty)
        return page

    def _mark_new_interview_setup_dirty(self, *_args: Any) -> None:
        self._setup_form_dirty = True

    def _cancel_new_interview_setup(self) -> None:
        if getattr(self, "_setup_form_dirty", False):
            confirmed = self.QtWidgets.QMessageBox.question(
                self.window,
                "Cancel new interview",
                "Discard entered interview details?",
                self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
                self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirmed != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._reset_new_interview_setup()
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None:
            dashboard._show_dashboard_view()

    def _reset_new_interview_setup(self) -> None:
        if hasattr(self, "home_candidate_input"):
            self.home_candidate_input.clear()
        for combo_name in ("home_school_combo", "home_role_combo", "home_interview_type_combo"):
            combo = getattr(self, combo_name, None)
            if combo is not None and combo.count():
                combo.setCurrentIndex(0)
        honorific = getattr(self, "home_honorific_combo", None)
        if honorific is not None:
            honorific.setCurrentText("Ms.")
        validation = getattr(self, "home_setup_validation", None)
        if validation is not None:
            validation.clear()
            validation.hide()
        self._setup_form_dirty = False

    def _start_manual_audio_preflight(self) -> None:
        button = getattr(self, "home_test_audio_button", None)
        if button is None or not button.isEnabled():
            return
        source_combo = getattr(self, "home_audio_source_combo", None)
        source = self._selected_setup_audio_source() if source_combo is not None else ""
        if not source:
            result = AudioPreflightResult(
                False,
                False,
                False,
                "No system audio source is available. Check audio settings and record the interview in Zoom as a backup.",
            )
            self._apply_setup_audio_preflight_result(result)
            return
        button.setEnabled(False)
        button.setText("Testing 5 seconds...")
        results: queue.Queue[AudioPreflightResult] = queue.Queue()
        self._manual_audio_preflight_queue = results
        cancel_event = threading.Event()
        self._manual_audio_preflight_cancel_event = cancel_event

        def worker() -> None:
            results.put(self._run_manual_audio_preflight(source))

        threading.Thread(target=worker, daemon=True).start()
        timer = self.QtCore.QTimer(self.window)
        self._manual_audio_preflight_timer = timer
        timer.timeout.connect(lambda: self._poll_manual_audio_preflight(results, timer))
        timer.start(50)

    def _run_manual_audio_preflight(self, system_device: str) -> AudioPreflightResult:
        session: Any | None = None
        cancel_event = getattr(self, "_manual_audio_preflight_cancel_event", None)
        try:
            from interview_audio_recorder import start_recording

            with tempfile.TemporaryDirectory(prefix="lpl-audio-preflight-") as temp_dir:
                runtime_config = resolve_runtime(self._recording_runtime_settings())
                microphone_device = (
                    resolve_default_windows_microphone_device() if sys.platform.startswith("win") else None
                )
                session = start_recording(
                    os_name="windows" if sys.platform.startswith("win") else "linux",
                    output_dir=Path(temp_dir),
                    base_name=f"audio_preflight_{uuid4().hex}",
                    win_mic_device=microphone_device,
                    win_sys_device=system_device,
                    whisper_model=runtime_config.model,
                    whisper_device=runtime_config.device,
                    whisper_compute_type=runtime_config.compute_type,
                    whisper_backend=runtime_config.backend,
                )
                self._manual_audio_preflight_session = session
                time.sleep(5)
                if cancel_event is not None and cancel_event.is_set():
                    session.stop()
                    return AudioPreflightResult(
                        False,
                        False,
                        False,
                        "Audio test canceled.",
                    )
                session.stop()
                segments = session.transcribe_new_segments(language="en")
                return evaluate_audio_preflight(
                    microphone_wav=Path(session.mic_wav),
                    system_audio_wav=Path(session.sys_wav),
                    transcript_segments=segments,
                    candidate_label=str(session.sys_label or self.recording_candidate_label),
                )
        except (Exception, SystemExit):
            if session is not None:
                try:
                    session.stop()
                except Exception:
                    pass
            LOGGER.error("manual_audio_preflight_failed")
            return AudioPreflightResult(
                False,
                False,
                False,
                "Audio test could not complete. Check audio settings and record the interview in Zoom as a backup.",
            )
        finally:
            if getattr(self, "_manual_audio_preflight_session", None) is session:
                self._manual_audio_preflight_session = None

    def _poll_manual_audio_preflight(
        self,
        results: queue.Queue[AudioPreflightResult],
        timer: Any,
    ) -> None:
        try:
            result = results.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        timer.deleteLater()
        if getattr(self, "_manual_audio_preflight_queue", None) is results:
            self._manual_audio_preflight_queue = None
            self._manual_audio_preflight_timer = None
            self._manual_audio_preflight_cancel_event = None
        button = getattr(self, "home_test_audio_button", None)
        if button is not None:
            button.setText("Test Audio")
            button.setEnabled(True)
        self._apply_setup_audio_preflight_result(result)

    def _apply_setup_audio_preflight_result(self, result: AudioPreflightResult) -> None:
        rows = (
            (
                getattr(self, "home_microphone_status", None),
                result.microphone_ready,
                "Microphone connected",
                "Microphone audio not detected",
            ),
            (
                getattr(self, "home_system_audio_status", None),
                result.system_audio_ready,
                "System audio connected",
                "System audio not detected",
            ),
            (
                getattr(self, "home_transcript_status", None),
                result.transcription_ready,
                "Live transcription ready",
                "Candidate transcription not detected",
            ),
        )
        for widget, ready, ready_text, failed_text in rows:
            if widget is None:
                continue
            widget.setText(ready_text if ready else failed_text)
            widget.setProperty("readinessState", "ready" if ready else "failed")
            self._refresh_widget_style(widget)
        validation = getattr(self, "home_setup_validation", None)
        if validation is not None:
            validation.setText(result.warning)
            validation.setVisible(bool(result.warning))

    def _first_flow_item(self, *, kind: str | None = None) -> FlowQuestion | None:
        for flow in self.model.flows.values():
            for item in flow.items:
                if kind is None or item.kind == kind:
                    return item
        return None

    def _live_question_tab(self) -> Any:
        class LiveInterviewScrollArea(self.QtWidgets.QScrollArea):
            def resizeEvent(inner_self, event: Any) -> None:
                super().resizeEvent(event)
                child = inner_self.widget()
                if child is None:
                    return
                width = max(0, inner_self.viewport().width())
                child.setMinimumWidth(width)
                child.setMaximumWidth(width)
                child.resize(width, child.height())

        page = self.QtWidgets.QWidget()
        outer_layout = self.QtWidgets.QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = LiveInterviewScrollArea()
        scroll.setObjectName("LiveInterviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(self.QtWidgets.QFrame.Shape.NoFrame)
        content = self.QtWidgets.QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(
            self.QtWidgets.QSizePolicy.Policy.Expanding,
            self.QtWidgets.QSizePolicy.Policy.Minimum,
        )
        layout = self.QtWidgets.QVBoxLayout(content)
        layout.setSizeConstraint(self.QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
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
        scroll = page.findChild(self.QtWidgets.QScrollArea)
        if scroll is not None:
            scroll.setObjectName("CompletedInterviewScroll")
            content = scroll.widget()
            if content is not None:
                content.setMinimumWidth(0)
                content.setSizePolicy(
                    self.QtWidgets.QSizePolicy.Policy.Expanding,
                    self.QtWidgets.QSizePolicy.Policy.Minimum,
                )
                content.layout().setSizeConstraint(self.QtWidgets.QLayout.SizeConstraint.SetNoConstraint)
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
        honorific = self.home_honorific_combo.currentText().strip() if hasattr(self, "home_honorific_combo") else ""
        school = self.home_school_combo.currentText().strip() if hasattr(self, "home_school_combo") else ""
        preferred_name = ""
        email = ""
        phone = ""
        if not candidate_name or honorific not in {"Mr.", "Ms."} or not school or not label:
            validation = getattr(self, "home_setup_validation", None)
            if validation is not None:
                validation.setText("Candidate name, honorific, school, and position / track are required.")
                validation.show()
            else:
                self.QtWidgets.QMessageBox.warning(
                    self.window,
                    "New interview",
                    "Candidate name, school, and role are required.",
                )
            return
        validation = getattr(self, "home_setup_validation", None)
        if sys.platform.startswith("win"):
            selected_source = self._selected_setup_audio_source()
            try:
                available_sources = {
                    str(value or "").strip().casefold()
                    for value in list_windows_dshow_audio_devices()
                    if str(value or "").strip()
                }
            except Exception:
                available_sources = set()
            if selected_source and available_sources and selected_source.casefold() not in available_sources:
                if validation is not None:
                    validation.setText(
                        "The selected system audio source is no longer available. "
                        "Choose an available source or reconnect the device."
                    )
                    validation.show()
                return
        self._stop_manual_audio_preflight()
        if validation is not None:
            validation.clear()
            validation.hide()
        hiring_service = HiringWorkflowService(HiringPipelineStore(self.model.history_path))
        application = hiring_service.start_application(
            legal_name=candidate_name,
            honorific=honorific,
            email=email,
            phone=phone,
            school=school,
            position=label,
            actor="Admin User",
        )
        hiring_service.update_candidate_profile(
            application.candidate_id,
            legal_name=candidate_name,
            preferred_name=preferred_name,
            email=email,
            phone=phone,
        )
        draft_path = self._drafts_dir() / f"{application.application_id}.json"
        self.session = PySideInterviewSession(
            model=self.model,
            draft_path=draft_path,
            application_id=application.application_id,
        )
        self.session.start(
            candidate_name=candidate_name,
            honorific=honorific,
            school=school,
            track_key=self.session_track_key,
        )
        self._start_pyside_interview_recording()
        self._render_live_question_page()
        self._render_review_page()
        self.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
        self._set_hiring_focus_mode(True)

    def _import_indeed_transcript_from_home(self) -> None:
        request = self._collect_indeed_transcript_import_request()
        if not request:
            return
        candidate_name = str(request.get("candidate_name") or "").strip()
        school = str(request.get("school") or "").strip()
        track_key = str(request.get("track_key") or "").strip()
        interview_date = str(request.get("interview_date") or "").strip()
        source_path = Path(str(request.get("transcript_path") or ""))
        existing_row = self._matching_history_row_for_indeed_import(
            candidate_name=candidate_name,
            interview_date=interview_date,
            school=school,
            track_key=track_key,
        )
        try:
            if existing_row is not None:
                session = self._session_from_history_row(existing_row)
                result = session.import_indeed_transcript_file(source_path)
                artifact_updates = self._regenerate_history_import_artifacts(existing_row, session)
                updated = self.history_store.update_row(
                    existing_row.row_key,
                    self._history_import_updates(existing_row, session, result, source_path, artifact_updates),
                )
                if not updated:
                    raise ValueError("Matching history entry was not found.")
                history_id = existing_row.row_key
            else:
                draft_path = self._default_draft_path(candidate_name or "Candidate")
                session = PySideInterviewSession(model=self.model, draft_path=draft_path)
                session.start(candidate_name=candidate_name, school=school, track_key=track_key)
                session.interview_date = interview_date or date.today().isoformat()
                result = session.import_indeed_transcript_file(source_path)
                history_id = self._persist_new_indeed_import_history_row(session, result, source_path)
            self._sync_candidate_report_import(history_id, result)
        except Exception as exc:  # noqa: BLE001
            self.QtWidgets.QMessageBox.warning(self.window, "Indeed Transcript", f"Could not import transcript: {exc}")
            return
        self.session = session
        self.session_track_key = session.track_key
        self.session_index = session.current_index
        self.session_answers = dict(session.answers)
        self._review_history_id = history_id
        self._review_score_dirty = False
        skipped_count = len(result.unmatched_question_ids)
        self._reload_history_model()
        self._render_live_question_page()
        self._render_review_page()
        self._refresh_home_draft_panel()
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_interview()
        self.interview_tabs.setCurrentIndex(
            _INTERVIEW_LIVE_TAB_INDEX if session.active_question() is not None else _INTERVIEW_REVIEW_TAB_INDEX
        )
        if hasattr(self, "home_draft_label"):
            self.home_draft_label.setText(
                f"Imported Indeed transcript: {result.mapped_count} answers split, {skipped_count} questions marked skipped."
            )

    def _matching_history_row_for_indeed_import(
        self,
        *,
        candidate_name: str,
        interview_date: str,
        school: str,
        track_key: str,
    ) -> PySideHistoryRow | None:
        def normalized(value: Any) -> str:
            return " ".join(str(value or "").split()).casefold()

        matches = [
            row
            for row in self.model.home.history_rows
            if normalized(row.candidate) == normalized(candidate_name)
            and normalized(row.interview_date) == normalized(interview_date)
            and normalized(row.school) == normalized(school)
            and self._track_key_for_history_row(row) == track_key
        ]
        if not matches:
            return None

        def existing_score_count(row: PySideHistoryRow) -> tuple[int, int]:
            payload = self._history_payload_for_row(row)
            answers = payload.get("answers", {}) if isinstance(payload, dict) else {}
            score_count = sum(
                1
                for answer in answers.values()
                if isinstance(answer, dict) and str(answer.get("score") or "").strip()
            ) if isinstance(answers, dict) else 0
            review_scores = payload.get("review_scores", {}) if isinstance(payload, dict) else {}
            if isinstance(review_scores, dict):
                score_count = max(score_count, sum(1 for score in review_scores.values() if str(score or "").strip()))
            original_row = 0 if str(row.row_key).startswith("indeed-import-") else 1
            return score_count, original_row

        return max(matches, key=existing_score_count)

    def _sync_candidate_report_import(
        self,
        history_id: str,
        result: IndeedTranscriptImportResult,
    ) -> None:
        repository = CandidateReportRepository(self.model.history_path)
        if not repository.exists(history_id):
            return
        repository.sync_imported_transcripts(
            history_id,
            {match.question_id: match.candidate_transcript for match in result.matches},
        )

    def _collect_indeed_transcript_import_request(self) -> dict[str, Any] | None:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("PySideIndeedTranscriptImportDialog")
        dialog.setWindowTitle("Import Indeed Transcript")
        layout = self.QtWidgets.QVBoxLayout(dialog)
        form = self.QtWidgets.QFormLayout()
        candidate = self.QtWidgets.QLineEdit()
        candidate.setObjectName("PySideIndeedImportCandidateName")
        candidate.setText(self.home_candidate_input.text().strip() if hasattr(self, "home_candidate_input") else "")
        school = self.QtWidgets.QComboBox()
        school.setObjectName("PySideIndeedImportSchool")
        school.addItems(self.model.school_options)
        current_school = self.home_school_combo.currentText().strip() if hasattr(self, "home_school_combo") else ""
        if current_school:
            idx = school.findText(current_school)
            if idx >= 0:
                school.setCurrentIndex(idx)
        role = self.QtWidgets.QComboBox()
        role.setObjectName("PySideIndeedImportTrack")
        for key, label in self.model.track_labels.items():
            role.addItem(label, key)
        current_role = self.home_role_combo.currentText() if hasattr(self, "home_role_combo") else ""
        if current_role:
            idx = role.findText(current_role)
            if idx >= 0:
                role.setCurrentIndex(idx)
        interview_date = self.QtWidgets.QDateEdit()
        interview_date.setObjectName("PySideIndeedImportInterviewDate")
        interview_date.setCalendarPopup(True)
        interview_date.setDate(self.QtCore.QDate.currentDate())
        transcript_path = self.QtWidgets.QLineEdit()
        transcript_path.setObjectName("PySideIndeedImportTranscriptPath")
        browse = self.QtWidgets.QPushButton("Browse")
        browse.setObjectName("PySideIndeedImportBrowse")
        path_row = self.QtWidgets.QHBoxLayout()
        path_row.addWidget(transcript_path, 1)
        path_row.addWidget(browse)

        def _browse() -> None:
            file_name, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
                dialog,
                "Import Indeed Transcript",
                str(Path.home()),
                "Text files (*.txt)",
            )
            if file_name:
                transcript_path.setText(file_name)

        browse.clicked.connect(_browse)
        form.addRow("Candidate Name", candidate)
        form.addRow("Interview Date", interview_date)
        form.addRow("School", school)
        form.addRow("Track", role)
        form.addRow("Transcript", path_row)
        layout.addLayout(form)
        buttons = self.QtWidgets.QDialogButtonBox(
            self.QtWidgets.QDialogButtonBox.StandardButton.Ok | self.QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return None
        candidate_name = candidate.text().strip()
        path = transcript_path.text().strip()
        if not candidate_name or not path:
            self.QtWidgets.QMessageBox.warning(
                self.window,
                "Indeed Transcript",
                "Candidate name and transcript file are required.",
            )
            return None
        return {
            "candidate_name": candidate_name,
            "interview_date": interview_date.date().toString("yyyy-MM-dd"),
            "school": school.currentText().strip(),
            "track_key": str(role.currentData() or self._track_key_for_label(role.currentText())),
            "transcript_path": Path(path),
        }

    def _persist_new_indeed_import_history_row(
        self,
        session: PySideInterviewSession,
        result: IndeedTranscriptImportResult,
        source_path: Path,
    ) -> str:
        history_id = f"indeed-import-{uuid4()}"
        updates = self._history_import_updates(
            PySideHistoryRow(
                row_key=history_id,
                interview_date=session.interview_date,
                candidate=session.candidate_name,
                school=session.school,
                position=self.model.track_labels.get(session.track_key, session.track_key),
                score="",
                status="Incomplete",
                offer_status="not_generated",
                offer_action="Review",
                notes_path="",
                report_path="",
            ),
            session,
            result,
            source_path,
        )
        payload = {
            **updates,
            "history_id": history_id,
            "outcome": "Incomplete",
            "status": "Incomplete",
            "interview_status": "Incomplete",
            "score": "",
            "interview_score": "",
            "percent_of_max": "",
            "saved_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
        self.history_store.append(payload)
        return history_id

    def _track_key_for_history_row(self, row: PySideHistoryRow) -> str:
        position = str(row.position or "").strip()
        if position:
            for key, label in self.model.track_labels.items():
                if position == label or position.lower() == label.lower():
                    return key
            for key, label in self.model.track_labels.items():
                lowered = position.lower()
                if key.lower() in lowered or label.lower() in lowered:
                    return key
        return next(iter(self.model.flows), "")

    def _session_from_history_row(self, row: PySideHistoryRow) -> PySideInterviewSession:
        track_key = self._track_key_for_history_row(row)
        draft_path = self._default_draft_path(row.candidate or "Candidate")
        session = PySideInterviewSession(model=self.model, draft_path=draft_path)
        session.start(candidate_name=row.candidate, school=row.school, track_key=track_key)
        if row.interview_date:
            session.interview_date = row.interview_date
        payload = self._history_payload_for_row(row)
        self._hydrate_session_from_history_payload(session, payload)
        history_id = str(row.row_key or "").strip()
        report_repository = CandidateReportRepository(self.model.history_path)
        if history_id and report_repository.exists(history_id):
            report = report_repository.load_visible_version(history_id, role="admin")
            self._hydrate_session_from_history_payload(session, report.snapshot)
        return session

    def _history_payload_for_row(self, row: PySideHistoryRow) -> dict[str, Any]:
        key = str(row.row_key or "").strip()
        if not key:
            return {}
        for payload in self.history_store.load():
            if self.history_store.build_row_key(payload) == key:
                return payload
        return {}

    def _hydrate_session_from_history_payload(
        self,
        session: PySideInterviewSession,
        payload: dict[str, Any],
    ) -> None:
        if not isinstance(payload, dict):
            return
        qualification = payload.get("qualification", {})
        candidate = payload.get("candidate", {})
        if isinstance(candidate, dict) and isinstance(candidate.get("qualification"), dict):
            qualification = candidate.get("qualification", {})
        if isinstance(qualification, dict):
            session.qualification = dict(qualification)
        stored_answers = payload.get("answers", {})
        if isinstance(stored_answers, dict):
            for question_id, answer in stored_answers.items():
                if isinstance(answer, dict):
                    session.answers[str(question_id)] = dict(answer)
        questions = payload.get("questions", [])
        if isinstance(questions, list):
            for index, question in enumerate(questions):
                if not isinstance(question, dict):
                    continue
                question_id = str(question.get("question_id") or question.get("id") or "").strip()
                if not question_id:
                    continue
                answer = dict(session.answers.get(question_id, {}) or {})
                fallback_fields = {
                    "kind": question.get("type"),
                    "title": question.get("title"),
                    "prompt": question.get("prompt"),
                    "notes": question.get("interviewer_notes"),
                    "score": question.get("rating"),
                    "skip_reason": question.get("skip_reason"),
                }
                for field_name, value in fallback_fields.items():
                    if answer.get(field_name) in (None, "") and value not in (None, ""):
                        answer[field_name] = value
                if "skipped" not in answer and "skipped" in question:
                    answer["skipped"] = bool(question.get("skipped"))
                quick_actions = [str(value) for value in answer.get("quick_actions", []) or []]
                if question.get("absolute_disqualifier") and "Disqualifier observed" not in quick_actions:
                    quick_actions.append("Disqualifier observed")
                if question.get("no_example_after_followups") and "Candidate gave no example" not in quick_actions:
                    quick_actions.append("Candidate gave no example")
                answer["quick_actions"] = quick_actions
                session.answers[question_id] = answer
                try:
                    flow_index = int(question.get("flow_index", index))
                except (TypeError, ValueError):
                    flow_index = index
                transcript = str(question.get("transcript") or "").strip()
                if flow_index >= 0 and transcript and not session.flow_candidate_transcripts.get(flow_index):
                    session.flow_candidate_transcripts[flow_index] = transcript
        review_scores = payload.get("review_scores", {})
        if isinstance(review_scores, dict):
            for question_id, score in review_scores.items():
                answer = dict(session.answers.get(str(question_id), {}) or {})
                answer["score"] = str(score or "").strip()
                session.answers[str(question_id)] = answer
        scoring = payload.get("scoring", {})
        if isinstance(scoring, dict):
            for row in scoring.get("rows", []) or []:
                if not isinstance(row, dict):
                    continue
                question_id = str(row.get("trait_id") or "").strip()
                raw_score = row.get("raw_score")
                if not question_id or raw_score in (None, ""):
                    continue
                answer = dict(session.answers.get(question_id, {}) or {})
                answer.setdefault("kind", "trait")
                answer["score"] = str(raw_score)
                answer.pop("skipped", None)
                answer.pop("skip_reason", None)
                session.answers[question_id] = answer

    def _history_import_updates(
        self,
        row: PySideHistoryRow,
        session: PySideInterviewSession,
        result: IndeedTranscriptImportResult,
        source_path: Path,
        artifact_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = _PySideFinalizeAdapter(session, base_dir=DEFAULT_BASE_DIR, history_path=self.model.history_path)
        flow_transcript = adapter._build_flow_transcript()
        adapter._apply_candidate_transcripts_to_flow(flow_transcript)
        flow_recordings = [
            {**recording, "flow_index": flow_index}
            for flow_index, recording in sorted(session.flow_recordings.items())
            if isinstance(recording, dict)
        ]
        review_scores = {
            question_id: str(answer.get("score") or "")
            for question_id, answer in session.answers.items()
            if isinstance(answer, dict)
            and str(answer.get("kind") or "") == "trait"
            and str(answer.get("score") or "").strip()
        }
        updates = {
            "candidate_name": session.candidate_name,
            "school": session.school,
            "position": self.model.track_labels.get(session.track_key, session.track_key),
            "track_key": session.track_key,
            "interview_date": session.interview_date,
            "answers": session.answers,
            "flow_candidate_transcripts": {
                str(flow_index): transcript for flow_index, transcript in sorted(session.flow_candidate_transcripts.items())
            },
            "flow_transcript": flow_transcript,
            "custom_answers": adapter._ordered_custom_answers(),
            "flow_recordings": flow_recordings,
            "imported_indeed_transcript": {
                "source_path": str(source_path),
                "mapped_count": result.mapped_count,
                "unmatched_question_ids": list(result.unmatched_question_ids),
                "interviewer_speaker": result.interviewer_speaker,
                "candidate_speaker": result.candidate_speaker,
                "imported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            },
            "review_scores": review_scores,
        }
        if artifact_updates:
            updates.update(artifact_updates)
        return updates

    def _regenerate_history_import_artifacts(self, row: PySideHistoryRow, session: PySideInterviewSession) -> dict[str, Any]:
        has_saved_content = any(
            str(answer.get("notes") or "").strip()
            or str(answer.get("score") or "").strip()
            or bool(answer.get("skipped"))
            or bool(answer.get("quick_actions"))
            for answer in session.answers.values()
            if isinstance(answer, dict)
        ) or any(str(value or "").strip() for value in session.flow_candidate_transcripts.values())
        if not has_saved_content:
            raise ValueError("No saved interview answers, scores, or transcripts are available to regenerate notes.")
        adapter = _PySideFinalizeAdapter(session, base_dir=DEFAULT_BASE_DIR, history_path=self.model.history_path)
        warnings: list[str] = []
        scoring = ScoringEngine.evaluate(adapter._rubric_with_question_overrides(), adapter.state.track, adapter.state.trait_inputs)
        context = build_finalize_context(adapter, scoring, warnings, session._transcript_metadata())
        existing_notes_text = str(row.notes_path or row.report_path or "").strip()
        existing_notes_path = Path(existing_notes_text) if existing_notes_text else None
        output_dir = existing_notes_path.parent if existing_notes_path is not None else adapter._interview_notes_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = DocxExporter(output_dir).export_basic_interview_notes(
            adapter._rubric_with_question_overrides(),
            context.payload,
            scoring,
        )
        percent = scoring.get("percent_of_max", 0)
        percent_label = str(scoring.get("percent_of_max_label") or f"{percent}%")
        outcome = str(scoring.get("outcome", "") or "Incomplete")
        return {
            "saved_report_path": str(out_path),
            "interview_notes_path": str(out_path),
            "notes_path": str(out_path),
            "report_path": str(out_path),
            "interview_score": percent,
            "score": percent_label,
            "percent_of_max": percent,
            "percent_of_max_label": percent_label,
            "determination": outcome,
            "outcome": outcome,
            "status": outcome,
            "interview_status": outcome,
            "next_action": _next_action_for_outcome(outcome),
            "scoring": scoring,
            "basic_notes_regenerated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }

    def _regenerate_history_notes(self, row: PySideHistoryRow) -> None:
        try:
            session = self._session_from_history_row(row)
            updates = self._regenerate_history_import_artifacts(row, session)
            updated = self.history_store.update_row(row.row_key, updates)
        except Exception as exc:  # noqa: BLE001
            self.QtWidgets.QMessageBox.warning(self.window, "Regenerate Notes", f"Could not regenerate notes: {exc}")
            return
        if not updated:
            self.QtWidgets.QMessageBox.warning(self.window, "Regenerate Notes", "History entry was not found.")
            return
        self._reload_history_model()

    def _import_indeed_transcript_for_history_row(self, row: PySideHistoryRow) -> None:
        if not row.row_key:
            return
        file_name, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.window,
            f"Import Indeed Transcript - {row.candidate or 'Candidate'}",
            str(Path.home()),
            "Text files (*.txt)",
        )
        if not file_name:
            return
        source_path = Path(file_name)
        session = self._session_from_history_row(row)
        try:
            result = session.import_indeed_transcript_file(source_path)
            artifact_updates = self._regenerate_history_import_artifacts(row, session)
            updated = self.history_store.update_row(
                row.row_key,
                self._history_import_updates(row, session, result, source_path, artifact_updates),
            )
            if updated:
                self._sync_candidate_report_import(row.row_key, result)
        except Exception as exc:  # noqa: BLE001
            self.QtWidgets.QMessageBox.warning(self.window, "Indeed Transcript", f"Could not import transcript: {exc}")
            return
        if not updated:
            self.QtWidgets.QMessageBox.warning(self.window, "Indeed Transcript", "History entry was not found.")
            return
        self.session = session
        self.session_track_key = session.track_key
        self.session_index = session.current_index
        self.session_answers = dict(session.answers)
        self._review_history_id = row.row_key
        self._review_score_dirty = False
        self._reload_history_model()
        self._render_live_question_page()
        self._render_review_page()
        self._refresh_home_draft_panel()
        self.interview_tabs.setCurrentIndex(_INTERVIEW_REVIEW_TAB_INDEX)
        if hasattr(self, "home_draft_label"):
            skipped_count = len(result.unmatched_question_ids)
            self.home_draft_label.setText(
                f"Imported Indeed transcript for {session.candidate_name}: "
                f"{result.mapped_count} answers split, {skipped_count} questions marked skipped."
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
        self._start_pyside_interview_recording()
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_interview()
        self.interview_tabs.setCurrentIndex(
            _INTERVIEW_LIVE_TAB_INDEX if self.session.active_question() is not None else _INTERVIEW_REVIEW_TAB_INDEX
        )
        self._set_hiring_focus_mode(self.session.active_question() is not None)

    def _refresh_home_draft_panel(self) -> None:
        latest_draft = latest_pyside_draft_path(self._drafts_dir())
        if latest_draft is not None and not Path(latest_draft).exists():
            latest_draft = None
        label = getattr(self, "home_draft_label", None)
        if label is not None:
            label.setText(f"Saved draft: {latest_draft.name}" if latest_draft else "No saved draft available.")
            label.setToolTip(str(latest_draft) if latest_draft else "")
        candidate_label = getattr(self, "candidate_draft_label", None)
        if candidate_label is not None:
            candidate_label.setText(
                f"Saved draft: {latest_draft.name}" if latest_draft else "No saved draft available."
            )
            candidate_label.setToolTip(str(latest_draft) if latest_draft else "")
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

    def _selected_setup_audio_source(self) -> str:
        combo = getattr(self, "home_audio_source_combo", None)
        if combo is None:
            return ""
        data = combo.currentData()
        if data is not None:
            return str(data or "").strip()
        return str(combo.currentText() or "").strip()

    def _start_pyside_interview_recording(self) -> None:
        if self.session is None:
            return
        self.session.flow_time_marks = []
        self.recording_warning = ""
        self.recording_started_monotonic = time.monotonic()
        self.recording_base_name = self._safe_base_name()
        self.recording_candidate_label = "CANDIDATE"
        self.recording_system_device = ""
        try:
            from interview_audio_recorder import start_recording

            runtime_config = resolve_runtime(self._recording_runtime_settings())
            system_device = None
            if sys.platform.startswith("win"):
                system_device = self._selected_setup_audio_source() or resolve_default_windows_system_device()
            microphone_device = (
                resolve_default_windows_microphone_device() if sys.platform.startswith("win") else None
            )
            self.recording_system_device = str(system_device or "")
            self.recording_session = start_recording(
                os_name="windows" if sys.platform.startswith("win") else "linux",
                output_dir=DEFAULT_BASE_DIR,
                base_name=self.recording_base_name,
                win_mic_device=microphone_device,
                win_sys_device=system_device,
                whisper_model=runtime_config.model,
                whisper_device=runtime_config.device,
                whisper_compute_type=runtime_config.compute_type,
                whisper_backend=runtime_config.backend,
            )
            if sys.platform.startswith("win"):
                self.QtCore.QTimer.singleShot(2500, self._check_pyside_system_audio_capture)
            self._schedule_pyside_intro_audio_transcription_check()
            self._start_live_capture_monitor()
        except (Exception, SystemExit) as exc:
            self.recording_session = None
            self.recording_started_monotonic = None
            self.recording_base_name = ""
            self.recording_warning = f"Recording unavailable: {exc}"
            LOGGER.exception("pyside_recording_start_failed")
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
        self._stop_live_capture_monitor()
        try:
            with self._live_transcription_lock:
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
            self.session.apply_canonical_transcripts({})
            LOGGER.error("pyside_recording_stop_failed")
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
        self.session.apply_canonical_transcripts(by_flow_index)
        for flow_idx, candidate_transcript in by_flow_index.items():
            payload = dict(recording_result)
            payload["flow_index"] = flow_idx
            payload["candidate_transcript"] = self.session.flow_candidate_transcripts.get(flow_idx, candidate_transcript)
            self.session.flow_recordings[flow_idx] = payload
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
            self.interview_tabs.setCurrentIndex(_INTERVIEW_REVIEW_TAB_INDEX)
            return
        current_index = self.session.current_index if self.session is not None else self.session_index
        qualification: dict[str, Any] | None = None
        if item.kind == "qualification":
            qualification = self._collect_qualification_from_fields()
            if qualification is None:
                return
        live_snapshot = self.live_page.input_snapshot() if getattr(self, "live_page", None) is not None else None
        score = live_snapshot.score if live_snapshot is not None else ""
        if live_snapshot is None and hasattr(self, "score_group"):
            checked = self.score_group.checkedButton()
            score = checked.text().split(" ", 1)[0] if checked is not None else ""
        if item.score_cards and not score and not skip:
            self._update_live_next_enabled(item)
            return
        boundary_elapsed = self._close_flow_timestamp(current_index)
        notes = live_snapshot.notes if live_snapshot is not None else (
            self.live_notes.toPlainText() if hasattr(self, "live_notes") else ""
        )
        structured_notes = self._structured_live_notes()
        if structured_notes and structured_notes not in notes:
            notes = f"{structured_notes}\n{notes}".strip()
        quick_actions = list(live_snapshot.quick_actions) if live_snapshot is not None else [
            checkbox.text() for checkbox in getattr(self, "quick_action_checks", []) if checkbox.isChecked()
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
            self._stop_live_capture_monitor()
            if finalize:
                self._generate_interview_notes_from_session()
            self._render_review_page()
            self.interview_tabs.setCurrentIndex(_INTERVIEW_REVIEW_TAB_INDEX)
            self._show_hiring_closeout()
            return
        if finalize:
            self._render_review_page()
            self.interview_tabs.setCurrentIndex(_INTERVIEW_REVIEW_TAB_INDEX)
            self._show_hiring_closeout()
            self._generate_interview_notes_from_session()
            return
        self._render_live_question_page()

    def _finalize_from_live_question(self) -> None:
        self._save_and_next(finalize=True)

    def _skip_live_question(self) -> None:
        self._save_and_next(skip=True)

    def _go_back_live_question(self) -> None:
        self._save_live_snapshot_without_navigation()
        if self.session is not None:
            self.session.go_back()
            self.session_index = self.session.current_index
            self.session_answers = dict(self.session.answers)
            self._overwrite_next_live_boundary_timestamp = True
        elif self.session_index > 0:
            self.session_index -= 1
            self._overwrite_next_live_boundary_timestamp = True
        self._render_live_question_page()
        self._render_review_page()

    def _exit_live_interview(self) -> None:
        if self.session is not None and getattr(self, "live_page", None) is not None:
            self._save_live_snapshot_without_navigation()
        if self.session is not None:
            self.session.save_draft()
        self._stop_live_capture_monitor()
        recording = getattr(self, "recording_session", None)
        self.recording_session = None
        self.recording_started_monotonic = None
        if recording is not None:
            threading.Thread(target=recording.stop, daemon=True).start()
        self.session = None
        self.session_index = 0
        self.session_answers = {}
        self._set_hiring_focus_mode(False)
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_interview()
        self._reset_new_interview_setup()
        self.interview_tabs.setCurrentIndex(_INTERVIEW_HOME_TAB_INDEX)

    def _save_live_snapshot_without_navigation(self) -> None:
        if getattr(self, "session", None) is None or getattr(self, "live_page", None) is None:
            return
        item = self.session.active_question()
        if item is None:
            return
        snapshot = self.live_page.input_snapshot()
        existing = dict(self.session.answers.get(item.question_id, {}) or {})
        existing.update(
            {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": snapshot.notes,
                "score": snapshot.score,
                "quick_actions": list(snapshot.quick_actions),
            }
        )
        self.session.answers[item.question_id] = existing
        self.session.save_draft()

    def _show_hiring_pipeline(self) -> None:
        self._set_hiring_focus_mode(False)
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_pipeline()
        page = getattr(self, "hiring_v2_page", None)
        if page is not None:
            page.refresh()

    def _show_hiring_closeout(self) -> None:
        self._set_hiring_focus_mode(False)
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_closeout()
        self.QtCore.QTimer.singleShot(
            0,
            lambda: self.QtCore.QTimer.singleShot(0, self._apply_responsive_layout),
        )

    def _set_hiring_focus_mode(self, active: bool) -> None:
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is None:
            return
        dashboard.set_navigation_locked(False)
        dashboard.set_navigation_mode("rail" if active else "full")
        if active and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        dashboard.set_navigation_locked(active)

    def _render_live_question_page(self) -> None:
        layout = getattr(self, "live_question_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        item = self._active_question()
        if self.session is not None and item is None:
            self.live_page = None
            layout.addWidget(self._label("Interview responses complete. Continue to review."))
            return
        item = item or self._first_flow_item(kind="trait") or self._first_flow_item()
        if item is None:
            layout.addWidget(self._label("No configured interview questions."))
            self._render_live_footer(None)
            return
        current_index = self.session.current_index if self.session is not None else self.session_index
        self._mark_flow_timestamp(current_index)
        if item.kind in {"intro", "qualification", "custom", "trait"} and self.session is not None:
            self._render_mockup_live_page(layout, item, current_index)
            return
        self.live_page = None

        header, header_layout = self._surface()
        header_row = self.QtWidgets.QHBoxLayout()
        candidate_name = self.session.candidate_name if self.session is not None else "Candidate"
        school = self.session.school if self.session is not None else ""
        position = self._session_position_label() if self.session is not None else self.session_track_key
        header_row.addWidget(self._label(f"{candidate_name}  ·  {school}  ·  {position}", "SectionTitle"))
        header_row.addStretch(1)
        application_id = str(getattr(self.session, "application_id", "") or "") if self.session is not None else ""
        if application_id:
            header_row.addWidget(self._label(f"Application {application_id[:8]}"))
        header_layout.addLayout(header_row)
        progress = self.QtWidgets.QProgressBar()
        total_questions = len(self.session._workflow_items()) if self.session is not None else 1
        progress.setRange(0, max(1, total_questions))
        progress.setValue(current_index + 1)
        progress.setFormat(f"{item.progress_label}  ·  %p%")
        progress.setObjectName("HiringV2InterviewProgress")
        header_layout.addWidget(progress)
        capture = self.QtWidgets.QFrame()
        capture.setObjectName("HiringV2CaptureBar")
        capture_layout = self.QtWidgets.QHBoxLayout(capture)
        capture_layout.setContentsMargins(10, 6, 10, 6)
        capture_layout.setSpacing(10)
        capture_active = self.recording_session is not None
        capture_state = "critical" if capture_active else "warning" if self._recording_warning_text() else "neutral"
        capture.setProperty("semanticState", capture_state)
        recording_text = "● Recording" if capture_active else "○ Recording unavailable"
        capture_status = self._label(recording_text)
        capture_status.setObjectName("HiringV2CaptureStatus")
        capture_layout.addWidget(capture_status)
        device = str(getattr(self, "recording_system_device", "") or "Default capture device")
        capture_layout.addWidget(self._label(device))
        capture_layout.addStretch(1)
        transcript_text = "Transcript capture active" if capture_active else "Transcript pending"
        if self._recording_warning_text():
            transcript_text = "Transcript attention required"
        transcript_status = self._label(transcript_text)
        transcript_status.setObjectName("HiringV2TranscriptStatus")
        capture_layout.addWidget(transcript_status)
        autosave = self._label("Autosave on")
        autosave.setObjectName("HiringV2AutosaveStatus")
        capture_layout.addWidget(autosave)
        background, foreground = SEMANTIC_COLORS[capture_state]
        capture.setStyleSheet(
            f"QFrame#HiringV2CaptureBar {{ background: {background}; color: {foreground}; "
            "border: 1px solid #cbd5e1; border-radius: 8px; }}"
        )
        header_layout.addWidget(capture)
        layout.addWidget(header)
        recording_warning_text = self._recording_warning_text()
        if recording_warning_text:
            recording_warning_label = self._label(recording_warning_text)
            recording_warning_label.setObjectName("PySideRecordingWarning")
            layout.addWidget(recording_warning_label)
        split = self.QtWidgets.QHBoxLayout()
        rail = self.QtWidgets.QListWidget()
        rail.setObjectName("HiringV2QuestionRail")
        rail.setMaximumWidth(190)
        workflow = self.session._workflow_items() if self.session is not None else [item]
        for index, question in enumerate(workflow):
            answer = self.session.answers.get(question.question_id, {}) if self.session is not None else {}
            if index == current_index:
                prefix, state = "●", "active"
            elif answer.get("skipped"):
                prefix, state = "↷", "warning"
            elif index < current_index:
                prefix, state = "✓", "success"
            else:
                prefix, state = "○", "neutral"
            rail_item = self.QtWidgets.QListWidgetItem(f"{prefix} {index + 1}. {question.title}")
            rail_item.setData(self.QtCore.Qt.ItemDataRole.UserRole, state)
            rail_item.setToolTip(f"{question.title}: {state}")
            rail.addItem(rail_item)
        rail.setCurrentRow(current_index)
        rail.setFocusPolicy(self.QtCore.Qt.FocusPolicy.NoFocus)
        split.addWidget(rail)
        left, left_layout = self._surface()
        left_layout.addWidget(self._label(item.title, "SectionTitle"))
        left_layout.addWidget(self._label(item.prompt))
        if item.kind == "qualification":
            self._render_qualification_fields(left_layout)
        self._render_structured_live_response(left_layout, item)
        if item.followups:
            followups = self.QtWidgets.QListWidget()
            followups.addItems(item.followups)
            left_layout.addWidget(self._label("Follow-up prompts"))
            left_layout.addWidget(followups)
        notes = self.QtWidgets.QTextEdit()
        notes.setPlaceholderText("Type optional notes here...")
        self.live_notes = notes
        stored_answer = self.session.answers.get(item.question_id, {}) if self.session is not None else {}
        notes_label = "Imported Answer Transcript" if stored_answer.get("imported_transcript") else "Notes & evidence"
        left_layout.addWidget(self._label(notes_label))
        left_layout.addWidget(notes, 1)
        split.addWidget(left, 2)

        right, right_layout = self._surface()
        right_layout.addWidget(self._label("Score", "SectionTitle"))
        self.score_group = self.QtWidgets.QButtonGroup()
        for card in item.score_cards:
            row = self.QtWidgets.QFrame()
            row.setObjectName("HiringV2RubricCard")
            row_layout = self.QtWidgets.QVBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            radio = self.QtWidgets.QRadioButton(f"{card.label}  {card.description.split('.', 1)[0]}")
            radio.toggled.connect(lambda _checked, question=item: self._update_live_next_enabled(question))
            option_text = self._label(card.description, "ScoreOptionText")
            option_text.setWordWrap(True)
            option_text.hide()
            expand = self.QtWidgets.QToolButton()
            expand.setText("Show details")
            expand.setCheckable(True)
            expand.toggled.connect(option_text.setVisible)
            expand.toggled.connect(lambda checked, button=expand: button.setText("Hide details" if checked else "Show details"))
            rubric_header = self.QtWidgets.QHBoxLayout()
            rubric_header.addWidget(radio, 1)
            rubric_header.addWidget(expand)
            row_layout.addLayout(rubric_header)
            row_layout.addWidget(option_text)
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

    def _render_mockup_live_page(self, layout: Any, item: FlowQuestion, current_index: int) -> None:
        if self.session is None:
            return
        workflow = self.session._workflow_items()
        stages = derive_live_stages(
            [LiveQuestionSpec(question.question_id, question.kind) for question in workflow],
            current_index=current_index,
        )
        answer = self.session.answers.get(item.question_id, {})
        active_stage = next(stage for stage in stages if stage.first_index <= current_index <= stage.last_index)
        structured_widget = None
        self.live_structured_response = None
        if item.kind == "qualification":
            structured_widget = self.QtWidgets.QWidget()
            structured_widget.setObjectName("LiveQualificationFields")
            structured_layout = self.QtWidgets.QVBoxLayout(structured_widget)
            structured_layout.setContentsMargins(0, 0, 0, 0)
            self._render_qualification_fields(structured_layout)
        elif item.kind == "custom":
            candidate_widget = self.QtWidgets.QWidget()
            candidate_layout = self.QtWidgets.QVBoxLayout(candidate_widget)
            candidate_layout.setContentsMargins(0, 0, 0, 0)
            self._render_structured_live_response(candidate_layout, item)
            if self.live_structured_response is not None:
                structured_widget = candidate_widget
        page_title = {
            "intro": "Live Interview Introduction Script",
            "qualification": "Live Interview Candidate Qualifications",
            "trait": "Live Interview Scored Question and Rating",
        }.get(item.kind, "Live Interview Availability & Pay" if active_stage.key == "availability" else "Live Interview Non-Scored Questions")
        subtitle = {
            "intro": "Guide the candidate through the introduction and confirm they're ready to continue.",
            "qualification": "Capture the candidate's background and early childhood education qualifications.",
            "trait": "Rate the candidate's response using the configured scoring anchors.",
        }.get(item.kind, "Answer a few questions to give the interviewer more insight into the candidate's experience and approach.")
        is_last = current_index == len(workflow) - 1
        page = LiveInterviewPage(
            QtCore=self.QtCore,
            QtWidgets=self.QtWidgets,
            callbacks=LiveInterviewCallbacks(
                back=self._go_back_live_question,
                next=self._finalize_from_live_question if is_last else self._save_and_next,
                exit=self._exit_live_interview,
                skip=self._skip_live_question,
                edit_transcript=self._edit_live_transcript,
                view_anchor=self._show_live_rating_anchor,
            ),
        )
        root, footer_widget = page.render(
            LiveInterviewViewModel(
                kind=item.kind,
                page_title=page_title,
                page_subtitle=subtitle,
                candidate_name=self.session.candidate_name,
                school=self.session.school,
                position=self._session_position_label(),
                stage_label=active_stage.label,
                current_index=current_index,
                total_steps=len(workflow),
                stages=stages,
                prompt=item.prompt,
                question_title=item.title,
                group_question_index=current_index - active_stage.first_index + 1,
                group_question_count=active_stage.last_index - active_stage.first_index + 1,
                transcript=self.session.live_transcript(current_index),
                notes=str(answer.get("notes", "") or ""),
                quick_actions=frozenset(str(value) for value in answer.get("quick_actions", []) or []),
                structured_widget=structured_widget,
                priority=item.priority,
                weight=item.weight,
                rating_options=tuple(
                    LiveRatingOption(
                        score=int(card.label),
                        description=card.description,
                        sample_answer=card.sample_answer,
                    )
                    for card in item.score_cards
                ),
                selected_score=str(answer.get("score", "") or ""),
                is_last=is_last,
                recording_active=self.recording_session is not None,
                transcript_active=self.recording_session is not None and not bool(self._recording_warning_text()),
                audio_source=str(self.recording_system_device or "Default capture device"),
                warning=self._recording_warning_text(),
                intro_actions=frozenset(str(value) for value in answer.get("quick_actions", []) or []),
            )
        )
        layout.addWidget(root, 1)
        footer = getattr(self, "live_footer_layout", None)
        if footer is not None:
            self._clear_layout(footer)
            footer.addWidget(footer_widget)
        self.live_page = page
        self.live_next_button = footer_widget.findChild(self.QtWidgets.QPushButton, "LiveInterviewPrimaryAction")
        if page.notes_editor is not None:
            self.live_notes = page.notes_editor
        elif hasattr(self, "live_notes"):
            del self.live_notes
        if page.rating_group is not None:
            self.score_group = page.rating_group
        elif hasattr(self, "score_group"):
            del self.score_group
        self.QtCore.QTimer.singleShot(0, self._apply_responsive_layout)

    def _show_live_rating_anchor(self, score: int) -> None:
        item = self._active_question()
        if item is None or item.kind != "trait":
            return
        card = next((option for option in item.score_cards if int(option.label) == int(score)), None)
        if card is None:
            return
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("LiveRatingAnchorDialog")
        dialog.setWindowTitle(f"Rating {score} anchor")
        dialog.resize(600, 360)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        heading = self._label(f"Rating {score} anchor", "SectionTitle")
        layout.addWidget(heading)
        descriptor = self._label(card.description)
        descriptor.setWordWrap(True)
        layout.addWidget(descriptor)
        layout.addWidget(self._label("Sample answer", "SectionTitle"))
        sample = self._label(card.sample_answer or "No sample answer configured.")
        sample.setWordWrap(True)
        layout.addWidget(sample, 1)
        close = self._primary_button("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _edit_live_transcript(self) -> None:
        if self.session is None or getattr(self, "live_page", None) is None:
            return
        flow_index = self.session.current_index
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("LiveTranscriptEditor")
        dialog.setWindowTitle("Edit transcript")
        dialog.resize(620, 360)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(self._label("Edit the candidate transcript for this question.", "SectionTitle"))
        editor = self.QtWidgets.QTextEdit()
        editor.setObjectName("LiveTranscriptEditorText")
        editor.setPlainText(self.session.live_transcript(flow_index))
        layout.addWidget(editor, 1)
        actions = self.QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        cancel = self.QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        save = self._primary_button("Save")
        save.setObjectName("LiveTranscriptEditorSave")
        save.clicked.connect(dialog.accept)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.session.replace_live_transcript(flow_index, editor.toPlainText())
        self.live_page.update_transcript(self.session.live_transcript(flow_index))

    def _render_structured_live_response(self, layout: Any, item: FlowQuestion) -> None:
        prompt = item.prompt.casefold()
        self.live_structured_response = None
        if "full-time or part-time" in prompt:
            field = self.QtWidgets.QComboBox()
            field.addItems(["", "Full-time", "Part-time", "Flexible"])
            layout.addWidget(self._labeled_live_field("Employment preference", field))
            self.live_structured_response = ("Employment preference", field)
        elif "hours" in prompt and "not available" in prompt:
            field = self.QtWidgets.QLineEdit()
            field.setPlaceholderText("Example: weekdays before 7:00 AM")
            layout.addWidget(self._labeled_live_field("Unavailable hours", field))
            self.live_structured_response = ("Unavailable hours", field)
        elif "pay" in prompt and "looking" in prompt:
            container = self.QtWidgets.QWidget()
            row = self.QtWidgets.QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            pay = self.QtWidgets.QDoubleSpinBox()
            pay.setRange(0, 500)
            pay.setPrefix("$")
            pay.setSuffix(" / hour")
            negotiable = self.QtWidgets.QComboBox()
            negotiable.addItems(["Negotiability unknown", "Negotiable", "Not negotiable"])
            row.addWidget(pay)
            row.addWidget(negotiable)
            layout.addWidget(self._labeled_live_field("Requested compensation", container))
            self.live_structured_response = ("Requested compensation", (pay, negotiable))
        elif "when could you start" in prompt:
            field = self.QtWidgets.QDateEdit()
            field.setCalendarPopup(True)
            field.setDisplayFormat("MM/dd/yyyy")
            field.setDate(self.QtCore.QDate.currentDate())
            layout.addWidget(self._labeled_live_field("Available start date", field))
            self.live_structured_response = ("Available start date", field)

    def _labeled_live_field(self, label: str, field: Any) -> Any:
        panel, panel_layout = self._surface()
        panel.setObjectName("HiringV2StructuredResponseCard")
        panel_layout.addWidget(self._label(label, "SectionTitle"))
        panel_layout.addWidget(field)
        return panel

    def _structured_live_notes(self) -> str:
        response = getattr(self, "live_structured_response", None)
        if response is None:
            return ""
        label, field = response
        if isinstance(field, tuple):
            pay, negotiable = field
            return f"{label}: ${pay.value():.2f}/hour; {negotiable.currentText()}"
        if isinstance(field, self.QtWidgets.QComboBox):
            value = field.currentText().strip()
        elif isinstance(field, self.QtWidgets.QDateEdit):
            value = field.date().toString("yyyy-MM-dd")
        else:
            value = field.text().strip()
        return f"{label}: {value}" if value else ""

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
        editor = CandidateQualificationEditor(
            QtWidgets=self.QtWidgets,
            object_prefix="LiveInterview",
            values=stored,
        )
        self.live_qualification_editor = editor
        self.qualification_has_degree = editor.has_degree
        self.qualification_degree_type = editor.degree_type
        self.qualification_degree_in_ece = editor.degree_in_ece
        self.qualification_ece_units = editor.ece_units
        self.qualification_infant_toddler = editor.infant_toddler
        self.qualification_total_units = editor.total_units
        self.qualification_years = editor.years_experience
        self.qualification_status_label = self._label("")
        fields_layout.addWidget(editor.widget)
        fields_layout.addWidget(self.qualification_status_label)
        layout.addWidget(fields)

    def _collect_qualification_from_fields(self) -> dict[str, Any] | None:
        try:
            qualification = self.live_qualification_editor.validated_values()
        except ValueError as exc:
            self.qualification_status_label.setText(str(exc))
            return None
        self.qualification_status_label.setText("")
        return qualification

    def _render_review_page(self) -> None:
        layout = getattr(self, "review_layout", None)
        if layout is None:
            return
        self._clear_layout(layout)
        if self.session is not None:
            state = (
                CompletionState.PROCESSING
                if self._pyside_finalize_running
                else CompletionState.FAILED
                if self._completed_finalize_error
                else CompletionState.COMPLETE
                if self._review_history_id
                else CompletionState.PROCESSING
            )
            workflow_items = self.session._workflow_items()
            scoring = self._review_scoring()
            transcripts = {
                index: self.session.flow_candidate_transcripts.get(index, "") or self.session.live_transcript(index)
                for index in range(len(workflow_items))
            }
            page = CompletedInterviewPage(
                QtCore=self.QtCore,
                QtWidgets=self.QtWidgets,
                callbacks=CompletedInterviewCallbacks(
                    back=self._back_to_last_completed_question,
                    open_report=self._open_completed_candidate_report,
                    export=self._show_completed_export_menu,
                    finish=self._finish_completed_interview,
                    retry=self._generate_interview_notes_from_session,
                    edit_question=self._edit_completed_question,
                ),
            )
            self.completed_interview_page = page
            layout.addWidget(
                page.render(
                    build_completed_interview_view_model(
                        candidate_name=self.session.candidate_name,
                        school=self.session.school,
                        position=self._session_position_label(),
                        workflow=workflow_items,
                        answers=self.session.answers,
                        transcripts=transcripts,
                        scoring=scoring,
                        completion_state=state,
                        warning=self._completed_finalize_error if state is CompletionState.FAILED else "",
                    )
                )
            )
            layout.addStretch(1)
            self.QtCore.QTimer.singleShot(0, self._reset_completed_overview_scroll)
            return
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
        recording_warning_text = self._recording_warning_text()
        if recording_warning_text:
            recording_warning_label = self._label(recording_warning_text)
            recording_warning_label.setObjectName("PySideRecordingWarning")
            summary_layout.addWidget(recording_warning_label)
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
        self.review_question_table = question_table
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
        table_toggle = self.QtWidgets.QToolButton()
        table_toggle.setText("Show full score & transcript review")
        table_toggle.setCheckable(True)
        table_toggle.setObjectName("HiringV2CloseoutDetailsToggle")
        question_table.hide()
        table_toggle.toggled.connect(question_table.setVisible)
        table_toggle.toggled.connect(
            lambda checked: table_toggle.setText(
                "Hide full score & transcript review" if checked else "Show full score & transcript review"
            )
        )
        summary_layout.addWidget(self._label("Question Score Review", "SectionTitle"))
        summary_layout.addWidget(table_toggle)
        summary_layout.addWidget(question_table)
        summary_layout.addWidget(self._label("Send candidate to director interview if required by your hiring workflow."))
        actions = self.QtWidgets.QHBoxLayout()
        apply_scores_button = self.QtWidgets.QPushButton("Update Scores")
        apply_scores_button.setObjectName("PySideReviewApplyScoresButton")
        apply_scores_button.setEnabled(self._review_score_dirty)
        apply_scores_button.clicked.connect(self._apply_review_score_updates)
        self.review_apply_scores_button = apply_scores_button
        actions.addWidget(apply_scores_button)
        home_button = self.QtWidgets.QPushButton("Home")
        home_button.clicked.connect(self._show_hiring_pipeline)
        actions.addWidget(home_button)
        summary_layout.addLayout(actions)
        self.review_status_label = self._label("")
        summary_layout.addWidget(self.review_status_label)
        layout.addWidget(summary)
        layout.addStretch(1)

    def _edit_completed_question(self, question_id: str) -> None:
        if self.session is None or self._pyside_finalize_running:
            return
        workflow = self.session._workflow_items()
        match = next(
            ((index, item) for index, item in enumerate(workflow) if item.question_id == str(question_id)),
            None,
        )
        if match is None:
            return
        flow_index, item = match
        answer = dict(self.session.answers.get(item.question_id, {}) or {})
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setObjectName("CompletedQuestionDetailDialog")
        dialog.setWindowTitle(item.title or "Interview response")
        dialog.resize(720, 620)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        prompt = self.QtWidgets.QLabel(item.prompt)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        layout.addWidget(self._label("Candidate Transcript", "SectionTitle"))
        transcript = self.QtWidgets.QTextEdit()
        transcript.setObjectName("CompletedQuestionTranscriptEdit")
        transcript.setPlainText(self.session.live_transcript(flow_index) or self.session.flow_candidate_transcripts.get(flow_index, ""))
        layout.addWidget(transcript)
        layout.addWidget(self._label("Interviewer Notes", "SectionTitle"))
        notes = self.QtWidgets.QTextEdit()
        notes.setObjectName("CompletedQuestionNotesEdit")
        notes.setPlainText(str(answer.get("notes", "") or ""))
        layout.addWidget(notes)
        rating = None
        anchor = None
        if item.kind == "trait":
            rating_row = self.QtWidgets.QHBoxLayout()
            rating_row.addWidget(self.QtWidgets.QLabel("Rating"))
            rating = self.QtWidgets.QSpinBox()
            rating.setObjectName("CompletedQuestionRatingEdit")
            rating.setRange(1, 5)
            rating.setValue(_coerce_session_score(answer.get("score")) or 1)
            rating_row.addWidget(rating)
            rating_row.addStretch(1)
            layout.addLayout(rating_row)
            anchor = self.QtWidgets.QLabel()
            anchor.setObjectName("CompletedQuestionRatingAnchor")
            anchor.setWordWrap(True)
            layout.addWidget(anchor)

            def update_anchor(value: int) -> None:
                card = next((option for option in item.score_cards if int(option.label) == int(value)), None)
                anchor.setText(
                    "\n\n".join(
                        value
                        for value in (
                            str(card.description if card is not None else ""),
                            f"Sample answer: {card.sample_answer}" if card is not None and card.sample_answer else "",
                        )
                        if value
                    )
                )

            rating.valueChanged.connect(update_anchor)
            update_anchor(rating.value())
        quick_actions = set(str(value) for value in answer.get("quick_actions", []) or [])
        flags: list[tuple[Any, str]] = []
        flag_options = (
            (
                ("CompletedQuestionNeedsFollowUp", "Needs follow-up", "Needs follow-up"),
                ("CompletedQuestionNoExample", "No example after follow-ups", "Candidate gave no example"),
                ("CompletedQuestionDisqualifier", "Absolute disqualifier", "Disqualifier observed"),
            )
            if item.kind == "trait"
            else (("CompletedQuestionMarkImportant", "Mark as important", "Mark as important"),)
        )
        for object_name, visible, canonical in flag_options:
            checkbox = self.QtWidgets.QCheckBox(visible)
            checkbox.setObjectName(object_name)
            checkbox.setChecked(canonical in quick_actions)
            flags.append((checkbox, canonical))
            layout.addWidget(checkbox)
        buttons = self.QtWidgets.QDialogButtonBox()
        cancel = buttons.addButton("Cancel", self.QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        save = buttons.addButton("Save", self.QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        cancel.setObjectName("CompletedQuestionCancel")
        save.setObjectName("CompletedQuestionSave")
        cancel.clicked.connect(dialog.reject)
        save.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != self.QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.session.replace_live_transcript(flow_index, transcript.toPlainText())
        self.session.flow_candidate_transcripts[flow_index] = self.session.live_transcript(flow_index)
        answer.update(
            {
                "kind": item.kind,
                "title": item.title,
                "prompt": item.prompt,
                "notes": notes.toPlainText(),
                "quick_actions": [canonical for checkbox, canonical in flags if checkbox.isChecked()],
            }
        )
        if rating is not None:
            answer["score"] = str(rating.value())
            answer.pop("skipped", None)
        self.session.answers[item.question_id] = answer
        self.session.save_draft()
        self.session_answers = dict(self.session.answers)
        self._completed_artifacts_dirty = True
        self._generate_interview_notes_from_session()
        self._render_review_page()

    def _back_to_last_completed_question(self) -> None:
        if self.session is None or self._pyside_finalize_running or not self._review_history_id:
            return
        workflow = self.session._workflow_items()
        if not workflow:
            return
        self.recording_session = None
        self._stop_live_capture_monitor()
        self.session.current_index = len(workflow) - 1
        self.session_index = self.session.current_index
        self.session.save_draft()
        self._render_live_question_page()
        self.interview_tabs.setCurrentIndex(_INTERVIEW_LIVE_TAB_INDEX)
        self._set_hiring_focus_mode(True)

    def _open_completed_candidate_report(self) -> None:
        if not self._review_history_id or self.session is None or self._pyside_finalize_running:
            return
        host = getattr(self, "staffing_v2_host", None)
        if host is None:
            self.QtWidgets.QMessageBox.warning(self.window, "Candidate Report", "Candidate report is not available yet.")
            return
        host.open_candidate_report(self._review_history_id, self.session.school)

    def _show_completed_export_menu(self) -> None:
        if self.session is None or self._pyside_finalize_running or not self._review_history_id:
            return
        menu = self.QtWidgets.QMenu()
        menu.setObjectName("CompletedInterviewExportMenu")
        menu.addAction("Word report (.docx)", self._export_completed_word_report)
        menu.addAction("PDF report (.pdf)", self._export_completed_pdf_report)
        menu.addAction("Transcript (.txt)", self._export_completed_transcript)
        self.completed_export_menu = menu
        menu.popup(self.QtGui.QCursor.pos())

    def _completed_history_row(self) -> dict[str, Any]:
        if not self._review_history_id:
            return {}
        return next(
            (
                row
                for row in self.history_store.load()
                if self.history_store.build_row_key(row) == self._review_history_id
            ),
            {},
        )

    def _completed_report_path(self) -> Path | None:
        row = self._completed_history_row()
        value = str(
            row.get("saved_report_path")
            or row.get("report_path")
            or row.get("interview_notes_path")
            or ""
        ).strip()
        path = Path(value) if value else None
        return path if path is not None and path.is_file() else None

    def _export_completed_word_report(self) -> None:
        source = self._completed_report_path()
        if source is None:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", "Word report is not available yet.")
            return
        destination, _selected = self.QtWidgets.QFileDialog.getSaveFileName(
            self.window,
            "Export Word Interview Report",
            source.name,
            "Word Documents (*.docx)",
        )
        if not destination:
            return
        target = Path(destination).with_suffix(".docx")
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", f"Could not export Word report: {exc}")

    def _export_completed_pdf_report(self) -> None:
        source = self._completed_report_path()
        if source is None:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", "Word report is not available yet.")
            return
        generated = _ensure_offer_pdf_path(str(source))
        if not generated:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", "PDF conversion failed. Confirm Microsoft Word is installed.")
            return
        destination, _selected = self.QtWidgets.QFileDialog.getSaveFileName(
            self.window,
            "Export PDF Interview Report",
            source.with_suffix(".pdf").name,
            "PDF Files (*.pdf)",
        )
        if not destination:
            return
        try:
            shutil.copy2(Path(generated), Path(destination).with_suffix(".pdf"))
        except OSError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", f"Could not export PDF report: {exc}")

    def _export_completed_transcript(self) -> None:
        if self.session is None:
            return
        destination, _selected = self.QtWidgets.QFileDialog.getSaveFileName(
            self.window,
            "Export Candidate Transcript",
            f"{self.session.candidate_name} - transcript.txt",
            "Text Files (*.txt)",
        )
        if not destination:
            return
        workflow = self.session._workflow_items()
        model = build_completed_interview_view_model(
            candidate_name=self.session.candidate_name,
            school=self.session.school,
            position=self._session_position_label(),
            workflow=workflow,
            answers=self.session.answers,
            transcripts={
                index: self.session.flow_candidate_transcripts.get(index, "") or self.session.live_transcript(index)
                for index in range(len(workflow))
            },
            scoring=self._review_scoring(),
            completion_state=CompletionState.COMPLETE,
        )
        try:
            Path(destination).with_suffix(".txt").write_text(build_completed_transcript_export(model), encoding="utf-8")
        except OSError as exc:
            self.QtWidgets.QMessageBox.warning(self.window, "Export Interview", f"Could not export transcript: {exc}")

    def _finish_completed_interview(self) -> None:
        if self.session is None or self._pyside_finalize_running or self._completed_finalize_error:
            return
        if self.session.review_summary().missing_scores:
            return
        draft_path = Path(self.session.draft_path)
        try:
            draft_path.unlink(missing_ok=True)
        except OSError:
            self.QtWidgets.QMessageBox.warning(
                self.window,
                "Save & Finish",
                "The completed interview was saved, but its local draft could not be removed. "
                "Try Save & Finish again.",
            )
            return
        self._stop_live_capture_monitor()
        self.recording_session = None
        self.session = None
        self.session_index = 0
        self.session_answers = {}
        self._review_history_id = ""
        self._completed_artifacts_dirty = False
        self._set_hiring_focus_mode(False)
        self._reset_new_interview_setup()
        self.interview_tabs.setCurrentIndex(_INTERVIEW_HOME_TAB_INDEX)
        dashboard = getattr(self, "staffing_v2_dashboard", None)
        if dashboard is not None and "interviews" in dashboard.external_pages:
            dashboard.show_external_page("interviews")
        router = getattr(self, "hiring_v2_router", None)
        if router is not None:
            router.show_interview()

    def _update_review_rating(self, question_id: str, value: int) -> None:
        if self.session is None:
            return
        try:
            self.session.update_review_score(question_id, value)
        except ValueError as exc:
            self.review_status_label.setText(str(exc))
            return
        self.session_answers = dict(self.session.answers)
        self._review_score_dirty = True
        self._render_review_page()

    def _review_scoring(self) -> dict[str, Any]:
        if self.session is None:
            return {}
        adapter = _PySideFinalizeAdapter(self.session, base_dir=DEFAULT_BASE_DIR, history_path=INTERVIEW_HISTORY_PATH)
        return ScoringEngine.evaluate(adapter._rubric_with_question_overrides(), adapter.state.track, adapter.state.trait_inputs)

    def _review_history_key(self) -> str:
        if self._review_history_id:
            return self._review_history_id
        if self.session is None:
            return ""
        rows = self.history_store.load()
        for row in reversed(rows):
            if str(row.get("candidate_name") or "").strip() != self.session.candidate_name:
                continue
            if str(row.get("interview_date") or "").strip() != self.session.interview_date:
                continue
            key = self.history_store.build_row_key(row)
            if key:
                self._review_history_id = key
                return key
        return ""

    def _session_position_label(self) -> str:
        if self.session is None:
            return ""
        flow = self.model.flows.get(self.session.track_key)
        return flow.label if flow is not None else self.session.track_key

    def _sync_review_score_staffing_referral(self, history_id: str, scoring: dict[str, Any]) -> None:
        if self.session is None:
            return
        outcome = _director_referral_outcome(str(scoring.get("outcome", "") or ""))
        candidate_email, candidate_phone = self._current_session_candidate_contact()
        payload = {
            "history_id": history_id,
            "candidate_name": self.session.candidate_name,
            "school": self.session.school,
            "position": self._session_position_label(),
            "interviewer_rating": _director_referral_rating(str(scoring.get("percent_of_max_label") or scoring.get("percent_of_max") or "")),
            "interview_date": self.session.interview_date,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "referral_date": self.session.interview_date,
        }
        if outcome:
            _append_staffing_referral_queue({**payload, "interviewer_outcome": outcome})
        else:
            _append_staffing_referral_dismissal_queue(payload)
        if (
            getattr(self, "staffing_v2_dashboard", None) is not None
            and self.director_staffing_school
            and self.director_staffing_school == self.session.school
        ):
            self._poll_staffing_referral_queue()

    def _apply_review_score_updates(self) -> None:
        if self.session is None:
            return
        history_id = self._review_history_key()
        if not history_id:
            self.review_status_label.setText("Scores not updated: finalized history row is not ready.")
            return
        try:
            scoring = self._review_scoring()
            outcome = str(scoring.get("outcome", "") or "Incomplete")
            percent = scoring.get("percent_of_max", 0)
            percent_label = str(scoring.get("percent_of_max_label") or f"{percent}%")
            review_scores = {
                question_id: str(answer.get("score") or "")
                for question_id, answer in self.session.answers.items()
                if isinstance(answer, dict) and str(answer.get("kind") or "") == "trait"
            }
            updated = self.history_store.update_row(
                history_id,
                {
                    "interview_score": percent,
                    "score": percent_label,
                    "percent_of_max": percent,
                    "percent_of_max_label": percent_label,
                    "determination": outcome,
                    "outcome": outcome,
                    "status": outcome,
                    "interview_status": outcome,
                    "next_action": _next_action_for_outcome(outcome),
                    "review_scores": review_scores,
                    "scoring": scoring,
                    "review_score_updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                },
            )
            if not updated:
                self.review_status_label.setText("Scores not updated: finalized history row is not ready.")
                return
            self._sync_review_score_staffing_referral(history_id, scoring)
        except Exception:
            self.review_status_label.setText("Scores not updated. Review the finalized history row and try again.")
            return
        self._review_score_dirty = False
        self._reload_history_model()
        self._render_review_page()
        self.review_status_label.setText(f"Scores updated: {percent_label} {outcome}.")

    def _refresh_review_transcript_cells(self) -> None:
        if getattr(self, "completed_interview_page", None) is not None:
            self._render_review_page()
            return
        table = self.review_question_table
        if self.session is None or table is None:
            return
        for row in range(table.rowCount()):
            transcript_text = str(self.session.flow_candidate_transcripts.get(row, "") or "").strip()
            if not transcript_text:
                recording = self.session.flow_recordings.get(row, {}) or {}
                transcript_text = str(recording.get("candidate_transcript") or "").strip()
            item = table.item(row, 3)
            if item is None:
                item = self.QtWidgets.QTableWidgetItem("")
                table.setItem(row, 3, item)
            item.setText(transcript_text or "Not generated")

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
            self._set_review_status("Start an interview before generating notes.")
            return
        if self._pyside_finalize_running:
            self._set_review_status("Finalizing interview. Recording and notes are still processing.")
            return
        self._pyside_finalize_running = True
        self._completed_finalize_error = ""
        self._show_pyside_finalize_progress("Preparing finalize")
        self._set_review_status("Finalizing interview. Recording and notes are processing in the background.")
        results: queue.Queue[dict[str, Any]] = queue.Queue()
        session = self.session

        class _PySideLiveRefreshFinalizeGateways(FinalizeGateways):
            def persist_finalize_history(self, app: Any, context: Any, out_path: str) -> str:
                history_id = super().persist_finalize_history(app, context, out_path)
                results.put({"ok": True, "event": "history_persisted", "history_id": history_id})
                return history_id

        def _worker() -> None:
            try:
                artifact_update = bool(self._review_history_id)
                self._report_pyside_finalize_progress("Stopping recording and transcribing")
                self._stop_pyside_interview_recording()
                results.put({"ok": True, "event": "transcripts_updated"})
                self._report_pyside_finalize_progress("Building interview notes")
                if self._review_history_id:
                    result = session.update_completed_artifacts(
                        history_id=self._review_history_id,
                        base_dir=DEFAULT_BASE_DIR,
                        history_path=self.model.history_path,
                    )
                else:
                    result = session.finalize_interview(
                        base_dir=DEFAULT_BASE_DIR,
                        history_path=self.model.history_path,
                        gateways=_PySideLiveRefreshFinalizeGateways(),
                    )
                self._report_pyside_finalize_progress("Saving interview artifacts")
                results.put(
                    {
                        "ok": True,
                        "result": result,
                        "warning": self.recording_warning,
                        "artifact_update": artifact_update,
                    }
                )
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
            self._review_history_id = str(message.get("history_id") or "")
            self._reload_history_model()
            return
        if message.get("event") == "transcripts_updated":
            self._refresh_review_transcript_cells()
            return
        timer.stop()
        timer.deleteLater()
        self._pyside_finalize_running = False
        if not message.get("ok"):
            self._completed_finalize_error = (
                "Interview could not be finalized. The saved draft and provisional transcript were preserved. "
                "Retry finalization."
            )
            self._set_review_status(self._completed_finalize_error)
            self._report_pyside_finalize_progress("Interview notes not generated")
            self._refresh_pyside_finalize_progress()
            self._render_review_page()
            return
        result = message.get("result", {})
        if isinstance(result, dict):
            self._review_history_id = str(result.get("history_id") or self._review_history_id)
        output_path = result.get("out_path", "") if isinstance(result, dict) else ""
        warning_text = str(message.get("warning") or "").strip()
        warning = f" {warning_text}" if warning_text else ""
        self._set_review_status(f"Interview finalized: {output_path}{warning}")
        self._completed_finalize_error = ""
        self._completed_artifacts_dirty = False
        if not message.get("artifact_update"):
            self._prompt_candidate_contact_handoff(result)
            self._emit_pyside_rating_notification(result)
            self._record_staffing_director_referral_from_finalize_result(result)
        self._reload_history_model()
        if not message.get("artifact_update"):
            self._sync_staffing_director_referrals_from_history()
            dashboard = getattr(self, "staffing_v2_dashboard", None)
            if dashboard is not None:
                dashboard.refresh()
        self._report_pyside_finalize_progress("Interview finalized")
        self._refresh_pyside_finalize_progress()
        self._schedule_close_pyside_finalize_progress()
        self._render_review_page()

    def _prompt_candidate_contact_handoff(self, result: dict[str, Any]) -> None:
        if self.session is None or not isinstance(result, dict):
            return
        scoring = result.get("scoring", {})
        score = scoring.get("percent_of_max") if isinstance(scoring, dict) else None
        if not should_prompt_candidate_contact_handoff(score):
            return

        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("Candidate Contact Information")
        dialog.setModal(True)
        dialog.resize(680, 440)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        script = self._label(
            "I would like to have you come in for an in-person interview with our director. "
            "I am going to send you a message on Indeed with her email address. If you could "
            "please email a copy of your resume and transcript, official or un-official, to that "
            "email address along with 3-4 dates and times that work for your in-person interview, "
            "that would be great. If you have any questions in-between now and then, please send "
            "me a message on Indeed."
        )
        script.setWordWrap(True)
        layout.addWidget(script)
        form = self.QtWidgets.QFormLayout()
        email_input = self.QtWidgets.QLineEdit()
        email_input.setObjectName("CandidateContactEmail")
        phone_input = self.QtWidgets.QLineEdit()
        phone_input.setObjectName("CandidateContactPhone")
        form.addRow("Email (optional)", email_input)
        form.addRow("Phone (optional)", phone_input)
        layout.addLayout(form)
        buttons = self.QtWidgets.QDialogButtonBox(
            self.QtWidgets.QDialogButtonBox.StandardButton.Save
            | self.QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        while dialog.exec() == self.QtWidgets.QDialog.DialogCode.Accepted:
            email = email_input.text().strip()
            raw_phone = phone_input.text().strip()
            try:
                if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    raise ValueError("Enter a valid candidate email address.")
                phone = normalize_candidate_phone(raw_phone) if raw_phone else ""
                self._persist_candidate_contact(email=email, phone=phone, result=result)
                return
            except ValueError as exc:
                self.QtWidgets.QMessageBox.warning(dialog, "Candidate Contact Information", str(exc))

    def _persist_candidate_contact(self, *, email: str, phone: str, result: dict[str, Any]) -> None:
        application_id = str(getattr(self.session, "application_id", "") or "")
        service = HiringWorkflowService(HiringPipelineStore(self.model.history_path))
        application = service.store.get_application(application_id)
        candidate = service.store.get_candidate(application.candidate_id)
        service.update_candidate_profile(
            candidate.candidate_id,
            legal_name=candidate.legal_name,
            preferred_name=candidate.preferred_name,
            email=email,
            phone=phone,
        )
        history_id = str(result.get("history_id") or application.history_id or "").strip()
        if history_id:
            InterviewHistoryStore(self.model.history_path).update_row(
                history_id,
                {"candidate_email": email, "candidate_phone": phone, "email": email, "phone": phone},
            )

    def _set_review_status(self, text: str) -> None:
        label = getattr(self, "review_status_label", None)
        if label is None:
            return
        try:
            label.setText(str(text or ""))
        except RuntimeError:
            self.review_status_label = None

    def _reset_completed_overview_scroll(self) -> None:
        if not hasattr(self, "interview_tabs"):
            return
        review_page = self.interview_tabs.widget(_INTERVIEW_REVIEW_TAB_INDEX)
        scroll = review_page.findChild(self.QtWidgets.QScrollArea, "CompletedInterviewScroll")
        if scroll is not None:
            scroll.horizontalScrollBar().setValue(0)
            scroll.verticalScrollBar().setValue(0)
        outer = getattr(self, "hiring_v2_router", None)
        guide = outer.interview_widget if outer is not None else None
        if isinstance(guide, self.QtWidgets.QScrollArea):
            guide.horizontalScrollBar().setValue(0)
            guide.verticalScrollBar().setValue(0)

    def _show_pyside_finalize_progress(self, step: str) -> None:
        normalized = str(step or "").strip() or "Preparing finalize"
        self._pyside_finalize_progress_step = normalized
        self._pyside_finalize_progress_tasks = build_finalize_progress_tasks(
            normalized,
            existing_tasks=getattr(self, "_pyside_finalize_progress_tasks", []),
            queued_steps=PYSIDE_CORE_FINALIZE_PROGRESS_TASKS,
        )
        self._clear_pyside_finalize_progress_dialog()

    def _clear_pyside_finalize_progress_dialog(self) -> None:
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

    def _schedule_close_pyside_finalize_progress(self) -> None:
        return

    def _report_pyside_finalize_progress(self, step: str) -> None:
        normalized = str(step or "").strip()
        if not normalized:
            return
        self._pyside_finalize_progress_tasks = build_finalize_progress_tasks(
            normalized,
            existing_tasks=getattr(self, "_pyside_finalize_progress_tasks", []),
            queued_steps=PYSIDE_CORE_FINALIZE_PROGRESS_TASKS,
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
                        queued_steps=PYSIDE_CORE_FINALIZE_PROGRESS_TASKS,
                    )
                except queue.Empty:
                    break
        label = self.pyside_finalize_progress_label
        if label is not None and self._pyside_finalize_progress_step:
            label.setText(
                format_finalize_progress_tasks(
                    getattr(self, "_pyside_finalize_progress_tasks", []),
                    fallback=self._pyside_finalize_progress_step,
                )
            )

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
        hiring_page = getattr(self, "hiring_v2_page", None)
        if hiring_page is not None:
            hiring_page.refresh()

    def _open_history_notes(self, row: PySideHistoryRow) -> None:
        path = Path(row.notes_path)
        if not path.exists():
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

    def _emit_pyside_rating_notification(self, result: dict[str, Any]) -> None:
        scoring = result.get("scoring", {}) if isinstance(result, dict) else {}
        if not isinstance(scoring, dict):
            return
        outcome = str(scoring.get("outcome", "") or "").strip()
        score_value = scoring.get("percent_of_max", scoring.get("percent_of_max_label"))
        if not should_prompt_candidate_contact_handoff(score_value) and _coerce_history_percent(score_value) != 65:
            return
        event_type = "interview.rating.qualified"
        notification_service = self._notification_service()
        directory = getattr(notification_service, "directory", None)
        director_names = getattr(directory, "director_names", {})
        payload = {
            "candidate_name": self.session.candidate_name,
            "school": self.session.school,
            "director_name": str(director_names.get(self.session.school.casefold(), "Director")),
            "position": self.session.position,
            "interview_date": self.session.interview_date,
            "outcome": outcome,
            "score": str(scoring.get("percent_of_max_label") or scoring.get("percent_of_max") or ""),
            "interview_score": str(scoring.get("percent_of_max_label") or scoring.get("percent_of_max") or ""),
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
            notification_service.emit_event(event_type, payload, f"{key}:{event_type}")
        except Exception:
            return

    def _admin_studio_paths(self) -> AdminStudioPaths:
        return AdminStudioPaths(
            rubric_path=DEFAULT_RUBRIC_PATH,
            overrides_path=QUESTIONS_OVERRIDE_PATH,
            school_settings_path=SCHOOL_OFFER_SETTINGS_PATH,
        )

    def _staffing_v2_page(self) -> Any:
        self.staffing_store.initialize()
        self._staffing_service().replay_staged_changes()
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
        defer_director_sync = bool(self.director_staffing_mode)
        if not defer_director_sync:
            self._import_queued_staffing_director_referrals()
            self._sync_staffing_director_referrals_from_history()
        role = "director" if self.director_staffing_mode else "admin"
        host = StaffingDashboardHost(
            QtCore=self.QtCore,
            QtGui=self.QtGui,
            QtWidgets=self.QtWidgets,
            parent=self.window,
            store=self.staffing_store,
            service_factory=self._staffing_service,
            access=StaffingDashboardAccess(
                role=role,
                actor=str(os.environ.get("USERNAME") or os.environ.get("USER") or role),
                school_scope=self.director_staffing_school if role == "director" else "",
                removal_source=(
                    "director_staffing_dashboard" if role == "director" else "admin_staffing_dashboard"
                ),
            ),
            history_path=self.model.history_path,
            notification_store_path=NOTIFICATION_RULES_PATH,
            notification_service_factory=self._notification_service,
            director_referral_dismissal_callback=self._queue_director_referral_dismissals,
            rubric=self.model.rubric,
            finalized_callback=self._candidate_report_finalized if role == "admin" else None,
        )
        self.staffing_v2_host = host
        dashboard = host.page
        self.staffing_v2_dashboard = dashboard
        if role == "admin":
            self._register_hiring_v2_pages(dashboard)
            dashboard.register_settings_page(
                self._build_staffing_settings_v2_page,
                before_leave=self._before_leaving_staffing_settings,
            )
        else:
            dashboard.hide_settings_navigation()
        self._start_staffing_referral_queue_polling()
        if defer_director_sync:
            self.QtCore.QTimer.singleShot(100, self._sync_staffing_v2_director_referrals_after_first_paint)
        return dashboard.widget

    def _build_staffing_settings_v2_page(self) -> Any:
        onboarding_workspace = getattr(getattr(self, "staffing_v2_host", None), "onboarding_workspace", None)
        self.staffing_settings_v2_page = StaffingSettingsV2Page(
            QtCore=self.QtCore,
            QtGui=self.QtGui,
            QtWidgets=self.QtWidgets,
            studio=AdminStudio.load(self._admin_studio_paths()),
            email_settings_path=EMAIL_ACCOUNT_SETTINGS_PATH,
            on_email_settings_saved=self._invalidate_notification_service,
            onboarding_service=getattr(onboarding_workspace, "service", None),
        )
        return self.staffing_settings_v2_page.widget

    def _before_leaving_staffing_settings(self) -> bool:
        page = getattr(self, "staffing_settings_v2_page", None)
        return True if page is None else bool(page.request_navigation_away())

    def _request_window_close(self) -> bool:
        page = getattr(self, "staffing_settings_v2_page", None)
        allowed = True if page is None else bool(page.request_close())
        if not allowed:
            return False
        staffing_host = getattr(self, "staffing_v2_host", None)
        request_onboarding_close = getattr(staffing_host, "request_onboarding_close", None)
        if callable(request_onboarding_close) and not request_onboarding_close():
            return False
        self._save_live_snapshot_without_navigation()
        self._stop_manual_audio_preflight()
        self._stop_live_capture_monitor()
        for name in ("_pyside_intro_audio_check_timer", "_recording_preload_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            setattr(self, name, None)
        self._pyside_intro_audio_check_queue = None
        self._recording_preload_queue = None
        recording = getattr(self, "recording_session", None)
        self.recording_session = None
        self.recording_started_monotonic = None
        self.recording_base_name = ""
        stop_recording = getattr(recording, "stop", None)
        if callable(stop_recording):
            threading.Thread(target=stop_recording, daemon=True).start()
        cleanup_onboarding = getattr(staffing_host, "cleanup_onboarding", None)
        if callable(cleanup_onboarding):
            cleanup_onboarding()
        return True

    def _invalidate_notification_service(self) -> None:
        if hasattr(self, "notification_service"):
            delattr(self, "notification_service")

    def _candidate_report_finalized(self, record: Any) -> None:
        previous_history = next(
            (row for row in self.history_store.load() if self.history_store.build_row_key(row) == record.history_id),
            {},
        )
        snapshot = record.snapshot if isinstance(getattr(record, "snapshot", None), dict) else {}
        candidate = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), dict) else {}
        scoring = snapshot.get("scoring") if isinstance(snapshot.get("scoring"), dict) else {}
        questions = snapshot.get("questions") if isinstance(snapshot.get("questions"), list) else []
        flow_transcript = [
            {
                "flow_index": item.get("flow_index", index),
                "id": item.get("question_id", ""),
                "type": item.get("type", ""),
                "title": item.get("title", ""),
                "prompt": item.get("prompt", ""),
                "candidate_transcript": item.get("transcript", ""),
                "evaluator_notes": item.get("interviewer_notes", ""),
                "skipped": bool(item.get("skipped", False)),
                "skip_reason": item.get("skip_reason", ""),
            }
            for index, item in enumerate(questions)
            if isinstance(item, dict)
        ]
        updates = {
            "candidate_name": str(candidate.get("candidate_name") or candidate.get("name") or ""),
            "interview_date": str(candidate.get("interview_date") or ""),
            "school": str(candidate.get("school") or ""),
            "track": str(candidate.get("track") or ""),
            "qualification": candidate.get("qualification", {}),
            "flow_transcript": flow_transcript,
            "scoring": scoring,
            "interview_score": scoring.get("percent_of_max", 0),
            "determination": str(scoring.get("outcome") or "Incomplete"),
        }
        self.history_store.update_row(record.history_id, updates)
        rating = _director_referral_rating(str(scoring.get("percent_of_max") or ""))
        StaffingService(self.staffing_store, notification_service=self._notification_service()).reconcile_director_referral(
            history_id=record.history_id,
            candidate_name=updates["candidate_name"],
            school=updates["school"],
            position=updates["track"],
            interviewer_rating=rating,
            calculated_outcome=str(scoring.get("outcome") or ""),
            interview_date=updates["interview_date"],
        )
        previous_school = str(previous_history.get("school") or "").strip()
        current_school = str(updates["school"] or "").strip()
        calculated_outcome = str(scoring.get("outcome") or "")
        eligible_outcome = _director_referral_outcome(calculated_outcome)
        removal_schools = set()
        if previous_school and (not eligible_outcome or previous_school.casefold() != current_school.casefold()):
            removal_schools.add(previous_school)
        if current_school and not eligible_outcome:
            removal_schools.add(current_school)
        queue_events = [
            (
                "director_candidate_referral_reconciliation_removal",
                {"history_id": record.history_id, "school": target_school},
            )
            for target_school in removal_schools
        ]
        if eligible_outcome and current_school:
            queue_events.append(
                (
                    "director_candidate_referral_reconciliation",
                    {
                    "history_id": record.history_id,
                    "candidate_name": updates["candidate_name"],
                    "school": current_school,
                    "position": updates["track"],
                    "interviewer_rating": rating,
                    "interviewer_outcome": eligible_outcome,
                    "interview_date": updates["interview_date"],
                    },
                )
            )
        for operation, payload in queue_events:
            try:
                _append_staffing_referral_queue(payload, operation=operation)
            except OSError:
                continue
        self._reload_history_model()
        if getattr(self, "staffing_v2_dashboard", None) is not None:
            self.staffing_v2_dashboard.refresh()

    def _sync_staffing_v2_director_referrals_after_first_paint(self) -> None:
        if getattr(self, "_staffing_v2_director_referrals_sync_started", False):
            return
        self._staffing_v2_director_referrals_sync_started = True
        self._import_queued_staffing_director_referrals()
        self._sync_staffing_director_referrals_from_history()
        if getattr(self, "staffing_v2_dashboard", None) is not None:
            self.staffing_v2_dashboard.refresh()

    def _start_staffing_referral_queue_polling(self) -> None:
        if self._staffing_referral_queue_timer is not None:
            return
        timer = self.QtCore.QTimer(self.window)
        timer.setInterval(5000)
        timer.timeout.connect(self._poll_staffing_referral_queue)
        timer.start()
        self._staffing_referral_queue_timer = timer

    def _poll_staffing_referral_queue(self) -> None:
        imported = self._staffing_service().replay_staged_changes()
        if self.director_staffing_school:
            imported += self._import_queued_staffing_director_referrals()
        if imported and getattr(self, "staffing_v2_dashboard", None) is not None:
            self.staffing_v2_dashboard.refresh()

    def _staffing_service(self) -> StaffingService:
        role = "director" if self.director_staffing_mode else "admin"
        replica = f"director:{_staffing_school_slug(self.director_staffing_school)}" if role == "director" else "admin"
        actor = str(os.environ.get("USERNAME") or os.environ.get("USER") or role)
        publisher = f"{replica}:{_staffing_school_slug(actor) or role}"
        return StaffingService(
            self.staffing_store,
            notification_service=self._notification_service(),
            change_stage=self.staffing_change_stage,
            replica=replica,
            publisher=publisher,
            school_scope=self.director_staffing_school if role == "director" else "",
            conflict_resolver=self._resolve_staffing_change_conflict,
        )

    def _resolve_staffing_change_conflict(self, conflict: StaffingChangeConflict) -> bool:
        answer = self.QtWidgets.QMessageBox.question(
            self.window,
            "Staffing Change Conflict",
            staffing_change_conflict_message(conflict),
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == self.QtWidgets.QMessageBox.StandardButton.Yes

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
            operation = str(payload.get("_operation") or "director_candidate_referral")
            try:
                if operation == "director_candidate_referral_dismissal":
                    removed = service.dismiss_director_referral_history_ids(
                        [str(payload["history_id"])],
                        removed_by=str(payload.get("removed_by") or "unknown"),
                        removal_source=str(payload.get("removal_source") or "director_referral_queue"),
                    )
                    if not removed:
                        self.staffing_store.record_director_referral_removal_audit(
                            history_id=str(payload["history_id"]),
                            candidate_name=str(payload.get("candidate_name") or ""),
                            school=str(payload.get("school") or self.director_staffing_school),
                            removed_by=str(payload.get("removed_by") or "unknown"),
                            removal_source=str(payload.get("removal_source") or "director_referral_queue"),
                        )
                elif operation == "director_candidate_referral_reconciliation_removal":
                    self.staffing_store.remove_pending_director_referral_for_reconciliation(str(payload["history_id"]))
                elif operation == "director_candidate_referral_reconciliation":
                    service.reconcile_director_referral(
                        history_id=str(payload["history_id"]),
                        candidate_name=str(payload["candidate_name"]),
                        school=str(payload["school"]),
                        position=str(payload.get("position", "")),
                        interviewer_rating=payload.get("interviewer_rating"),
                        calculated_outcome=str(payload["interviewer_outcome"]),
                        interview_date=str(payload.get("interview_date", "")),
                        candidate_email=str(payload.get("candidate_email", "")),
                        candidate_phone=str(payload.get("candidate_phone", "")),
                    )
                else:
                    service.upsert_director_candidate_referral(
                        history_id=str(payload["history_id"]),
                        candidate_name=str(payload["candidate_name"]),
                        school=str(payload["school"]),
                        position=str(payload.get("position", "")),
                        interviewer_rating=payload.get("interviewer_rating"),
                        interviewer_outcome=str(payload["interviewer_outcome"]),
                        interview_date=str(payload.get("interview_date", "")),
                        candidate_email=str(payload.get("candidate_email", "")),
                        candidate_phone=str(payload.get("candidate_phone", "")),
                        referral_date=str(payload.get("referral_date", "")),
                        queue_on_lock=True,
                    )
            except (OSError, ValueError, StaffingEditLock, KeyError):
                continue
            imported += 1
        return imported

    def _queue_director_referral_dismissals(
        self,
        candidates: Sequence[StaffingDirectorCandidate],
        removed_by: str,
        removal_source: str,
    ) -> None:
        for candidate in candidates:
            target_path = STAFFING_DB_PATH if self.director_staffing_mode else staffing_db_path_for_school(candidate.school)
            if target_path.exists():
                target_store = StaffingStore(target_path)
                removed = StaffingService(target_store).dismiss_director_referral_history_ids(
                    [candidate.history_id],
                    removed_by=removed_by,
                    removal_source=removal_source,
                )
                if not removed:
                    target_store.record_director_referral_removal_audit(
                        history_id=candidate.history_id,
                        candidate_name=candidate.candidate_name,
                        school=candidate.school,
                        removed_by=removed_by,
                        removal_source=removal_source,
                    )
            _append_staffing_referral_dismissal_queue(
                {
                    "history_id": candidate.history_id,
                    "candidate_name": candidate.candidate_name,
                    "school": candidate.school,
                    "position": candidate.position,
                    "removed_by": removed_by,
                    "removal_source": removal_source,
                }
            )

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
                    candidate_phone=row.candidate_phone,
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
        candidate_email, candidate_phone = self._current_session_candidate_contact()
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
                    "candidate_email": candidate_email,
                    "candidate_phone": candidate_phone,
                    "referral_date": self.session.interview_date,
                }
            )
        except OSError:
            return

    def _current_session_candidate_contact(self) -> tuple[str, str]:
        application_id = str(getattr(self.session, "application_id", "") or "")
        if not application_id:
            return "", ""
        try:
            store = HiringPipelineStore(self.model.history_path)
            application = store.get_application(application_id)
            candidate = store.get_candidate(application.candidate_id)
        except ValueError:
            return "", ""
        return candidate.email, candidate.phone

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
            service.store.ensure_default_rules()
            activator = getattr(service, "activate_ready_system_rules", None)
            if callable(activator):
                activator()
            staffing_store = StaffingStore(STAFFING_DB_PATH)
            staffing_store.initialize()
            rollout_date = service.store.get_or_create_rollout_date(date.today())
            StaffingNotificationScheduler(
                staffing_store=staffing_store,
                notification_service=service,
                rollout_date=rollout_date,
                candidate_contact_resolver=self._staffing_candidate_contact,
            ).run()
            settings = getattr(service, "email_settings", None)
            if settings is not None and (not getattr(settings, "smtp_host", "") or not getattr(settings, "sender_email", "")):
                return
            runner = getattr(service, "run_due_notifications", None)
            if callable(runner):
                runner()
        except Exception:
            return

    def _staffing_candidate_contact(self, candidate_name: str, school: str) -> dict[str, str]:
        hiring_store = HiringPipelineStore(self.model.history_path)
        normalized_name = str(candidate_name or "").strip().casefold()
        normalized_school = str(school or "").strip().casefold()
        applications = hiring_store.list_applications(include_archived=True)
        for candidate in hiring_store.search_candidate_profiles(candidate_name):
            if candidate.legal_name.strip().casefold() != normalized_name:
                continue
            schools = {
                application.school.strip().casefold()
                for application in applications
                if application.candidate_id == candidate.candidate_id
            }
            if normalized_school not in schools:
                continue
            return {"email": candidate.email, "honorific": candidate.honorific}
        return {}


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
    _QtCore, _QtGui, _QtPdf, _QtPdfWidgets, QtWidgets = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    apply_staffing_v2_light_theme(QtWidgets, _QtGui, app)
    _apply_styles(app)
    active_model = model or build_interview_redesign_model()
    if director_staffing:
        active_model = build_director_staffing_model(active_model, school=director_school)
    window = PySideInterviewWindow(active_model, defer_secondary_pages=True)
    apply_staffing_app_icon(_QtGui, app, window.window)
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
