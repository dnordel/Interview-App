from __future__ import annotations

import json
from typing import Any
from pathlib import Path

import tkinter as tk
from tkinter import END, BooleanVar, IntVar, StringVar, filedialog, messagebox, ttk

from platform_services import DEFAULT_BASE_DIR, MAX_FONT_SIZE, MIN_FONT_SIZE, now_stamp
from data_store import default_school_offer_settings
from scoring_reporting import (
    insert_token_into_focused_widget,
    placeholder_picker_options,
    token_from_picker_label,
    validate_template_map,
)
from interview_runtime import (
    DEEPSEEK_PROMPT_TEMPLATE_KEYS,
    DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS,
    DEFAULT_DEEPSEEK_PROMPT_TEMPLATES,
    format_deepseek_question_prompt_overrides,
    load_deepseek_prompt_templates,
    normalize_deepseek_prompt_templates,
    parse_deepseek_question_prompt_overrides,
    save_deepseek_prompt_templates,
)
from ui_composition import KeyboardPathSession, format_guidance
from tk_theme import COLORS, apply_professional_ops_theme, configure_text_widget


QUESTION_AUDIO_MODE_LEGACY_INCREMENTAL = "legacy_incremental"
QUESTION_AUDIO_MODE_TIMESTAMP_SLICING = "timestamp_slicing"


class SettingsWindow(tk.Toplevel):
    _TAB_GENERAL = "general"
    _TAB_TEMPLATES = "templates"
    _TAB_NOTIFICATIONS = "notifications"
    _TAB_STORAGE = "storage"
    _TAB_SECURITY = "security"
    _TAB_DEEPSEEK = "deepseek"

    def __init__(self, app: "InterviewApp"):
        super().__init__(app)
        self.app = app

        self.title("Settings")
        self.geometry("820x560")
        apply_professional_ops_theme(self, font_size=int(self.app.settings.get("font_size", 10)))

        self.path_var = StringVar(value=self.app.settings["base_dir"])
        self.school_notes_dir_vars = self._build_school_notes_dir_vars()
        self.size_var = IntVar(value=self.app.settings["font_size"])
        mode = str(self.app.settings.get("question_audio_mode") or QUESTION_AUDIO_MODE_TIMESTAMP_SLICING)
        self.audio_mode_var = StringVar(value=mode)
        self.endpoint_var = StringVar(value=str(self.app.settings.get("director_referral_endpoint", "")))
        self.send_on_finalize_var = BooleanVar(value=bool(self.app.settings.get("send_director_referral_on_finalize", False)))
        self.director_email_to_var = StringVar(value=str(self.app.settings.get("director_email_to", "")))
        self.director_email_subject_var = StringVar(value=str(self.app.settings.get("director_email_subject_template", "Director Referral: {candidate_name}")))
        self.director_email_body_var = StringVar(value=str(self.app.settings.get("director_email_body_template", "")))
        self.offer_email_to_var = StringVar(value=str(self.app.settings.get("offer_email_to", "")))
        self.offer_approval_subject_var = StringVar(value=str(self.app.settings.get("offer_approval_subject_template", "Offer Approval Needed: {candidate_name}")))
        self.offer_approval_body_var = StringVar(value=str(self.app.settings.get("offer_approval_body_template", "")))
        self.offer_acceptance_subject_var = StringVar(value=str(self.app.settings.get("offer_acceptance_subject_template", "Offer Accepted: {candidate_name}")))
        self.offer_acceptance_body_var = StringVar(value=str(self.app.settings.get("offer_acceptance_body_template", "")))
        self.offer_acceptance_attach_var = BooleanVar(value=bool(self.app.settings.get("offer_acceptance_attach_offer_file", True)))
        self.welcome_subject_var = StringVar(value=str(self.app.settings.get("welcome_email_subject_template", "Welcome to {school}, {candidate_name}!")))
        self.welcome_body_var = StringVar(value=str(self.app.settings.get("welcome_email_body_template", "")))
        self.onboarding_pdf_var = StringVar(value=str(self.app.settings.get("welcome_onboarding_pdf_path", "")))
        self.whisper_language_var = StringVar(value=str(self.app.settings.get("whisper_language", "en")))
        self.whisper_vad_filter_var = BooleanVar(value=bool(self.app.settings.get("whisper_vad_filter", True)))
        self.whisper_beam_size_var = IntVar(value=int(self.app.settings.get("whisper_beam_size", 5) or 5))
        self.whisper_temperature_var = StringVar(value=str(self.app.settings.get("whisper_temperature", 0.0)))
        self.deepseek_prompt_templates = normalize_deepseek_prompt_templates(load_deepseek_prompt_templates())
        self._high_risk_toggle_guard = False
        self.settings_placeholder_var = StringVar(value="")
        self._settings_template_widgets: list[tk.Misc] = []
        self._tab_message_vars: dict[str, StringVar] = {}
        self._tab_summary_frames: dict[str, ttk.Frame] = {}
        self._field_error_vars: dict[str, StringVar] = {}
        self._field_focus_targets: dict[str, tk.Misc] = {}
        self._tab_focus_targets: dict[str, list[tk.Misc]] = {}
        self._tab_order: list[str] = [
            self._TAB_GENERAL,
            self._TAB_TEMPLATES,
            self._TAB_NOTIFICATIONS,
            self._TAB_DEEPSEEK,
            self._TAB_STORAGE,
            self._TAB_SECURITY,
        ]
        self.keyboard_session = KeyboardPathSession(
            logger=getattr(self.app, "metrics_logger", None),
            flow_id="interview_settings",
            screen_id="settings_window",
        )

        self._build()

    def _build(self) -> None:
        self._configure_focus_styles()
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        general_tab = ttk.Frame(self.notebook, padding=10)
        templates_tab = ttk.Frame(self.notebook, padding=10)
        notifications_tab = ttk.Frame(self.notebook, padding=10)
        deepseek_tab = ttk.Frame(self.notebook, padding=10)
        storage_tab = ttk.Frame(self.notebook, padding=10)
        security_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(general_tab, text="General")
        self.notebook.add(templates_tab, text="Templates")
        self.notebook.add(notifications_tab, text="Notifications")
        self.notebook.add(deepseek_tab, text="DeepSeek")
        self.notebook.add(storage_tab, text="Storage")
        self.notebook.add(security_tab, text="Security")

        self._build_general_tab(general_tab)
        self._build_templates_tab(templates_tab)
        self._build_notifications_tab(notifications_tab)
        self._build_deepseek_tab(deepseek_tab)
        self._build_storage_tab(storage_tab)
        self._build_security_tab(security_tab)

        btn_row = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_row.pack(fill="x")
        save_button = ttk.Button(btn_row, text="Save", command=self._save)
        save_button.pack(side="right")
        cancel_button = ttk.Button(btn_row, text="Cancel", command=self._cancel)
        cancel_button.pack(side="right", padx=6)

        self.keyboard_session.bind(self)
        self.keyboard_session.bind(self.notebook)
        self._run_accessibility_gate()

    def _build_tab_header(self, parent: ttk.Frame, tab_key: str, title: str, detail: str) -> None:
        self._tab_message_vars[tab_key] = StringVar(value="")
        ttk.Label(parent, text=title, style="Heading.TLabel").pack(anchor="w")
        self._wrapped_label(parent, text=detail, foreground="#475569").pack(anchor="w", pady=(2, 6))
        self._wrapped_label(parent, textvariable=self._tab_message_vars[tab_key], foreground="#b91c1c").pack(anchor="w", pady=(0, 6))
        summary_frame = ttk.Frame(parent)
        summary_frame.pack(fill="x", pady=(0, 6))
        self._tab_summary_frames[tab_key] = summary_frame

    def _build_field_error(self, parent: ttk.Frame, field_key: str) -> None:
        var = StringVar(value="")
        self._field_error_vars[field_key] = var
        self._wrapped_label(parent, textvariable=var, foreground="#b91c1c").pack(anchor="w", pady=(2, 0))

    def _build_general_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_GENERAL,
            "General settings",
            "Adjust in-app display and question-audio behavior.",
        )

        ttk.Label(parent, text="Font size:").pack(anchor="w", pady=(12, 0))
        scale = ttk.Scale(parent, from_=MIN_FONT_SIZE, to=MAX_FONT_SIZE, orient="horizontal", variable=self.size_var)
        scale.pack(fill="x", pady=(6, 10))

        ttk.Label(parent, text="Question audio mode:").pack(anchor="w")
        mode_combo = ttk.Combobox(
            parent,
            textvariable=self.audio_mode_var,
            state="readonly",
            values=[QUESTION_AUDIO_MODE_TIMESTAMP_SLICING, QUESTION_AUDIO_MODE_LEGACY_INCREMENTAL],
        )
        mode_combo.pack(fill="x", pady=(4, 2))
        self._wrapped_label(
            parent,
            text=(
                "timestamp_slicing: derive per-question answers from final transcript timestamps\n"
                "legacy_incremental: retain legacy hook calls on each Next/Back/Skip"
            ),
            foreground="#475569",
        ).pack(anchor="w", pady=(0, 2))
        self._build_field_error(parent, "question_audio_mode")
        self._field_focus_targets["question_audio_mode"] = mode_combo
        self._tab_focus_targets[self._TAB_GENERAL] = [scale, mode_combo]

    def _build_templates_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_TEMPLATES,
            "Template settings",
            "Configure email recipients and templates for director referral, offers, and welcome messages.",
        )

        self._build_settings_placeholder_picker(parent)

        director_row = ttk.LabelFrame(parent, text="Director referral email templates", padding=10)
        director_row.pack(fill="x", pady=(8, 6))
        ttk.Label(director_row, text="To:").pack(anchor="w")
        ttk.Entry(director_row, textvariable=self.director_email_to_var).pack(fill="x", pady=(4, 0))
        ttk.Label(director_row, text="Subject template:").pack(anchor="w", pady=(8, 0))
        director_subject_entry = ttk.Entry(director_row, textvariable=self.director_email_subject_var)
        director_subject_entry.pack(fill="x", pady=(4, 0))
        self.director_email_subject_widget = director_subject_entry
        self._field_focus_targets["director_subject"] = director_subject_entry
        self._build_field_error(director_row, "director_subject")
        ttk.Label(director_row, text="Body template:").pack(anchor="w", pady=(8, 0))
        director_body_entry = tk.Text(director_row, height=4, wrap="word")
        configure_text_widget(director_body_entry, font_size=int(self.app.settings.get("font_size", 10)))
        director_body_entry.pack(fill="x", pady=(4, 0))
        director_body_entry.insert("1.0", self.director_email_body_var.get())
        self.director_email_body_widget = director_body_entry
        self._field_focus_targets["director_body"] = director_body_entry
        self._build_field_error(director_row, "director_body")

        offer_row = ttk.LabelFrame(parent, text="Offer and welcome email templates", padding=10)
        offer_row.pack(fill="both", expand=True, pady=(4, 6))
        ttk.Label(offer_row, text="Offer recipients (To):").pack(anchor="w")
        ttk.Entry(offer_row, textvariable=self.offer_email_to_var).pack(fill="x", pady=(4, 0))

        ttk.Label(offer_row, text="Offer approval subject template:").pack(anchor="w", pady=(8, 0))
        offer_approval_subject_entry = ttk.Entry(offer_row, textvariable=self.offer_approval_subject_var)
        offer_approval_subject_entry.pack(fill="x", pady=(4, 0))
        self.offer_approval_subject_widget = offer_approval_subject_entry
        self._field_focus_targets["offer_approval_subject"] = offer_approval_subject_entry
        self._build_field_error(offer_row, "offer_approval_subject")
        ttk.Label(offer_row, text="Offer approval body template:").pack(anchor="w", pady=(8, 0))
        offer_approval_body_entry = tk.Text(offer_row, height=4, wrap="word")
        configure_text_widget(offer_approval_body_entry, font_size=int(self.app.settings.get("font_size", 10)))
        offer_approval_body_entry.pack(fill="x", pady=(4, 0))
        offer_approval_body_entry.insert("1.0", self.offer_approval_body_var.get())
        self.offer_approval_body_widget = offer_approval_body_entry
        self._field_focus_targets["offer_approval_body"] = offer_approval_body_entry
        self._build_field_error(offer_row, "offer_approval_body")

        ttk.Label(offer_row, text="Offer acceptance subject template:").pack(anchor="w", pady=(8, 0))
        offer_acceptance_subject_entry = ttk.Entry(offer_row, textvariable=self.offer_acceptance_subject_var)
        offer_acceptance_subject_entry.pack(fill="x", pady=(4, 0))
        self.offer_acceptance_subject_widget = offer_acceptance_subject_entry
        self._field_focus_targets["offer_acceptance_subject"] = offer_acceptance_subject_entry
        self._build_field_error(offer_row, "offer_acceptance_subject")
        ttk.Checkbutton(offer_row, text="Attach generated offer file in acceptance draft", variable=self.offer_acceptance_attach_var).pack(anchor="w", pady=(8, 0))
        ttk.Label(offer_row, text="Offer acceptance body template:").pack(anchor="w", pady=(8, 0))
        offer_acceptance_body_entry = tk.Text(offer_row, height=4, wrap="word")
        configure_text_widget(offer_acceptance_body_entry, font_size=int(self.app.settings.get("font_size", 10)))
        offer_acceptance_body_entry.pack(fill="x", pady=(4, 0))
        offer_acceptance_body_entry.insert("1.0", self.offer_acceptance_body_var.get())
        self.offer_acceptance_body_widget = offer_acceptance_body_entry
        self._field_focus_targets["offer_acceptance_body"] = offer_acceptance_body_entry
        self._build_field_error(offer_row, "offer_acceptance_body")

        ttk.Label(offer_row, text="Welcome email subject template:").pack(anchor="w", pady=(8, 0))
        welcome_subject_entry = ttk.Entry(offer_row, textvariable=self.welcome_subject_var)
        welcome_subject_entry.pack(fill="x", pady=(4, 0))
        self.welcome_subject_widget = welcome_subject_entry
        self._field_focus_targets["welcome_subject"] = welcome_subject_entry
        self._build_field_error(offer_row, "welcome_subject")
        ttk.Label(offer_row, text="Welcome email body template:").pack(anchor="w", pady=(8, 0))
        welcome_body_entry = tk.Text(offer_row, height=4, wrap="word")
        configure_text_widget(welcome_body_entry, font_size=int(self.app.settings.get("font_size", 10)))
        welcome_body_entry.pack(fill="x", pady=(4, 0))
        welcome_body_entry.insert("1.0", self.welcome_body_var.get())
        self.welcome_body_widget = welcome_body_entry
        self._field_focus_targets["welcome_body"] = welcome_body_entry
        self._build_field_error(offer_row, "welcome_body")

        onboarding_path_row = ttk.Frame(offer_row)
        onboarding_path_row.pack(fill="x", pady=(8, 0))
        ttk.Label(onboarding_path_row, text="Welcome onboarding guide PDF path:").pack(anchor="w")
        ttk.Entry(onboarding_path_row, textvariable=self.onboarding_pdf_var).pack(side="left", fill="x", expand=True, pady=(4, 0))
        ttk.Button(onboarding_path_row, text="Browse...", command=self._browse_onboarding_pdf).pack(side="left", padx=(6, 0), pady=(4, 0))

        self._settings_template_widgets = [
            self.director_email_subject_widget,
            self.director_email_body_widget,
            self.offer_approval_subject_widget,
            self.offer_approval_body_widget,
            self.offer_acceptance_subject_widget,
            self.offer_acceptance_body_widget,
            self.welcome_subject_widget,
            self.welcome_body_widget,
        ]
        self._tab_focus_targets[self._TAB_TEMPLATES] = [
            self.director_email_subject_widget,
            self.director_email_body_widget,
            self.offer_approval_subject_widget,
            self.offer_approval_body_widget,
            self.offer_acceptance_subject_widget,
            self.offer_acceptance_body_widget,
            self.welcome_subject_widget,
            self.welcome_body_widget,
        ]

    def _build_notifications_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_NOTIFICATIONS,
            "Notification automation settings",
            "Configure how director referral notifications are triggered and where they are sent.",
        )

        ttk.Label(parent, text="Director referral endpoint URL:").pack(anchor="w")
        endpoint_entry = ttk.Entry(parent, textvariable=self.endpoint_var)
        endpoint_entry.pack(fill="x", pady=(4, 4))
        self._field_focus_targets["director_referral_endpoint"] = endpoint_entry
        self._build_field_error(parent, "director_referral_endpoint")

        high_risk_row = ttk.LabelFrame(parent, text="High-Risk Actions / Security & Automation", padding=10)
        high_risk_row.pack(fill="x", pady=(8, 8))
        self._wrapped_label(
            high_risk_row,
            text=(
                "These automation actions can send referral content without an extra review step. "
                "Enable only when your endpoint and recipient policies are verified."
            ),
            foreground="#7f1d1d",
        ).pack(anchor="w", pady=(0, 6))
        send_check = ttk.Checkbutton(
            high_risk_row,
            text="Send director referral packet when finalizing interview",
            variable=self.send_on_finalize_var,
        )
        send_check.pack(anchor="w", pady=(0, 2))
        self._wrapped_label(
            high_risk_row,
            text="Risk: Finalize will trigger external referral delivery and may share sensitive candidate details.",
            foreground="#7f1d1d",
        ).pack(anchor="w")
        self.send_on_finalize_var.trace_add("write", self._on_send_on_finalize_toggled)
        self._tab_focus_targets[self._TAB_NOTIFICATIONS] = [endpoint_entry, send_check]

    def _build_deepseek_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_DEEPSEEK,
            "DeepSeek prompt settings",
            "Edit every prompt sent to local DeepSeek during finalize.",
        )
        scroller = ttk.Frame(parent)
        scroller.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroller, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroller, orient="vertical", command=canvas.yview)
        prompt_frame = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=prompt_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        prompt_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        self.deepseek_prompt_widgets: dict[str, tk.Text] = {}
        self.deepseek_question_prompt_widgets: dict[str, tk.Text] = {}
        labels = {
            "answer_summary_system": "Per-question summary system prompt",
            "answer_summary_user": "Per-question summary user prompt",
            "executive_summary_system": "Executive summary system prompt",
            "executive_summary_user": "Executive summary user prompt",
            "trait_suggestion_system": "Trait signal suggestion system prompt",
            "trait_suggestion_user": "Trait signal suggestion user prompt",
            "trait_scoring_system": "Trait scoring system prompt",
            "trait_scoring_user": "Trait scoring user prompt",
        }
        question_labels = {
            "answer_summary_system_by_question": "Question-specific answer summary system prompts",
            "answer_summary_user_by_question": "Question-specific answer summary user prompts",
            "trait_suggestion_system_by_question": "Question-specific AI advisory evaluation system prompts",
            "trait_suggestion_user_by_question": "Question-specific AI advisory evaluation user prompts",
            "trait_scoring_system_by_question": "Question-specific AI trait scoring system prompts",
            "trait_scoring_user_by_question": "Question-specific AI trait scoring user prompts",
        }
        focus_targets: list[tk.Misc] = []
        for row, key in enumerate(DEEPSEEK_PROMPT_TEMPLATE_KEYS):
            box = ttk.LabelFrame(prompt_frame, text=labels[key], padding=6)
            box.grid(row=row // 2, column=row % 2, sticky="nsew", padx=4, pady=4)
            widget = tk.Text(box, height=5, wrap="word")
            configure_text_widget(widget, font_size=int(self.app.settings.get("font_size", 10)))
            widget.pack(fill="both", expand=True)
            widget.insert("1.0", self.deepseek_prompt_templates.get(key, DEFAULT_DEEPSEEK_PROMPT_TEMPLATES[key]))
            self.deepseek_prompt_widgets[key] = widget
            self._field_focus_targets[f"deepseek_{key}"] = widget
            self._build_field_error(box, f"deepseek_{key}")
            focus_targets.append(widget)
        for offset, key in enumerate(DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS, start=4):
            box = ttk.LabelFrame(prompt_frame, text=question_labels[key], padding=6)
            box.grid(row=offset, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
            widget = tk.Text(box, height=5, wrap="word")
            configure_text_widget(widget, font_size=int(self.app.settings.get("font_size", 10)))
            widget.pack(fill="both", expand=True)
            value = self.deepseek_prompt_templates.get(key, {})
            widget.insert("1.0", format_deepseek_question_prompt_overrides(value if isinstance(value, dict) else {}))
            self.deepseek_question_prompt_widgets[key] = widget
            self._field_focus_targets[f"deepseek_{key}"] = widget
            self._build_field_error(box, f"deepseek_{key}")
            self._wrapped_label(
                box,
                text="Format: Question: trait_1, next line Prompt:, then prompt text. Separate entries with ---.",
                foreground="#475569",
            ).pack(anchor="w", pady=(4, 0))
            focus_targets.append(widget)
        prompt_frame.columnconfigure(0, weight=1)
        prompt_frame.columnconfigure(1, weight=1)
        for row in range(10):
            prompt_frame.rowconfigure(row, weight=1)
        self._tab_focus_targets[self._TAB_DEEPSEEK] = focus_targets

    def _on_send_on_finalize_toggled(self, *_args: object) -> None:
        if self._high_risk_toggle_guard:
            return
        if not bool(self.send_on_finalize_var.get()):
            return
        confirmed = self._confirm_high_risk_toggle_enabled(
            title="Enable high-risk automation?",
            detail=(
                "This will allow interview finalize to auto-send director referral packets. "
                "Confirm only if this behavior is intentional for your workflow."
            ),
        )
        if confirmed:
            return
        self._high_risk_toggle_guard = True
        self.send_on_finalize_var.set(False)
        self._high_risk_toggle_guard = False

    @staticmethod
    def _confirm_high_risk_toggle_enabled(title: str, detail: str) -> bool:
        return bool(messagebox.askyesno(title, detail))

    def _build_storage_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_STORAGE,
            "Storage settings",
            "Set where interview outputs are saved.",
        )
        ttk.Label(parent, text="Base output folder:").pack(anchor="w")
        entry = ttk.Entry(parent, textvariable=self.path_var)
        entry.pack(fill="x", pady=(4, 4))
        ttk.Button(parent, text="Browse...", command=self._browse).pack(anchor="w")
        self._build_field_error(parent, "base_dir")
        focus_targets: list[tk.Misc] = [entry]

        notes_frame = ttk.LabelFrame(parent, text="Interview notes folders by school", padding=10)
        notes_frame.pack(fill="x", pady=(12, 0))
        self._wrapped_label(
            notes_frame,
            text="Use paths under the local Dropbox root. Leading \\Dropbox is portable across computers.",
            foreground="#475569",
        ).pack(anchor="w", pady=(0, 6))
        for school, var in getattr(self, "school_notes_dir_vars", {}).items():
            row = ttk.Frame(notes_frame)
            row.pack(fill="x", pady=(4, 0))
            ttk.Label(row, text=f"{school}:", width=18).pack(side="left")
            notes_entry = ttk.Entry(row, textvariable=var)
            notes_entry.pack(side="left", fill="x", expand=True)
            focus_targets.append(notes_entry)
        self._tab_focus_targets[self._TAB_STORAGE] = focus_targets

    def _build_security_tab(self, parent: ttk.Frame) -> None:
        self._build_tab_header(
            parent,
            self._TAB_SECURITY,
            "Security and safety settings",
            "Whisper tuning is optional. Keep values in recommended ranges to reduce runtime failures.",
        )
        whisper_row = ttk.LabelFrame(parent, text="Whisper Transcription Settings", padding=10)
        whisper_row.pack(fill="x", pady=(0, 4))

        ttk.Label(whisper_row, text="Language code (e.g., en, es, fr):").grid(row=0, column=0, sticky="w")
        language_entry = ttk.Entry(whisper_row, textvariable=self.whisper_language_var, width=12)
        language_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(whisper_row, text="Beam size (1-10):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        beam_spin = ttk.Spinbox(whisper_row, from_=1, to=10, textvariable=self.whisper_beam_size_var, width=8)
        beam_spin.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(whisper_row, text="Temperature (0.0-1.0):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        temperature_entry = ttk.Entry(whisper_row, textvariable=self.whisper_temperature_var, width=8)
        temperature_entry.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self._field_focus_targets["whisper_temperature"] = temperature_entry
        temp_error = self._wrapped_label(whisper_row, text="", foreground="#b91c1c")
        temp_error.grid(row=2, column=2, sticky="w", padx=(10, 0), pady=(6, 0))
        self._field_error_vars["whisper_temperature"] = StringVar(value="")
        temp_error.configure(textvariable=self._field_error_vars["whisper_temperature"])

        vad_check = ttk.Checkbutton(whisper_row, text="Enable VAD filter", variable=self.whisper_vad_filter_var)
        vad_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(whisper_row, text="Reset advanced defaults", command=self._confirm_restore_recommended_whisper_defaults).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        self._wrapped_label(
            whisper_row,
            text="If custom settings fail on question 1, the app will switch to recommended defaults for this session.",
            foreground="#475569",
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        whisper_row.columnconfigure(0, weight=1)
        self._tab_focus_targets[self._TAB_SECURITY] = [language_entry, beam_spin, temperature_entry, vad_check]

    @staticmethod
    def _wrapped_label(parent: tk.Misc, text: str = "", foreground: str = "", textvariable: StringVar | None = None) -> ttk.Label:
        options: dict[str, Any] = {"justify": "left", "wraplength": 760}
        if text:
            options["text"] = text
        if textvariable is not None:
            options["textvariable"] = textvariable
        if foreground:
            options["foreground"] = foreground
        return ttk.Label(parent, **options)

    def _configure_focus_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Accessible.TEntry", padding=3)

    def _run_accessibility_gate(self) -> None:
        failures: list[str] = []
        for tab_key, widgets in self._tab_focus_targets.items():
            if not widgets:
                failures.append(f"{tab_key}: no focusable widgets")
                continue
            for widget in widgets:
                if not widget.winfo_exists():
                    failures.append(f"{tab_key}: missing widget")
                    continue
                widget.configure(takefocus=True)
                self._bind_focus_visibility(widget)
        if failures:
            self._log_telemetry("settings_accessibility_gate_failed", failures=len(failures), tabs="|".join(sorted(self._tab_focus_targets.keys())))

    @staticmethod
    def _bind_focus_visibility(widget: tk.Misc) -> None:
        if "highlightthickness" not in widget.keys():
            return
        widget.bind("<FocusIn>", lambda _e, w=widget: w.configure(highlightthickness=2, highlightbackground=COLORS["focus"], highlightcolor=COLORS["focus"]), add="+")
        widget.bind("<FocusOut>", lambda _e, w=widget: w.configure(highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"]), add="+")

    def _on_tab_changed(self, _event: tk.Event) -> None:
        index = self.notebook.index(self.notebook.select())
        tab_key = self._tab_order[index]
        self._log_telemetry("settings_tab_viewed", tab=tab_key)
        self.after_idle(lambda: self._focus_first_widget(tab_key))

    def _focus_first_widget(self, tab_key: str) -> None:
        for widget in self._tab_focus_targets.get(tab_key, []):
            if not widget.winfo_exists():
                continue
            widget.focus_set()
            return

    def _browse(self) -> None:
        initial = self.path_var.get() or str(DEFAULT_BASE_DIR)
        d = filedialog.askdirectory(title="Select Base Output Folder", initialdir=initial)
        if d:
            self.path_var.set(d)

    def _build_school_notes_dir_vars(self) -> dict[str, StringVar]:
        settings = default_school_offer_settings()
        store = getattr(self.app, "school_offer_store", None)
        if store is not None:
            for school, cfg in store.load().items():
                settings.setdefault(str(school), {
                    "full_time_template": "",
                    "part_time_template": "",
                    "offer_output_dir": "",
                    "interview_notes_dir": "",
                })
                settings[str(school)].update(cfg)
        return {
            school: StringVar(value=str(cfg.get("interview_notes_dir", "")).strip())
            for school, cfg in sorted(settings.items())
        }

    def _save_school_notes_dirs(self) -> None:
        store = getattr(self.app, "school_offer_store", None)
        if store is None:
            return
        current = store.load()
        for school, var in getattr(self, "school_notes_dir_vars", {}).items():
            cfg = dict(current.get(school, {}))
            cfg.setdefault("full_time_template", "")
            cfg.setdefault("part_time_template", "")
            cfg.setdefault("offer_output_dir", "")
            cfg["interview_notes_dir"] = var.get().strip()
            current[school] = cfg
        store.save(current)
        self.app.school_offer_settings = current

    def _confirm_restore_recommended_whisper_defaults(self) -> None:
        should_reset = messagebox.askyesno(
            "Reset advanced defaults",
            "This will replace your custom Whisper settings with recommended defaults. Continue?",
        )
        if not should_reset:
            return
        self._restore_recommended_whisper_defaults()

    def _restore_recommended_whisper_defaults(self) -> None:
        self.whisper_language_var.set("en")
        self.whisper_vad_filter_var.set(True)
        self.whisper_beam_size_var.set(5)
        self.whisper_temperature_var.set("0.0")
        self._tab_message_vars[self._TAB_SECURITY].set("Advanced defaults were restored.")
        self._log_telemetry("settings_whisper_defaults_restored")

    def _deepseek_prompt_template_values(self) -> dict[str, Any]:
        widgets = getattr(self, "deepseek_prompt_widgets", {})
        question_widgets = getattr(self, "deepseek_question_prompt_widgets", {})
        if not isinstance(widgets, dict):
            return dict(getattr(self, "deepseek_prompt_templates", {}))
        output: dict[str, Any] = {}
        for key in DEEPSEEK_PROMPT_TEMPLATE_KEYS:
            widget = widgets.get(key)
            if widget is None:
                output[key] = str(getattr(self, "deepseek_prompt_templates", {}).get(key, "")).strip()
                continue
            output[key] = widget.get("1.0", END).strip()
        if isinstance(question_widgets, dict):
            for key in DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS:
                widget = question_widgets.get(key)
                if widget is None:
                    output[key] = getattr(self, "deepseek_prompt_templates", {}).get(key, {})
                    continue
                output[key] = widget.get("1.0", END).strip()
        return output

    @staticmethod
    def _normalize_deepseek_question_prompt_json(value: Any) -> dict[str, str]:
        return parse_deepseek_question_prompt_overrides(value)

    def _clear_tab_messages(self) -> None:
        for var in self._tab_message_vars.values():
            var.set("")
        for frame in self._tab_summary_frames.values():
            for child in frame.winfo_children():
                child.destroy()
        for var in self._field_error_vars.values():
            var.set("")

    def _validation_errors(self) -> dict[str, list[dict[str, str]]]:
        errors: dict[str, list[dict[str, str]]] = {tab: [] for tab in self._tab_order}

        def add_error(tab_key: str, field_key: str, message: str, guidance: str) -> None:
            errors[tab_key].append(
                {
                    "field": field_key,
                    "message": message,
                    "guidance": guidance,
                }
            )

        templates = self._settings_template_values()
        unknown = validate_template_map(templates, self._settings_template_contexts())
        if unknown:
            detail_lines = [f"{key}: {', '.join(sorted(values))}" for key, values in sorted(unknown.items())]
            for field_key in unknown:
                add_error(
                    self._TAB_TEMPLATES,
                    field_key,
                    "Contains unsupported template placeholders.",
                    "Open Placeholders picker and replace unsupported token.",
                )
            add_error(
                self._TAB_TEMPLATES,
                "template_placeholders",
                "Unknown template placeholders: " + "; ".join(detail_lines),
                "Open Placeholders picker and replace unsupported token.",
            )

        try:
            whisper_temperature = float(self.whisper_temperature_var.get())
        except (TypeError, ValueError):
            whisper_temperature = -1.0
            add_error(
                self._TAB_SECURITY,
                "whisper_temperature",
                "Temperature must be a number between 0.0 and 1.0.",
                "Enter a numeric value like 0.2.",
            )
        if not 0.0 <= whisper_temperature <= 1.0:
            add_error(
                self._TAB_SECURITY,
                "whisper_temperature",
                "Temperature must be between 0.0 and 1.0.",
                "Choose a value from 0.0 to 1.0.",
            )

        endpoint = self.endpoint_var.get().strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            add_error(
                self._TAB_NOTIFICATIONS,
                "director_referral_endpoint",
                "Director referral endpoint must start with http:// or https://.",
                "Use <https://...> (or <http://...>) for the endpoint URL.",
            )
        for school, var in getattr(self, "school_notes_dir_vars", {}).items():
            if self._path_has_parent_segment(var.get()):
                add_error(
                    self._TAB_STORAGE,
                    f"interview_notes_dir_{school}",
                    f"{school} notes folder cannot contain '..'.",
                    "Choose a Dropbox folder path without parent-directory segments.",
                )
        prompt_templates = self._deepseek_prompt_template_values()
        required_placeholders = {
            "answer_summary_user": "{payload_json}",
            "executive_summary_user": "{answer_summaries_json}",
            "trait_suggestion_user": "{payload_json}",
            "trait_scoring_user": "{payload_json}",
        }
        per_question_required = {
            "answer_summary_user_by_question": "{payload_json}",
            "trait_suggestion_user_by_question": "{payload_json}",
            "trait_scoring_user_by_question": "{payload_json}",
        }
        for key in DEEPSEEK_PROMPT_TEMPLATE_KEYS:
            value = prompt_templates.get(key, "").strip()
            if not value:
                add_error(
                    self._TAB_DEEPSEEK,
                    f"deepseek_{key}",
                    "DeepSeek prompt cannot be blank.",
                    "Restore default text or enter a complete prompt.",
                )
                continue
            required = required_placeholders.get(key)
            if required and required not in value:
                add_error(
                    self._TAB_DEEPSEEK,
                    f"deepseek_{key}",
                    f"DeepSeek prompt must include {required}.",
                    "Keep the data placeholder so candidate evidence reaches the model.",
                )
        for key, required in per_question_required.items():
            try:
                overrides = self._normalize_deepseek_question_prompt_json(prompt_templates.get(key, {}))
            except (TypeError, ValueError, json.JSONDecodeError):
                add_error(
                    self._TAB_DEEPSEEK,
                    f"deepseek_{key}",
                    "Per-question prompt overrides must use valid Question/Prompt blocks.",
                    "Use: Question: trait_1, Prompt:, prompt text; separate entries with ---.",
                )
                continue
            for question_key, prompt in overrides.items():
                if required not in prompt:
                    add_error(
                        self._TAB_DEEPSEEK,
                        f"deepseek_{key}",
                        f"Prompt for {question_key} must include {required}.",
                        "Keep the data placeholder so candidate evidence reaches the model.",
                    )
        return errors

    @staticmethod
    def _path_has_parent_segment(value: str) -> bool:
        return any(part.strip() == ".." for part in str(value or "").replace("/", "\\").split("\\"))

    def _apply_validation_messages(self, errors: dict[str, list[dict[str, str]]]) -> tuple[list[str], str | None]:
        invalid_tabs: list[str] = []
        first_invalid_field: str | None = None
        field_errors: dict[str, str] = {}

        for items in errors.values():
            for item in items:
                field_key = item["field"]
                if field_key in self._field_error_vars and field_key not in field_errors:
                    field_errors[field_key] = f"{item['message']} {item['guidance']}"
                if first_invalid_field is None and field_key in self._field_focus_targets:
                    first_invalid_field = field_key

        for tab_key, items in errors.items():
            frame = self._tab_summary_frames.get(tab_key)
            if frame is not None:
                for child in frame.winfo_children():
                    child.destroy()
            if items:
                message = f"{len(items)} issue(s) need attention in this section."
            else:
                message = ""
            self._tab_message_vars[tab_key].set(message)
            if items:
                invalid_tabs.append(tab_key)
                for item in items:
                    bullet = f"• {item['message']} {item['guidance']}"
                    self._wrapped_label(frame, text=bullet, foreground="#b91c1c").pack(anchor="w", pady=(0, 2))

        for field_key, message in field_errors.items():
            if field_key in self._field_error_vars:
                self._field_error_vars[field_key].set(message)
        return invalid_tabs, first_invalid_field

    def _save(self) -> None:
        self._clear_tab_messages()
        errors = self._validation_errors()
        invalid_tabs, first_invalid_field = self._apply_validation_messages(errors)
        if invalid_tabs:
            self._log_telemetry("settings_validation_failed", tab_count=len([x for x in errors.values() if x]), issues=sum(len(v) for v in errors.values()))
            first_invalid = invalid_tabs[0]
            self.notebook.select(self._tab_order.index(first_invalid))
            if first_invalid_field is not None:
                self.after_idle(lambda: self._focus_field(first_invalid_field))
            return

        templates = self._settings_template_values()
        raw_deepseek_prompt_templates = self._deepseek_prompt_template_values()
        for key in DEEPSEEK_QUESTION_PROMPT_TEMPLATE_KEYS:
            raw_deepseek_prompt_templates[key] = self._normalize_deepseek_question_prompt_json(raw_deepseek_prompt_templates.get(key, {}))
        deepseek_prompt_templates = normalize_deepseek_prompt_templates(raw_deepseek_prompt_templates)
        whisper_beam_size = max(1, min(10, int(self.whisper_beam_size_var.get() or 5)))
        whisper_temperature = float(self.whisper_temperature_var.get())

        self.app.settings["base_dir"] = self.path_var.get().strip() or str(DEFAULT_BASE_DIR)
        self.app.settings["question_audio_mode"] = self.audio_mode_var.get().strip() or QUESTION_AUDIO_MODE_TIMESTAMP_SLICING
        self.app.settings["director_referral_endpoint"] = self.endpoint_var.get().strip()
        self.app.settings["send_director_referral_on_finalize"] = bool(self.send_on_finalize_var.get())
        self.app.settings["director_email_to"] = self.director_email_to_var.get().strip()
        self.app.settings["director_email_subject_template"] = templates["director_subject"] or "Director Referral: {candidate_name}"
        self.app.settings["director_email_body_template"] = templates["director_body"]
        self.app.settings["offer_email_to"] = self.offer_email_to_var.get().strip()
        self.app.settings["offer_approval_subject_template"] = templates["offer_approval_subject"] or "Offer Approval Needed: {candidate_name}"
        self.app.settings["offer_approval_body_template"] = templates["offer_approval_body"]
        self.app.settings["offer_acceptance_subject_template"] = templates["offer_acceptance_subject"] or "Offer Accepted: {candidate_name}"
        self.app.settings["offer_acceptance_body_template"] = templates["offer_acceptance_body"]
        self.app.settings["offer_acceptance_attach_offer_file"] = bool(self.offer_acceptance_attach_var.get())
        self.app.settings["welcome_email_subject_template"] = templates["welcome_subject"] or "Welcome to {school}, {candidate_name}!"
        self.app.settings["welcome_email_body_template"] = templates["welcome_body"]
        self.app.settings["welcome_onboarding_pdf_path"] = self.onboarding_pdf_var.get().strip()
        deepseek_prompt_templates = save_deepseek_prompt_templates(deepseek_prompt_templates)
        self.app.settings["deepseek_prompt_templates"] = deepseek_prompt_templates
        self.app.settings["whisper_language"] = self.whisper_language_var.get().strip().lower() or "en"
        self.app.settings["whisper_vad_filter"] = bool(self.whisper_vad_filter_var.get())
        self.app.settings["whisper_beam_size"] = whisper_beam_size
        self.app.settings["whisper_temperature"] = whisper_temperature
        self._save_school_notes_dirs()
        self.app.apply_font_size(int(self.size_var.get()))
        self.app.app_settings_store.save(self.app.settings)
        self._log_telemetry("settings_saved")
        self.keyboard_session.complete(abandoned=False, screen_id="settings_save")
        self.destroy()

    def _focus_field(self, field_key: str) -> None:
        widget = self._field_focus_targets.get(field_key)
        if widget is None:
            return
        if not widget.winfo_exists():
            return
        widget.focus_set()


    def _cancel(self) -> None:
        self.keyboard_session.complete(abandoned=True, screen_id="settings_cancel")
        self.destroy()
    def _log_telemetry(self, event_type: str, **fields: Any) -> None:
        logger = getattr(self.app, "metrics_logger", None)
        if logger is None:
            return
        sanitized: dict[str, Any] = {}
        for key, value in fields.items():
            if key.endswith("_value"):
                continue
            sanitized[key] = value
        if event_type.endswith("validation_failed"):
            logger.log_ux_validation_error(app="interview", surface="settings", error_type=event_type, **sanitized)
            return
        if event_type.endswith("saved"):
            logger.log_ux_completion(app="interview", surface="settings", outcome="saved", event_name=event_type, **sanitized)
            return
        logger.log_ux_click(app="interview", surface="settings", target=event_type, **sanitized)

    def _browse_onboarding_pdf(self) -> None:
        initial_file = self.onboarding_pdf_var.get().strip()
        initial_dir = ""
        if initial_file:
            initial_dir = str(Path(initial_file).expanduser().parent)
        chosen = filedialog.askopenfilename(
            title="Select Onboarding Guide PDF",
            initialdir=initial_dir or str(DEFAULT_BASE_DIR),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if chosen:
            self.onboarding_pdf_var.set(chosen)

    def _build_settings_placeholder_picker(self, parent: ttk.Frame) -> None:
        picker_row = ttk.Frame(parent)
        picker_row.pack(fill="x", pady=(4, 0))
        ttk.Label(picker_row, text="Placeholders:").pack(side="left")
        options = placeholder_picker_options(["director", "offer", "welcome"])
        if options:
            self.settings_placeholder_var.set(options[0])
        picker = ttk.Combobox(picker_row, state="readonly", textvariable=self.settings_placeholder_var, values=options)
        picker.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(picker_row, text="Insert", command=self._insert_settings_placeholder).pack(side="left")

    def _insert_settings_placeholder(self) -> None:
        token = token_from_picker_label(self.settings_placeholder_var.get())
        if not token:
            return
        inserted = insert_token_into_focused_widget(self, token, self._settings_template_widgets)
        if inserted:
            return
        messagebox.showinfo("Template placeholders", "Focus a subject or body template field, then click Insert.")

    def _settings_template_values(self) -> dict[str, str]:
        return {
            "director_subject": self.director_email_subject_var.get().strip(),
            "director_body": self.director_email_body_widget.get("1.0", END).strip(),
            "offer_approval_subject": self.offer_approval_subject_var.get().strip(),
            "offer_approval_body": self.offer_approval_body_widget.get("1.0", END).strip(),
            "offer_acceptance_subject": self.offer_acceptance_subject_var.get().strip(),
            "offer_acceptance_body": self.offer_acceptance_body_widget.get("1.0", END).strip(),
            "welcome_subject": self.welcome_subject_var.get().strip(),
            "welcome_body": self.welcome_body_widget.get("1.0", END).strip(),
        }

    @staticmethod
    def _settings_template_contexts() -> dict[str, str]:
        return {
            "director_subject": "director",
            "director_body": "director",
            "offer_approval_subject": "offer",
            "offer_approval_body": "offer",
            "offer_acceptance_subject": "offer",
            "offer_acceptance_body": "offer",
            "welcome_subject": "welcome",
            "welcome_body": "welcome",
        }


# =========================
# Question editor window
# =========================

class QuestionEditorWindow(tk.Toplevel):
    def __init__(self, app: "InterviewApp"):
        super().__init__(app)
        self.app = app

        self.title("Edit Questions")
        self.geometry("1050x700")
        apply_professional_ops_theme(self, font_size=int(self.app.settings.get("font_size", 10)))

        default_track = self.app.state.track or next(iter(self.app.rubric["tracks"].keys()))
        self.track_var = StringVar(value=default_track)

        self.flow_list: tk.Listbox
        self.custom_list: tk.Listbox
        self.override_text: tk.Text
        self.cq_id_var: StringVar
        self.cq_text: tk.Text
        self.status_var = StringVar(value="")
        self.dirty = False

        self._draft_trait_overrides: dict[str, str] = {}
        self._draft_custom_questions: dict[str, list[dict[str, Any]]] = {}
        self._draft_track_flow: dict[str, list[dict[str, Any]]] = {}

        self._load_drafts_from_store()

        self._build()
        self._bind()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_all()

    # ---- parsing / formatting ----

    @staticmethod
    def format_flow_line(item: dict[str, Any], trait_by_id: dict[str, dict[str, Any]], custom_by_id: dict[str, dict[str, Any]]) -> str:
        if item["type"] == "trait":
            t = trait_by_id.get(item["id"])
            name = t["name"] if t else item["id"]
            return f"[SCORED COMPETENCY] {item['id']}  |  {name}"
        q = custom_by_id.get(item["id"])
        txt = (q.get("text", "") if q else "").strip() or item["id"]
        return f"[CUSTOM] {item['id']}  |  {txt}"

    @staticmethod
    def parse_flow_line(line: str) -> dict[str, Any] | None:
        line = line.strip()
        if line.startswith("[SCORED COMPETENCY]"):
            rest = line[len("[SCORED COMPETENCY]"):].strip()
            tid = rest.split("|", 1)[0].strip()
            return {"type": "trait", "id": tid}
        if line.startswith("[SCORED]"):
            rest = line[len("[SCORED]"):].strip()
            tid = rest.split("|", 1)[0].strip()
            return {"type": "trait", "id": tid}
        if line.startswith("[CUSTOM]"):
            rest = line[len("[CUSTOM]"):].strip()
            cid = rest.split("|", 1)[0].strip()
            return {"type": "custom", "id": cid}
        return None

    # ---- UI build ----

    def _build(self) -> None:
        header = ttk.Frame(self, padding=10)
        header.pack(fill="x")

        ttk.Label(header, text="Track:").pack(side="left")
        track_keys = list(self.app.rubric["tracks"].keys())
        ttk.Combobox(header, textvariable=self.track_var, values=track_keys, width=20).pack(side="left", padx=8)
        ttk.Label(header, text="Edits are kept in memory until Apply Changes.").pack(side="left", padx=8)
        ttk.Button(
            header,
            text="Help",
            padding=6,
            command=lambda: self._show_help(
                "Question editor terms",
                "Scored competency: a rubric-based question that contributes to interview scoring.\n"
                "Custom question: a non-scored prompt used for additional context.\n"
                "Track: the role-specific interview flow (for example, Infant, Toddler, or Preschool).",
            ),
        ).pack(side="right")

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._build_flow_panel(left)
        self._build_override_panel(left)
        self._build_custom_panel(right)

        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        ttk.Button(footer, text="Close", command=self.on_close).pack(side="right")
        ttk.Button(footer, text="Discard", command=self.discard_changes).pack(side="right", padx=6)
        ttk.Button(footer, text="Apply Changes", command=self.apply_changes).pack(side="right")

    def _build_flow_panel(self, parent: ttk.Frame) -> None:
        flow_box = ttk.LabelFrame(parent, text="Interview flow (mixed scored competencies + non-scored questions)")
        flow_box.pack(fill="both", expand=True)

        self.flow_list = tk.Listbox(
            flow_box,
            height=20,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["primary"],
            selectforeground="#ffffff",
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self.flow_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)

        flow_scroll = ttk.Scrollbar(flow_box, orient="vertical", command=self.flow_list.yview)
        flow_scroll.pack(side="left", fill="y", pady=8, padx=(0, 8))
        self.flow_list.configure(yscrollcommand=flow_scroll.set)

        btns = ttk.Frame(flow_box)
        btns.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(btns, text="Move Up", command=lambda: self.move_flow(-1)).pack(side="left")
        ttk.Button(btns, text="Move Down", command=lambda: self.move_flow(1)).pack(side="left", padx=6)
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(btns, text="Insert Selected Custom Here", command=self.insert_selected_custom_into_flow).pack(side="left")

    def _build_override_panel(self, parent: ttk.Frame) -> None:
        override_box = ttk.LabelFrame(parent, text="Edit selected scored competency primary question (override)")
        override_box.pack(fill="both", expand=True, pady=(10, 0))

        self.override_text = tk.Text(override_box, height=8, wrap="word")
        configure_text_widget(self.override_text, font_size=int(self.app.settings.get("font_size", 10)))
        self.override_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        obtns = ttk.Frame(override_box)
        obtns.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(obtns, text="Save Override", command=self.save_override).pack(side="left")
        ttk.Button(obtns, text="Clear Override", command=self.clear_override).pack(side="left", padx=6)
        ttk.Button(
            obtns,
            text="Help",
            padding=6,
            command=lambda: self._show_help(
                "Question override",
                "A question override changes the wording shown to interviewers for one scored competency.\n"
                "It does not change rubric scoring rules, weights, or competency IDs.",
            ),
        ).pack(side="right")

    def _build_custom_panel(self, parent: ttk.Frame) -> None:
        custom_box = ttk.LabelFrame(parent, text="Custom Questions (non-scored) - add/edit/delete")
        custom_box.pack(fill="both", expand=True)

        self.custom_list = tk.Listbox(
            custom_box,
            height=20,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["primary"],
            selectforeground="#ffffff",
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self.custom_list.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)

        custom_scroll = ttk.Scrollbar(custom_box, orient="vertical", command=self.custom_list.yview)
        custom_scroll.pack(side="left", fill="y", pady=8, padx=(0, 8))
        self.custom_list.configure(yscrollcommand=custom_scroll.set)

        custom_edit = ttk.LabelFrame(parent, text="Edit custom question")
        custom_edit.pack(fill="both", expand=True, pady=(10, 0))

        self.cq_id_var = StringVar(value="")
        self.cq_text = tk.Text(custom_edit, height=8, wrap="word")
        configure_text_widget(self.cq_text, font_size=int(self.app.settings.get("font_size", 10)))
        self.cq_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        idrow = ttk.Frame(custom_edit)
        idrow.pack(fill="x", padx=8)
        ttk.Label(idrow, text="ID:").pack(side="left")
        ttk.Entry(idrow, textvariable=self.cq_id_var, width=28).pack(side="left", padx=6)
        ttk.Label(idrow, text="(leave blank to auto-generate on Add)").pack(side="left")

        btns = ttk.Frame(custom_edit)
        btns.pack(fill="x", padx=8, pady=(6, 8))
        ttk.Button(btns, text="Add/Update", command=self.add_or_update_custom).pack(side="left")
        ttk.Button(btns, text="Delete", command=self.delete_custom).pack(side="left", padx=6)

    def _bind(self) -> None:
        self.track_var.trace_add("write", lambda *_: self.refresh_all())
        self.flow_list.bind("<<ListboxSelect>>", lambda _e: self.load_override_for_selected_flow())
        self.custom_list.bind("<<ListboxSelect>>", lambda _e: self.load_custom_into_editor())

    # ---- helpers ----

    def track_key(self) -> str:
        return self.track_var.get().strip()

    def _show_help(self, title: str, text: str) -> None:
        messagebox.showinfo(title, text)

    def _load_drafts_from_store(self) -> None:
        data = self.app.qstore.data
        self._draft_trait_overrides = dict(data.get("trait_question_overrides", {}) or {})
        self._draft_custom_questions = {
            str(track): [dict(x) for x in items]
            for track, items in (data.get("custom_questions", {}) or {}).items()
        }
        self._draft_track_flow = {
            str(track): [dict(x) for x in items]
            for track, items in (data.get("track_question_flow", {}) or {}).items()
        }

    def _set_dirty(self, dirty: bool = True) -> None:
        self.dirty = dirty
        if dirty:
            self.status_var.set("Unsaved changes")

    def _track_trait_ids(self, tkey: str) -> list[str]:
        traits_raw = self.app.rubric_loader.get_traits_for_track(tkey)
        traits_ordered = self.app._apply_trait_presentation_order(tkey, traits_raw)
        return [t["id"] for t in traits_ordered]

    def _ensure_draft_flow(self, tkey: str) -> list[dict[str, Any]]:
        raw = self.app.qstore._clean_flow_items(self._draft_track_flow.get(tkey, []))
        valid_trait_ids_in_order = self._track_trait_ids(tkey)
        valid_custom_ids_in_order = [str(q.get("id")) for q in self._draft_custom_questions.get(tkey, [])]

        valid_trait_set = set(valid_trait_ids_in_order)
        valid_custom_set = set(valid_custom_ids_in_order)
        out: list[dict[str, Any]] = []
        seen_traits: set[str] = set()
        seen_customs: set[str] = set()

        for it in raw:
            item_type = it["type"]
            qid = it["id"]
            if item_type == "trait":
                if qid in valid_trait_set and qid not in seen_traits:
                    out.append({"type": "trait", "id": qid})
                    seen_traits.add(qid)
                continue
            if qid in valid_custom_set and qid not in seen_customs:
                out.append({"type": "custom", "id": qid})
                seen_customs.add(qid)

        for tid in valid_trait_ids_in_order:
            if tid not in seen_traits:
                out.append({"type": "trait", "id": tid})
                seen_traits.add(tid)

        for cid in valid_custom_ids_in_order:
            if cid not in seen_customs:
                out.append({"type": "custom", "id": cid})
                seen_customs.add(cid)

        self._draft_track_flow[tkey] = out
        return out

    def selected_flow_item(self) -> dict[str, Any] | None:
        sel = self.flow_list.curselection()
        if not sel:
            return None
        return self.parse_flow_line(self.flow_list.get(sel[0]))

    def selected_custom_id(self) -> str | None:
        sel = self.custom_list.curselection()
        if not sel:
            return None
        line = self.custom_list.get(sel[0])
        return line.split("|", 1)[0].strip()

    def persist_flow_from_listbox(self) -> None:
        tkey = self.track_key()
        out = [
            parsed
            for i in range(self.flow_list.size())
            if (parsed := self.parse_flow_line(self.flow_list.get(i)))
        ]
        self._draft_track_flow[tkey] = out
        self._set_dirty()

    # ---- reload panels ----

    def load_flow(self) -> None:
        self.flow_list.delete(0, END)
        tkey = self.track_key()

        traits_raw = self.app.rubric_loader.get_traits_for_track(tkey)
        traits_ordered = self.app._apply_trait_presentation_order(tkey, traits_raw)
        trait_ids = [t["id"] for t in traits_ordered]
        trait_by_id = {t["id"]: t for t in traits_ordered}

        customs = self._draft_custom_questions.get(tkey, [])
        custom_ids = [str(q["id"]) for q in customs]
        custom_by_id = {str(q["id"]): q for q in customs}

        flow = self._ensure_draft_flow(tkey)
        for it in flow:
            self.flow_list.insert(END, self.format_flow_line(it, trait_by_id, custom_by_id))

    def load_custom(self) -> None:
        self.custom_list.delete(0, END)
        items = self._draft_custom_questions.get(self.track_key(), [])
        for it in items:
            self.custom_list.insert(END, f"{it['id']}  |  {it.get('text','').strip()}")

    def refresh_all(self) -> None:
        self.load_custom()
        self.load_flow()
        self.load_override_for_selected_flow()

    # ---- flow operations ----

    def move_flow(self, delta: int) -> None:
        sel = self.flow_list.curselection()
        if not sel:
            return

        i = sel[0]
        j = i + delta
        if j < 0 or j >= self.flow_list.size():
            return

        a = self.flow_list.get(i)
        b = self.flow_list.get(j)

        self.flow_list.delete(i)
        self.flow_list.insert(i, b)
        self.flow_list.delete(j)
        self.flow_list.insert(j, a)

        self.flow_list.selection_set(j)
        self.flow_list.activate(j)
        self.persist_flow_from_listbox()

    def insert_selected_custom_into_flow(self) -> None:
        tkey = self.track_key()
        cid = self.selected_custom_id()
        if not cid:
            messagebox.showerror(
                "Selection needed",
                format_guidance(
                    "No custom question is selected.",
                    "Select a custom question on the right, then insert it into the flow.",
                ),
            )
            return

        sel = self.flow_list.curselection()
        insert_at = sel[0] if sel else self.flow_list.size()
        flow = self._ensure_draft_flow(tkey)
        if any(it.get("type") == "custom" and it.get("id") == cid for it in flow):
            return

        flow.insert(insert_at, {"type": "custom", "id": cid})
        self._draft_track_flow[tkey] = flow
        self._set_dirty()
        self.load_flow()

    # ---- override operations ----

    def load_override_for_selected_flow(self) -> None:
        self.override_text.delete("1.0", END)
        it = self.selected_flow_item()
        if not it or it["type"] != "trait":
            return

        tid = it["id"]
        tkey = self.track_key()
        trait = next((t for t in self.app.rubric_loader.get_traits_for_track(tkey) if t["id"] == tid), None)
        default = trait["primary_question"] if trait else ""
        ov = self._draft_trait_overrides.get(tid)
        self.override_text.insert(END, ov if ov is not None else default)

    def save_override(self) -> None:
        it = self.selected_flow_item()
        if not it or it["type"] != "trait":
            messagebox.showerror(
                "Selection needed",
                format_guidance(
                    "No scored competency is selected in the flow list.",
                    "Select a scored competency in the flow list, then try again.",
                ),
            )
            return

        tid = it["id"]
        text = self.override_text.get("1.0", END).strip()
        self._draft_trait_overrides[tid] = text
        self._set_dirty()

    def clear_override(self) -> None:
        it = self.selected_flow_item()
        if not it or it["type"] != "trait":
            messagebox.showerror(
                "Selection needed",
                format_guidance(
                    "No scored competency is selected in the flow list.",
                    "Select a scored competency in the flow list, then try again.",
                ),
            )
            return

        tid = it["id"]
        self._draft_trait_overrides.pop(tid, None)
        self._set_dirty()
        self.load_override_for_selected_flow()

    # ---- custom CRUD ----

    def load_custom_into_editor(self) -> None:
        self.cq_text.delete("1.0", END)
        qid = self.selected_custom_id()
        if not qid:
            self.cq_id_var.set("")
            return

        items = self._draft_custom_questions.get(self.track_key(), [])
        it = next((x for x in items if str(x.get("id")) == qid), None)
        if not it:
            return

        self.cq_id_var.set(it["id"])
        self.cq_text.insert(END, it.get("text", ""))

    def add_or_update_custom(self) -> None:
        tkey = self.track_key()
        txt = self.cq_text.get("1.0", END).strip()
        if not txt:
            messagebox.showerror(
                "Question text required",
                format_guidance(
                    "The custom question text is empty.",
                    "Enter the question text, then choose Add/Update.",
                ),
            )
            return

        qid = self.cq_id_var.get().strip() or f"cq_{now_stamp()}"
        existing = self._draft_custom_questions.setdefault(tkey, [])

        if any(str(x.get("id")) == qid for x in existing):
            order = next((int(x.get("order", 999999)) for x in existing if str(x.get("id")) == qid), 999999)
        else:
            order = len(existing) + 1

        replaced = False
        for i, it in enumerate(existing):
            if str(it.get("id")) == qid:
                existing[i] = {"id": qid, "text": txt, "order": order}
                replaced = True
                break
        if not replaced:
            existing.append({"id": qid, "text": txt, "order": order})

        flow = self._ensure_draft_flow(tkey)
        if not any(it.get("type") == "custom" and it.get("id") == qid for it in flow):
            flow.append({"type": "custom", "id": qid})
            self._draft_track_flow[tkey] = flow

        self._set_dirty()
        self.refresh_all()
        self._select_custom_in_list(qid)

    def _select_custom_in_list(self, qid: str) -> None:
        match_index = next(
            (i for i in range(self.custom_list.size()) if self.custom_list.get(i).startswith(qid)),
            None,
        )
        if match_index is None:
            return

        self.custom_list.selection_clear(0, END)
        self.custom_list.selection_set(match_index)
        self.custom_list.activate(match_index)

    def delete_custom(self) -> None:
        tkey = self.track_key()
        qid = self.selected_custom_id()
        if not qid:
            return
        if not messagebox.askyesno("Delete", f"Delete custom question '{qid}'?"):
            return

        self._draft_custom_questions[tkey] = [
            it for it in self._draft_custom_questions.get(tkey, []) if str(it.get("id")) != str(qid)
        ]
        flow = self._ensure_draft_flow(tkey)
        self._draft_track_flow[tkey] = [
            it for it in flow if not (it.get("type") == "custom" and str(it.get("id")) == str(qid))
        ]
        self._set_dirty()

        self.cq_id_var.set("")
        self.cq_text.delete("1.0", END)
        self.refresh_all()

    def apply_changes(self) -> None:
        all_tracks = set(self._draft_custom_questions) | set(self.app.qstore.data.get("custom_questions", {}).keys())
        for tkey in all_tracks:
            existing = self.app.qstore.list_custom_questions(tkey)
            for item in existing:
                if not any(str(x.get("id")) == str(item.get("id")) for x in self._draft_custom_questions.get(tkey, [])):
                    self.app.qstore.delete_custom_question(tkey, str(item.get("id")))

            for item in self._draft_custom_questions.get(tkey, []):
                self.app.qstore.upsert_custom_question(tkey, dict(item))

        existing_overrides = dict(self.app.qstore.data.get("trait_question_overrides", {}) or {})
        for tid in existing_overrides:
            if tid not in self._draft_trait_overrides:
                self.app.qstore.clear_trait_question_override(tid)
        for tid, text in self._draft_trait_overrides.items():
            self.app.qstore.set_trait_question_override(tid, text)

        flow_tracks = set(self._draft_track_flow) | set(self.app.qstore.data.get("track_question_flow", {}).keys())
        for tkey in flow_tracks:
            self.app.qstore.set_question_flow(tkey, self._ensure_draft_flow(tkey))

        self.status_var.set("Changes applied")
        self.dirty = False

    def discard_changes(self) -> None:
        self._load_drafts_from_store()
        self.dirty = False
        self.status_var.set("Changes discarded")
        self.refresh_all()

    def on_close(self) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved changes", "Discard unsaved changes and close?"):
            return
        self.destroy()
