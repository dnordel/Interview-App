from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from typing import Any
from tkinter import font as tkfont

from platform_services import DEFAULT_BASE_DIR, DEFAULT_FONT_SIZE, INTRO_BODY_FONT_SPEC, INTRO_HEADING_FONT_SPEC
from scoring_reporting import default_referral_endpoint
from interview_runtime import AppSharedState, load_deepseek_prompt_templates

from .audio_runtime import AudioRuntimeController
from .dashboard_controller import DashboardController
from .finalize_pipeline import FinalizePipelineController
from .flow_controller import FlowController
from .history_controller import HistoryController
from .transcript_writer import TranscriptWriterController
from ui_composition import AppRouterPorts, UiRouter, UiShellController
from .views import CandidateSetupView, SignalReferenceView, StartScreenView


@dataclass(slots=True)
class IntroFonts:
    intro_body_font: tkfont.Font
    intro_heading_font: tkfont.Font


def build_default_settings() -> dict[str, Any]:
    return {
        "base_dir": str(DEFAULT_BASE_DIR),
        "font_size": DEFAULT_FONT_SIZE,
        "question_audio_mode": "per_question",
        "whisper_model": "large-v3",
        "whisper_device": "cuda",
        "whisper_compute_type": "float16",
        "whisper_language": "en",
        "whisper_vad_filter": True,
        "whisper_beam_size": 5,
        "whisper_temperature": 0.0,
        "whisper_fallback_model": "small",
        "whisper_runtime_mode": "preferred",
        "whisper_relaunch_notice": "",
        "transcription_max_workers": 0,
        "transcription_job_timeout_seconds": 180,
        "deepseek_summary_enabled": True,
        "deepseek_api_key": "ollama",
        "deepseek_api_base_url": "http://127.0.0.1:11434/v1",
        "deepseek_summary_model": "deepseek-r1:14b",
        "deepseek_summary_timeout_seconds": 600,
        "deepseek_prompt_templates": load_deepseek_prompt_templates(),
        "director_referral_endpoint": default_referral_endpoint(),
        "send_director_referral_on_finalize": False,
        "director_email_to": str(os.environ.get("DIRECTOR_EMAIL_TO", "")).strip(),
        "director_email_subject_template": "Director Referral: {candidate_name}",
        "director_email_body_template": (
            "Hi Director,\n\n"
            "Please review the attached referral package.\n\n"
            "Candidate: {candidate_name}\n"
            "School: {school}\n"
            "Track: {track}\n"
            "Interview date: {interview_date}\n\n"
            "Thanks."
        ),
        "offer_email_to": str(os.environ.get("OFFER_EMAIL_TO", "")).strip(),
        "offer_approval_subject_template": "Offer Approval Needed: {candidate_name}",
        "offer_approval_body_template": (
            "Hi Team,\n\n"
            "Please review and approve the attached offer letter.\n\n"
            "Candidate: {candidate_name}\n"
            "School: {school}\n"
            "Track: {track}\n"
            "Interview date: {interview_date}\n\n"
            "Thanks."
        ),
        "offer_acceptance_subject_template": "Offer Accepted: {candidate_name}",
        "offer_acceptance_body_template": (
            "Hi Team,\n\n"
            "The candidate has accepted the offer.\n\n"
            "Candidate: {candidate_name}\n"
            "School: {school}\n"
            "Track: {track}\n"
            "Interview date: {interview_date}\n\n"
            "Thanks."
        ),
        "offer_acceptance_attach_offer_file": True,
        "welcome_email_subject_template": "Welcome to {school}, {candidate_name}!",
        "welcome_email_body_template": (
            "Hi {candidate_name},\n\n"
            "Welcome to {school}! We're excited to have you join us.\n\n"
            "Please review the attached onboarding guide for next steps.\n\n"
            "Best regards,"
        ),
        "welcome_onboarding_pdf_path": "",
    }


def create_fonts(root: tk.Tk) -> IntroFonts:
    return IntroFonts(
        intro_body_font=tkfont.Font(
            root=root,
            family=INTRO_BODY_FONT_SPEC[0],
            size=INTRO_BODY_FONT_SPEC[1],
            weight=INTRO_BODY_FONT_SPEC[2],
        ),
        intro_heading_font=tkfont.Font(
            root=root,
            family=INTRO_HEADING_FONT_SPEC[0],
            size=INTRO_HEADING_FONT_SPEC[1],
            weight=INTRO_HEADING_FONT_SPEC[2],
        ),
    )


def wire_controllers(app: Any, shared_state: AppSharedState) -> None:
    app.history_controller = HistoryController(app, shared_state)
    app.audio_runtime_controller = AudioRuntimeController(app, shared_state)
    app.finalize_pipeline_controller = FinalizePipelineController(app, shared_state)
    app.flow_controller = FlowController(app, shared_state)
    app.ui_shell_controller = UiShellController(app, shared_state)
    router_ports = AppRouterPorts(app)
    app.ui_router = UiRouter(router_ports, router_ports, router_ports)
    app.transcript_writer_controller = TranscriptWriterController(app, shared_state)
    app.dashboard_controller = DashboardController(app, shared_state)


def wire_views(app: Any) -> None:
    app.start_screen_view = StartScreenView(app.page_frame, app)
    app.candidate_setup_view = CandidateSetupView(app.page_frame, app)
    app.signal_reference_view = SignalReferenceView(app.page_frame, app)
