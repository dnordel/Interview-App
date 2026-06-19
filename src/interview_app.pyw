"""
Structured Preschool Interview Tool (Offline)

Updated: Adds a full GUI Question Editor so you can:
- Reorder scored questions (traits) per track (presentation order only)
- Edit a trait's primary question text (override, without touching rubric.json)
- Add / edit / delete / reorder custom (non-scored) questions per track
- MIX scored + non-scored questions in ANY order (single unified interview flow per track)
- Ask questions in that mixed order, save in drafts, and export them to DOCX

Important constraint:
- Scored rubric "traits" are tied to scoring weights, thresholds, and track max totals.
  Deleting scored traits inside the GUI would break scoring math unless you also update rubric.json.
  Therefore, scored traits can be reordered and their displayed question text can be overridden,
  but they are NOT deletable from the GUI.

Files:
- rubric.json (required)
- disqualifier_signals.json (optional)
- question_overrides.json (auto-created, stores ordering/flow and question overrides + custom questions)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
from collections import deque
import traceback
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any, Optional

import tkinter as tk
from tkinter import font as tkfont
from tkinter import END, StringVar, filedialog, messagebox, simpledialog, ttk
from platform_services import Document
from tkcalendar import DateEntry

# =========================
# App constants and defaults
# =========================


from platform_services import (
    APP_TITLE,
    DEFAULT_BASE_DIR,
    DEFAULT_RUBRIC_PATH,
    DEFAULT_SCHOOL_OPTIONS,
    DEFAULT_SIGNALS_PATH,
    MAX_FONT_SIZE,
    MIN_FONT_SIZE,
    INTERVIEW_HISTORY_PATH,
    QUESTIONS_OVERRIDE_PATH,
    SCHOOL_INFO,
    SCHOOL_OFFER_SETTINGS_PATH,
    SCHOOL_EMAIL_TEMPLATE_SETTINGS_PATH,
    INTERVIEW_APP_SETTINGS_PATH,
    compose_intro_script,
    is_valid_date_yyyy_mm_dd,
    now_stamp,
)
from scoring_reporting import CandidateQualification
from platform_services import (
    DisqualifierSignalLibrary,
    InterviewHistoryStore,
    QuestionOverridesStore,
    RubricLoader,
    SchoolEmailTemplateStore,
    SchoolOfferSettingsStore,
    InterviewAppSettingsStore,
)
from scoring_reporting import (
    append_communication_log,
    build_director_packet,
    send_director_packet,
)
from scoring_reporting import DirectorEmailDraftError, build_mailto_url, open_outlook_draft
from scoring_reporting import build_integration_payload, serialize_integration_payload
from interview_runtime import InterviewState
from interview_runtime import InterviewSessionStore
from ui_composition import CustomQuestionScreenUI, TraitScreenUI
from scoring_reporting import is_supported_document_path, missing_required_docs
from scoring_reporting import DocxExporter, DraftManager, ReportingValidationError, ScoringEngine
from scoring_reporting import OfferInput, OfferLetterService, POSITION_OPTIONS, build_offer_filename
from onboarding_operations import JsonStore
from onboarding_operations import evaluate_onboarding_reminder_health
from onboarding_operations import TASK_STATUS_COLORS, TASK_STATUS_LABELS
from onboarding_operations import build_dashboard_today_summary
from scoring_reporting import missing_placeholder_keys, render_template
from interview_runtime import append_candidate_segment_text
from interview_runtime import InterviewSessionContext
from interview_runtime import InterviewSessionManager, SessionPayloadValidationError
from interview_runtime import (
    build_flow_time_windows,
    extract_candidate_text_from_jsonl,
    format_seconds_for_transcript,
    load_candidate_segments,
    map_segments_to_flow_indices,
)
from ui_composition import QuestionEditorWindow, SettingsWindow
from ui_composition import QuestionSettingsWindow
from platform_services import cleanup_stale_artifacts, delete_recording_artifacts
from platform_services import EVENT_INTERVIEW_FINALIZED, SCOPE_CREATED_MONTH, SCOPE_EVENT_MONTH, SUMMARY_SCOPES, UxMetricsLogger, build_monthly_summary
from platform_services import get_configured_log_path, initialize_app_logging, write_crash_report
from ui_composition import (
    MainGuiWarningPresenter,
    TRANSCRIPTION_PARTIAL_WARNING_COPY,
    create_main_gui_warning_presenter,
    present_transcription_partial_warning,
)
from interview_runtime import (
    build_transcription_log_hint,
    format_runtime_init_error_message,
    format_transcription_health_summary,
    redact_paths,
    sanitize_transcription_error_reason,
)

try:
    from interview_app import (
        AppSharedState,
        AudioRuntimeController,
        DashboardController,
        FinalizePipelineController,
        FlowController,
        HistoryActionsService,
        HistoryController,
        IntroFonts,
        LEGACY_FINALIZE_GUARDRAIL_MESSAGE,
        TranscriptWriterController,
        TranscriptionQueueState,
        BoundedTranscriptionExecutor,
        TranscriptionJobStatusEvent,
        resolve_transcription_max_workers,
        resolve_transcription_job_timeout_seconds,
        UiRouter,
        UiShellController,
        build_default_settings,
        create_fonts,
        raise_legacy_finalize_guardrail,
        validate_before_finalize as validate_before_finalize_controller,
        wire_controllers,
        wire_views,
    )
except Exception:
    import importlib.util

    _PKG_DIR = Path(__file__).with_name("interview_app")
    _SPEC = importlib.util.spec_from_file_location("interview_app_pkg", _PKG_DIR / "__init__.py")
    if _SPEC is None or _SPEC.loader is None:
        raise
    _MOD = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_MOD)
    AppSharedState = _MOD.AppSharedState
    AudioRuntimeController = _MOD.AudioRuntimeController
    DashboardController = _MOD.DashboardController
    FinalizePipelineController = _MOD.FinalizePipelineController
    FlowController = _MOD.FlowController
    HistoryActionsService = _MOD.HistoryActionsService
    HistoryController = _MOD.HistoryController
    IntroFonts = _MOD.IntroFonts
    LEGACY_FINALIZE_GUARDRAIL_MESSAGE = _MOD.LEGACY_FINALIZE_GUARDRAIL_MESSAGE
    TranscriptWriterController = _MOD.TranscriptWriterController
    TranscriptionQueueState = _MOD.TranscriptionQueueState
    BoundedTranscriptionExecutor = _MOD.BoundedTranscriptionExecutor
    TranscriptionJobStatusEvent = _MOD.TranscriptionJobStatusEvent
    resolve_transcription_max_workers = _MOD.resolve_transcription_max_workers
    resolve_transcription_job_timeout_seconds = _MOD.resolve_transcription_job_timeout_seconds
    UiRouter = _MOD.UiRouter
    UiShellController = _MOD.UiShellController
    build_default_settings = _MOD.build_default_settings
    create_fonts = _MOD.create_fonts
    raise_legacy_finalize_guardrail = _MOD.raise_legacy_finalize_guardrail
    validate_before_finalize_controller = _MOD.validate_before_finalize
    wire_controllers = _MOD.wire_controllers
    wire_views = _MOD.wire_views

from ui_composition import HistoryDataGrid
from tk_theme import COLORS, apply_professional_ops_theme


logger = logging.getLogger(__name__)

QUESTION_AUDIO_MODE_LEGACY_INCREMENTAL = "legacy_incremental"
QUESTION_AUDIO_MODE_TIMESTAMP_SLICING = "timestamp_slicing"
QUESTION_AUDIO_MODE_PER_QUESTION = "per_question"
QUESTION_AUDIO_MODES = {
    QUESTION_AUDIO_MODE_PER_QUESTION,
    QUESTION_AUDIO_MODE_LEGACY_INCREMENTAL,
    QUESTION_AUDIO_MODE_TIMESTAMP_SLICING,
}


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_outcome_label(value: Any) -> str:
    outcome = str(value or "").strip().lower()
    canonical = {
        "hire": "Hire",
        "borderline": "Borderline",
        "no hire": "No Hire",
        "no_hire": "No Hire",
        "nohire": "No Hire",
    }
    return canonical.get(outcome, str(value or "").strip())


def _split_name_parts(name: str) -> tuple[str, str]:
    parts = str(name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]

# =========================
# Tkinter GUI app
# =========================

class InterviewApp(tk.Tk):
    """Main Tkinter application."""

    def __init__(self):
        super().__init__()
        self._app_log_path = initialize_app_logging()
        # Fonts must be created after the Tk root exists (avoid 'Too early to use font').
        intro_fonts: IntroFonts = create_fonts(self)
        self.intro_body_font = intro_fonts.intro_body_font
        self.intro_heading_font = intro_fonts.intro_heading_font

        # Audio recording session (optional).
        self.recording_session: Any | None = None
        self.recording_base_name: str = ""
        self.recording_flow_idx: int | None = None
        self.recording_started_monotonic: float | None = None
        self.recording_candidate_label: str = "CANDIDATE"
        self.live_transcript_docx: Path | None = None
        self.transcript_available: bool = True
        self.transcript_warning: str = ""
        self.finalize_warning: str = ""
        self.main_warning_presenter: MainGuiWarningPresenter | None = None
        self.finalize_window: tk.Toplevel | None = None
        self.finalize_progress: ttk.Progressbar | None = None
        self.finalize_status_label: ttk.Label | None = None
        self._finalize_worker_running = False
        self._transcription_cv = threading.Condition()
        self._transcription_in_progress = False
        self._transcription_queue_state = TranscriptionQueueState()
        self._transcription_worker_started = False
        self._transcription_executor: BoundedTranscriptionExecutor | None = None
        self._transcription_max_workers = resolve_transcription_max_workers(self.settings if hasattr(self, "settings") else {})
        self._audio_state_lock = threading.Lock()
        self.interview_session_id: str = ""
        self.current_finalize_correlation_id: str = ""
        self.interview_session_store: InterviewSessionStore | None = None
        self.session_manager: InterviewSessionManager | None = None

        self.session_context = InterviewSessionContext(
            app_root=Path(__file__).resolve().parent.parent,
            default_base_dir=Path(DEFAULT_BASE_DIR),
        )

        self.shared_state = AppSharedState()
        wire_controllers(self, self.shared_state)

        self.title(APP_TITLE)
        self.minsize(1000, 700)
        self._set_initial_window_state()

        self.settings: dict[str, Any] = build_default_settings()
        self.school_options = DEFAULT_SCHOOL_OPTIONS.copy()
        self._session_status_flags: set[str] = set()
        self._trait_disclosure_state: dict[tuple[str, str], bool] = {}
        self._timestamp_slicing_notice_emitted = False
        self._session_use_default_whisper_settings = False
        self._current_question_whisper_source = "user"

        self.app_settings_store = InterviewAppSettingsStore(INTERVIEW_APP_SETTINGS_PATH)
        self.settings.update(self.app_settings_store.load())
        self._transcription_max_workers = resolve_transcription_max_workers(self.settings)

        base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        base_dir.mkdir(parents=True, exist_ok=True)
        self._startup_deleted_artifacts = cleanup_stale_artifacts(base_dir)
        self.session_manager = InterviewSessionManager(
            draft_manager=DraftManager(base_dir),
            session_store=None,
        )

        self.rubric_loader = RubricLoader(DEFAULT_RUBRIC_PATH)
        self.rubric = self.rubric_loader.data
        self.signals = DisqualifierSignalLibrary(DEFAULT_SIGNALS_PATH)

        self.qstore = QuestionOverridesStore(QUESTIONS_OVERRIDE_PATH)
        self.history_store = InterviewHistoryStore(INTERVIEW_HISTORY_PATH)
        self.school_offer_store = SchoolOfferSettingsStore(SCHOOL_OFFER_SETTINGS_PATH)
        self.school_offer_settings = self.school_offer_store.load()
        self.school_email_template_store = SchoolEmailTemplateStore(SCHOOL_EMAIL_TEMPLATE_SETTINGS_PATH)
        self.school_email_template_settings = self.school_email_template_store.load()
        self.history_sort_column = "interview_date"
        self.history_sort_desc = True
        self.history_search_var = StringVar(value="")
        self.history_rows: list[dict[str, Any]] = []
        self.history_selected_row: dict[str, Any] | None = None
        self.history_search_trace_id: str | None = None
        self.history_actions_service: HistoryActionsService | None = None
        self.state = InterviewState(interview_date=date.today().isoformat())
        self.last_finalize_result: dict[str, Any] = {}
        self.metrics_logger = UxMetricsLogger(Path(__file__).resolve().parent)
        self.monthly_metrics_scope_var = StringVar(value=SCOPE_EVENT_MONTH)
        self.monthly_metrics_month_var = StringVar(value=date.today().replace(day=1).isoformat())
        self.monthly_metrics_label: ttk.Label | None = None

        self.active_traits: list[dict[str, Any]] = []
        self.custom_questions: list[dict[str, Any]] = []
        self.active_flow: list[dict[str, Any]] = []
        self._footer_actions_by_label: dict[str, Any] = {}
        self._current_screen: Any | None = None

        self._configure_theme()
        self.ui_router.setup_shortcuts()
        self.apply_font_size(self.settings["font_size"])
        self._build_layout()
        # Ensure we stop recording cleanly if the window is closed.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_start_screen()
        self.after_idle(self._show_relaunch_whisper_notice)

    # -------------------------
    # Window state
    # -------------------------

    def _set_initial_window_state(self) -> None:
        self.update_idletasks()

        if sys.platform.startswith("win"):
            if self._try_zoomed_windows():
                return

        if sys.platform.startswith("linux"):
            if self._try_zoomed_linux():
                return

        self._set_geometry_to_screen_ratio(0.95)

    def _safe_attr(self, name: str, default: Any = None) -> Any:
        """Read optional runtime attrs without falling through Tk.__getattr__."""
        return self.__dict__.get(name, default)

    def _try_zoomed_windows(self) -> bool:
        try:
            self.state("zoomed")
            return True
        except tk.TclError:
            return False

    def _try_zoomed_linux(self) -> bool:
        try:
            self.attributes("-zoomed", True)
            return True
        except tk.TclError:
            return False

    def _set_geometry_to_screen_ratio(self, ratio: float) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if sw <= 0 or sh <= 0:
            self.geometry("1100x800")
            return

        w = max(1000, int(sw * ratio))
        h = max(700, int(sh * ratio))
        x = max(0, int((sw - w) / 2))
        y = max(0, int((sh - h) / 2))
        self.geometry(f"{w}x{h}+{x}+{y}")

    # -------------------------
    # Layout and theming
    # -------------------------

    def _build_layout(self) -> None:
        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill="x", padx=8, pady=4)

        ttk.Label(self.toolbar, text="Text Size:").pack(side="left")
        ttk.Button(self.toolbar, text="A-", command=lambda: self.adjust_font_size(-1)).pack(side="left", padx=2)

        self.font_label = ttk.Label(self.toolbar, text=str(self.settings["font_size"]))
        self.font_label.pack(side="left", padx=2)

        ttk.Button(self.toolbar, text="A+", command=lambda: self.adjust_font_size(1)).pack(side="left", padx=2)

        self.main_warning_presenter = create_main_gui_warning_presenter(self, on_dismiss=self._clear_finalize_warning)

        self.main_holder = ttk.Frame(self)
        self.main_holder.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(self.main_holder, highlightthickness=0, bg=COLORS["app_bg"])
        self.v_scroll = ttk.Scrollbar(self.main_holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.v_scroll.set)

        self.v_scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.page_frame = ttk.Frame(self.canvas)
        self.page_window = self.canvas.create_window((0, 0), window=self.page_frame, anchor="nw")

        self.page_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)

        self.footer_separator = ttk.Separator(self, orient="horizontal")
        self.footer_separator.pack(fill="x")

        self.footer = ttk.Frame(self, padding=(8, 6))
        self.footer.pack(fill="x")

        wire_views(self)

    def _configure_theme(self) -> None:
        apply_professional_ops_theme(self, font_size=int(self.settings["font_size"]))

    def show_keyboard_shortcuts_help(self) -> None:
        messagebox.showinfo(
            "Keyboard shortcuts",
            "Ctrl+N or Ctrl+Right: Next\n"
            "Ctrl+B or Ctrl+Left: Back\n"
            "Ctrl+S: Save Draft\n"
            "Ctrl+Shift+F: Finalize/Continue\n"
            "Ctrl+, : Open Settings\n"
            "Ctrl+E: Open Question Editor\n"
            "F1: Show this shortcuts help",
        )

    def _on_frame_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfig(self.page_window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-3, "units")
            return
        if getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(3, "units")
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # -------------------------
    # Font sizing
    # -------------------------

    def apply_font_size(self, size: int) -> None:
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size)))
        self.settings["font_size"] = size
        self._configure_theme()

        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                f = tkfont.nametofont(font_name)
                f.configure(size=size)
            except tk.TclError:
                pass

        if hasattr(self, "font_label"):
            self.font_label.config(text=str(size))

    def adjust_font_size(self, delta: int) -> None:
        self.apply_font_size(self.settings["font_size"] + delta)

    # -------------------------
    # Page utilities
    # -------------------------

    def scroll_top(self) -> None:
        self.canvas.yview_moveto(0)

    def clear_page(self) -> None:
        for child in self.page_frame.winfo_children():
            child.destroy()
        self.clear_footer()
        self.scroll_top()

    def clear_footer(self) -> None:
        for child in self.footer.winfo_children():
            child.destroy()
        self._footer_actions_by_label = {}

    def set_footer_actions(self, left_actions=None, right_actions=None) -> None:
        self.clear_footer()

        left = ttk.Frame(self.footer)
        left.pack(side="left")
        for label, command in (left_actions or []):
            self._footer_actions_by_label[label] = command
            style = "Danger.TButton" if label.lower() == "exit" else "Secondary.TButton"
            ttk.Button(left, text=label, command=command, style=style).pack(side="left", padx=(0, 6))

        right = ttk.Frame(self.footer)
        right.pack(side="right")
        right_items = list(right_actions or [])
        for index, (label, command) in enumerate(right_items):
            self._footer_actions_by_label[label] = command
            is_primary = index == len(right_items) - 1 or label in {"Continue", "Finalize", "Start Interview", "Generate Offer"}
            style = "Primary.TButton" if is_primary else "Secondary.TButton"
            if label.lower() == "exit":
                style = "Danger.TButton"
            ttk.Button(right, text=label, command=command, style=style).pack(side="left", padx=(6, 0))

        footer_warning = self._safe_attr("transcript_warning", "").strip()
        if footer_warning:
            ttk.Label(self.footer, text=footer_warning, foreground=COLORS["warning"]).pack(side="left", padx=(12, 0))

    # -------------------------
    # Trait order + question override helpers
    # -------------------------

    def _apply_trait_presentation_order(self, track_key: str, traits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order = self.qstore.get_trait_order(track_key)
        if not order:
            return traits

        by_id = {t["id"]: t for t in traits}
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()

        for tid in order:
            if tid in by_id:
                ordered.append(by_id[tid])
                seen.add(tid)

        for t in traits:
            if t["id"] not in seen:
                ordered.append(t)

        return ordered

    def get_primary_question_text(self, trait: dict[str, Any]) -> str:
        override = self.qstore.get_trait_question_override(trait["id"])
        return override if override else trait["primary_question"]

    def _rubric_with_question_overrides(self) -> dict[str, Any]:
        r = dict(self.rubric)
        r["trait_question_overrides"] = dict(self.qstore.data.get("trait_question_overrides", {}) or {})
        return r

    def _refresh_custom_questions(self, track_key: str) -> None:
        self.custom_questions = self.qstore.list_custom_questions(track_key)

        for q in self.custom_questions:
            qid = str(q["id"])
            qtext = str(q.get("text", "")).strip()
            self.state.custom_inputs.setdefault(qid, {"question_text": qtext, "answer": "", "skipped": False})
            self.state.custom_inputs[qid]["question_text"] = qtext

        active_ids = {str(q["id"]) for q in self.custom_questions}
        for existing_id in list(self.state.custom_inputs.keys()):
            if existing_id not in active_ids:
                del self.state.custom_inputs[existing_id]

    # -------------------------
    # Mixed flow helpers
    # -------------------------

    def _build_active_flow(self, track_key: str) -> None:
        traits_raw = self.rubric_loader.get_traits_for_track(track_key)
        traits_ordered = self._apply_trait_presentation_order(track_key, traits_raw)

        self.active_traits = traits_ordered
        self._refresh_custom_questions(track_key)

        trait_ids_in_order = [t["id"] for t in self.active_traits]
        custom_ids_in_order = [str(q["id"]) for q in self.custom_questions]

        self.active_flow = self.qstore.ensure_flow(track_key, trait_ids_in_order, custom_ids_in_order)

        for trait in self.active_traits:
            self.state.trait_inputs.setdefault(
                trait["id"],
                {
                    "raw_score": None,
                    "question_notes": "",
                    "trait_notes": "",
                    "verbatim_notes": "",
                    "absolute_disqualifier": False,
                    "skipped": False,
                    # Interview edge-case marker: candidate could not provide a behavioral example
                    # even after follow-ups. If checked, interviewer should score conservatively.
                    "no_example_after_followups": False,
                },
            )

    def _flow_len(self) -> int:
        return len(self.active_flow)

    def _get_flow_item(self, index: int) -> Optional[dict[str, Any]]:
        if 0 <= index < len(self.active_flow):
            return self.active_flow[index]
        return None

    def _trait_by_id(self, trait_id: str) -> Optional[dict[str, Any]]:
        for t in self.active_traits:
            if t["id"] == trait_id:
                return t
        return None

    def _custom_by_id(self, custom_id: str) -> Optional[dict[str, Any]]:
        for q in self.custom_questions:
            if str(q.get("id")) == str(custom_id):
                return q
        return None

    def _mark_flow_timestamp(self, flow_index: int) -> None:
        # Record when a flow item is shown, relative to recording start.
        if self.recording_session is None or self.recording_started_monotonic is None:
            return
        try:
            elapsed = max(0.0, time.monotonic() - self.recording_started_monotonic)
        except Exception:
            return
        item = self._get_flow_item(flow_index)
        if not item:
            return

        # Avoid duplicate marks when re-rendering the same screen.
        marks = self.state.flow_time_marks
        if marks and marks[-1].get("flow_index") == flow_index:
            marks[-1]["t"] = elapsed
            marks[-1]["type"] = item.get("type")
            marks[-1]["id"] = item.get("id")
            return

        marks.append(
            {
                "flow_index": flow_index,
                "t": elapsed,
                "end_t": None,
                "type": item.get("type"),
                "id": item.get("id"),
            }
        )

    def _close_flow_timestamp(self, flow_index: int) -> None:
        # Mark the end boundary for the current question when interviewer advances.
        if self.recording_session is None or self.recording_started_monotonic is None:
            return
        try:
            elapsed = max(0.0, time.monotonic() - self.recording_started_monotonic)
        except Exception:
            return

        for mk in reversed(self.state.flow_time_marks):
            if int(mk.get("flow_index", -1)) == flow_index:
                mk["end_t"] = elapsed
                return

    def _ensure_live_transcript_doc(self) -> None:
        return

    def _append_live_transcript_segment(self, flow_idx: int) -> None:
        self._resolve_flow_candidate_transcript(flow_idx)

    def _resolve_flow_candidate_transcript(self, flow_idx: int) -> str:
        idx = int(flow_idx)
        current = str(self.state.flow_candidate_transcripts.get(idx, "")).strip()
        rec = (self.state.flow_recordings or {}).get(idx)
        if not isinstance(rec, dict):
            return current

        backfilled = self._extract_candidate_transcript(rec)
        if not backfilled:
            return current
        self.state.flow_candidate_transcripts[idx] = backfilled
        rec["candidate_transcript"] = backfilled
        return backfilled

    def _rewrite_live_transcript_docx_from_flow(self, flow_transcript: list[dict[str, Any]]) -> None:
        return

    def _mark_transcript_unavailable(self, exc: Exception) -> None:
        if not self._safe_attr("transcript_available", True):
            return
        self.transcript_available = False
        self.transcript_warning = (
            "Transcript file is unavailable. Interview can continue without DOCX transcript output."
        )
        logger.warning("live_transcript_unavailable", exc_info=exc)

    def _live_transcript_doc_metadata(self, doc: Document | None) -> tuple[str, str, str, str]:
        heading = "Interview Transcript (Live)"
        candidate_name = str(self.state.candidate_name or "").strip()
        interview_date = str(self.state.interview_date or "").strip()
        school = str(self.state.school or "").strip()
        if doc is None:
            return heading, candidate_name, interview_date, school

        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            if text.startswith("Candidate Name:"):
                candidate_name = text.split(":", 1)[1].strip()
                continue
            if text.startswith("Interview Date:"):
                interview_date = text.split(":", 1)[1].strip()
                continue
            if text.startswith("School/Location:"):
                school = text.split(":", 1)[1].strip()
                continue
            heading = text
            break
        return heading, candidate_name, interview_date, school

    def _flow_transcript_question_block(self, item: dict[str, Any], idx: int) -> list[str]:
        candidate_tx = str(item.get("candidate_transcript") or "").strip()
        if item.get("type") == "trait":
            name = str(item.get("title") or item.get("id") or "").strip()
            question = str(item.get("question") or "").strip()
            evaluator_notes = str(item.get("verbatim_notes") or item.get("question_notes") or "").strip()
            answer = candidate_tx if candidate_tx else "(No candidate transcript captured)"
            lines = [f"Q{idx + 1} (Scored): {name}"]
            if question:
                lines.append(f"Question: {question}")
            lines.append(f"Answer Segment (auto-transcribed): {answer}")
            if evaluator_notes:
                lines.append(f"Evaluator Notes: {evaluator_notes}")
            return lines

        question = str(item.get("question") or "").strip()
        answer = candidate_tx if candidate_tx else "(No candidate transcript captured)"
        lines = [f"Q{idx + 1} (Custom)"]
        if question:
            lines.append(f"Question: {question}")
        lines.append(f"Answer Segment (auto-transcribed): {answer}")
        return lines

    def show_flow_screen(self, flow_index: int) -> None:
        self.ui_router.show_flow_screen(flow_index)

    def _start_question_recording_for_flow(self, flow_index: int) -> None:
        try:
            self._start_question_recording(flow_index)
        except Exception as exc:
            self._handle_question_recording_start_failure(flow_index, exc)

    def _handle_question_recording_start_failure(self, flow_index: int, exc: Exception) -> None:
        question_label = self._question_number_label(flow_index)
        self.recording_session = None
        self.recording_flow_idx = None
        self.recording_base_name = ""
        self.transcript_available = False
        self.transcript_warning = (
            "Live transcript is unavailable for this interview. Continue in fallback mode and capture notes manually."
        )
        logger.exception("question_recording_start_failed", extra={"flow_idx": flow_index})
        messagebox.showerror(
            "Live transcript unavailable",
            f"Could not start recording for {question_label}.\n\n"
            "Recovery action: Click OK to continue in fallback mode, capture notes in the question fields, "
            "and finalize without live transcript output.",
        )

    # -------------------------
    # Screens
    # -------------------------

    def show_start_screen(self) -> None:
        self.start_screen_view.render()

    def _clear_finalize_warning(self) -> None:
        self.finalize_warning = ""
        self.transcript_warning = ""
        self.set_footer_actions()

    def _show_finalize_partial_transcript_warning(self, message: str) -> None:
        normalized_message = str(message or "").strip()
        if normalized_message == TRANSCRIPTION_PARTIAL_WARNING_COPY:
            normalized_message = TRANSCRIPTION_PARTIAL_WARNING_COPY
        self.finalize_warning = normalized_message
        if not self.finalize_warning:
            return
        self.transcript_warning = self.finalize_warning
        presenter = self.__dict__.get("main_warning_presenter")
        if presenter is not None:
            if self.finalize_warning == TRANSCRIPTION_PARTIAL_WARNING_COPY:
                present_transcription_partial_warning(presenter)
            else:
                presenter.show(self.finalize_warning)
        self.set_footer_actions()

    def _latest_draft_path(self) -> Path | None:
        base_dir = Path(self.settings["base_dir"])
        drafts_dir = DraftManager(base_dir).drafts_dir
        if not drafts_dir.exists():
            return None
        candidates = [p for p in drafts_dir.glob("*.json") if p.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    def continue_last_draft(self) -> None:
        latest = self._latest_draft_path()
        if not latest:
            messagebox.showinfo("No drafts", "No saved drafts were found in your drafts folder yet.")
            return
        self._open_draft_path(latest)

    def _render_today_dashboard(self, parent: ttk.Frame) -> None:
        self.start_screen_view.render_today_dashboard(parent)

    def _render_interview_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        self.start_screen_view.render_interview_dashboard_card(parent, summary)

    def _render_onboarding_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        self.start_screen_view.render_onboarding_dashboard_card(parent, summary)

    def _render_monthly_metrics_card(self, parent: ttk.LabelFrame, onboarding_state: Any) -> None:
        self.start_screen_view.render_monthly_metrics_card(parent, onboarding_state)

    def _monthly_metric_month_choices(self) -> list[str]:
        current = date.today().replace(day=1)
        choices: list[str] = []
        cursor = current
        for _ in range(6):
            choices.append(cursor.isoformat())
            if cursor.month == 1:
                cursor = date(cursor.year - 1, 12, 1)
                continue
            cursor = date(cursor.year, cursor.month - 1, 1)
        return choices

    def _refresh_monthly_metrics_text(self, onboarding_state: Any) -> None:
        if not self.monthly_metrics_label:
            return

        scope = self.monthly_metrics_scope_var.get()
        if scope not in SUMMARY_SCOPES:
            scope = SCOPE_EVENT_MONTH

        month_text = self.monthly_metrics_month_var.get()
        try:
            month_value = date.fromisoformat(month_text)
        except ValueError:
            month_value = date.today().replace(day=1)
            self.monthly_metrics_month_var.set(month_value.isoformat())

        summary = build_monthly_summary(
            month=month_value,
            scope=scope,
            employees=onboarding_state.employees,
            events=self.metrics_logger.read_events(),
            grace_hours=24,
        )
        trend = "n/a"
        if summary.overdue_change_pct is not None:
            trend = f"{summary.overdue_change_pct:+.1f}%"

        medians = [f"{k}: {v}d" for k, v in sorted(summary.median_days_by_task_type.items())]
        if not medians:
            medians = ["No completed tasks in this scope."]

        lines = [
            f"Scope: {summary.scope} • Month: {summary.month_key}",
            f"On-time completion (24h grace): {summary.on_time_completion_pct:.1f}%",
            f"Overdue trend: {summary.overdue_count} vs prev {summary.previous_overdue_count} ({trend})",
            "Median days-to-complete by task type:",
            *[f"- {line}" for line in medians],
        ]
        self.monthly_metrics_label.configure(text="\n".join(lines))

    def _export_metrics_log(self) -> None:
        onboarding_state = self._load_onboarding_state()
        export_dir = str(onboarding_state.scheduler_settings.get("metrics_export_dir") or (Path.cwd() / "exports"))
        path = self.metrics_logger.export_events_csv(export_dir)
        messagebox.showinfo("Export Metrics", f"Metrics exported to:\n{path}")

    def _render_dashboard_actions(self, parent: ttk.LabelFrame, summary: Any) -> None:
        self.start_screen_view.render_dashboard_actions(parent, summary)
    def _open_next_critical_task(self, summary: Any) -> None:
        next_item = summary.onboarding.next_critical
        if next_item is None:
            messagebox.showinfo("Onboarding", "There are no critical onboarding tasks due right now.")
            return
        self.open_onboarding_tracker(employee_id=next_item.employee_id, urgent_only=True)

    def _run_onboarding_reminders_now(self) -> None:
        onboarding_path = Path(__file__).with_name("onboarding_app.pyw")
        if not onboarding_path.exists():
            messagebox.showerror("Onboarding app missing", f"Could not find onboarding_app.pyw at:\n{onboarding_path}")
            return

        try:
            subprocess.run(
                [sys.executable, str(onboarding_path), "--run-reminders", "--run-source", "manual_dashboard"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip() or "Unable to run reminders from dashboard."
            messagebox.showerror("Run reminders", detail)
            return

        messagebox.showinfo("Run reminders", "Reminder run completed.")

    def _render_snapshot_reminder_warning(self, parent: ttk.Frame, state: Any) -> None:
        health = evaluate_onboarding_reminder_health(state.last_reminder_run_at, state.scheduler_settings)
        warning_text = self._snapshot_warning_text(health.severity)
        if not warning_text:
            return

        banner = tk.Frame(parent, bg="#FEF3C7", highlightthickness=1, highlightbackground="#B45309")
        banner.pack(fill="x", padx=8, pady=(8, 4), anchor="w")
        tk.Label(
            banner,
            text=warning_text,
            bg="#FEF3C7",
            fg="#7C2D12",
            anchor="w",
            justify="left",
            font=("TkDefaultFont", self.settings["font_size"], "bold"),
        ).pack(fill="x", padx=8, pady=6)

    @staticmethod
    def _snapshot_warning_text(severity: str) -> str:
        if severity == "warning":
            return "⚠ Reminder cadence has never run. Open Onboarding Tracker to run reminders now."
        if severity == "overdue":
            return "⚠ Reminder cadence is overdue. Open Onboarding Tracker to run reminders now."
        return ""
    @staticmethod
    def _load_onboarding_state() -> Any:
        return JsonStore(Path.cwd()).load()

    def open_onboarding_tracker(self, employee_id: str | None = None, urgent_only: bool = False) -> None:
        onboarding_path = Path(__file__).with_name("onboarding_app.pyw")
        if not onboarding_path.exists():
            messagebox.showerror("Onboarding app missing", f"Could not find onboarding_app.pyw at:\n{onboarding_path}")
            return
        cmd = [sys.executable, str(onboarding_path)]

        if employee_id:
            cmd.extend(["--employee-id", str(employee_id).strip()])

        if urgent_only:
            cmd.append("--urgent-only")

        state_path = self._write_onboarding_launch_state(employee_id, urgent_only)
        if state_path:
            cmd.extend(["--state-file", str(state_path)])

        subprocess.Popen(cmd)

    def _write_onboarding_launch_state(self, employee_id: str | None, urgent_only: bool) -> Path | None:
        payload = {
            "employee_id": str(employee_id or "").strip(),
            "urgent_only": bool(urgent_only),
        }
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="onboarding_launch_", delete=False, encoding="utf-8") as handle:
                json.dump(payload, handle)
                return Path(handle.name)
        except OSError:
            return None

    def _set_history_search(self, value: str) -> None:
        self.history_search_var.set(value)

    def _history_actions_service(self) -> HistoryActionsService:
        service = self.__dict__.get("history_actions_service")
        if service is None:
            service = HistoryActionsService(self)
            self.history_actions_service = service
        return service

    def _build_history_table(self, parent: ttk.Frame) -> None:
        controller = self.__dict__.get("history_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = HistoryController(self, shared_state)
            self.history_controller = controller
        controller.build_history_table(parent)

    def _refresh_history_tree(self) -> None:
        controller = self.__dict__.get("history_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = HistoryController(self, shared_state)
            self.history_controller = controller
        controller.refresh_history_tree()

    @staticmethod
    def _history_offer_action_label(row: dict[str, Any]) -> str:
        return HistoryDataGrid._offer_action_label(row)

    def _handle_retranscribe_for_row(self, row: dict[str, Any]) -> None:
        self._history_actions_service().handle_retranscribe_for_row(row)

    @staticmethod
    def _format_seconds_for_transcript(seconds: Any) -> str:
        return format_seconds_for_transcript(seconds)


    def _open_path_in_default_app(self, path_value: str) -> None:
        candidate = Path(path_value).expanduser()
        if not candidate.exists():
            messagebox.showerror("Open File", "Selected file does not exist.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(candidate))
                return
            if sys.platform == "darwin":
                subprocess.run(["open", str(candidate)], check=True)
                return
            subprocess.run(["xdg-open", str(candidate)], check=True)
        except Exception as exc:
            messagebox.showerror("Open File", f"Could not open file: {exc}")

    @staticmethod
    def _offer_transition(status: str) -> tuple[str, str] | None:
        transition = HistoryActionsService.offer_transition(status)
        if transition is None:
            return None
        return transition["next_status"], transition["done_message"]

    @staticmethod
    def _candidate_from_history_row(row: dict[str, Any]) -> dict[str, str]:
        candidate_name = str(row.get("candidate_name", "")).strip()
        first_name, last_name = _split_name_parts(candidate_name)
        return {
            "candidate_name": candidate_name,
            "first_name": first_name,
            "last_name": last_name,
            "school": str(row.get("school", "")).strip(),
            "track": str(row.get("track", "")).strip(),
            "interview_date": str(row.get("interview_date", "")).strip(),
            "offer_path": str(row.get("offer_path", "") or row.get("offer_letter_path", "")).strip(),
        }

    @staticmethod
    def _format_offer_email_template(template: str, candidate: dict[str, str], *, context: str = "offer") -> str:
        return render_template(
            str(template or ""),
            candidate,
            context=context,
            unknown_policy="empty",
        )

    @staticmethod
    def _existing_path(path_text: str) -> str:
        candidate = Path(path_text).expanduser()
        if not candidate.exists():
            return ""
        return str(candidate)

    def _draft_email_with_fallback(
        self,
        *,
        title: str,
        subject: str,
        body: str,
        recipients: str,
        attachments: list[str],
    ) -> bool:
        try:
            open_outlook_draft(subject=subject, body=body, attachments=attachments, to_recipients=recipients)
            return True
        except DirectorEmailDraftError as exc:
            mailto_url = build_mailto_url(subject=subject, body=body, to_recipients=recipients)
            copied_text = (
                f"To: {recipients}\n"
                f"Subject: {subject}\n\n"
                f"{body}\n\n"
                "Attachments:\n"
                + "\n".join(attachments)
            )
            self._copy_text_to_clipboard(copied_text)
            webbrowser.open(mailto_url)
            messagebox.showwarning(
                title,
                f"{exc}\n\nOpened your default mail app via mailto and copied template text (including attachments) to clipboard.",
            )
            return True

    def _school_email_template_defaults(self) -> dict[str, str]:
        return {
            "director_referral_subject_template": str(self.settings.get("director_email_subject_template", "")).strip(),
            "director_referral_body_template": str(self.settings.get("director_email_body_template", "")).strip(),
            "director_email_to": str(self.settings.get("director_email_to", "")).strip(),
            "offer_approval_subject_template": str(self.settings.get("offer_approval_subject_template", "")).strip(),
            "offer_approval_body_template": str(self.settings.get("offer_approval_body_template", "")).strip(),
            "offer_acceptance_subject_template": str(self.settings.get("offer_acceptance_subject_template", "")).strip(),
            "offer_acceptance_body_template": str(self.settings.get("offer_acceptance_body_template", "")).strip(),
            "offer_email_to": str(self.settings.get("offer_email_to", "")).strip(),
            "welcome_email_subject_template": str(self.settings.get("welcome_email_subject_template", "")).strip(),
            "welcome_email_body_template": str(self.settings.get("welcome_email_body_template", "")).strip(),
        }

    def _resolve_school_email_templates(self, school: str) -> dict[str, str]:
        templates = self._school_email_template_defaults()
        overrides = self.school_email_template_config(school)
        for key, value in overrides.items():
            if value:
                templates[key] = value
        return templates

    def _resolve_row_school_email_templates(self, row: dict[str, Any]) -> dict[str, str]:
        school = str(row.get("school", "")).strip()
        return self._resolve_school_email_templates(school)

    def _offer_approval_attachments(self, candidate: dict[str, str]) -> list[str] | None:
        offer_path = self._existing_path(candidate.get("offer_path", ""))
        if offer_path:
            return [offer_path]
        messagebox.showerror("Offer Workflow", "Cannot mark approved: generated offer file is missing.")
        return None

    def _offer_acceptance_attachments(self, candidate: dict[str, str]) -> list[str] | None:
        if not bool(self.settings.get("offer_acceptance_attach_offer_file", True)):
            return []
        offer_path = self._existing_path(candidate.get("offer_path", ""))
        if offer_path:
            return [offer_path]
        messagebox.showerror("Offer Workflow", "Cannot mark accepted: generated offer file is missing.")
        return None

    def _welcome_email_attachments(self) -> list[str] | None:
        onboarding_path = self._existing_path(str(self.settings.get("welcome_onboarding_pdf_path", "")).strip())
        if onboarding_path:
            return [onboarding_path]
        messagebox.showerror("Offer Workflow", "Cannot send welcome email: onboarding guide PDF path is missing or invalid.")
        return None

    def _prompt_for_template_values(self, templates: list[tuple[str, str]], values: dict[str, str]) -> bool:
        missing: list[str] = []
        for template, context in templates:
            missing.extend(missing_placeholder_keys(template, values, context))
        for key in sorted(set(missing)):
            if str(values.get(key, "")).strip():
                continue
            entered = simpledialog.askstring("Missing placeholder value", f"Enter value for [{key}]", parent=self)
            if entered is None:
                return False
            text = entered.strip()
            if not text:
                messagebox.showerror("Missing value", f"Placeholder [{key}] requires a value.")
                return False
            values[key] = text
        return True

    def _draft_offer_approval_email(self, row: dict[str, Any]) -> bool:
        candidate = self._candidate_from_history_row(row)
        attachments = self._offer_approval_attachments(candidate)
        if attachments is None:
            return False
        templates = self._resolve_row_school_email_templates(row)
        if not self._prompt_for_template_values([
            (templates.get("offer_approval_subject_template", ""), "offer"),
            (templates.get("offer_approval_body_template", ""), "offer"),
        ], candidate):
            return False
        subject = self._format_offer_email_template(templates.get("offer_approval_subject_template", ""), candidate)
        body = self._format_offer_email_template(templates.get("offer_approval_body_template", ""), candidate)
        return self._draft_email_with_fallback(
            title="Offer Approval Draft",
            subject=subject,
            body=body,
            recipients=templates.get("offer_email_to", ""),
            attachments=attachments,
        )

    def _draft_offer_acceptance_email(self, row: dict[str, Any]) -> bool:
        candidate = self._candidate_from_history_row(row)
        attachments = self._offer_acceptance_attachments(candidate)
        if attachments is None:
            return False
        templates = self._resolve_row_school_email_templates(row)
        if not self._prompt_for_template_values([
            (templates.get("offer_acceptance_subject_template", ""), "offer"),
            (templates.get("offer_acceptance_body_template", ""), "offer"),
        ], candidate):
            return False
        subject = self._format_offer_email_template(templates.get("offer_acceptance_subject_template", ""), candidate)
        body = self._format_offer_email_template(templates.get("offer_acceptance_body_template", ""), candidate)
        return self._draft_email_with_fallback(
            title="Offer Acceptance Draft",
            subject=subject,
            body=body,
            recipients=templates.get("offer_email_to", ""),
            attachments=attachments,
        )

    def _draft_welcome_email(self, row: dict[str, Any]) -> bool:
        candidate = self._candidate_from_history_row(row)
        attachments = self._welcome_email_attachments()
        if attachments is None:
            return False
        templates = self._resolve_row_school_email_templates(row)
        if not self._prompt_for_template_values([
            (templates.get("welcome_email_subject_template", ""), "welcome"),
            (templates.get("welcome_email_body_template", ""), "welcome"),
        ], candidate):
            return False
        subject = self._format_offer_email_template(
            templates.get("welcome_email_subject_template", ""),
            candidate,
            context="welcome",
        )
        body = self._format_offer_email_template(
            templates.get("welcome_email_body_template", ""),
            candidate,
            context="welcome",
        )
        return self._draft_email_with_fallback(
            title="Welcome Email Draft",
            subject=subject,
            body=body,
            recipients=templates.get("offer_email_to", ""),
            attachments=attachments,
        )

    def _draft_offer_email_for_transition(self, status: str, row: dict[str, Any]) -> bool:
        if status == "generated":
            return self._draft_offer_approval_email(row)
        if status == "approved":
            return self._draft_offer_acceptance_email(row)
        if status == "accepted":
            return self._draft_welcome_email(row)
        return True

    def _history_row_by_key(self, row_key: str) -> dict[str, Any] | None:
        key = str(row_key).strip()
        if not key:
            return None
        for row in self.history_rows:
            if self.history_store.build_row_key(row) == key:
                return row
        return None

    def _update_history_offer_status(self, row: dict[str, Any], status: str, offer_path: str = "") -> bool:
        return self._history_actions_service().update_history_offer_status(row, status, offer_path)

    def _handle_offer_action_for_row(self, row: dict[str, Any]) -> None:
        self._history_actions_service().handle_offer_action_for_row(row)

    def _open_offer_generator(self, row: dict[str, Any]) -> None:
        OfferGeneratorWindow(self, row)

    def _mark_offer_generated(self, row: dict[str, Any], offer_path: Path) -> None:
        offer_text = str(offer_path).strip()
        if not offer_text:
            return
        if not self._update_history_offer_status(row, "generated", offer_text):
            return

    def _open_onboarding_tracker(self) -> bool:
        onboarding_script = Path(__file__).resolve().parent / "onboarding_app.pyw"
        if not onboarding_script.exists():
            messagebox.showerror("Onboarding", f"Onboarding app not found:\n{onboarding_script}")
            return False
        try:
            subprocess.Popen([sys.executable, str(onboarding_script)])
            return True
        except Exception as exc:
            messagebox.showerror("Onboarding", f"Could not open onboarding app: {exc}")
            return False

    def _selected_history_row(self) -> dict[str, Any] | None:
        controller = self.__dict__.get("history_controller")
        if controller is None:
            return self.history_selected_row
        return controller.selected_history_row()

    def new_interview(self) -> None:
        self.state = InterviewState(interview_date=date.today().isoformat())
        self._trait_disclosure_state = {}
        self.active_traits = []
        self.custom_questions = []
        self.active_flow = []
        self.show_candidate_info()

    def open_draft(self) -> None:
        base_dir = Path(self.settings["base_dir"])
        dm = DraftManager(base_dir)
        self.session_manager = InterviewSessionManager(draft_manager=dm, session_store=self._session_store())

        path = filedialog.askopenfilename(
            title="Open Draft",
            initialdir=str(dm.drafts_dir),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self._open_draft_path(Path(path))

    def _open_draft_path(self, draft_path: Path) -> None:
        base_dir = Path(self.settings["base_dir"])
        dm = DraftManager(base_dir)
        manager = InterviewSessionManager(draft_manager=dm, session_store=self._session_store())
        try:
            self._reset_interview_recording_state()
            self.state = manager.hydrate_state(manager.load_draft_payload(draft_path))
            self._resume_from_loaded_state(manager)
        except SessionPayloadValidationError as exc:
            messagebox.showerror("Open Draft Error", f"{exc}")
        except Exception as exc:
            messagebox.showerror("Open Draft Error", f"{exc}\n\n{traceback.format_exc()}")

    def _resume_from_loaded_state(self, manager: InterviewSessionManager) -> None:
        self._build_active_flow(self.state.track)
        resume = manager.build_resume_instruction(self.state, self._flow_len())
        if resume.target == "candidate_info":
            self.show_candidate_info()
            return
        self.show_flow_screen(int(resume.flow_index or 0))

    def open_settings(self) -> None:
        # SettingsWindow uses UI tab groups but saves back into the same app.settings schema.
        SettingsWindow(self)


    def open_offer_dialog_from_selection(self) -> None:
        row = self._selected_history_row()
        if not row:
            messagebox.showerror("Offer Generator", "Select an interview from history first.")
            return
        OfferGeneratorWindow(self, row)

    def save_school_offer_config(self, school: str, config: dict[str, str]) -> None:
        school_name = school.strip()
        if not school_name:
            raise ValueError("School is required.")
        current = self.school_offer_store.load()
        current[school_name] = {
            "full_time_template": config.get("full_time_template", "").strip(),
            "part_time_template": config.get("part_time_template", "").strip(),
            "offer_output_dir": config.get("offer_output_dir", "").strip(),
        }
        self.school_offer_store.save(current)
        self.school_offer_settings = current

    def school_offer_config(self, school: str) -> dict[str, str]:
        data = self.school_offer_store.load()
        self.school_offer_settings = data
        return data.get(school.strip(), {
            "full_time_template": "",
            "part_time_template": "",
            "offer_output_dir": "",
        })

    def save_school_email_template_config(self, school: str, config: dict[str, str]) -> None:
        school_name = school.strip()
        if not school_name:
            raise ValueError("School is required.")
        current = self.school_email_template_store.load()
        current[school_name] = {
            key: str(config.get(key, "")).strip()
            for key in SchoolEmailTemplateStore.TEMPLATE_KEYS
        }
        self.school_email_template_store.save(current)
        self.school_email_template_settings = current

    def school_email_template_config(self, school: str) -> dict[str, str]:
        data = self.school_email_template_store.load()
        self.school_email_template_settings = data
        empty = {key: "" for key in SchoolEmailTemplateStore.TEMPLATE_KEYS}
        return data.get(school.strip(), empty)

    def show_candidate_info(self) -> None:
        self.candidate_setup_view.render()

    def render_progress_strip(self, parent: ttk.Frame, flow_idx: int, *, is_scored: bool) -> None:
        self.signal_reference_view.render_progress_strip(parent, flow_idx, is_scored=is_scored)

    def _open_combo_dropdown(self, combo: ttk.Combobox) -> None:
        combo.focus_set()
        combo.after_idle(lambda: combo.event_generate("<Down>"))

    def _add_school(self, school_var: StringVar, combo: ttk.Combobox) -> None:
        value = school_var.get().strip()
        if not value:
            messagebox.showerror("Validation", "Enter a school name before adding.")
            return
        if value not in self.school_options:
            self.school_options.append(value)
        combo.configure(values=self.school_options)
        school_var.set(value)

    def _pick_referral_document(self, doc_key: str, doc_var: StringVar, missing_var: StringVar) -> None:
        selected = filedialog.askopenfilename(
            title="Select referral document",
            initialdir=str(Path(self.settings["base_dir"])),
            filetypes=[
                ("Documents", "*.pdf *.doc *.docx *.txt *.rtf"),
                ("All files", "*.*"),
            ],
        )
        if not selected:
            return

        if not is_supported_document_path(selected):
            messagebox.showerror("Validation", "Unsupported file type. Use PDF, DOC, DOCX, TXT, or RTF.")
            return

        doc_var.set(selected)
        missing_var.set("")

    def _referral_missing_docs(self) -> list[str]:
        return missing_required_docs(self.state.referral_packet)

    def _require_referral_packet_or_raise(self) -> None:
        missing = self._referral_missing_docs()
        if not missing:
            return
        msg = "Upload required referral packet documents before send: " + ", ".join(missing)
        raise ValueError(msg)

    def _refresh_school_info(self, school_var: StringVar, label: ttk.Label) -> None:
        school = (school_var.get() or "").strip()
        if not school:
            label.config(text="")
            return

        text = SCHOOL_INFO.get(school)
        if text:
            label.config(text=text)
            return

        # If the school is custom or not configured, show a safe default.
        label.config(text="Hours: (not configured)")

    def _refresh_intro_script(self, school_var: StringVar, intro_text: tk.Text) -> None:
        school = (school_var.get() or "").strip()
        script = compose_intro_script(school)

        intro_text.config(state="normal")
        intro_text.delete("1.0", END)
        intro_text.insert(END, script)
        intro_text.config(state="disabled")

    def _refresh_thresholds(self, track_var: StringVar, label: ttk.Label) -> None:
        t = track_var.get()
        if not t or t not in self.rubric["tracks"]:
            label.config(text="")
            return

        cfg = self.rubric["tracks"][t]
        label.config(
            text=(
                f"Thresholds: Hire >= {cfg.get('hire_percent', '??')}% "
                f"| Borderline {cfg.get('borderline_min_percent', '??')}%-{cfg.get('borderline_max_percent', '??')}% "
                f"| No Hire < {cfg.get('borderline_min_percent', '??')}% "
                f"or any Critical < 3 "
                f"or any Critical = 1 "
                f"or any Absolute Disqualifier"
            )
        )

    def _validate_candidate_vars(
        self,
        name_var: StringVar,
        school_var: StringVar,
        track_var: StringVar,
    ) -> tuple[bool, str]:
        name = name_var.get().strip()
        school = school_var.get().strip()
        track = track_var.get().strip()

        if not name:
            return False, "Candidate Name is required."
        if not school:
            return False, "School selection is required."
        if not track:
            return False, "Track selection is required."
        return True, ""

    def _safe_base_name(self) -> str:
        return self._session_context().safe_base_name(
            str(self.state.candidate_name or ""),
            str(getattr(self.state, "interview_date", "") or ""),
        )

    def _session_context(self) -> InterviewSessionContext:
        context = self.__dict__.get("session_context")
        if context is not None:
            return context
        context = InterviewSessionContext(
            app_root=Path(__file__).resolve().parent.parent,
            default_base_dir=Path(DEFAULT_BASE_DIR),
        )
        self.session_context = context
        return context

    def _safe_interview_date(self) -> str:
        return self._session_context().safe_interview_date(str(getattr(self.state, "interview_date", "") or ""))

    def _active_session_key(self) -> tuple[str, str, str]:
        return self._session_context().active_session_key(
            str(self._safe_attr("interview_session_id", "") or ""),
            str(self.state.candidate_name or ""),
            str(getattr(self.state, "interview_date", "") or ""),
        )

    def _session_store(self) -> InterviewSessionStore:
        store = self._safe_attr("interview_session_store")
        if store is not None:
            return store
        base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        store = InterviewSessionStore(base_dir)
        self.interview_session_store = store
        return store

    def _session_notes_for_flow(self, flow_idx: int) -> dict[str, Any]:
        item = self._get_flow_item(flow_idx)
        if not item:
            return {}
        if item.get("type") == "trait":
            tid = str(item.get("id"))
            return dict(self.state.trait_inputs.get(tid, {}) or {})
        cid = str(item.get("id"))
        return dict(self.state.custom_inputs.get(cid, {}) or {})

    def _persist_interview_session_snapshot(self, flow_idx: int) -> None:
        item = self._get_flow_item(flow_idx)
        if not item:
            return
        interview_id, candidate_name, interview_date = self._active_session_key()
        candidate_tx = str(self.state.flow_candidate_transcripts.get(flow_idx, "")).strip()
        notes = self._session_notes_for_flow(flow_idx)
        self._session_store().save_question_snapshot(
            interview_id=interview_id,
            candidate_name=candidate_name,
            interview_date=interview_date,
            flow_idx=flow_idx,
            item_type=str(item.get("type") or ""),
            item_id=str(item.get("id") or ""),
            notes=notes,
            candidate_transcript=candidate_tx,
        )

    def _hydrate_state_from_session_store(self) -> None:
        interview_id, candidate_name, interview_date = self._active_session_key()
        manager = InterviewSessionManager(
            draft_manager=DraftManager(Path(self.settings["base_dir"])),
            session_store=self._session_store(),
        )
        payload = manager.load_session_payload(
            interview_id=interview_id,
            candidate_name=candidate_name,
            interview_date=interview_date,
        )
        manager.hydrate_state_from_session_payload(self.state, payload)

    def _safe_question_base_name(self, flow_idx: int, item: dict[str, Any]) -> str:
        interview_base = self._safe_base_name()
        item_type = re.sub(r"[^A-Za-z0-9]+", "_", str(item.get("type") or "item")).strip("_") or "item"
        item_id = re.sub(r"[^A-Za-z0-9]+", "_", str(item.get("id") or "unknown")).strip("_") or "unknown"
        return f"{interview_base}_Q{flow_idx + 1}_{item_type}_{item_id}"

    def _question_number_label(self, flow_idx: int) -> str:
        return f"Q{int(flow_idx) + 1}"

    def _build_attempt_marker(self, flow_idx: int, attempt_no: int) -> str:
        return f"[{self._question_number_label(flow_idx)} Attempt {attempt_no}]"

    def _recording_entry_for_flow(self, flow_idx: int) -> dict[str, Any]:
        raw = self.state.flow_recordings.get(flow_idx)
        if isinstance(raw, dict):
            entry = dict(raw)
        else:
            entry = {}
        attempts = entry.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
        entry["attempts"] = attempts
        entry["candidate_transcript"] = str(entry.get("candidate_transcript") or "").strip()
        self.state.flow_recordings[flow_idx] = entry
        return entry

    def _append_recording_attempt(self, flow_idx: int, payload: dict[str, Any]) -> dict[str, Any]:
        entry = self._recording_entry_for_flow(flow_idx)
        attempts = entry["attempts"]
        attempts.append(payload)
        attempt_no = len(attempts)
        attempt_text = str(payload.get("candidate_transcript") or "").strip()
        marker = self._build_attempt_marker(flow_idx, attempt_no)
        merged = str(entry.get("candidate_transcript") or "").strip()
        segment = marker if not attempt_text else f"{marker}\n{attempt_text}"
        entry["candidate_transcript"] = f"{merged}\n\n{segment}".strip() if merged else segment
        for key in ["base_name", "output_dir", "mic_wav", "sys_wav", "transcript_txt", "transcript_jsonl", "candidate_label"]:
            entry[key] = payload.get(key)
        return entry

    def _recording_candidate_transcript(self, rec: dict[str, Any] | None) -> str:
        if not rec:
            return ""
        tx = str(rec.get("candidate_transcript") or "").strip()
        if tx:
            return tx
        attempts = rec.get("attempts") or []
        if not isinstance(attempts, list):
            return ""
        parts: list[str] = []
        try:
            flow_idx = int(rec.get("flow_index", -1))
        except Exception:
            flow_idx = -1
        for idx, attempt in enumerate(attempts, start=1):
            text = str((attempt or {}).get("candidate_transcript") or "").strip()
            marker = self._build_attempt_marker(flow_idx, idx)
            parts.append(marker if not text else f"{marker}\n{text}")
        return "\n\n".join(parts).strip()

    def _transcription_health_snapshot(self) -> tuple[str, str, str]:
        transcription_errors = self._transcription_queue_state.error_reasons()
        return format_transcription_health_summary(
            transcription_errors=transcription_errors,
            question_labeler=self._question_number_label,
            log_path=self.__dict__.get("_app_log_path") or get_configured_log_path(),
        )

    def _validate_transcription_health(self) -> None:
        joined, detail_block, log_hint = self._transcription_health_snapshot()
        if not joined:
            return
        extra_lines = ["", "Details:", detail_block]
        if log_hint:
            extra_lines.extend(["", log_hint])
        raise ReportingValidationError(
            (
                f"Audio transcription failed for {joined}. "
                "Revisit those questions and record again before continuing."
                f"{chr(10).join(extra_lines)}"
            )
        )

    def _collect_transcription_health_warnings(self) -> list[str]:
        joined, detail_block, log_hint = self._transcription_health_snapshot()
        if not joined:
            return []
        warning_lines = [
            f"Audio transcription failed for {joined}.",
            "You can revisit those questions and re-record to append a new attempt.",
            "Details:",
            detail_block,
        ]
        if log_hint:
            warning_lines.append(log_hint)
        logger.warning(
            "transcription_health_failed",
            extra={"questions": joined, "details": detail_block},
        )
        return ["\n".join(warning_lines)]

    def _sanitize_transcription_error_reason(self, raw_reason: str) -> str:
        return sanitize_transcription_error_reason(raw_reason)


    def _transcription_error_log_hint(self) -> str:
        return build_transcription_log_hint(self.__dict__.get("_app_log_path") or get_configured_log_path())

    def report_callback_exception(
        self,
        exc: type[BaseException],
        val: BaseException,
        tb: TracebackType | None,
    ) -> None:
        log_path = self.__dict__.get("_app_log_path") or get_configured_log_path()
        crash_report_path = write_crash_report(
            source="tk_callback",
            exc_type=exc,
            exc_value=val,
            exc_traceback=tb,
        )
        logger.error(
            "tk_callback_exception",
            extra={"exception_type": exc.__name__},
            exc_info=(exc, val, tb),
        )
        hint = f"Details were logged to:\n{log_path}" if log_path else "Details were logged to the application log file."
        if crash_report_path is not None:
            hint = f"{hint}\nCrash report:\n{crash_report_path}"
        messagebox.showerror(
            "Unexpected Error",
            "An unexpected application error occurred while handling a UI action.\n\n"
            f"{hint}",
        )

    def _wait_for_pending_transcriptions(self) -> None:
        controller = self.__dict__.get("audio_runtime_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = AudioRuntimeController(self, shared_state)
            self.audio_runtime_controller = controller
        controller.wait_for_pending_transcriptions()

    def _pending_transcription_count(self) -> int:
        return self._transcription_queue_state.pending_count()

    def _ensure_transcription_executor(self) -> BoundedTranscriptionExecutor:
        existing = self.__dict__.get("_transcription_executor")
        if existing is not None:
            return existing
        executor = BoundedTranscriptionExecutor(
            queue_state=self._transcription_queue_state,
            worker_fn=self._background_transcribe_question,
            max_workers=int(self.__dict__.get("_transcription_max_workers", resolve_transcription_max_workers(self.__dict__.get("settings", {})))),
            on_status_change=self._on_transcription_job_status,
        )
        self._transcription_executor = executor
        return executor

    def _on_transcription_job_status(self, event: TranscriptionJobStatusEvent) -> None:
        logger.info(
            "transcription_job_state_changed",
            extra={
                "flow_idx": event.flow_idx,
                "status": event.status,
                "pending_count": event.snapshot["pending_count"],
                "queued_count": event.snapshot["queued_count"],
            },
        )
        if self.__dict__.get("tk") is not None:
            self.after(0, self._refresh_finalize_processing_state)

    def _start_background_question_transcription(self, flow_idx: int) -> None:
        session = self._safe_attr("recording_session")
        if session is None or self._safe_attr("recording_flow_idx") != flow_idx:
            raise ReportingValidationError(
                f"Recording for {self._question_number_label(flow_idx)} was not active. Please retry this question."
            )

        try:
            session.stop()
        except Exception as exc:
            raise ReportingValidationError(
                f"Recording for {self._question_number_label(flow_idx)} could not be stopped before transcription.\n\n{exc}"
            ) from exc

        base_name = str(self._safe_attr("recording_base_name", ""))
        candidate_label = self.recording_candidate_label or "CANDIDATE"
        base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        base_dir.mkdir(parents=True, exist_ok=True)

        self.recording_session = None
        self.recording_base_name = ""
        self.recording_flow_idx = None
        if self._transcription_queue_state.is_pending(flow_idx):
            raise ReportingValidationError(
                f"Transcription for {self._question_number_label(flow_idx)} is already running."
            )

        self._transcription_queue_state.clear_error(flow_idx)
        payload = {
            "flow_idx": flow_idx,
            "session": session,
            "base_dir": base_dir,
            "base_name": base_name,
            "candidate_label": candidate_label,
            "job_timeout_seconds": resolve_transcription_job_timeout_seconds(self.settings),
            "retry_count": 0,
            "interview_session_id": str(self.__dict__.get("interview_session_id", "")),
            "finalize_correlation_id": str(self.__dict__.get("current_finalize_correlation_id", "")),
        }
        self._ensure_transcription_executor().submit(flow_idx, payload)

    def _background_transcribe_question(
        self,
        *,
        flow_idx: int,
        session: Any,
        base_dir: Path,
        base_name: str,
        candidate_label: str,
        job_timeout_seconds: float = 180.0,
    ) -> None:
        controller = self.__dict__.get("audio_runtime_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = AudioRuntimeController(self, shared_state)
            self.audio_runtime_controller = controller
        controller.background_transcribe_question(
            flow_idx=flow_idx,
            session=session,
            base_dir=base_dir,
            base_name=base_name,
            candidate_label=candidate_label,
            job_timeout_seconds=job_timeout_seconds,
        )


    def _start_interview_recording(self) -> None:
        base_dir = self._validate_interview_runtime_paths()
        self._reset_interview_recording_state()
        self._initialize_transcript_output_target(base_dir)
        self._start_continuous_interview_recording(base_dir)

    def _reset_interview_recording_state(self) -> None:
        self.state.flow_time_marks = []
        self.state.flow_recordings = {}
        self.state.flow_candidate_transcripts = {}
        self._transcription_queue_state.clear()
        self._transcription_worker_started = False
        executor = self.__dict__.get("_transcription_executor")
        if executor is not None:
            executor.shutdown(wait=False)
            self._transcription_executor = None
        self._transcription_max_workers = resolve_transcription_max_workers(self.settings if hasattr(self, "settings") else {})
        self._session_use_default_whisper_settings = False
        self.recording_started_monotonic = time.monotonic()
        self.recording_base_name = self._safe_base_name()
        self.live_transcript_docx = None
        self.interview_session_id = f"{self._safe_base_name()}_{uuid4().hex[:8]}"
        self.interview_session_store = InterviewSessionStore(Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR))))
        self.transcript_available = True
        self.transcript_warning = ""
        self.finalize_warning = ""
        presenter = self.__dict__.get("main_warning_presenter")
        if presenter is not None:
            presenter.dismiss()

    def _initialize_transcript_output_target(self, base_dir: Path) -> None:
        self.live_transcript_docx = None

    def _start_continuous_interview_recording(self, base_dir: Path) -> None:
        try:
            from interview_audio_recorder import start_recording
        except ImportError as exc:
            messagebox.showwarning(
                "Recording unavailable",
                "Audio recording/transcription is unavailable because optional dependencies "
                f"could not be loaded.\n\n{exc}",
            )
            self.transcript_available = False
            self.transcript_warning = (
                "Live transcript is unavailable for this interview. Continue in fallback mode and capture notes manually."
            )
            return

        try:
            self._current_question_whisper_source = "default" if self._session_use_default_whisper_settings else "user"
            self.recording_session = self._start_recording_with_runtime_fallback(
                start_recording,
                base_dir,
                self.recording_base_name,
            )
            self.recording_candidate_label = "CANDIDATE"
            self.recording_flow_idx = 0
        except Exception as exc:
            self.recording_session = None
            self.recording_base_name = ""
            self.recording_flow_idx = None
            raise ReportingValidationError(f"Could not start interview recording.\n\n{exc}") from exc

    def _initialize_interview_runtime(self) -> bool:
        try:
            self._start_interview_recording()
            return True
        except Exception as exc:
            log_path = self._write_runtime_init_error_log(exc)
            messagebox.showerror(
                "Interview runtime setup failed",
                self._format_runtime_init_error_message(log_path),
            )
            return False

    def _validate_interview_runtime_paths(self) -> Path:
        return self._session_context().validate_runtime_base_dir(
            str(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        )

    def _runtime_init_log_path(self) -> Path:
        return self._session_context().runtime_init_log_path()

    def _write_runtime_init_error_log(self, exc: Exception) -> Path | None:
        log_path = self._runtime_init_log_path()
        details = "\n".join(
            [
                f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
                f"base_dir: {redact_paths(str(self.settings.get('base_dir', str(DEFAULT_BASE_DIR))))}",
                f"error_type: {type(exc).__name__}",
                f"error: {redact_paths(str(exc))}",
                "--- traceback ---",
                traceback.format_exc().strip() or "<empty>",
            ]
        )
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(details, encoding="utf-8")
            return log_path
        except Exception:
            logger.exception("runtime_init_error_log_write_failed")
            return None

    def _format_runtime_init_error_message(self, log_path: Path | None) -> str:
        return format_runtime_init_error_message(log_path)

    def _handle_start_interview_navigation_failure(self, exc: Exception) -> None:
        logger.exception("start_interview_navigation_failed")
        messagebox.showerror(
            "Interview start issue",
            "Unable to open the first interview question.\n\n"
            "Recovery action: Click OK to return to Candidate setup, then click Start Interview again.\n\n"
            f"Technical details: {exc}",
        )
        self.show_candidate_info()

    def _discard_question_recording(self, flow_idx: int) -> None:
        self._transcription_queue_state.cancel(flow_idx)

        audio_lock = self._safe_attr("_audio_state_lock")
        if audio_lock is None:
            self.state.flow_recordings.pop(flow_idx, None)
            self.state.flow_candidate_transcripts.pop(flow_idx, None)
            self._transcription_queue_state.clear_error(flow_idx)
        else:
            with audio_lock:
                self.state.flow_recordings.pop(flow_idx, None)
                self.state.flow_candidate_transcripts.pop(flow_idx, None)
                self._transcription_queue_state.clear_error(flow_idx)

        session = self._safe_attr("recording_session")
        active_flow_idx = self._safe_attr("recording_flow_idx")
        if session is None or active_flow_idx != flow_idx:
            return

        try:
            session.stop()
        except Exception:
            logger.exception("question_recording_discard_stop_failed", extra={"flow_idx": flow_idx})

        for attr_name in ["mic_wav", "sys_wav"]:
            path_value = getattr(session, attr_name, None)
            if path_value:
                self._delete_file_if_exists(Path(path_value))

        self.recording_session = None
        self.recording_base_name = ""
        self.recording_flow_idx = None

    @staticmethod
    def _default_whisper_transcription_settings() -> dict[str, Any]:
        return {"vad_filter": True, "beam_size": 5, "temperature": 0.0}

    def _current_whisper_transcription_settings(self) -> dict[str, Any]:
        if self._session_use_default_whisper_settings:
            return self._default_whisper_transcription_settings()
        return {
            "vad_filter": bool(self.settings.get("whisper_vad_filter", True)),
            "beam_size": int(self.settings.get("whisper_beam_size", 5) or 5),
            "temperature": float(self.settings.get("whisper_temperature", 0.0) or 0.0),
        }

    def _current_whisper_language(self) -> str:
        lang = str(self.settings.get("whisper_language") or "en").strip().lower()
        return lang or "en"

    def _show_relaunch_whisper_notice(self) -> None:
        notice = str(self.settings.get("whisper_relaunch_notice") or "").strip()
        if not notice:
            return
        messagebox.showwarning("Transcription settings notice", notice)
        self.settings["whisper_relaunch_notice"] = ""
        self.app_settings_store.save(self.settings)

    def _warn_whisper_fallback_once(self) -> None:
        notice_flag = "whisper_cpu_fallback_notice"
        if notice_flag in self._session_status_flags:
            return
        self._session_status_flags.add(notice_flag)
        messagebox.showwarning("Transcription mode", "GPU unavailable; using CPU transcription mode.")

    def _start_recording_session(
        self,
        start_recording: Any,
        *,
        base_dir: Path,
        base_name: str,
        runtime_config: Any,
    ) -> Any:
        controller = self.__dict__.get("audio_runtime_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = AudioRuntimeController(self, shared_state)
            self.audio_runtime_controller = controller
        return controller.start_recording_session(
            start_recording,
            base_dir=base_dir,
            base_name=base_name,
            runtime_config=runtime_config,
        )

    def _start_recording_with_runtime_fallback(self, start_recording: Any, base_dir: Path, base_name: str) -> Any:
        controller = self.__dict__.get("audio_runtime_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = AudioRuntimeController(self, shared_state)
            self.audio_runtime_controller = controller
        return controller.start_recording_with_runtime_fallback(
            start_recording,
            base_dir=base_dir,
            base_name=base_name,
        )

    def _start_question_recording(self, flow_idx: int) -> None:
        recording_session = self._safe_attr("recording_session")
        if recording_session is not None:
            self.recording_flow_idx = flow_idx
            return

        item = self._get_flow_item(flow_idx)
        if not item:
            return

        base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        base_dir.mkdir(parents=True, exist_ok=True)
        if self.recording_started_monotonic is None:
            self.recording_started_monotonic = time.monotonic()

        try:
            from interview_audio_recorder import start_recording
        except ImportError as exc:
            messagebox.showwarning(
                "Recording unavailable",
                "Audio recording/transcription is unavailable because optional dependencies "
                f"could not be loaded.\n\n{exc}",
            )
            return

        try:
            self.recording_base_name = self._safe_question_base_name(flow_idx, item)
            self._current_question_whisper_source = "default" if self._session_use_default_whisper_settings else "user"
            self.recording_session = self._start_recording_with_runtime_fallback(
                start_recording,
                base_dir,
                self.recording_base_name,
            )
            self.recording_candidate_label = "CANDIDATE"
            self.recording_flow_idx = flow_idx
        except Exception as exc:
            self.recording_session = None
            self.recording_base_name = ""
            self.recording_flow_idx = None
            raise ReportingValidationError(
                f"Could not start recording for {self._question_number_label(flow_idx)}.\n\n{exc}"
            )

    def _stop_question_recording(self, flow_idx: int, show_warning: bool = True) -> dict[str, Any] | None:
        recording_session = self._safe_attr("recording_session")
        if recording_session is None:
            return self.state.flow_recordings.get(flow_idx)
        if self._safe_attr("recording_flow_idx") != flow_idx:
            return self.state.flow_recordings.get(flow_idx)

        with self._transcription_cv:
            while self._transcription_in_progress:
                self._transcription_cv.wait()
            self._transcription_in_progress = True

        t0 = time.perf_counter()
        try:
            base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
            base_dir.mkdir(parents=True, exist_ok=True)
            recording_base_name = str(self._safe_attr("recording_base_name", ""))
            result = recording_session.stop_and_transcribe(
                output_dir=base_dir,
                base_name=recording_base_name,
                language=self._current_whisper_language(),
            )
            candidate_label = self.recording_candidate_label or "CANDIDATE"
            candidate_transcript = self._extract_candidate_transcript_from_jsonl(result.transcript_jsonl, candidate_label)
            payload = {
                "flow_index": flow_idx,
                "base_name": recording_base_name,
                "output_dir": str(base_dir),
                "mic_wav": str(result.mic_wav),
                "sys_wav": str(result.sys_wav),
                "transcript_txt": str(result.transcript_txt),
                "transcript_jsonl": str(result.transcript_jsonl),
                "candidate_label": candidate_label,
                "candidate_transcript": candidate_transcript,
            }
            entry = self._append_recording_attempt(flow_idx, payload)
            self.state.flow_candidate_transcripts[flow_idx] = str(entry.get("candidate_transcript") or "").strip()
            logger.info("Stop question recording + transcription took %.2fs", time.perf_counter() - t0)
            return entry
        except Exception as exc:
            should_retry_default = flow_idx == 0 and self._current_question_whisper_source == "user"
            if should_retry_default:
                retry_entry = self._retry_first_question_with_default_settings(flow_idx, base_dir)
                if retry_entry is not None:
                    return retry_entry
            if show_warning:
                messagebox.showwarning(
                    "Recording",
                    f"Question recording stop/transcription failed.\n\n{exc}",
                )
            return None
        finally:
            self.recording_session = None
            self.recording_base_name = ""
            self.recording_flow_idx = None
            with self._transcription_cv:
                self._transcription_in_progress = False
                self._transcription_cv.notify_all()

    def _retry_first_question_with_default_settings(self, flow_idx: int, base_dir: Path) -> dict[str, Any] | None:
        recording_session = self._safe_attr("recording_session")
        if recording_session is None:
            return None
        try:
            recording_base_name = str(self._safe_attr("recording_base_name", ""))
            result = recording_session.stop_and_transcribe(
                output_dir=base_dir,
                base_name=recording_base_name,
                language=self._current_whisper_language(),
                whisper_settings=self._default_whisper_transcription_settings(),
            )
        except Exception:
            return None

        candidate_label = self.recording_candidate_label or "CANDIDATE"
        candidate_transcript = self._extract_candidate_transcript_from_jsonl(result.transcript_jsonl, candidate_label)
        payload = {
            "flow_index": flow_idx,
            "base_name": recording_base_name,
            "output_dir": str(base_dir),
            "mic_wav": str(result.mic_wav),
            "sys_wav": str(result.sys_wav),
            "transcript_txt": str(result.transcript_txt),
            "transcript_jsonl": str(result.transcript_jsonl),
            "candidate_label": candidate_label,
            "candidate_transcript": candidate_transcript,
        }
        self._session_use_default_whisper_settings = True
        self.settings["whisper_vad_filter"] = True
        self.settings["whisper_beam_size"] = 5
        self.settings["whisper_temperature"] = 0.0
        self.settings["whisper_relaunch_notice"] = (
            "Custom Whisper transcription settings failed during the first question. "
            "This interview switched to recommended defaults (vad_filter=True, beam_size=5, temperature=0.0). "
            "Please review and save settings."
        )
        self.app_settings_store.save(self.settings)
        messagebox.showwarning(
            "Transcription settings",
            "Custom Whisper settings failed for the first question. "
            "Using recommended defaults for the rest of this interview session.",
        )
        entry = self._append_recording_attempt(flow_idx, payload)
        self.state.flow_candidate_transcripts[flow_idx] = str(entry.get("candidate_transcript") or "").strip()
        return entry



    def _stop_interview_recording(self, *, show_warning: bool = True) -> dict[str, Any] | None:
        recording_session = self._safe_attr("recording_session")
        if recording_session is None:
            return None

        with self._transcription_cv:
            while self._transcription_in_progress:
                self._transcription_cv.wait()
            self._transcription_in_progress = True

        try:
            base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
            base_dir.mkdir(parents=True, exist_ok=True)
            recording_base_name = str(self._safe_attr("recording_base_name", "")) or self._safe_base_name()
            result = recording_session.stop_and_transcribe(
                output_dir=base_dir,
                base_name=recording_base_name,
                language=self._current_whisper_language(),
            )
            candidate_label = self.recording_candidate_label or "CANDIDATE"
            full_payload = {
                "flow_index": -1,
                "base_name": recording_base_name,
                "output_dir": str(base_dir),
                "mic_wav": str(result.mic_wav),
                "sys_wav": str(result.sys_wav),
                "transcript_txt": str(result.transcript_txt),
                "transcript_jsonl": str(result.transcript_jsonl),
                "candidate_label": candidate_label,
                "candidate_transcript": self._extract_candidate_transcript_from_jsonl(result.transcript_jsonl, candidate_label),
            }
            self._apply_continuous_recording_result(full_payload)
            return full_payload
        except Exception as exc:
            if show_warning:
                messagebox.showwarning(
                    "Recording",
                    f"Interview recording stop/transcription failed.\n\n{exc}",
                )
            return None
        finally:
            self.recording_session = None
            self.recording_base_name = ""
            self.recording_flow_idx = None
            with self._transcription_cv:
                self._transcription_in_progress = False
                self._transcription_cv.notify_all()

    def _apply_continuous_recording_result(self, recording_result: dict[str, Any]) -> None:
        jsonl_path = Path(str(recording_result.get("transcript_jsonl") or "")).expanduser()
        candidate_label = str(recording_result.get("candidate_label") or "CANDIDATE")
        by_flow_index = self._map_jsonl_candidate_segments_to_flow(jsonl_path, candidate_label)
        for flow_idx, candidate_transcript in by_flow_index.items():
            if self._is_flow_marked_skipped(flow_idx):
                self.state.flow_candidate_transcripts.pop(flow_idx, None)
                self.state.flow_recordings.pop(flow_idx, None)
                self._persist_interview_session_snapshot(flow_idx)
                continue
            payload = dict(recording_result)
            payload["flow_index"] = flow_idx
            payload["candidate_transcript"] = candidate_transcript
            entry = self._append_recording_attempt(flow_idx, payload)
            self.state.flow_candidate_transcripts[flow_idx] = str(entry.get("candidate_transcript") or "").strip()
            self._persist_interview_session_snapshot(flow_idx)

    def _is_flow_marked_skipped(self, flow_idx: int) -> bool:
        item = self._get_flow_item(flow_idx)
        if not item:
            return False
        if item.get("type") == "trait":
            state = self.state.trait_inputs.get(str(item.get("id")), {}) or {}
            return bool(state.get("skipped", False))
        if item.get("type") == "custom":
            state = self.state.custom_inputs.get(str(item.get("id")), {}) or {}
            return bool(state.get("skipped", False))
        return False

    def _extract_candidate_transcript(self, rec: dict[str, Any] | None) -> str:
        if not rec:
            return ""
        in_memory = str(rec.get("candidate_transcript") or "").strip()
        if in_memory:
            return in_memory

        jsonl_path = Path(str(rec.get("transcript_jsonl") or "")).expanduser()
        if not jsonl_path.exists():
            return ""

        return self._extract_candidate_transcript_from_jsonl(
            jsonl_path,
            str(rec.get("candidate_label") or "CANDIDATE"),
        )

    def _extract_candidate_transcript_from_jsonl(self, jsonl_path: Path, candidate_label: str) -> str:
        return extract_candidate_text_from_jsonl(jsonl_path, candidate_label)

    def _delete_file_if_exists(self, path: Path) -> None:
        try:
            if path and path.exists() and path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _question_audio_mode(self) -> str:
        mode = str(self.settings.get("question_audio_mode") or "").strip()
        if mode in QUESTION_AUDIO_MODES:
            return mode
        return QUESTION_AUDIO_MODE_PER_QUESTION

    def _is_legacy_time_mapping_mode(self) -> bool:
        return self._question_audio_mode() == QUESTION_AUDIO_MODE_TIMESTAMP_SLICING

    def _is_legacy_incremental_mode(self) -> bool:
        return self._question_audio_mode() == QUESTION_AUDIO_MODE_LEGACY_INCREMENTAL

    def _stop_transcribe_and_attach_flow_question(self, flow_idx: int) -> None:
        rec = self._stop_question_recording(flow_idx, show_warning=False)
        if not rec and self._is_legacy_incremental_mode():
            self._capture_incremental_candidate_transcript(flow_idx)
            self._persist_interview_session_snapshot(flow_idx)
            logger.info(
                "question_audio_legacy_incremental_used",
                extra={"flow_idx": flow_idx, "question_audio_mode": self._question_audio_mode()},
            )
            return

        tx = self._recording_candidate_transcript(rec).strip()
        if tx:
            self.state.flow_candidate_transcripts[flow_idx] = tx
        self._persist_interview_session_snapshot(flow_idx)

    def _queue_transcription_and_transition(
        self,
        flow_idx: int,
        next_index: int | None = None,
        *,
        is_last: bool = False,
        discard_recording: bool = False,
    ) -> None:
        self._close_flow_timestamp(flow_idx)
        self._persist_interview_session_snapshot(flow_idx)
        if discard_recording:
            self.state.flow_recordings.pop(flow_idx, None)
            self.state.flow_candidate_transcripts.pop(flow_idx, None)
        if next_index is not None:
            self.show_flow_screen(next_index)
            return
        if is_last:
            self.finalize_interview()
            return
        self.show_flow_screen(flow_idx + 1)

    def _finalize_current_question_audio_and_doc(self, flow_idx: int) -> None:
        self._close_flow_timestamp(flow_idx)
        self._stop_interview_recording(show_warning=False)

    def _capture_incremental_candidate_transcript(self, flow_idx: int) -> None:
        session = self._safe_attr("recording_session")
        if session is None:
            return
        if not getattr(session, "is_running", False):
            return

        transcribe_new_segments = getattr(session, "transcribe_new_segments", None)
        if not callable(transcribe_new_segments):
            return

        try:
            segments = transcribe_new_segments(language="en")
        except Exception:
            logger.exception("incremental_transcription_failed", extra={"flow_idx": flow_idx})
            return

        if not segments:
            return

        candidate_label = self.recording_candidate_label or "CANDIDATE"
        existing = str(self.state.flow_candidate_transcripts.get(flow_idx, "")).strip()
        merged = append_candidate_segment_text(
            existing,
            segments,
            candidate_label=candidate_label,
        )
        if merged:
            self.state.flow_candidate_transcripts[flow_idx] = merged


    def exit_current_interview(self, flow_idx: int, *, persist_current: Any = None) -> None:
        confirmed = messagebox.askyesno(
            "Exit interview",
            "Stop and save this interview as incomplete, then return to the main screen?",
        )
        if not confirmed:
            return

        self._save_partial_interview_and_return(flow_idx, persist_current=persist_current)

    def _save_partial_interview_and_return(self, flow_idx: int, *, persist_current: Any = None) -> None:
        try:
            if callable(persist_current):
                persist_current()
        except Exception:
            logger.exception("partial_interview_current_question_persist_failed", extra={"flow_idx": flow_idx})

        try:
            self._close_flow_timestamp(flow_idx)
        except Exception:
            logger.exception("partial_interview_timestamp_close_failed", extra={"flow_idx": flow_idx})

        self._stop_interview_recording(show_warning=True)

        try:
            self._persist_interview_session_snapshot(flow_idx)
        except Exception:
            logger.exception("partial_interview_session_snapshot_failed", extra={"flow_idx": flow_idx})

        try:
            payload = self.state.to_dict()
            payload["interview_status"] = "incomplete"
            payload["exit_reason"] = "operator_exit"
            payload["exited_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload["exit_flow_index"] = int(flow_idx)
            DraftManager(Path(self.settings["base_dir"])).save_draft(payload)
        except Exception:
            logger.exception("partial_interview_draft_save_failed", extra={"flow_idx": flow_idx})

        self.recording_session = None
        self.recording_base_name = ""
        self.recording_flow_idx = None
        self.show_start_screen()


    def _on_close(self) -> None:
        try:
            recording_flow_idx = self._safe_attr("recording_flow_idx")
            if recording_flow_idx is not None and self.recording_session is not None:
                self._close_flow_timestamp(recording_flow_idx)
                self.withdraw()
                threading.Thread(target=self._shutdown_after_background_work, daemon=False).start()
                return

            pending_count = self._pending_transcription_count()
            if self._finalize_worker_running or pending_count > 0:
                self.withdraw()
                threading.Thread(target=self._shutdown_after_background_work, daemon=False).start()
                return

            self._delete_interview_recording_artifacts()
        finally:
            self.destroy()

    def _shutdown_after_background_work(self) -> None:
        if self.recording_session is not None:
            self._stop_interview_recording(show_warning=False)
        self._wait_for_pending_transcriptions()
        self._delete_interview_recording_artifacts()

    def _delete_interview_recording_artifacts(self) -> None:
        base_dir = Path(self.settings.get("base_dir", str(DEFAULT_BASE_DIR)))
        delete_recording_artifacts(base_dir, self.state.flow_recordings)

    def show_disqualifier_reference(self) -> None:
        self.signal_reference_view.show_disqualifier_reference()

    def _render_signal_examples(self, parent: ttk.Frame, trait_id: str) -> None:
        self.signal_reference_view.render_signal_examples(parent, trait_id)

    def show_trait_screen_by_trait_id(self, flow_idx: int, trait: dict[str, Any]) -> None:
        self.clear_page()

        tid = trait["id"]
        state = self.state.trait_inputs.setdefault(
            tid,
            {
                "raw_score": None,
                "question_notes": "",
                "trait_notes": "",
                "verbatim_notes": "",
                "absolute_disqualifier": False,
                "skipped": False,
                "no_example_after_followups": False,
            },
        )

        ui = TraitScreenUI(self, flow_idx, trait, state)
        ui.render()

    # -------------------------
    # Custom question screen
    # -------------------------

    def show_custom_question_item_screen(self, flow_idx: int, cq: dict[str, Any]) -> None:
        self.clear_page()

        qid = str(cq["id"])
        qtext = str(cq.get("text", "")).strip()

        self.state.custom_inputs.setdefault(qid, {"question_text": qtext, "answer": "", "skipped": False})
        self.state.custom_inputs[qid]["question_text"] = qtext

        ui = CustomQuestionScreenUI(self, flow_idx, qid, qtext)
        ui.render()

    # -------------------------
    # Finalize + validation
    # -------------------------

    def validate_before_finalize(self) -> None:
        validate_before_finalize_controller(self)

    def finalize_interview(self) -> None:
        controller = self.__dict__.get("finalize_pipeline_controller")
        if controller is None:
            shared_state = self.__dict__.setdefault("shared_state", AppSharedState())
            controller = FinalizePipelineController(self, shared_state)
            self.finalize_pipeline_controller = controller
        controller.finalize_interview()

    def _start_finalize_worker(self, *, attempt: int) -> None:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._finalize_worker_running = True

        def worker() -> None:
            try:
                result = self._run_finalize_pipeline()
                q.put({"ok": True, "result": result, "attempt": attempt})
            except Exception as exc:
                q.put({"ok": False, "error": exc, "tb": traceback.format_exc(), "attempt": attempt})

        threading.Thread(target=worker, daemon=False).start()
        self._poll_finalize_worker(q)

    def _run_finalize_pipeline(self) -> dict[str, Any]:
        controller = self.__dict__.get("finalize_pipeline_controller")
        if controller is None:
            shared_state = self.__dict__.get("shared_state") or SimpleNamespace()
            controller = FinalizePipelineController(self, shared_state)
            self.finalize_pipeline_controller = controller
        return controller.run_finalize_pipeline()

    def _run_finalize_pipeline_legacy(self) -> dict[str, Any]:
        raise_legacy_finalize_guardrail()
        scoring = ScoringEngine.evaluate(self._rubric_with_question_overrides(), self.state.track, self.state.trait_inputs)
        warnings: list[str] = []
        recording_flow_idx = self._safe_attr("recording_flow_idx")

        if recording_flow_idx is not None:
            self._finalize_current_question_audio_and_doc(recording_flow_idx)
        self._wait_for_pending_transcriptions()
        warnings.extend(self._collect_transcription_health_warnings())

        self._hydrate_state_from_session_store()
        payload = self.state.to_dict()
        payload["flow_recordings"] = self.state.flow_recordings
        payload["audio_recording"] = self._serialize_flow_audio_recordings()
        if not self.state.flow_recordings:
            warnings.append("Recording/transcription did not complete. Interview was finalized without transcript text.")
        payload["custom_answers"] = self._ordered_custom_answers()
        flow_tx = self._build_flow_transcript()
        self._apply_candidate_transcripts_to_flow(flow_tx)
        self._rewrite_live_transcript_docx_from_flow(flow_tx)
        payload["flow_transcript"] = flow_tx
        self.state.referral_packet["transcript_path"] = ""

        exporter = DocxExporter(Path(self.settings["base_dir"]) / "Indeed Interview Notes")
        out_path = exporter.export(self._rubric_with_question_overrides(), payload, scoring)
        generated_notes_path = Path(out_path).as_posix().strip()
        self.state.referral_packet["interview_notes_path"] = generated_notes_path

        integration_payload = build_integration_payload(payload, scoring, include_flow_slices=True)
        integration_path = serialize_integration_payload(
            Path(self.settings["base_dir"]),
            integration_payload,
            candidate_name=self.state.candidate_name,
        )

        director_packet = build_director_packet(
            payload=payload,
            scoring=scoring,
            report_path=out_path,
            integration_path=integration_path,
            referral_packet=self.state.referral_packet,
            generated_transcript_path=None,
        )
        send_enabled = bool(self.settings.get("send_director_referral_on_finalize", False))
        endpoint = str(self.settings.get("director_referral_endpoint", "")).strip()
        comm_log_path: Path | None = None
        if send_enabled:
            send_result = send_director_packet(director_packet, endpoint)
            log_event = {
                "event": "director_referral_sent",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "candidate": self.state.candidate_name,
                "status": "success",
                "endpoint": endpoint,
                "documents": director_packet.get("documents", {}),
                "response_status_code": send_result.get("status_code"),
            }
            self.state.communication_log.append(log_event)
            comm_log_path = append_communication_log(
                Path(self.settings["base_dir"]),
                log_event,
                candidate_name=self.state.candidate_name,
            )
        payload["communication_log"] = self.state.communication_log
        integration_payload = build_integration_payload(payload, scoring, include_flow_slices=True)
        integration_path = serialize_integration_payload(
            Path(self.settings["base_dir"]),
            integration_payload,
            candidate_name=self.state.candidate_name,
        )

        saved_at = datetime.utcnow().isoformat() + "Z"
        history_entry = {
            "history_id": str(uuid4()),
            "interview_date": payload.get("candidate", {}).get("interview_date", ""),
            "candidate_name": payload.get("candidate", {}).get("name", ""),
            "interview_score": scoring.get("percent_of_max", 0),
            "determination": scoring.get("outcome", ""),
            "school": payload.get("candidate", {}).get("school", ""),
            "track": payload.get("candidate", {}).get("track", ""),
            "saved_report_path": str(out_path),
            "transcript_path": "",
            "interview_notes_path": generated_notes_path,
            "saved_at": saved_at,
            "offer_status": "not_generated",
            "offer_path": "",
            "offer_letter_path": "",
            "flow_recordings": self._serialize_flow_audio_recordings(),
        }
        self.history_store.append(history_entry)

        return {
            "scoring": scoring,
            "out_path": out_path,
            "integration_path": integration_path,
            "transcript_path": "",
            "director_packet": director_packet,
            "comm_log_enabled": send_enabled,
            "communication_log_path": comm_log_path,
            "warnings": warnings,
        }

    def _serialize_flow_audio_recordings(self) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for flow_idx in sorted(self.state.flow_recordings.keys()):
            rec = self.state.flow_recordings.get(flow_idx, {}) or {}
            attempts = rec.get("attempts") or []
            if not isinstance(attempts, list):
                attempts = []
            serialized.append(
                {
                    "flow_index": flow_idx,
                    "base_name": str(rec.get("base_name") or "").strip(),
                    "wav_paths": {
                        "mic": str(rec.get("mic_wav") or "").strip(),
                        "system": str(rec.get("sys_wav") or "").strip(),
                    },
                    "transcript_paths": {
                        "jsonl": str(rec.get("transcript_jsonl") or "").strip(),
                        "txt": str(rec.get("transcript_txt") or "").strip(),
                    },
                    "candidate_label": str(rec.get("candidate_label") or "").strip(),
                    "candidate_transcript": str(rec.get("candidate_transcript") or "").strip(),
                    "attempt_count": len(attempts),
                    "attempts": [dict(a or {}) for a in attempts],
                }
            )
        return serialized

    def _director_referral_email_attachments(self) -> list[str]:
        packet = self.last_finalize_result.get("director_packet", {}) or {}
        docs = packet.get("documents", {}) or {}
        keys = [
            "resume_path",
            "final_report_path",
            "transcript_path",
            "integration_export_path",
        ]
        attachments: list[str] = []
        for key in keys:
            path_text = str(docs.get(key, "")).strip()
            if not path_text:
                continue
            if not Path(path_text).expanduser().exists():
                continue
            attachments.append(path_text)
        return attachments

    @staticmethod
    def _format_director_email_template(template: str, candidate: dict[str, Any]) -> str:
        candidate_name = str(candidate.get("name", "")).strip()
        first_name, last_name = _split_name_parts(candidate_name)
        values = {
            "candidate_name": candidate_name,
            "first_name": first_name,
            "last_name": last_name,
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
        }
        return render_template(
            str(template or ""),
            values,
            context="director",
            unknown_policy="empty",
        )

    def _copy_text_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def open_director_referral_email_draft(self) -> None:
        packet = self.last_finalize_result.get("director_packet", {}) or {}
        candidate = packet.get("candidate", {}) or {}
        if not packet:
            messagebox.showinfo(
                "Refer Director",
                "Finalize an interview first. Then click Refer Director to open a prefilled Outlook draft.",
            )
            return

        attachments = self._director_referral_email_attachments()
        if not attachments:
            messagebox.showerror("Refer Director", "No referral attachments were found for the last finalized interview.")
            return

        school = str(candidate.get("school", "")).strip()
        templates = self._resolve_school_email_templates(school)
        subject_template = templates.get("director_referral_subject_template", "Director Referral: {candidate_name}")
        body_template = templates.get("director_referral_body_template", "")
        recipient_list = templates.get("director_email_to", "")
        template_values = {
            "candidate_name": str(candidate.get("name", "")).strip(),
            "school": str(candidate.get("school", "")).strip(),
            "track": str(candidate.get("track", "")).strip(),
            "interview_date": str(candidate.get("interview_date", "")).strip(),
        }
        first_name, last_name = _split_name_parts(template_values["candidate_name"])
        template_values["first_name"] = first_name
        template_values["last_name"] = last_name
        if not self._prompt_for_template_values([(subject_template, "director"), (body_template, "director")], template_values):
            return
        candidate = dict(candidate)
        candidate["name"] = template_values["candidate_name"]
        candidate["school"] = template_values["school"]
        candidate["track"] = template_values["track"]
        candidate["interview_date"] = template_values["interview_date"]
        subject = self._format_director_email_template(subject_template, candidate)
        body = self._format_director_email_template(body_template, candidate)
        self._draft_email_with_fallback(
            title="Refer Director",
            subject=subject,
            body=body,
            recipients=recipient_list,
            attachments=attachments,
        )

    def _prompt_resume_if_outcome_requires_it(self, scoring: dict[str, Any]) -> None:
        outcome = _normalize_outcome_label(scoring.get("outcome", ""))
        scoring["outcome"] = outcome
        if outcome not in {"Hire", "Borderline"}:
            return

        existing_resume = str(self.state.referral_packet.get("resume_path", "")).strip()
        if not existing_resume:
            existing_resume = str(
                self.last_finalize_result.get("director_packet", {}).get("documents", {}).get("resume_path", "")
            ).strip()
        if existing_resume:
            self.state.referral_packet["resume_path"] = existing_resume
            self._sync_resume_path_to_finalize_result(existing_resume)
            return

        should_attach = messagebox.askyesno(
            "Attach Resume",
            "This outcome typically requires a resume for director referral.\n\n"
            "Would you like to attach one now?",
        )
        if not should_attach:
            return

        selected = self._pick_resume_document()
        if not selected:
            return
        self.state.referral_packet["resume_path"] = selected
        self._sync_resume_path_to_finalize_result(selected)

    def _pick_resume_document(self) -> str:
        selected = filedialog.askopenfilename(
            title="Select Resume",
            initialdir=str(Path(self.settings["base_dir"])),
            filetypes=[
                ("Documents", "*.pdf *.doc *.docx *.txt *.rtf"),
                ("All files", "*.*"),
            ],
        )
        if not selected:
            return ""
        if is_supported_document_path(selected):
            return selected
        messagebox.showerror("Validation", "Unsupported file type. Use PDF, DOC, DOCX, TXT, or RTF.")
        return ""

    def _sync_resume_path_to_finalize_result(self, resume_path: str) -> None:
        docs = self.last_finalize_result.get("director_packet", {}).get("documents", {})
        if isinstance(docs, dict):
            docs["resume_path"] = resume_path

    def _show_finalize_progress(self) -> None:
        if self.finalize_window and self.finalize_window.winfo_exists():
            return

        win = tk.Toplevel(self)
        apply_professional_ops_theme(win, font_size=int(self.settings["font_size"]))
        win.title("Finalizing Interview")
        win.geometry("480x140")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        container = ttk.Frame(win, padding=14)
        container.pack(fill="both", expand=True)

        log_hint = self.__dict__.get("_app_log_path") or get_configured_log_path()
        status_text = "Processing transcription... this may take a minute."
        if log_hint:
            status_text += f"\nStatus logs: {log_hint}"
        status_label = ttk.Label(
            container,
            text=status_text,
            wraplength=440,
        )
        status_label.pack(anchor="w", pady=(0, 10))

        bar = ttk.Progressbar(container, mode="indeterminate")
        bar.pack(fill="x")
        bar.start(12)

        ttk.Label(container, text="You can close this window; processing will complete in the background.").pack(anchor="w", pady=(10, 0))

        self.finalize_window = win
        self.finalize_progress = bar
        self.finalize_status_label = status_label

    def _close_finalize_progress(self) -> None:
        if self.finalize_progress is not None:
            self.finalize_progress.stop()
            self.finalize_progress = None
        self.finalize_status_label = None
        if self.finalize_window is not None:
            if self.finalize_window.winfo_exists():
                self.finalize_window.grab_release()
                self.finalize_window.destroy()
            self.finalize_window = None

    def _poll_finalize_worker(self, q: queue.Queue[dict[str, Any]]) -> None:
        controller = self.__dict__.get("finalize_pipeline_controller")
        if controller is None:
            self.__dict__.setdefault("shared_state", AppSharedState())
            controller = FinalizePipelineController(self, self.shared_state)
            self.finalize_pipeline_controller = controller
        controller.poll_finalize_worker(q)

    def _refresh_finalize_processing_state(self) -> None:
        label = self.finalize_status_label
        if label is None:
            return
        pending = self._pending_transcription_count()
        if pending > 0:
            label.config(text=f"Processing transcription... {pending} question(s) remaining.")
            return
        label.config(text="Processing transcription... preparing final report.")

    def _ordered_custom_answers(self) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        for q in self.custom_questions:
            qid = str(q["id"])
            ordered.append(self.state.custom_inputs.get(qid, {"question_text": q.get("text", ""), "answer": ""}))
        return ordered

    def _apply_candidate_transcripts_to_flow(
        self,
        flow_transcript: list[dict[str, Any]],
        rec: dict[str, Any] | None = None,
        recording_result: dict[str, Any] | None = None,
    ) -> None:
        # Mutates flow_transcript in place by adding a 'candidate_transcript' field for each item.
        canonical_recording_result = recording_result if recording_result is not None else rec

        by_flow_index: dict[int, str] = {}
        for k, v in (self.state.flow_candidate_transcripts or {}).items():
            try:
                idx = int(k)
            except Exception:
                continue
            txt = str(v or "").strip()
            if txt:
                by_flow_index[idx] = txt

        for idx in sorted((self.state.flow_recordings or {}).keys(), key=lambda value: int(value)):
            try:
                flow_idx = int(idx)
            except Exception:
                continue
            txt = self._resolve_flow_candidate_transcript(flow_idx)
            if txt:
                by_flow_index[flow_idx] = txt

        if canonical_recording_result:
            try:
                flow_idx = int(canonical_recording_result.get("flow_index", -1))
            except Exception:
                flow_idx = -1
            txt = self._extract_candidate_transcript(canonical_recording_result)
            if flow_idx >= 0 and txt:
                by_flow_index[flow_idx] = txt

        for idx, item in enumerate(flow_transcript):
            item["candidate_transcript"] = by_flow_index.get(idx, "")

    def _map_jsonl_candidate_segments_to_flow(
        self,
        jsonl_path: Path,
        candidate_label: str,
    ) -> dict[int, str]:
        windows = self._build_flow_windows()
        if not windows:
            return {}
        segments = self._load_candidate_segments_from_jsonl(jsonl_path, candidate_label)
        return map_segments_to_flow_indices(segments, windows)

    def _build_flow_windows(self) -> list[tuple[int, float, float]]:
        return build_flow_time_windows(self.state.flow_time_marks or [])

    def _load_candidate_segments_from_jsonl(self, jsonl_path: Path, candidate_label: str) -> list[dict[str, Any]]:
        return load_candidate_segments(jsonl_path, candidate_label)

    def _build_flow_transcript(self) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        for it in self.active_flow:
            if it["type"] == "trait":
                entry = self._build_trait_transcript_entry(it["id"])
                if entry:
                    transcript.append(entry)
                continue

            entry = self._build_custom_transcript_entry(it["id"])
            if entry:
                transcript.append(entry)

        return transcript

    def _build_trait_transcript_entry(self, tid: str) -> Optional[dict[str, Any]]:
        trait = self._trait_by_id(tid)
        if not trait:
            return None

        tstate = self.state.trait_inputs.get(tid, {}) or {}
        return {
            "type": "trait",
            "id": tid,
            "title": trait["name"],
            "question": self.get_primary_question_text(trait),
            "raw_score": tstate.get("raw_score"),
            "skipped": bool(tstate.get("skipped", False)),
            "no_example_after_followups": bool(tstate.get("no_example_after_followups", False)),
            "question_notes": tstate.get("question_notes", ""),
            "trait_notes": tstate.get("trait_notes", ""),
            "verbatim_notes": tstate.get("verbatim_notes", ""),
            "absolute_disqualifier": bool(tstate.get("absolute_disqualifier", False)),
        }

    def _build_custom_transcript_entry(self, cid: str) -> Optional[dict[str, Any]]:
        cq = self._custom_by_id(cid)
        if not cq:
            return None

        cstate = self.state.custom_inputs.get(cid, {}) or {}
        ans = cstate.get("answer", "")
        return {
            "type": "custom",
            "id": cid,
            "title": "Custom Question",
            "question": str(cq.get("text", "")).strip(),
            "answer": ans,
            "skipped": bool(cstate.get("skipped", False)),
        }

    # -----------------------------
    # Question editor GUI
    # -----------------------------

    def open_question_editor(self) -> None:
        QuestionEditorWindow(self)

    def open_question_settings(self) -> None:
        QuestionSettingsWindow(self)


# =========================
# Trait screen UI helper
# =========================


class OfferGeneratorWindow(tk.Toplevel):
    def __init__(self, app: InterviewApp, history_row: dict[str, Any]):
        super().__init__(app)
        self.app = app
        self.history_row = history_row
        self.title("Generate Offer")
        self.geometry("760x700")
        apply_professional_ops_theme(self, font_size=int(self.app.settings.get("font_size", 10)))

        full_name = str(history_row.get("candidate_name", "")).strip().split()
        first_name = full_name[0] if full_name else ""
        last_name = " ".join(full_name[1:]) if len(full_name) > 1 else ""

        self.school_var = StringVar(value=str(history_row.get("school", "")).strip())
        self.first_name_var = StringVar(value=first_name)
        self.last_name_var = StringVar(value=last_name)
        self.city_var = StringVar(value="")
        self.position_var = StringVar(value=POSITION_OPTIONS[0])
        self.start_time_var = StringVar(value="08:00 AM")
        self.end_time_var = StringVar(value="05:00 PM")
        self.hourly_pay_var = StringVar(value="")
        self.hours_var = StringVar(value="40")
        self.status_var = StringVar(value="")

        self.template_full_var = StringVar(value="")
        self.template_part_var = StringVar(value="")
        self.output_dir_var = StringVar(value="")

        self._load_school_defaults()
        self._build()

    def _load_school_defaults(self) -> None:
        cfg = self.app.school_offer_config(self.school_var.get())
        self.template_full_var.set(cfg.get("full_time_template", ""))
        self.template_part_var.set(cfg.get("part_time_template", ""))
        self.output_dir_var.set(cfg.get("offer_output_dir", ""))

    def _build(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Generate Offer Letter", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        determination = str(self.history_row.get("determination", ""))
        ttk.Label(container, text=f"Determination: {determination} (offers are always allowed)", foreground="#b45309").pack(anchor="w", pady=(0, 8))

        self._labeled_entry(container, "School", self.school_var)
        self._labeled_entry(container, "First Name", self.first_name_var)
        self._labeled_entry(container, "Last Name", self.last_name_var)
        self._labeled_entry(container, "City", self.city_var)

        ttk.Label(container, text="Position").pack(anchor="w")
        ttk.Combobox(container, textvariable=self.position_var, values=POSITION_OPTIONS, state="readonly").pack(fill="x", pady=(0, 6))

        ttk.Label(container, text="Start Date").pack(anchor="w")
        self.start_date_picker = DateEntry(container, date_pattern="mm/dd/yyyy")
        self.start_date_picker.pack(fill="x", pady=(0, 6))

        times = self._time_slots()
        ttk.Label(container, text="Start Time").pack(anchor="w")
        ttk.Combobox(container, textvariable=self.start_time_var, values=times, state="readonly").pack(fill="x", pady=(0, 6))
        ttk.Label(container, text="End Time").pack(anchor="w")
        ttk.Combobox(container, textvariable=self.end_time_var, values=times, state="readonly").pack(fill="x", pady=(0, 6))

        self._labeled_entry(container, "Hourly Pay", self.hourly_pay_var)
        self._labeled_entry(container, "Hours per week", self.hours_var)

        template_box = ttk.LabelFrame(container, text="School Offer Template Settings")
        template_box.pack(fill="x", pady=(8, 6))
        self._labeled_entry(template_box, "Full-time template (.docx/.docm)", self.template_full_var)
        ttk.Button(template_box, text="Browse", command=lambda: self._browse_file(self.template_full_var)).pack(anchor="w")
        self._labeled_entry(template_box, "Part-time template (.docx/.docm)", self.template_part_var)
        ttk.Button(template_box, text="Browse", command=lambda: self._browse_file(self.template_part_var)).pack(anchor="w")
        self._labeled_entry(template_box, "Offer output folder", self.output_dir_var)
        ttk.Button(template_box, text="Browse", command=self._browse_dir).pack(anchor="w")
        ttk.Button(template_box, text="Save school settings", command=self._save_school_settings).pack(anchor="e", pady=(6, 0))

        ttk.Label(container, textvariable=self.status_var, foreground="#0f766e").pack(anchor="w", pady=(4, 0))
        action = ttk.Frame(container)
        action.pack(fill="x", pady=(10, 0))
        ttk.Button(action, text="Generate Offer", command=self._generate_offer).pack(side="right")

    @staticmethod
    def _labeled_entry(parent: tk.Widget, text: str, var: StringVar) -> None:
        ttk.Label(parent, text=text).pack(anchor="w")
        ttk.Entry(parent, textvariable=var).pack(fill="x", pady=(0, 6))

    @staticmethod
    def _time_slots() -> list[str]:
        slots: list[str] = []
        base = datetime.strptime("12:00 AM", "%I:%M %p")
        for i in range(96):
            value = base + (i * timedelta(minutes=15))
            slots.append(value.strftime("%I:%M %p"))
        return slots

    def _browse_file(self, target: StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select Template",
            filetypes=[("Word Documents", "*.docx *.docm"), ("All files", "*.*")],
        )
        if path:
            target.set(path)

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="Select offer output folder")
        if path:
            self.output_dir_var.set(path)

    def _save_school_settings(self) -> None:
        config = {
            "full_time_template": self.template_full_var.get(),
            "part_time_template": self.template_part_var.get(),
            "offer_output_dir": self.output_dir_var.get(),
        }
        self.app.save_school_offer_config(self.school_var.get(), config)
        self.status_var.set("School settings saved.")

    def _generate_offer(self) -> None:
        try:
            hours = int(self.hours_var.get().strip())
            hourly_pay = float(self.hourly_pay_var.get().strip())
        except ValueError:
            messagebox.showerror("Offer Generator", "Hours must be an integer and hourly pay must be numeric.")
            return

        data = OfferInput(
            first_name=self.first_name_var.get().strip(),
            last_name=self.last_name_var.get().strip(),
            city=self.city_var.get().strip(),
            position=self.position_var.get().strip(),
            start_date=self.start_date_picker.get_date(),
            start_time_12h=self.start_time_var.get().strip(),
            end_time_12h=self.end_time_var.get().strip(),
            hourly_pay=hourly_pay,
            hours=hours,
            created_on=date.today(),
        )

        employment_type = OfferLetterService.classify_employment_type(data.hours)
        template_path = self.template_full_var.get().strip() if employment_type == "full_time" else self.template_part_var.get().strip()
        output_dir = Path(self.output_dir_var.get().strip())
        if not template_path:
            messagebox.showerror("Offer Generator", f"Missing {employment_type.replace('_', '-')} template path.")
            return
        if not output_dir.as_posix().strip():
            messagebox.showerror("Offer Generator", "Offer output folder is required.")
            return

        filename = build_offer_filename(data.first_name, data.last_name, data.created_on)
        out_path = output_dir / filename
        try:
            OfferLetterService.render_offer(Path(template_path), out_path, data)
        except Exception as exc:
            messagebox.showerror("Offer Generator", str(exc))
            return

        self.app._mark_offer_generated(self.history_row, out_path)
        self.status_var.set(f"Offer created: {out_path}")
        messagebox.showinfo("Offer Generated", f"Offer saved to:\n{out_path}")


# =========================
# Entrypoint
# =========================

if __name__ == "__main__":
    try:
        initialize_app_logging()
        app = InterviewApp()
        app.mainloop()
    except Exception as exc:
        crash_report_path = write_crash_report(
            source="mainloop",
            exc_type=type(exc),
            exc_value=exc,
            exc_traceback=exc.__traceback__,
        )
        try:
            crash_hint = f"\n\nCrash report:\n{crash_report_path}" if crash_report_path else ""
            messagebox.showerror("Fatal Error", f"{exc}\n\n{traceback.format_exc()}{crash_hint}")
        except Exception:
            raise
