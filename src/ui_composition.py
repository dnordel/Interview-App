from __future__ import annotations

import json
import re
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, Callable, Protocol

import tkinter as tk
from tkinter import END, BooleanVar, IntVar, StringVar, filedialog, messagebox, ttk

from platform_services import (
    DEFAULT_BASE_DIR,
    DEFAULT_RUBRIC_PATH,
    NEVER_HAPPENED_BY_TRAIT,
    NEVER_HAPPENED_GLOBAL_SCRIPT,
    text_suggests_no_example,
)
from platform_services import atomic_write_json
from scoring_reporting import (
    CANONICAL_DEGREE_TYPES,
    DraftManager,
    load_trait_signal_ui_definition,
    normalize_model_signal_suggestions,
    parse_yes_no,
    validate_candidate_qualification,
    write_canonical_selected_signal_ids,
)
from tk_theme import COLORS, configure_plain_button, configure_text_widget


VALIDATION_SEVERITY_INFO = "info"
VALIDATION_SEVERITY_WARNING = "warning"
VALIDATION_SEVERITY_ERROR = "error"
VALIDATION_SEVERITY_BLOCKING = "blocking"
VALIDATION_SEVERITIES = {
    VALIDATION_SEVERITY_INFO,
    VALIDATION_SEVERITY_WARNING,
    VALIDATION_SEVERITY_ERROR,
    VALIDATION_SEVERITY_BLOCKING,
}

VALIDATION_INLINE_COLORS = {
    VALIDATION_SEVERITY_INFO: "#1d4ed8",
    VALIDATION_SEVERITY_WARNING: "#92400e",
    VALIDATION_SEVERITY_ERROR: "#b91c1c",
    VALIDATION_SEVERITY_BLOCKING: "#991b1b",
}

TRANSCRIPTION_PARTIAL_WARNING_COPY = "Transcription still processing in background; report may be partial."
RuntimeSignalDefinition = dict[str, Any]
RuntimeSignalRecord = dict[str, Any]
RuntimeSignalGroup = dict[str, Any]
HistoryRow = dict[str, Any]
RowCallback = Callable[[HistoryRow], None]
SortCallback = Callable[[str, bool], None]
TRAIT_FILE_PATTERN = "T*.json"
TRAIT_ID_ALIAS_PATTERN = re.compile(r"trait_(\d+)", re.IGNORECASE)
PREFIXED_TRAIT_ID_ALIAS_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9]*)_trait_(\d+)", re.IGNORECASE)
BSS_TRAIT_ID_ALIAS_PATTERN = PREFIXED_TRAIT_ID_ALIAS_PATTERN
RUNTIME_TRAIT_ID_PATTERN = re.compile(r"(?:[A-Z][A-Z0-9]*_)?T\d+(?:_[A-Za-z0-9_]+)?")
SIGNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
GROUP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


SCORE_PANEL_BG = COLORS["warning_bg"]
DQ_PANEL_BG = COLORS["danger_bg"]
FOCUS_RING = COLORS["focus"]


def render_question_footer(
    app: InterviewApp,
    *,
    flow_idx: int,
    is_last: bool,
    go_back: Callable[[], None],
    skip_question: Callable[[], None],
    save_draft: Callable[[], None],
    continue_or_finalize: Callable[[], None],
    persist_for_exit: Callable[[], None] | None = None,
) -> None:
    exit_action = getattr(app, "exit_current_interview", None)
    if callable(exit_action):
        exit_command = lambda: exit_action(flow_idx, persist_current=persist_for_exit)
    else:
        exit_command = app.show_start_screen

    app.set_footer_actions(
        left_actions=[
            ("Back", go_back),
            ("Skip", skip_question),
            ("Save Draft", save_draft),
        ],
        right_actions=[
            ("Play Audio", lambda: app.play_flow_question_audio(flow_idx)),
            ("Exit", exit_command),
            ("Finalize" if is_last else "Next", continue_or_finalize),
        ],
    )


def render_qualification_box(parent: ttk.Frame, app: InterviewApp) -> dict[str, Any]:
    qualification = app.state.qualification
    has_degree_default = ""
    if qualification.has_degree is True:
        has_degree_default = "yes"
    if qualification.has_degree is False:
        has_degree_default = "no"

    vars_map = {
        "has_degree": StringVar(value=has_degree_default),
        "degree_type": StringVar(value=qualification.degree_type),
        "degree_in_ece": tk.BooleanVar(value=qualification.degree_in_ece),
        "ece_units": StringVar(value="" if qualification.ece_units_completed is None else str(qualification.ece_units_completed)),
        "infant_toddler": tk.BooleanVar(value=qualification.infant_toddler_class_completed),
        "total_units": StringVar(value="" if qualification.total_units_completed is None else str(qualification.total_units_completed)),
        "years_experience": StringVar(value="" if qualification.years_experience is None else str(qualification.years_experience)),
    }

    qualification_box = ttk.LabelFrame(parent, text="Step B: Candidate education details (required)")
    qualification_box.pack(fill="x", pady=(2, 8))

    has_degree_label = ttk.Label(qualification_box, text="Does the candidate have a degree?")
    has_degree_label.pack(anchor="w", padx=10, pady=(10, 0))
    has_degree_row = ttk.Frame(qualification_box)
    has_degree_row.pack(fill="x", padx=10, pady=4)
    has_degree_yes = ttk.Radiobutton(has_degree_row, text="Yes", variable=vars_map["has_degree"], value="yes")
    has_degree_yes.pack(side="left")
    ttk.Radiobutton(has_degree_row, text="No", variable=vars_map["has_degree"], value="no").pack(side="left", padx=(12, 0))
    associate_label_with_control(has_degree_label, has_degree_yes)

    degree_type_label = ttk.Label(qualification_box, text="Degree type (AA, AS, BA, BS, etc.)")
    degree_type_label.pack(anchor="w", padx=10, pady=(6, 0))
    degree_type_combo = ttk.Combobox(
        qualification_box,
        textvariable=vars_map["degree_type"],
        values=list(CANONICAL_DEGREE_TYPES),
        state="readonly",
    )
    degree_type_combo.pack(fill="x", padx=10, pady=4)
    associate_label_with_control(degree_type_label, degree_type_combo)

    ttk.Checkbutton(
        qualification_box,
        text="Degree is in Early Childhood Education (ECE)",
        variable=vars_map["degree_in_ece"],
    ).pack(anchor="w", padx=10, pady=(2, 0))

    ece_units_label = ttk.Label(qualification_box, text="ECE units completed (whole number)")
    ece_units_label.pack(anchor="w", padx=10, pady=(6, 0))
    ece_units_entry = ttk.Entry(qualification_box, textvariable=vars_map["ece_units"])
    ece_units_entry.pack(fill="x", padx=10, pady=4)
    associate_label_with_control(ece_units_label, ece_units_entry)

    ttk.Checkbutton(
        qualification_box,
        text="Completed infant/toddler class",
        variable=vars_map["infant_toddler"],
    ).pack(anchor="w", padx=10, pady=(2, 0))

    years_experience_label = ttk.Label(qualification_box, text="Years of teaching experience (whole number)")
    years_experience_label.pack(anchor="w", padx=10, pady=(6, 0))
    years_experience_entry = ttk.Entry(qualification_box, textvariable=vars_map["years_experience"])
    years_experience_entry.pack(fill="x", padx=10, pady=4)
    associate_label_with_control(years_experience_label, years_experience_entry)

    total_units_row = ttk.Frame(qualification_box)
    total_units_label = ttk.Label(total_units_row, text="Total units completed (required when no degree)")
    total_units_label.pack(anchor="w", pady=(6, 0))
    total_units_entry = ttk.Entry(total_units_row, textvariable=vars_map["total_units"])
    total_units_entry.pack(fill="x", pady=4)
    total_units_row.pack(fill="x", padx=10, pady=(0, 2))
    associate_label_with_control(total_units_label, total_units_entry)

    vars_map["inline_validation"] = create_inline_validation_message(qualification_box)
    vars_map["controls"] = {
        "has_degree": has_degree_yes,
        "degree_type": degree_type_combo,
        "ece_units": ece_units_entry,
        "total_units": total_units_entry,
        "years_experience": years_experience_entry,
    }

    def refresh_qualification_visibility() -> None:
        has_degree = parse_yes_no(vars_map["has_degree"].get())
        if has_degree:
            degree_type_combo.configure(state="readonly")
            total_units_row.pack_forget()
            return
        degree_type_combo.configure(state="disabled")
        vars_map["degree_type"].set("")
        if not total_units_row.winfo_ismapped():
            total_units_row.pack(fill="x", padx=10, pady=(0, 2))

    vars_map["has_degree"].trace_add("write", lambda *_: refresh_qualification_visibility())
    refresh_qualification_visibility()
    return vars_map


def validate_and_store_qualification(app: InterviewApp, vars_map: dict[str, Any]) -> bool:
    inline_validation = vars_map["inline_validation"]
    ok, msg, qualification = validate_candidate_qualification(
        vars_map["has_degree"].get(),
        vars_map["degree_type"].get(),
        bool(vars_map["degree_in_ece"].get()),
        vars_map["ece_units"].get(),
        vars_map["total_units"].get(),
        bool(vars_map["infant_toddler"].get()),
        vars_map["years_experience"].get(),
    )
    if not ok:
        error_type = _qualification_error_type(msg)
        focus_widget = vars_map["controls"][error_type]
        inline_validation.show(
            issue=msg,
            next_step=_qualification_next_step(error_type),
            focus_widget=focus_widget,
            severity=VALIDATION_SEVERITY_ERROR,
        )
        _log_validation_error(app, error_type=error_type, context="qualification")
        return False
    inline_validation.clear()
    app.state.qualification = qualification
    return True


def _qualification_error_type(message: str) -> str:
    lowered = message.lower()
    if "candidate has a degree" in lowered:
        return "has_degree"
    if "degree type" in lowered:
        return "degree_type"
    if "total units" in lowered:
        return "total_units"
    if "years of experience" in lowered:
        return "years_experience"
    return "ece_units"


def _qualification_next_step(error_type: str) -> str:
    steps = {
        "has_degree": "Select Yes or No, then continue.",
        "degree_type": "Choose one of the listed degree types, then continue.",
        "ece_units": "Enter a non-negative whole number for ECE units, then continue.",
        "total_units": "Enter total completed units as a non-negative whole number, then continue.",
        "years_experience": "Enter years of experience as a non-negative whole number, then continue.",
    }
    return steps[error_type]


def _log_validation_error(app: InterviewApp, *, error_type: str, context: str) -> None:
    logger = getattr(app, "metrics_logger", None)
    if logger is None:
        return
    logger.log_ux_validation_error(app="interview", surface=context, error_type=error_type, error_count=1)


class TraitScreenUI:
    DISCLOSURE_DEFAULTS: dict[str, bool] = {
        "descriptors": False,
        "samples": False,
    }
    DISCLOSURE_LABELS: dict[str, str] = {
        "descriptors": "rubric detail",
        "samples": "sample answers",
        "history": "history",
    }
    SECONDARY_OPTIONAL_HELPER_TEXT = "Optional reference content for interviewers."
    SECTION_PADY_TIGHT = (4, 4)
    SECTION_PADY_STANDARD = (6, 6)
    SECTION_PADY_RELAXED = (8, 6)

    def __init__(self, app: InterviewApp, flow_idx: int, trait: dict[str, Any], state: dict[str, Any]):
        self.app = app
        self.flow_idx = flow_idx
        self.trait = trait
        self.tid = trait["id"]
        self.state = state

        self.raw_var = IntVar(value=int(state["raw_score"]) if state.get("raw_score") in {1, 2, 3, 4, 5} else 0)
        self.dq_var = BooleanVar(value=bool(state.get("absolute_disqualifier", False)))
        self.no_example_var = BooleanVar(value=bool(state.get("no_example_after_followups", False)))
        self.descriptor_expanded_var = BooleanVar(value=self._initial_disclosure_state("descriptors"))
        self.samples_expanded_var = BooleanVar(value=self._initial_disclosure_state("samples"))

        # If the interviewer marks "no example", keep the score capped.
        self.raw_var.trace_add("write", lambda *_: self._enforce_no_example_cap(show_message=False))

        self.score_widgets: list[tk.Radiobutton] = []
        self.v_text: tk.Text
        self.v_label: tk.Label
        self.q_text: tk.Text
        self.t_text: tk.Text
        self.qualification_vars: dict[str, Any] | None = None

        self.normal_v_bg = ""
        self.dq_required_bg = COLORS["danger_bg"]
        self._descriptor_body: ttk.Frame | None = None
        self._samples_body: ttk.Frame | None = None
        self.inline_validation = None
        self.signal_selection_vars: dict[str, BooleanVar] = {}
        self.model_signal_suggestions: dict[str, dict[str, Any]] = {}
        self._signal_ui_definition: dict[str, Any] = {}
        self.keyboard_session = KeyboardPathSession(
            logger=getattr(self.app, "metrics_logger", None),
            flow_id="interview_question",
            screen_id=f"trait_{self.tid}",
        )

    def render(self) -> None:
        frm = ttk.Frame(self.app.page_frame, padding=12)
        frm.pack(fill="both", expand=True)
        self.keyboard_session.bind(frm)
        self._render_footer_actions()

        if self.flow_idx == 0:
            self.qualification_vars = render_qualification_box(frm, self.app)

        self.app.render_progress_strip(frm, self.flow_idx, is_scored=True)
        self._render_header(frm)
        primary_frame, secondary_frame = self._render_container_frames(frm)
        self._render_primary_viewport(primary_frame)
        self._render_secondary_context(secondary_frame)

        self._sync_dq_dependent_ui()
        self.app.after_idle(self.q_text.focus_set)

    def _render_container_frames(self, parent: ttk.Frame) -> tuple[ttk.Frame, ttk.LabelFrame]:
        primary_frame = ttk.Frame(parent)
        primary_frame.pack(fill="both", expand=True, pady=self.SECTION_PADY_STANDARD)

        secondary_frame = ttk.LabelFrame(parent, text="Reference & Guidance")
        secondary_frame.pack(fill="x", pady=self.SECTION_PADY_STANDARD)
        return primary_frame, secondary_frame

    def _render_primary_viewport(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="Score & Required Inputs",
            font=("TkDefaultFont", self.app.settings["font_size"] + 1, "bold"),
        ).pack(anchor="w", pady=self.SECTION_PADY_TIGHT)
        self._render_score_box(parent)
        self._render_disqualifier_box(parent)

        ttk.Label(
            parent,
            text="Candidate Notes",
            font=("TkDefaultFont", self.app.settings["font_size"] + 1, "bold"),
        ).pack(anchor="w", pady=self.SECTION_PADY_RELAXED)
        self._render_notes(parent)

    def _render_secondary_context(self, parent: ttk.Frame) -> None:
        self._render_optional_context(parent, "Rubric guidance", self._render_ladders)
        self._render_optional_context(parent, "No-example guidance", self._render_no_example_addendum)
        self._render_optional_context(
            parent,
            "DeepSeek observation hints",
            lambda container: self.app._render_signal_examples(container, self.tid),
        )

    def _render_optional_context(self, parent: ttk.Frame, title: str, renderer: Callable[[ttk.Frame], None]) -> None:
        try:
            renderer(parent)
        except Exception as exc:
            self._render_context_warning(parent, title, exc)

    def _render_context_warning(self, parent: ttk.Frame, title: str, exc: Exception) -> None:
        box = ttk.LabelFrame(parent, text=title)
        box.pack(fill="x", pady=self.SECTION_PADY_STANDARD)
        ttk.Label(
            box,
            text=f"{title} unavailable for this JSON question: {exc}",
            foreground="#92400e",
            wraplength=1030,
        ).pack(anchor="w", padx=10, pady=8)

    def _render_header(self, parent: ttk.Frame) -> None:
        intro = ttk.LabelFrame(parent, text=f"Question {self.flow_idx + 1} of {self.app._flow_len()} (Scored competency)")
        intro.pack(fill="x", pady=6)

        ttk.Label(intro, text=self.trait["name"], font=("TkDefaultFont", self.app.settings["font_size"] + 4, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        ttk.Label(intro, text=f"Priority: {self.trait['priority']} | Weight: x{self.trait['weight']}", foreground="#334155").pack(anchor="w", padx=10)

        pq = self.app.get_primary_question_text(self.trait)
        ttk.Label(intro, text=f"Primary Question: {pq}", wraplength=1050).pack(anchor="w", padx=10, pady=(4, 10))

    def _render_ladders(self, parent: ttk.Frame) -> None:
        self._descriptor_body = self._render_disclosure_section(
            parent=parent,
            frame_title="Scoring descriptors (1-5)",
            section_key="descriptors",
            section_var=self.descriptor_expanded_var,
            line_values=self.trait["descriptors"],
            header_text="Use these anchors to score candidate evidence consistently.",
            help_title="Scoring descriptors",
            help_text=(
                "Scoring descriptors are the 1-5 quality anchors for this scored competency.\n\n"
                "Score based on the strongest concrete behavioral evidence you heard."
            ),
        )
        self._samples_body = self._render_disclosure_section(
            parent=parent,
            frame_title="Sample answers (for interviewer reference, not shared with candidate)",
            section_key="samples",
            section_var=self.samples_expanded_var,
            line_values=self.trait["sample_answers"],
        )

    def _initial_disclosure_state(self, section_key: str) -> bool:
        """Read session disclosure preference first, then deterministic defaults."""
        disclosure_state = getattr(self.app, "_trait_disclosure_state", None)
        if isinstance(disclosure_state, dict):
            persisted = disclosure_state.get((self.tid, section_key))
            if isinstance(persisted, bool):
                return persisted
        return self.DISCLOSURE_DEFAULTS.get(section_key, False)

    def _render_disclosure_section(
        self,
        parent: ttk.Frame,
        frame_title: str,
        section_key: str,
        section_var: BooleanVar,
        line_values: dict[str, str],
        header_text: str | None = None,
        help_title: str | None = None,
        help_text: str | None = None,
    ) -> ttk.Frame:
        section_frame = ttk.LabelFrame(parent, text=frame_title)
        section_frame.pack(fill="x", pady=self.SECTION_PADY_TIGHT)
        section_head = ttk.Frame(section_frame)
        section_head.pack(fill="x", padx=2, pady=(2, 4))

        if header_text:
            ttk.Label(section_head, text=header_text, foreground="#475569").pack(side="left")
        ttk.Label(
            section_head,
            text=self.SECONDARY_OPTIONAL_HELPER_TEXT,
            foreground="#64748B",
        ).pack(side="left", padx=(8, 0))
        if help_title and help_text:
            ttk.Button(
                section_head,
                text="?",
                width=3,
                command=lambda: self._show_help_definition(help_title, help_text),
            ).pack(side="right", padx=(6, 0))

        section_body = ttk.Frame(section_frame)
        toggle_button = self._make_toggle_button(
            section_head,
            section_key=section_key,
            section_var=section_var,
            command=lambda: self._toggle_section(
                section_key=section_key,
                section_var=section_var,
                section_body=section_body,
                toggle_button=toggle_button,
            ),
        )
        toggle_button.pack(side="right")

        anchor_values = line_values if isinstance(line_values, dict) else {}
        for n in [5, 4, 3, 2, 1]:
            value = str(anchor_values.get(str(n), "") or "").strip()
            if not value:
                continue
            ttk.Label(section_body, text=f"{n}: {value}", wraplength=1030).pack(anchor="w")
        self._apply_section_visibility(section_key, section_var, section_body, toggle_button)
        return section_body

    def _make_toggle_button(
        self,
        parent: ttk.Frame,
        section_key: str,
        section_var: BooleanVar,
        command: Any,
    ) -> tk.Button:
        btn = tk.Button(
            parent,
            text=self._toggle_label(section_key, section_var),
            padx=10,
            pady=2,
            highlightthickness=2,
            highlightcolor=FOCUS_RING,
            takefocus=True,
            command=command,
        )
        btn.bind("<Return>", lambda _e: self._run_toggle_from_keyboard(command, section_key))
        btn.bind("<space>", lambda _e: self._run_toggle_from_keyboard(command, section_key))
        self._bind_focus_visible_style(btn)
        btn.configure(text=self._toggle_label(section_key, section_var))
        return btn

    def _bind_focus_visible_style(self, widget: tk.Widget, blur_color: str = "#9CA3AF") -> None:
        widget.bind("<FocusIn>", lambda _e: widget.configure(highlightbackground=FOCUS_RING))
        widget.bind("<FocusOut>", lambda _e: widget.configure(highlightbackground=blur_color))
        widget.configure(highlightbackground=blur_color)


    def _run_toggle_from_keyboard(self, command: Any, section_key: str) -> str:
        self.keyboard_session.mark_step()
        logger = getattr(self.app, "metrics_logger", None)
        if logger is not None:
            logger.log_ux_completion(
                app="interview",
                surface="trait_screen",
                outcome="keyboard_only_success",
                target=f"toggle_{section_key}",
                input_method="keyboard",
            )
        command()
        return "break"

    def _toggle_label(self, section_key: str, section_var: BooleanVar) -> str:
        label_suffix = self.DISCLOSURE_LABELS.get(section_key, section_key.replace("_", " "))
        action = "Hide" if section_var.get() else "Show"
        return f"{action} {label_suffix}"

    def _toggle_section(
        self,
        section_key: str,
        section_var: BooleanVar,
        section_body: ttk.Frame | None,
        toggle_button: tk.Button,
    ) -> None:
        if not section_body:
            return
        section_var.set(not section_var.get())
        disclosure_state = getattr(self.app, "_trait_disclosure_state", None)
        if isinstance(disclosure_state, dict):
            disclosure_state[(self.tid, section_key)] = bool(section_var.get())
        self._apply_section_visibility(section_key, section_var, section_body, toggle_button)
        self._log_section_toggle(section_key, section_var.get())

    def _apply_section_visibility(
        self,
        section_key: str,
        section_var: BooleanVar,
        section_body: ttk.Frame,
        toggle_button: tk.Button,
    ) -> None:
        is_expanded = bool(section_var.get())
        toggle_button.configure(text=self._toggle_label(section_key, section_var))
        if is_expanded:
            section_body.pack(fill="x", padx=6, pady=(0, 6))
            return
        section_body.pack_forget()

    def _log_section_toggle(self, section_key: str, expanded: bool) -> None:
        logger = getattr(self.app, "metrics_logger", None)
        if logger is None:
            return
        logger.log_ux_click(
            app="interview",
            surface="trait_screen",
            target="section_toggle",
            section=section_key,
            expanded=expanded,
            trait_id=self.tid,
            flow_index=self.flow_idx,
        )

    def _render_no_example_addendum(self, parent: ttk.Frame) -> None:
        """UI-only addendum to close the common 'no example' failure mode.

        This does not change rubric wording, weights, or disqualifier rules.
        """

        box = ttk.LabelFrame(parent, text="If candidate says: 'That's never happened to me'")
        box.pack(fill="x", pady=self.SECTION_PADY_STANDARD)

        ttk.Label(
            box,
            text=self.SECONDARY_OPTIONAL_HELPER_TEXT,
            foreground="#64748B",
        ).pack(anchor="w", padx=10, pady=(8, 2))

        ttk.Label(
            box,
            text=(
                "Do not score yet. Treat this as missing behavioral evidence and probe neutrally.\n\n"
                f"Default script: {NEVER_HAPPENED_GLOBAL_SCRIPT}"
            ),
            wraplength=1030,
            foreground="#334155",
        ).pack(anchor="w", padx=10, pady=(8, 6))

        row = ttk.Frame(box)
        row.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Checkbutton(
            row,
            text="Still no example after follow-ups (cap scored competency at 3)",
            variable=self.no_example_var,
            command=self._enforce_no_example_cap,
        ).pack(side="left")

        ttk.Button(row, text="Show competency-specific follow-ups", command=self._show_no_example_help).pack(side="right")

        # Bind after q_text exists (render order calls this before notes), so we delay the binding.
        self.app.after(50, self._bind_no_example_hint)

    def _bind_no_example_hint(self) -> None:
        if not hasattr(self, "q_text"):
            return
        self.q_text.bind("<FocusOut>", self._hint_from_notes)

    def _hint_from_notes(self, _e: tk.Event | None = None) -> None:
        try:
            txt = self.q_text.get("1.0", END)
        except Exception:
            return
        if not text_suggests_no_example(txt):
            return
        if self.no_example_var.get():
            return
        self.no_example_var.set(True)
        self._enforce_no_example_cap(show_message=False)

    def _enforce_no_example_cap(self, show_message: bool = True) -> None:
        """If interviewer indicates no usable example after follow-ups, cap raw score at 3."""
        if not self.no_example_var.get():
            return
        if self.dq_var.get():
            return

        raw = int(self.raw_var.get() or 0)
        if raw <= 3:
            return

        self.raw_var.set(3)
        if not show_message:
            return
        self.inline_validation.show(
            issue="No usable example was provided after follow-ups, so this scored competency was capped at 3.",
            next_step="Select 2 or 3 based on the strength of reflection and evidence.",
            focus_widget=self.score_widgets[0] if self.score_widgets else None,
        )

    def _show_no_example_help(self) -> None:
        info = NEVER_HAPPENED_BY_TRAIT.get(self.tid)
        title = info.get("title") if info else self.trait.get("name", "Scored competency")

        win = tk.Toplevel(self.app)
        win.title("No-example follow-ups")
        win.geometry("820x620")

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text=f"{title}",
            font=("TkDefaultFont", self.app.settings["font_size"] + 3, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        ttk.Label(
            outer,
            text=f"Default script: {NEVER_HAPPENED_GLOBAL_SCRIPT}",
            wraplength=780,
            foreground="#334155",
        ).pack(anchor="w", pady=(0, 10))

        if not info:
            ttk.Label(
                outer,
                text=(
                    "No competency-specific follow-ups are configured for this competency ID.\n\n"
                    "Use the default script above, then ask for the closest similar situation, how they generally handle it, and what they learned."
                ),
                wraplength=780,
            ).pack(anchor="w")
            ttk.Button(outer, text="Close", command=win.destroy).pack(anchor="e", pady=12)
            return

        # Follow-ups
        fbox = ttk.LabelFrame(outer, text="Approved follow-ups")
        fbox.pack(fill="x", pady=6)
        for q in info.get("followups", []):
            ttk.Label(fbox, text=f"• {q}", wraplength=760).pack(anchor="w", padx=10, pady=2)

        # Scoring interpretation
        sbox = ttk.LabelFrame(outer, text="How to score after follow-up")
        sbox.pack(fill="x", pady=6)
        for line in info.get("scoring", []):
            ttk.Label(sbox, text=f"• {line}", wraplength=760).pack(anchor="w", padx=10, pady=2)

        # Concerning vs disqualifying (guidance only)
        cbox = ttk.LabelFrame(outer, text="When this becomes concerning")
        cbox.pack(fill="x", pady=6)
        concerns = info.get("concerns", [])
        concern_lines = concerns or [
            "If denial is paired with rigidity, minimization, or control language, probe thoroughly.",
        ]
        for line in concern_lines:
            ttk.Label(cbox, text=f"• {line}", wraplength=760).pack(anchor="w", padx=10, pady=2)

        ttk.Label(
            outer,
            text=(
                "Final rule: If after two follow-ups the candidate still cannot provide reflection, generalization, or insight, "
                "score no higher than 3 (often 2). If denial matches existing disqualifier criteria, use the disqualifier checkbox and quote box."
            ),
            wraplength=780,
            foreground="#334155",
        ).pack(anchor="w", pady=(10, 0))

        ttk.Button(outer, text="Close", command=win.destroy).pack(anchor="e", pady=12)

    def _render_score_box(self, parent: ttk.Frame) -> None:
        score_box = tk.Frame(parent, bg=SCORE_PANEL_BG, bd=1, relief="solid", highlightthickness=1, highlightbackground=COLORS["border"])
        score_box.pack(fill="x", pady=(8, 6))

        score_label = tk.Label(
            score_box,
            text="Select raw score (required unless absolute disqualifier is checked)",
            bg=SCORE_PANEL_BG,
            fg=COLORS["text"],
            font=("TkDefaultFont", self.app.settings["font_size"] + 2, "bold"),
        )
        score_label.pack(anchor="w", padx=12, pady=(10, 6))

        help_button = tk.Button(
            score_box,
            text="Help",
            padx=10,
            pady=4,
            command=lambda: self._show_help_definition(
                "Raw score",
                "Raw score is the 1-5 rating for this scored competency before weighting.\n\n"
                "If an absolute disqualifier is checked, leave raw score unselected and document verbatim evidence.",
            ),
        )
        configure_plain_button(help_button, font_size=self.app.settings["font_size"])
        help_button.pack(anchor="e", padx=12, pady=(0, 6))

        score_row = tk.Frame(score_box, bg=SCORE_PANEL_BG)
        score_row.pack(fill="x", padx=12, pady=(0, 10))

        for n in [1, 2, 3, 4, 5]:
            rb = tk.Radiobutton(
                score_row,
                text=str(n),
                value=n,
                variable=self.raw_var,
                bg=SCORE_PANEL_BG,
                activebackground=SCORE_PANEL_BG,
                font=("TkDefaultFont", self.app.settings["font_size"] + 2, "bold"),
                padx=10,
                pady=6,
                highlightthickness=2,
                highlightcolor=FOCUS_RING,
                highlightbackground=COLORS["border"],
            )
            rb.pack(side="left", padx=(0, 14))
            self.score_widgets.append(rb)
        if self.score_widgets:
            associate_label_with_control(score_label, self.score_widgets[0])

        self.inline_validation = create_inline_validation_message(score_box, pady=(0, 10))

    def _render_disqualifier_box(self, parent: ttk.Frame) -> None:
        dq_wrap = tk.Frame(parent, bg=DQ_PANEL_BG, bd=1, relief="solid", highlightthickness=1, highlightbackground=COLORS["danger"])
        dq_wrap.pack(fill="x", pady=(6, 6))

        dq_inner = tk.Frame(dq_wrap, bg=DQ_PANEL_BG)
        dq_inner.pack(fill="x", padx=10, pady=8)

        dq_toggle = tk.Checkbutton(
            dq_inner,
            text="Absolute disqualifier observed (for this scored competency)",
            variable=self.dq_var,
            bg=DQ_PANEL_BG,
            activebackground=DQ_PANEL_BG,
            font=("TkDefaultFont", self.app.settings["font_size"], "bold"),
            padx=6,
            pady=4,
            highlightthickness=2,
            highlightcolor=FOCUS_RING,
            highlightbackground=COLORS["border"],
        )
        dq_toggle.pack(side="left")

        help_button = tk.Button(
            dq_inner,
            text="Help",
            padx=10,
            pady=4,
            command=lambda: self._show_help_definition(
                "Absolute disqualifier",
                "An absolute disqualifier is a behavior or statement that requires an automatic no-hire recommendation.\n\n"
                "When checked, include a verbatim quote or specific notes that justify the flag.",
            ),
        )
        configure_plain_button(help_button, role="danger", font_size=self.app.settings["font_size"])
        help_button.pack(side="left", padx=(8, 0))

        global_button = tk.Button(
            dq_inner,
            text="View Global Disqualifiers",
            padx=10,
            pady=4,
            command=self.app.show_disqualifier_reference,
        )
        configure_plain_button(global_button, role="danger", font_size=self.app.settings["font_size"])
        global_button.pack(side="right")

        verbatim_wrap = tk.Frame(dq_wrap, bg=DQ_PANEL_BG)
        verbatim_wrap.pack(fill="x", padx=10, pady=(6, 8))

        self.v_label = tk.Label(
            verbatim_wrap,
            text="Verbatim quote/notes (required when absolute disqualifier is checked)",
            bg=DQ_PANEL_BG,
            fg=COLORS["danger"],
            font=("TkDefaultFont", self.app.settings["font_size"], "bold"),
        )

        self.v_text = tk.Text(verbatim_wrap, height=6, wrap="word")
        configure_text_widget(self.v_text, font_size=self.app.settings["font_size"], danger=True)
        self.v_text.insert(END, self.state.get("verbatim_notes", ""))
        self.keyboard_session.bind(self.v_text)

        self.normal_v_bg = self.v_text.cget("bg")

        self.dq_var.trace_add("write", lambda *_: self._sync_dq_dependent_ui())
        self.v_text.bind("<KeyRelease>", lambda _e: self._sync_dq_dependent_ui())
        self.v_text.bind("<<Paste>>", lambda _e: self.v_text.after(1, self._sync_dq_dependent_ui))

        associate_label_with_control(self.v_label, self.v_text)
        self._verbatim_wrap = verbatim_wrap  # for packing control

    def _render_notes(self, parent: ttk.Frame) -> None:
        notes_frame = ttk.Frame(parent)
        notes_frame.pack(fill="both", expand=True)

        ttk.Label(notes_frame, text="Question notes (capture concrete evidence / follow-ups)").pack(anchor="w")
        self.q_text = tk.Text(notes_frame, height=7, wrap="word")
        configure_text_widget(self.q_text, font_size=self.app.settings["font_size"])
        self.q_text.pack(fill="x", pady=4)
        self.q_text.insert(END, self.state.get("question_notes", ""))
        self.keyboard_session.bind(self.q_text)

        ttk.Label(notes_frame, text="Scored competency notes (evaluation summary for final report)").pack(anchor="w", pady=(4, 0))
        self.t_text = tk.Text(notes_frame, height=7, wrap="word")
        configure_text_widget(self.t_text, font_size=self.app.settings["font_size"])
        self.t_text.pack(fill="x", pady=4)
        self.t_text.insert(END, self.state.get("trait_notes", ""))
        self.keyboard_session.bind(self.t_text)

    def _render_trait_signal_checkboxes(self, parent: ttk.Frame) -> None:
        try:
            definition = load_trait_signal_ui_definition(self.tid)
        except Exception as exc:
            box = ttk.LabelFrame(parent, text="DeepSeek observation hints")
            box.pack(fill="x", pady=self.SECTION_PADY_STANDARD)
            ttk.Label(
                box,
                text=f"DeepSeek observation hints unavailable: {exc}",
                foreground="#92400e",
                wraplength=1030,
            ).pack(anchor="w", padx=10, pady=8)
            self.signal_selection_vars = {}
            self._signal_ui_definition = {}
            return

        self._signal_ui_definition = definition
        valid_signal_ids = list(definition.get("valid_signal_ids", []) or [])
        self.model_signal_suggestions = {
            item["signal_id"]: item
            for item in normalize_model_signal_suggestions(
                self.state.get("model_signal_suggestions", []),
                valid_signal_ids,
            )
        }
        self.signal_selection_vars = {}

        box = ttk.LabelFrame(parent, text="DeepSeek observation hints")
        box.pack(fill="x", pady=self.SECTION_PADY_STANDARD)

        self._render_signal_section(
            box,
            str(definition.get("core_section_label") or "Core Signals"),
            list(definition.get("core_signals", []) or []),
        )
        for group in definition.get("extended_groups", []) or []:
            self._render_signal_section(
                box,
                str(group.get("group_label") or definition.get("extended_section_label") or "Additional Observations"),
                list(group.get("signals", []) or []),
            )

    def _render_signal_section(
        self,
        parent: ttk.Frame,
        section_label: str,
        signals: list[dict[str, Any]],
    ) -> None:
        if not signals:
            return
        section = ttk.LabelFrame(parent, text=section_label)
        section.pack(fill="x", padx=10, pady=(8, 4))
        for signal in signals:
            signal_id = str(signal.get("signal_id", "") or "").strip()
            if not signal_id:
                continue
            label = str(signal.get("label", "") or signal_id).strip()
            suggestion = self.model_signal_suggestions.get(signal_id)
            if not suggestion:
                continue
            confidence = suggestion.get("confidence", 0.0)
            rationale = str(suggestion.get("rationale") or "").strip()
            text = f"{label}: suggested by model ({confidence:.2f})"
            if rationale:
                text = f"{text}: {rationale}"
            ttk.Label(section, text=text, foreground="#4b5563", wraplength=980).pack(anchor="w", padx=8, pady=(0, 4))

    def _selected_signal_ids(self) -> list[str]:
        return []

    def _get_verbatim_value(self) -> str:
        return self.v_text.get("1.0", END).strip()

    def _set_score_enabled(self, enabled: bool) -> None:
        st = "normal" if enabled else "disabled"
        for rb2 in self.score_widgets:
            rb2.configure(state=st)

    def _sync_dq_dependent_ui(self) -> None:
        dq_on = bool(self.dq_var.get())
        has_quote = bool(self._get_verbatim_value())

        self._set_verbatim_visibility(dq_on)
        self._set_score_enabled(not dq_on)

        if dq_on and not has_quote:
            self.v_text.configure(bg=self.dq_required_bg)
            return
        self.v_text.configure(bg=self.normal_v_bg)

    def _set_verbatim_visibility(self, is_visible: bool) -> None:
        if is_visible:
            self._pack_if_hidden(self.v_label, anchor="w", pady=(0, 4))
            self._pack_if_hidden(self.v_text, fill="x", pady=(0, 2))
            return

        self._hide_if_shown(self.v_label)
        self._hide_if_shown(self.v_text)

    @staticmethod
    def _pack_if_hidden(widget: tk.Widget, **pack_kwargs: Any) -> None:
        if widget.winfo_ismapped():
            return
        widget.pack(**pack_kwargs)

    @staticmethod
    def _hide_if_shown(widget: tk.Widget) -> None:
        if not widget.winfo_ismapped():
            return
        widget.pack_forget()

    def persist_state(self) -> bool:
        if self.flow_idx == 0 and self.qualification_vars:
            if not validate_and_store_qualification(self.app, self.qualification_vars):
                return False

        if self.inline_validation is not None:
            self.inline_validation.clear()

        dq_on = bool(self.dq_var.get())
        raw = self.raw_var.get()

        if not dq_on and bool(self.no_example_var.get()) and raw in {1, 2, 3, 4, 5} and raw > 3:
            self.raw_var.set(3)
            raw = 3
            if self.inline_validation is not None:
                self.inline_validation.show(
                    issue="No usable example was provided after follow-ups, so this scored competency was capped at 3.",
                    next_step="Select 2 or 3 based on the strength of reflection and evidence.",
                    focus_widget=self.score_widgets[0] if self.score_widgets else None,
                    severity=VALIDATION_SEVERITY_WARNING,
                )

        if not dq_on and raw not in {1, 2, 3, 4, 5}:
            if self.inline_validation is not None:
                self.inline_validation.show(
                    issue="A raw score is missing for this scored competency.",
                    next_step="Select 1-5, or check absolute disqualifier if that applies.",
                    focus_widget=self.score_widgets[0] if self.score_widgets else None,
                    severity=VALIDATION_SEVERITY_ERROR,
                )
            _log_validation_error(self.app, error_type="raw_score_missing", context="trait_score")
            return False

        verbatim = self.v_text.get("1.0", END).strip()
        if dq_on and not verbatim:
            if self.inline_validation is not None:
                self.inline_validation.show(
                    issue="Absolute disqualifier is checked without supporting quote or notes.",
                    next_step="Add a verbatim quote or specific notes, then continue.",
                    focus_widget=self.v_text,
                    severity=VALIDATION_SEVERITY_ERROR,
                )
            _log_validation_error(self.app, error_type="dq_quote_missing", context="trait_score")
            return False

        t = self.app.state.trait_inputs[self.tid]
        t["absolute_disqualifier"] = dq_on
        t["raw_score"] = (raw if (not dq_on) else None)
        t["skipped"] = False
        t["no_example_after_followups"] = bool(self.no_example_var.get())
        t["question_notes"] = self.q_text.get("1.0", END).strip()
        t["trait_notes"] = self.t_text.get("1.0", END).strip()
        t["verbatim_notes"] = verbatim
        write_canonical_selected_signal_ids(t, [])

        self.app.state.current_index = self.flow_idx + 1
        persist_snapshot = getattr(self.app, "_persist_interview_session_snapshot", None)
        if callable(persist_snapshot):
            persist_snapshot(self.flow_idx)
        return True

    def _render_footer_actions(self) -> None:
        render_question_footer(
            self.app,
            flow_idx=self.flow_idx,
            is_last=self._is_last(),
            go_back=self.go_back,
            skip_question=self.skip_question,
            save_draft=self.save_draft,
            continue_or_finalize=self.finalize_or_continue if self._is_last() else self.go_next,
            persist_for_exit=self.persist_state_for_exit,
        )

    def _is_last(self) -> bool:
        return self.flow_idx == self.app._flow_len() - 1

    def go_back(self) -> None:
        if not self.persist_state():
            return
        try:
            self.app._queue_transcription_and_transition(self.flow_idx, self.flow_idx - 1)
        except Exception as exc:
            self._show_recording_failure(exc)

    def go_next(self) -> None:
        if not self.persist_state():
            return
        self._advance()

    def persist_state_for_exit(self) -> None:
        t = self.app.state.trait_inputs.setdefault(self.tid, {})
        raw = self.raw_var.get()
        t["absolute_disqualifier"] = bool(self.dq_var.get())
        if raw in {1, 2, 3, 4, 5} and not t["absolute_disqualifier"]:
            t["raw_score"] = raw
        t["skipped"] = False
        t["no_example_after_followups"] = bool(self.no_example_var.get())
        t["question_notes"] = self.q_text.get("1.0", END).strip()
        t["trait_notes"] = self.t_text.get("1.0", END).strip()
        t["verbatim_notes"] = self.v_text.get("1.0", END).strip()
        write_canonical_selected_signal_ids(t, [])
        self.app.state.current_index = self.flow_idx + 1

    def skip_question(self) -> None:
        t = self.app.state.trait_inputs[self.tid]
        t["raw_score"] = None
        t["absolute_disqualifier"] = False
        t["verbatim_notes"] = ""
        t["skipped"] = True
        write_canonical_selected_signal_ids(t, [])
        self._advance(discard_recording=True)

    def _advance(self, next_index: int | None = None, discard_recording: bool = False) -> None:
        is_last = self._is_last()
        try:
            self.app._queue_transcription_and_transition(
                self.flow_idx,
                next_index,
                is_last=is_last,
                discard_recording=discard_recording,
            )
        except Exception as exc:
            self._show_recording_failure(exc)

    def save_draft(self) -> None:
        if not self.persist_state():
            return
        self.keyboard_session.complete(abandoned=False, screen_id=f"trait_{self.tid}_save_draft")
        try:
            dm = DraftManager(Path(self.app.settings["base_dir"]))
            payload = self.app.state.to_dict()
            path = dm.save_draft(payload)
            messagebox.showinfo(
                "Draft saved",
                format_guidance(
                    f"Your draft was saved to {path}.",
                    "Use Open Draft later to resume this interview.",
                ),
            )
        except Exception as exc:
            details = traceback.format_exc()
            log_path = Path(self.app.settings["base_dir"]) / "ui_error_log.txt"
            append_error_log(log_path, "Draft save failed", f"{exc}\n\n{details}")
            show_actionable_error(
                self.app,
                title="Draft save failed",
                issue="The draft could not be saved right now.",
                next_step=f"Try Save Draft again; if it still fails, use Copy technical details and share {log_path}.",
                technical_details=f"{exc}\n\n{details}",
            )

    def finalize_or_continue(self) -> None:
        if not self.persist_state():
            return
        self.keyboard_session.complete(abandoned=False, screen_id=f"trait_{self.tid}_finalize_or_continue")
        self._advance()

    def _show_help_definition(self, title: str, text: str) -> None:
        messagebox.showinfo(title, text)

    def _show_recording_failure(self, exc: Exception) -> None:
        details = traceback.format_exc()
        log_path = Path(self.app.settings["base_dir"]) / "ui_error_log.txt"
        append_error_log(log_path, "Recording transition failed", f"{exc}\n\n{details}")
        show_actionable_error(
            self.app,
            title="Recording unavailable",
            issue="The recording step did not complete.",
            next_step="Try the action again. If it keeps failing, copy technical details and contact support.",
            technical_details=f"{exc}\n\n{details}",
        )


# =========================
# Custom question screen UI helper
# =========================

class CustomQuestionScreenUI:
    def __init__(self, app: InterviewApp, flow_idx: int, qid: str, qtext: str):
        self.app = app
        self.flow_idx = flow_idx
        self.qid = qid
        self.qtext = qtext
        self.text_box: tk.Text
        self.qualification_vars: dict[str, Any] | None = None
        self.keyboard_session = KeyboardPathSession(
            logger=getattr(self.app, "metrics_logger", None),
            flow_id="interview_question",
            screen_id=f"custom_{self.qid}",
        )

    def render(self) -> None:
        frm = ttk.Frame(self.app.page_frame, padding=12)
        frm.pack(fill="both", expand=True)
        self.keyboard_session.bind(frm)
        self._render_footer_actions()

        if self.flow_idx == 0:
            self.qualification_vars = render_qualification_box(frm, self.app)

        self.app.render_progress_strip(frm, self.flow_idx, is_scored=False)

        header = ttk.LabelFrame(frm, text=f"Question {self.flow_idx + 1} of {self.app._flow_len()} (Non-scored)")
        header.pack(fill="x", pady=6)

        ttk.Label(
            header,
            text=self.qtext,
            font=("TkDefaultFont", self.app.settings["font_size"] + 2, "bold"),
            wraplength=1050,
        ).pack(anchor="w", padx=10, pady=(10, 10))

        box = ttk.LabelFrame(frm, text="Answer / Notes")
        box.pack(fill="x", pady=8)

        ttk.Label(box, text="Capture the candidate's response as objectively as possible.", foreground="#475569").pack(anchor="w", padx=10, pady=(10, 0))

        self.text_box = tk.Text(box, height=8, wrap="word")
        configure_text_widget(self.text_box, font_size=self.app.settings["font_size"])
        self.text_box.pack(fill="x", padx=10, pady=10)
        self.keyboard_session.bind(self.text_box)

        existing = (self.app.state.custom_inputs.get(self.qid, {}) or {}).get("answer", "")
        if existing:
            self.text_box.insert(END, existing)

        self.app.after_idle(self.text_box.focus_set)

    def persist_custom(self) -> bool:
        if self.flow_idx == 0 and self.qualification_vars:
            if not validate_and_store_qualification(self.app, self.qualification_vars):
                return False

        ans = self.text_box.get("1.0", END).strip()
        self.app.state.custom_inputs[self.qid] = {"question_text": self.qtext, "answer": ans, "skipped": False}
        self.app.state.current_index = self.flow_idx + 1
        persist_snapshot = getattr(self.app, "_persist_interview_session_snapshot", None)
        if callable(persist_snapshot):
            persist_snapshot(self.flow_idx)
        return True

    def _render_footer_actions(self) -> None:
        render_question_footer(
            self.app,
            flow_idx=self.flow_idx,
            is_last=self._is_last(),
            go_back=self.go_back,
            skip_question=self.skip_question,
            save_draft=self.save_draft,
            continue_or_finalize=self.finalize_or_continue if self._is_last() else self.go_next,
            persist_for_exit=self.persist_custom_for_exit,
        )

    def _is_last(self) -> bool:
        return self.flow_idx == self.app._flow_len() - 1

    def _advance(self, next_index: int | None = None, discard_recording: bool = False) -> None:
        is_last = self._is_last()
        try:
            self.app._queue_transcription_and_transition(
                self.flow_idx,
                next_index,
                is_last=is_last,
                discard_recording=discard_recording,
            )
        except Exception as exc:
            self._show_recording_failure(exc)

    def go_back(self) -> None:
        if not self.persist_custom():
            return
        try:
            self.app._queue_transcription_and_transition(self.flow_idx, self.flow_idx - 1)
        except Exception as exc:
            self._show_recording_failure(exc)

    def go_next(self) -> None:
        if not self.persist_custom():
            return
        self._advance()

    def persist_custom_for_exit(self) -> None:
        ans = self.text_box.get("1.0", END).strip()
        self.app.state.custom_inputs[self.qid] = {"question_text": self.qtext, "answer": ans, "skipped": False}
        self.app.state.current_index = self.flow_idx + 1

    def skip_question(self) -> None:
        self.app.state.custom_inputs[self.qid] = {
            "question_text": self.qtext,
            "answer": "",
            "skipped": True,
        }
        self._advance(discard_recording=True)

    def save_draft(self) -> None:
        if not self.persist_custom():
            return
        self.keyboard_session.complete(abandoned=False, screen_id=f"custom_{self.qid}_save_draft")
        try:
            dm = DraftManager(Path(self.app.settings["base_dir"]))
            payload = self.app.state.to_dict()
            path = dm.save_draft(payload)
            messagebox.showinfo(
                "Draft saved",
                format_guidance(
                    f"Your draft was saved to {path}.",
                    "Use Open Draft later to resume this interview.",
                ),
            )
        except Exception as exc:
            details = traceback.format_exc()
            log_path = Path(self.app.settings["base_dir"]) / "ui_error_log.txt"
            append_error_log(log_path, "Draft save failed", f"{exc}\n\n{details}")
            show_actionable_error(
                self.app,
                title="Draft save failed",
                issue="The draft could not be saved right now.",
                next_step=f"Try Save Draft again; if it still fails, use Copy technical details and share {log_path}.",
                technical_details=f"{exc}\n\n{details}",
            )

    def finalize_or_continue(self) -> None:
        if not self.persist_custom():
            return
        self.keyboard_session.complete(abandoned=False, screen_id=f"custom_{self.qid}_finalize_or_continue")
        self._advance()

    def _show_recording_failure(self, exc: Exception) -> None:
        details = traceback.format_exc()
        log_path = Path(self.app.settings["base_dir"]) / "ui_error_log.txt"
        append_error_log(log_path, "Recording transition failed", f"{exc}\n\n{details}")
        show_actionable_error(
            self.app,
            title="Recording unavailable",
            issue="The recording step did not complete.",
            next_step="Try the action again. If it keeps failing, copy technical details and contact support.",
            technical_details=f"{exc}\n\n{details}",
        )




class HistoryDataGrid(ttk.Frame):
    """Treeview-backed history grid with filter/sort state and callback dispatch."""

    COLUMNS = (
        "interview_date",
        "candidate_name",
        "interview_score",
        "determination",
        "offer_action",
        "notes_link",
        "regenerate_notes_action",
        "school",
        "position",
        "delete_action",
    )

    def __init__(
        self,
        parent: ttk.Frame,
        *,
        on_offer_action: RowCallback,
        on_retranscribe_action: RowCallback,
        on_open_transcript_link: RowCallback,
        on_open_notes_link: RowCallback,
        on_row_selected: RowCallback,
        on_regenerate_notes_action: RowCallback | None = None,
        on_delete_action: RowCallback | None = None,
        on_sort_changed: SortCallback | None = None,
        sort_column: str = "interview_date",
        sort_desc: bool = True,
    ) -> None:
        super().__init__(parent)
        self._on_offer_action = on_offer_action
        self._on_retranscribe_action = on_retranscribe_action
        self._on_open_transcript_link = on_open_transcript_link
        self._on_open_notes_link = on_open_notes_link
        self._on_regenerate_notes_action = on_regenerate_notes_action
        self._on_delete_action = on_delete_action
        self._on_row_selected = on_row_selected
        self._on_sort_changed = on_sort_changed
        self.sort_column = sort_column
        self.sort_desc = sort_desc
        self.filter_text = ""
        self._all_rows: list[HistoryRow] = []
        self._visible_rows: list[HistoryRow] = []
        self._tooltip_window: tk.Toplevel | None = None
        self._tooltip_label: ttk.Label | None = None
        self._tooltip_text = ""
        self._tree = self._build_tree()

    def _build_tree(self) -> ttk.Treeview:
        tree = ttk.Treeview(self, columns=self.COLUMNS, show="headings", height=14)
        self._configure_headers(tree)
        self._configure_columns(tree)
        tree.bind("<ButtonRelease-1>", self._handle_click)
        tree.bind("<Motion>", self._handle_motion)
        tree.bind("<Leave>", lambda _event: self._hide_tooltip())
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        y_scroll.pack(side="left", fill="y", padx=(0, 8), pady=8)
        return tree

    def _configure_headers(self, tree: ttk.Treeview) -> None:
        tree.heading("interview_date", text="Date", command=lambda: self.toggle_sort("interview_date"))
        tree.heading("candidate_name", text="Interviewee", command=lambda: self.toggle_sort("candidate_name"))
        tree.heading("interview_score", text="Interview Score", command=lambda: self.toggle_sort("interview_score"))
        tree.heading("determination", text="Determination", command=lambda: self.toggle_sort("determination"))
        tree.heading("offer_action", text="Offer")
        tree.heading("notes_link", text="Interview Notes")
        tree.heading("regenerate_notes_action", text="Regenerate Notes")
        tree.heading("school", text="School", command=lambda: self.toggle_sort("school"))
        tree.heading("position", text="Position", command=lambda: self.toggle_sort("position"))
        tree.heading("delete_action", text="Delete")

    @staticmethod
    def _configure_columns(tree: ttk.Treeview) -> None:
        tree.column("interview_date", width=140, anchor="w")
        tree.column("candidate_name", width=220, anchor="w")
        tree.column("interview_score", width=120, anchor="center")
        tree.column("determination", width=130, anchor="center")
        tree.column("offer_action", width=130, anchor="center")
        tree.column("notes_link", width=140, anchor="center")
        tree.column("regenerate_notes_action", width=150, anchor="center")
        tree.column("school", width=160, anchor="w")
        tree.column("position", width=190, anchor="w")
        tree.column("delete_action", width=90, anchor="center")

    def set_rows(self, rows: list[HistoryRow]) -> None:
        self._all_rows = [dict(row) for row in rows]
        self.refresh_rows()

    def set_filter_text(self, value: str) -> None:
        self.filter_text = str(value or "").strip().lower()
        self.refresh_rows()

    def visible_rows(self) -> list[HistoryRow]:
        return [dict(row) for row in self._visible_rows]

    def selected_row(self) -> HistoryRow | None:
        selected = self._tree.selection()
        if not selected:
            return None
        row_key = str(selected[0]).strip()
        if not row_key:
            return None
        for row in self._visible_rows:
            if self._row_key(row) == row_key:
                return row
        return None

    def toggle_sort(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = False
        if self._on_sort_changed is not None:
            self._on_sort_changed(self.sort_column, self.sort_desc)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        for item_id in self._tree.get_children():
            self._tree.delete(item_id)
        self._visible_rows = self._filtered_sorted_rows(self._all_rows)
        for row in self._visible_rows:
            row_key = self._row_key(row)
            if not row_key:
                continue
            self._tree.insert("", "end", iid=row_key, values=self._row_values(row))

    def _filtered_sorted_rows(self, rows: list[HistoryRow]) -> list[HistoryRow]:
        if not self.filter_text:
            filtered = list(rows)
        else:
            filtered = [row for row in rows if self.filter_text in self._row_blob(row)]
        return sorted(filtered, key=lambda row: self._sort_key(row, self.sort_column), reverse=self.sort_desc)

    @staticmethod
    def _row_blob(row: HistoryRow) -> str:
        return " | ".join(
            [
                str(row.get("history_id", "")),
                str(row.get("interview_date", "")),
                str(row.get("candidate_name", "")),
                str(row.get("school", "")),
                HistoryDataGrid._position_value(row),
                str(row.get("interview_score", "")),
                str(row.get("determination", "")),
                str(row.get("offer_status", "")),
                str(row.get("offer_path", "")),
                str(row.get("offer_letter_path", "")),
                str(row.get("transcript_path", "")),
                str(row.get("interview_notes_path", "")),
            ]
        ).lower()

    @staticmethod
    def _sort_key(row: HistoryRow, column: str) -> Any:
        if column != "interview_score":
            if column == "position":
                return HistoryDataGrid._position_value(row).lower()
            return str(row.get(column, "")).lower()
        value = row.get("interview_score", 0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    def _handle_click(self, event: tk.Event) -> None:
        item_id = self._tree.identify_row(event.y)
        if not item_id:
            return
        self._tree.selection_set(item_id)
        row = self.selected_row()
        if row is None:
            return
        self._on_row_selected(row)
        column_name = self._column_name(self._tree.identify_column(event.x))
        if column_name == "offer_action":
            self._hide_tooltip()
            self._on_offer_action(row)
            return
        if column_name == "notes_link":
            self._hide_tooltip()
            self._on_open_notes_link(row)
            return
        if column_name == "regenerate_notes_action" and self._on_regenerate_notes_action is not None:
            self._hide_tooltip()
            self._on_regenerate_notes_action(row)
            return
        if column_name == "delete_action" and self._on_delete_action is not None:
            self._hide_tooltip()
            self._on_delete_action(row)

    def _handle_motion(self, event: tk.Event) -> None:
        item_id = self._tree.identify_row(event.y)
        if not item_id:
            self._hide_tooltip()
            return
        column_name = self._column_name(self._tree.identify_column(event.x))
        if column_name not in {"offer_action", "notes_link", "regenerate_notes_action", "delete_action"}:
            self._hide_tooltip()
            return
        row = self._row_by_key(str(item_id))
        if row is None:
            self._hide_tooltip()
            return
        text = self._tooltip_for_cell(row, column_name)
        if not text:
            self._hide_tooltip()
            return
        self._show_tooltip(event.x_root, event.y_root, text)

    @staticmethod
    def _column_name(column_id: str) -> str:
        if not column_id.startswith("#"):
            return ""
        index = int(column_id[1:]) - 1
        if index < 0 or index >= len(HistoryDataGrid.COLUMNS):
            return ""
        return HistoryDataGrid.COLUMNS[index]

    def _row_by_key(self, row_key: str) -> HistoryRow | None:
        for row in self._visible_rows:
            if self._row_key(row) == row_key:
                return row
        return None

    @staticmethod
    def _row_key(row: HistoryRow) -> str:
        value = str(row.get("history_id", "")).strip()
        if value:
            return value
        return f"{row.get('interview_date', '')}|{row.get('candidate_name', '')}|{row.get('interview_score', '')}"

    def _row_values(self, row: HistoryRow) -> tuple[str, ...]:
        return (
            str(row.get("interview_date", "")),
            str(row.get("candidate_name", "")),
            str(row.get("interview_score", "")),
            str(row.get("determination", "")),
            self._offer_action_label(row),
            self._notes_link_label(row),
            "Regenerate",
            str(row.get("school", "")),
            self._position_value(row),
            "Delete",
        )

    @staticmethod
    def _position_value(row: HistoryRow) -> str:
        return str(row.get("position") or row.get("track") or "").strip()

    @staticmethod
    def _offer_action_label(row: HistoryRow) -> str:
        status = str(row.get("offer_status", "")).strip().lower()
        labels = {
            "not_generated": "Generate Offer",
            "generated": "Offer Approved",
            "approved": "Offer Accepted",
            "accepted": "Send Welcome Email",
            "welcome_email_sent": "Onboarding",
        }
        return labels.get(status, "Generate Offer")

    @staticmethod
    def _link_label(path_value: str) -> str:
        if HistoryDataGrid._path_exists(path_value):
            return "Open"
        return "Unavailable"

    @classmethod
    def _notes_link_label(cls, row: HistoryRow) -> str:
        status = str(row.get("deepseek_processing_status", "")).strip().lower()
        if status == "processing":
            return "Processing"
        if status == "failed" and not cls._path_exists(str(row.get("interview_notes_path", ""))):
            return "Failed"
        return cls._link_label(str(row.get("interview_notes_path", "")))

    @staticmethod
    def _path_exists(path_value: str) -> bool:
        path_text = str(path_value or "").strip()
        if not path_text:
            return False
        return Path(path_text).expanduser().exists()

    def _tooltip_for_cell(self, row: HistoryRow, column_name: str) -> str:
        if column_name == "offer_action":
            return self._offer_tooltip(row)
        if column_name == "regenerate_notes_action":
            return "Regenerate interview notes from saved data or rerun local DeepSeek first."
        if column_name == "delete_action":
            return "Delete this history entry after confirmation."
        status = str(row.get("deepseek_processing_status", "")).strip().lower()
        if status == "processing":
            return "DeepSeek is still processing interview notes. You can close the program; processing will continue."
        if status == "failed":
            warning = str(row.get("deepseek_processing_warning", "")).strip()
            return warning or "DeepSeek processing failed; open the available interview notes document."
        if self._path_exists(str(row.get("interview_notes_path", ""))):
            return ""
        return "File is not available for this interview."

    @staticmethod
    def _offer_tooltip(row: HistoryRow) -> str:
        status = str(row.get("offer_status", "")).strip().lower()
        messages = {
            "not_generated": "Click to generate an offer letter for this interview.",
            "generated": "Click to mark this offer as approved.",
            "approved": "Click to mark this offer as accepted by the candidate.",
            "accepted": "Click to send a welcome email and complete the offer flow.",
            "welcome_email_sent": "Click to open the onboarding checklist and task tracker.",
        }
        return messages.get(status, "Click to continue this offer workflow step.")

    def _show_tooltip(self, x_root: int, y_root: int, text: str) -> None:
        if self._tooltip_window is None or not self._tooltip_window.winfo_exists():
            tooltip = tk.Toplevel(self)
            tooltip.withdraw()
            tooltip.overrideredirect(True)
            tooltip.attributes("-topmost", True)
            label = ttk.Label(tooltip, text=text, background="#1f2937", foreground="white", padding=(8, 4))
            label.pack()
            self._tooltip_window = tooltip
            self._tooltip_label = label
            self._tooltip_text = ""
        if self._tooltip_label is not None and self._tooltip_text != text:
            self._tooltip_label.configure(text=text)
            self._tooltip_text = text
        if self._tooltip_window is not None:
            self._tooltip_window.geometry(f"+{x_root + 14}+{y_root + 10}")
            self._tooltip_window.deiconify()

    def _hide_tooltip(self) -> None:
        if self._tooltip_window is None:
            return
        if not self._tooltip_window.winfo_exists():
            self._tooltip_window = None
            self._tooltip_label = None
            self._tooltip_text = ""
            return
        self._tooltip_window.withdraw()


@dataclass(slots=True)
class KeyboardPathSession:
    logger: Any
    flow_id: str
    screen_id: str
    keyboard_step_count: int = 0
    _last_keyboard_at: float = 0.0

    def bind(self, widget: tk.Misc) -> None:
        widget.bind("<KeyPress>", self._on_keypress, add="+")

    def _on_keypress(self, _event: tk.Event) -> None:
        self.mark_step()

    def mark_step(self, step_count: int = 1) -> None:
        self.keyboard_step_count += max(1, int(step_count))
        self._last_keyboard_at = monotonic()

    def complete(self, *, abandoned: bool = False, screen_id: str | None = None) -> None:
        if self.logger is None:
            return
        active_screen_id = (screen_id or self.screen_id).strip() or self.screen_id
        completed_via_keyboard = self.keyboard_step_count > 0 and self._is_recent_keyboard_activity()
        if hasattr(self.logger, "log_keyboard_path_completed"):
            self.logger.log_keyboard_path_completed(
                screen_id=active_screen_id,
                flow_id=self.flow_id,
                completed_via_keyboard=completed_via_keyboard,
                keyboard_step_count=self.keyboard_step_count,
                abandoned=bool(abandoned),
            )
            return
        self.logger.log_event(
            "ux.keyboard_path_completed",
            screen_id=active_screen_id,
            flow_id=self.flow_id,
            completed_via_keyboard=completed_via_keyboard,
            keyboard_step_count=self.keyboard_step_count,
            abandoned=bool(abandoned),
        )

    def _is_recent_keyboard_activity(self, recency_s: float = 12.0) -> bool:
        if self._last_keyboard_at <= 0:
            return False
        return (monotonic() - self._last_keyboard_at) <= recency_s


def should_display_modal(*, severity: str, irreversible_action: bool = False) -> bool:
    """Return True only for blocking failures or irreversible confirmations."""
    return severity == VALIDATION_SEVERITY_BLOCKING or irreversible_action


def sanitize_user_error(message: str) -> str:
    """Redact noisy technical details before rendering user-facing copy."""
    clean = " ".join(str(message).replace("\n", " ").split())
    forbidden_fragments = ["traceback", "file \"", "line ", "exception", "error:"]
    lowered = clean.lower()
    for fragment in forbidden_fragments:
        if fragment in lowered:
            return "An unexpected system issue occurred."
    return clean


def format_guidance(issue: str, next_step: str) -> str:
    """Return standardized copy: one sentence issue + one sentence next step."""
    return f"{issue.strip()} {next_step.strip()}".strip()


@dataclass(slots=True)
class InlineValidationMessage:
    """Reusable inline recoverable-validation presenter."""

    message_var: tk.StringVar
    message_label: ttk.Label

    def show(
        self,
        *,
        issue: str,
        next_step: str,
        focus_widget: tk.Widget | None = None,
        severity: str = VALIDATION_SEVERITY_ERROR,
    ) -> None:
        normalized_severity = severity if severity in VALIDATION_SEVERITIES else VALIDATION_SEVERITY_ERROR
        self.message_label.configure(foreground=VALIDATION_INLINE_COLORS[normalized_severity])
        self.message_var.set(format_guidance(sanitize_user_error(issue), next_step))
        if focus_widget is None:
            return
        focus_widget.focus_set()

    def clear(self) -> None:
        self.message_var.set("")


@dataclass(slots=True)
class MainGuiWarningPresenter:
    """Non-blocking dismissible warning presenter for main-window alerts."""

    parent: tk.Misc
    frame: ttk.Frame
    message_var: tk.StringVar
    message_label: ttk.Label
    dismiss_button: ttk.Button
    _after_id: str | None = None

    def _find_pack_before_widget(self) -> tk.Widget | None:
        for widget in self.parent.winfo_children():
            if widget is self.frame:
                continue
            if widget.winfo_manager() == "pack":
                return widget
        return None

    def show(self, message: str, *, auto_expire_ms: int | None = 12000) -> None:
        clean_message = sanitize_user_error(message)
        if not clean_message:
            self.dismiss()
            return
        self.message_var.set(clean_message)
        if not self.frame.winfo_ismapped():
            before_widget = self._find_pack_before_widget()
            pack_kwargs = {"fill": "x", "padx": 8, "pady": (0, 6)}
            self._pack_frame_with_optional_before(pack_kwargs=pack_kwargs, before_widget=before_widget)
        self._cancel_timer()
        if auto_expire_ms is None or auto_expire_ms <= 0:
            return
        self._after_id = self.parent.after(auto_expire_ms, self.dismiss)

    def _pack_frame_with_optional_before(
        self,
        *,
        pack_kwargs: dict[str, object],
        before_widget: tk.Widget | None,
    ) -> None:
        if before_widget is None:
            self.frame.pack(**pack_kwargs)
            return
        try:
            self.frame.pack(**pack_kwargs, before=before_widget)
        except tk.TclError:
            self.frame.pack(**pack_kwargs)

    def dismiss(self) -> None:
        self._cancel_timer()
        self.message_var.set("")
        if self.frame.winfo_ismapped():
            self.frame.pack_forget()

    def _cancel_timer(self) -> None:
        if self._after_id is None:
            return
        self.parent.after_cancel(self._after_id)
        self._after_id = None


def create_main_gui_warning_presenter(
    parent: tk.Misc,
    *,
    on_dismiss: Callable[[], None] | None = None,
) -> MainGuiWarningPresenter:
    frame = ttk.Frame(parent, padding=(10, 6))
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(frame, textvariable=message_var, foreground="#92400e", justify="left", wraplength=900)
    message_label.pack(side="left", fill="x", expand=True)

    def _dismiss() -> None:
        presenter.dismiss()
        if on_dismiss is not None:
            on_dismiss()

    dismiss_button = ttk.Button(frame, text="Dismiss", command=_dismiss)
    dismiss_button.pack(side="right", padx=(10, 0))
    presenter = MainGuiWarningPresenter(
        parent=parent,
        frame=frame,
        message_var=message_var,
        message_label=message_label,
        dismiss_button=dismiss_button,
    )
    return presenter


def present_transcription_partial_warning(
    presenter: MainGuiWarningPresenter | None,
    *,
    auto_expire_ms: int | None = 12000,
) -> str:
    """Display standardized transcript-partial warning copy when a presenter is available."""
    if presenter is None:
        return TRANSCRIPTION_PARTIAL_WARNING_COPY
    presenter.show(TRANSCRIPTION_PARTIAL_WARNING_COPY, auto_expire_ms=auto_expire_ms)
    return TRANSCRIPTION_PARTIAL_WARNING_COPY


def create_inline_validation_message(parent: tk.Misc, *, pady: tuple[int, int] = (0, 8)) -> InlineValidationMessage:
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(parent, textvariable=message_var, foreground="#b91c1c", wraplength=760, justify="left")
    message_label.pack(anchor="w", padx=10, pady=pady)
    return InlineValidationMessage(message_var=message_var, message_label=message_label)


def create_inline_validation_message_grid(
    parent: tk.Misc,
    *,
    row: int,
    column: int = 0,
    columnspan: int = 1,
    padx: int | tuple[int, int] = 8,
    pady: tuple[int, int] = (0, 8),
    sticky: str = "w",
) -> InlineValidationMessage:
    message_var = tk.StringVar(value="")
    message_label = ttk.Label(parent, textvariable=message_var, foreground="#b91c1c", wraplength=760, justify="left")
    message_label.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, padx=padx, pady=pady)
    return InlineValidationMessage(message_var=message_var, message_label=message_label)


def show_inline_field_error(
    inline_validation: InlineValidationMessage,
    *,
    field_label: str,
    cause: str,
    corrective_action: str,
    focus_widget: tk.Widget | None = None,
    severity: str = VALIDATION_SEVERITY_ERROR,
) -> None:
    issue = f"{field_label}: {sanitize_user_error(cause)}" if field_label else sanitize_user_error(cause)
    inline_validation.show(issue=issue, next_step=corrective_action, focus_widget=focus_widget, severity=severity)


def associate_label_with_control(label: ttk.Label | tk.Label, control: tk.Widget) -> None:
    """Provide keyboard and pointer affordances between label and input control."""
    control.configure(takefocus=True)
    label.configure(cursor="hand2")
    label.bind("<Button-1>", lambda _event: control.focus_set())


def append_error_log(log_path: Path, title: str, technical_details: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] {title}\n{technical_details.rstrip()}\n")


def show_actionable_error(
    parent: tk.Misc,
    *,
    title: str,
    issue: str,
    next_step: str,
    technical_details: str | None = None,
) -> None:
    """Show user-facing guidance and optionally allow copying technical details."""
    safe_issue = sanitize_user_error(issue)
    if not technical_details:
        messagebox.showerror(title, format_guidance(safe_issue, next_step), parent=parent)
        return

    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent.winfo_toplevel())
    win.grab_set()
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)

    ttk.Label(
        frame,
        text=format_guidance(safe_issue, next_step),
        wraplength=520,
        justify="left",
    ).pack(anchor="w", pady=(0, 10))

    button_row = ttk.Frame(frame)
    button_row.pack(fill="x")

    def _copy_technical_details() -> None:
        win.clipboard_clear()
        win.clipboard_append(technical_details)
        messagebox.showinfo(
            "Technical details copied",
            format_guidance(
                "Technical details were copied to your clipboard.",
                "Paste them into a support message if troubleshooting is needed.",
            ),
            parent=win,
        )

    ttk.Button(button_row, text="Copy technical details", command=_copy_technical_details).pack(side="left")
    ttk.Button(button_row, text="OK", command=win.destroy).pack(side="right")

    win.wait_window()


class QuestionSettingsService:
    def __init__(self, rubric_path: Path, rubric_data: dict[str, Any]):
        self.rubric_path = Path(rubric_path)
        self._defaults = deepcopy(rubric_data)
        self._undo_stack: list[dict[str, Any]] = []

    @staticmethod
    def _trait_index(rubric: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(t.get("id")): t for t in rubric.get("traits", [])}

    def checkpoint(self, rubric: dict[str, Any]) -> None:
        self._undo_stack.append(deepcopy(rubric))

    def undo(self) -> dict[str, Any] | None:
        if not self._undo_stack:
            return None
        return self._undo_stack.pop()

    def restore_defaults(self) -> dict[str, Any]:
        return deepcopy(self._defaults)

    def save_rubric(self, rubric: dict[str, Any]) -> None:
        atomic_write_json(self.rubric_path, rubric, indent=2, ensure_ascii=False)

    def export_questions(self, rubric: dict[str, Any], path: Path) -> None:
        payload = {
            "tracks": rubric.get("tracks", {}),
            "traits": rubric.get("traits", []),
        }
        atomic_write_json(path, payload, indent=2, ensure_ascii=False)

    def import_questions(self, rubric: dict[str, Any], path: Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
        traits = payload.get("traits")
        if not isinstance(traits, list) or not traits:
            raise ValueError("Imported file must include a non-empty 'traits' list.")

        merged = deepcopy(rubric)
        tracks = payload.get("tracks")
        if isinstance(tracks, dict) and tracks:
            merged["tracks"] = tracks
        merged["traits"] = [self._validated_trait(merged, trait) for trait in traits]
        return merged

    def update_trait(self, rubric: dict[str, Any], trait_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        trait_id = ensure_valid_trait_id(trait_id)
        updated = deepcopy(rubric)
        by_id = self._trait_index(updated)
        trait = by_id.get(trait_id)
        if trait is None:
            raise ValueError(f"Trait not found: {trait_id}")
        trait.update(updates)
        self._validated_trait(updated, trait)
        return updated

    def add_trait(self, rubric: dict[str, Any], trait: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(rubric)
        traits = list(updated.get("traits", []))
        trait_id = ensure_valid_trait_id(str(trait.get("id") or "").strip())
        if trait_id in {str(t.get("id")) for t in traits}:
            raise ValueError(f"Trait id already exists: {trait_id}")
        trait = {**trait, "id": trait_id}
        trait = self._validated_trait(updated, trait)
        traits.append(trait)
        updated["traits"] = traits
        return updated

    def delete_trait(self, rubric: dict[str, Any], trait_id: str) -> dict[str, Any]:
        trait_id = ensure_valid_trait_id(trait_id)
        updated = deepcopy(rubric)
        traits = [t for t in updated.get("traits", []) if str(t.get("id")) != trait_id]
        if len(traits) == len(updated.get("traits", [])):
            raise ValueError(f"Trait not found: {trait_id}")
        updated["traits"] = traits
        return updated

    @classmethod
    def _validated_trait(cls, rubric: dict[str, Any], trait: dict[str, Any]) -> dict[str, Any]:
        source = dict(trait or {})
        source["id"] = ensure_valid_trait_id(str(source.get("id") or "").strip())
        source["name"] = cls._require_text(source.get("name"), "Trait name is required.")
        source["primary_question"] = cls._require_text(source.get("primary_question"), "Primary question is required.")
        source["priority"] = str(source.get("priority") or "non-critical").strip() or "non-critical"
        source["weight"] = cls._validated_weight(source.get("weight"))
        source["descriptors"] = cls._validated_anchor_map(source.get("descriptors"), "descriptors")
        source["sample_answers"] = cls._validated_anchor_map(source.get("sample_answers"), "sample_answers")
        source["applicable_tracks"] = cls._validated_applicable_tracks(source.get("applicable_tracks"), rubric)
        return source

    @staticmethod
    def _require_text(value: Any, message: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(message)
        return text

    @staticmethod
    def _validated_weight(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("Weight must be between 0 and 5.")
        try:
            weight = float(str(value or "0").strip())
        except ValueError as exc:
            raise ValueError("Weight must be between 0 and 5.") from exc
        if weight < 0 or weight > 5:
            raise ValueError("Weight must be between 0 and 5.")
        return weight

    @staticmethod
    def _validated_anchor_map(value: Any, field_name: str) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be a JSON object with keys 1 through 5.")
        normalized = {str(key): str(item or "").strip() for key, item in value.items()}
        missing = [key for key in ("1", "2", "3", "4", "5") if key not in normalized]
        if missing:
            raise ValueError(f"{field_name} must include keys 1 through 5.")
        return {key: normalized[key] for key in ("1", "2", "3", "4", "5")}

    @staticmethod
    def _validated_applicable_tracks(value: Any, rubric: dict[str, Any]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("Applicable tracks must be a list.")
        tracks = [str(item).strip() for item in value if str(item).strip()]
        if not tracks:
            raise ValueError("At least one applicable track is required.")
        if "all" in tracks:
            return ["all"]
        known_tracks = set((rubric.get("tracks") or {}).keys())
        unknown_tracks = [track for track in tracks if track not in known_tracks]
        if unknown_tracks:
            raise ValueError(f"Unknown applicable track: {unknown_tracks[0]}")
        return tracks


class QuestionRuntimeDefinitionService:
    def __init__(self, traits_dir: Path):
        self.traits_dir = Path(traits_dir)

    def load_definition(self, trait_id: str) -> RuntimeSignalDefinition:
        definition = self._load_definition_or_empty(trait_id)
        return normalize_runtime_definition(definition, trait_id=trait_id)

    def save_definition(
        self,
        trait_id: str,
        trait_name: str,
        definition: dict[str, Any],
    ) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition, trait_id=trait_id, trait_name=trait_name)
        target_path = self._target_path(trait_id, trait_name)
        existing_path = self._find_trait_file(trait_id)
        atomic_write_json(target_path, normalized, indent=2, ensure_ascii=False)
        if existing_path and existing_path != target_path and existing_path.exists():
            existing_path.unlink()
        return normalized

    def create_definition(self, trait_id: str, trait_name: str, question: str) -> RuntimeSignalDefinition:
        definition = default_runtime_definition(trait_id, question=question)
        return self.save_definition(trait_id, trait_name, definition)

    def delete_definition(self, trait_id: str) -> None:
        existing_path = self._find_trait_file(trait_id)
        if existing_path and existing_path.exists():
            existing_path.unlink()

    def sync_with_trait(self, trait_id: str, trait_name: str, question: str) -> RuntimeSignalDefinition:
        existing = self._load_definition_or_empty(trait_id)
        definition = normalize_runtime_definition(existing, trait_id=trait_id, trait_name=trait_name)
        definition["question"] = question.strip()
        return self.save_definition(trait_id, trait_name, definition)

    def add_core_signal(self, definition: dict[str, Any], signal: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["core_signals"].append(_normalize_signal(signal, default_group="Core"))
        return _finalize_definition(normalized)

    def update_core_signal(self, definition: dict[str, Any], signal_ref: str, updates: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        updated_signals = _replace_signal(normalized["core_signals"], signal_ref, updates, default_group="Core")
        normalized["core_signals"] = updated_signals
        return _finalize_definition(normalized)

    def delete_core_signal(self, definition: dict[str, Any], signal_ref: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["core_signals"] = _delete_signal(normalized["core_signals"], signal_ref)
        return _finalize_definition(normalized)

    def add_extended_group(self, definition: dict[str, Any], group: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"].append(_normalize_group(group))
        return _finalize_definition(normalized)

    def update_extended_group(self, definition: dict[str, Any], group_id: str, updates: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _replace_group(normalized["extended_signal_groups"], group_id, updates)
        return _finalize_definition(normalized)

    def delete_extended_group(self, definition: dict[str, Any], group_id: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _delete_group(normalized["extended_signal_groups"], group_id)
        return _finalize_definition(normalized)

    def add_group_signal(self, definition: dict[str, Any], group_id: str, signal: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_added(normalized["extended_signal_groups"], group_id, signal)
        return _finalize_definition(normalized)

    def update_group_signal(
        self,
        definition: dict[str, Any],
        group_id: str,
        signal_ref: str,
        updates: dict[str, Any],
    ) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_updated(
            normalized["extended_signal_groups"],
            group_id,
            signal_ref,
            updates,
        )
        return _finalize_definition(normalized)

    def delete_group_signal(self, definition: dict[str, Any], group_id: str, signal_ref: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_deleted(normalized["extended_signal_groups"], group_id, signal_ref)
        return _finalize_definition(normalized)

    def _load_definition_or_empty(self, trait_id: str) -> RuntimeSignalDefinition:
        existing_path = self._find_trait_file(trait_id)
        if not existing_path:
            return self._load_weighted_definition_or_empty(trait_id)
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        return self._load_weighted_definition_or_empty(trait_id)

    def _load_weighted_definition_or_empty(self, trait_id: str) -> RuntimeSignalDefinition:
        try:
            signal_definition = load_trait_signal_ui_definition(trait_id)
        except Exception:
            return default_runtime_definition(trait_id)
        if not signal_definition.get("valid_signal_ids"):
            return default_runtime_definition(trait_id)
        return {
            "trait_id": build_runtime_trait_id(trait_id, trait_name=""),
            "question": "",
            "core_signals": [
                {
                    "ref": signal.get("signal_id", ""),
                    "label": signal.get("label", ""),
                    "weight": signal.get("weight", 0),
                    "group": "Core",
                    "is_critical": signal.get("is_critical", False),
                }
                for signal in signal_definition.get("core_signals", []) or []
            ],
            "extended_signal_groups": [
                {
                    "group_id": group.get("group_id", ""),
                    "group_label": group.get("group_label", ""),
                    "signals": [
                        {
                            "ref": signal.get("signal_id", ""),
                            "label": signal.get("label", ""),
                            "weight": signal.get("weight", 0),
                            "group": group.get("group_label", ""),
                            "is_critical": signal.get("is_critical", False),
                        }
                        for signal in group.get("signals", []) or []
                    ],
                }
                for group in signal_definition.get("extended_groups", []) or []
            ],
        }

    def _find_trait_file(self, trait_id: str) -> Path | None:
        runtime_trait_id = runtime_trait_id_for_rubric_trait(trait_id)
        for candidate in sorted(self.traits_dir.glob(TRAIT_FILE_PATTERN)):
            if candidate.name == "trait_based_scoring_contract.yaml":
                continue
            payload = _read_json(candidate)
            if str(payload.get("trait_id", "") or "").strip() == runtime_trait_id:
                return candidate
        prefix = runtime_trait_id_prefix(trait_id)
        if not prefix:
            return None
        matches = sorted(self.traits_dir.glob(f"{prefix}*.json"))
        return matches[0] if matches else None

    def _target_path(self, trait_id: str, trait_name: str) -> Path:
        runtime_trait_id = build_runtime_trait_id(trait_id, trait_name=trait_name)
        return self.traits_dir / f"{runtime_trait_id}.json"


def default_runtime_definition(trait_id: str, *, question: str = "") -> RuntimeSignalDefinition:
    return {
        "trait_id": build_runtime_trait_id(trait_id, trait_name=""),
        "question": str(question or "").strip(),
        "core_signals": [],
        "extended_signal_groups": [],
    }


def normalize_runtime_definition(
    definition: dict[str, Any] | None,
    *,
    trait_id: str | None = None,
    trait_name: str = "",
) -> RuntimeSignalDefinition:
    source = definition if isinstance(definition, dict) else {}
    resolved_trait_id = build_runtime_trait_id(
        trait_id or source.get("trait_id", ""),
        trait_name=trait_name,
        existing_runtime_trait_id=source.get("trait_id", ""),
    )
    normalized = {
        "trait_id": resolved_trait_id,
        "question": str(source.get("question", "") or "").strip(),
        "core_signals": [_normalize_signal(signal, default_group="Core") for signal in _as_dict_list(source.get("core_signals"))],
        "extended_signal_groups": [_normalize_group(group) for group in _as_dict_list(source.get("extended_signal_groups"))],
    }
    return _finalize_definition(normalized)


def normalize_runtime_signal(signal: dict[str, Any], *, default_group: str) -> RuntimeSignalRecord:
    return _normalize_signal(signal, default_group=default_group)


def normalize_runtime_group(group: dict[str, Any]) -> RuntimeSignalGroup:
    return _normalize_group(group)


def runtime_trait_id_for_rubric_trait(trait_id: str) -> str:
    candidate = str(trait_id or "").strip()
    if not candidate:
        raise ValueError("Trait id is required.")
    match = TRAIT_ID_ALIAS_PATTERN.fullmatch(candidate)
    if match:
        return f"T{int(match.group(1))}"
    prefixed_match = PREFIXED_TRAIT_ID_ALIAS_PATTERN.fullmatch(candidate)
    if prefixed_match:
        prefix = prefixed_match.group(1).upper()
        return f"{prefix}_T{int(prefixed_match.group(2))}"
    if RUNTIME_TRAIT_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Trait id must use the 'trait_<number>' or '<prefix>_trait_<number>' format.")


def build_runtime_trait_id(trait_id: str, *, trait_name: str, existing_runtime_trait_id: Any = "") -> str:
    existing_candidate = str(existing_runtime_trait_id or "").strip()
    prefix = runtime_trait_id_for_rubric_trait(trait_id)
    if existing_candidate.startswith(f"{prefix}_"):
        return existing_candidate
    suffix = slugify_trait_name(trait_name)
    if suffix:
        return f"{prefix}_{suffix}"
    return prefix


def runtime_trait_id_prefix(trait_id: str) -> str:
    return f"{runtime_trait_id_for_rubric_trait(trait_id)}_"


def slugify_trait_name(trait_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(trait_name or "").strip())
    return cleaned.strip("_")


def ensure_valid_trait_id(trait_id: str) -> str:
    normalized = str(trait_id or "").strip()
    if not normalized:
        raise ValueError("Trait id is required.")
    runtime_trait_id_for_rubric_trait(normalized)
    return normalized


def next_trait_id(rubric: dict[str, Any]) -> str:
    numeric_ids: list[int] = []
    for trait in rubric.get("traits", []):
        match = TRAIT_ID_ALIAS_PATTERN.fullmatch(str(trait.get("id", "") or "").strip())
        if match:
            numeric_ids.append(int(match.group(1)))
    return f"trait_{max(numeric_ids, default=0) + 1}"


def list_signal_refs(definition: dict[str, Any]) -> list[str]:
    normalized = normalize_runtime_definition(definition)
    refs = [signal["ref"] for signal in normalized["core_signals"]]
    for group in normalized["extended_signal_groups"]:
        refs.extend(signal["ref"] for signal in group["signals"])
    return refs


def _normalize_signal(signal: dict[str, Any], *, default_group: str) -> RuntimeSignalRecord:
    source = signal if isinstance(signal, dict) else {}
    normalized = {
        "ref": _normalize_signal_ref(source.get("ref")),
        "label": str(source.get("label", "") or "").strip(),
        "weight": _normalize_weight(source.get("weight")),
        "group": str(source.get("group", "") or default_group).strip() or default_group,
        "is_critical": bool(source.get("is_critical", False)),
    }
    if not normalized["label"]:
        normalized["label"] = normalized["ref"]
    return normalized


def _normalize_group(group: dict[str, Any]) -> RuntimeSignalGroup:
    source = group if isinstance(group, dict) else {}
    group_label = str(source.get("group_label", "") or "").strip() or "Extended Group"
    signals = [_normalize_signal(signal, default_group=group_label) for signal in _as_dict_list(source.get("signals"))]
    return {"group_id": _normalize_group_id(source.get("group_id")), "group_label": group_label, "signals": signals}


def _finalize_definition(definition: RuntimeSignalDefinition) -> RuntimeSignalDefinition:
    _validate_runtime_definition(definition)
    return deepcopy(definition)


def _validate_runtime_definition(definition: RuntimeSignalDefinition) -> None:
    seen_refs: set[str] = set()
    for signal in definition["core_signals"]:
        _ensure_unique_ref(seen_refs, signal["ref"])
    for group in definition["extended_signal_groups"]:
        for signal in group["signals"]:
            _ensure_unique_ref(seen_refs, signal["ref"])


def _ensure_unique_ref(seen_refs: set[str], signal_ref: str) -> None:
    if signal_ref not in seen_refs:
        seen_refs.add(signal_ref)
        return
    raise ValueError(f"Duplicate signal ref: {signal_ref}")


def _replace_signal(signals: list[RuntimeSignalRecord], signal_ref: str, updates: dict[str, Any], *, default_group: str) -> list[RuntimeSignalRecord]:
    normalized_ref = _normalize_signal_ref(signal_ref)
    updated_signals: list[RuntimeSignalRecord] = []
    replaced = False
    for signal in signals:
        if signal["ref"] != normalized_ref:
            updated_signals.append(signal)
            continue
        updated_signals.append(_normalize_signal({**signal, **updates}, default_group=default_group))
        replaced = True
    if replaced:
        return updated_signals
    raise ValueError(f"Signal not found: {normalized_ref}")


def _delete_signal(signals: list[RuntimeSignalRecord], signal_ref: str) -> list[RuntimeSignalRecord]:
    normalized_ref = _normalize_signal_ref(signal_ref)
    remaining = [signal for signal in signals if signal["ref"] != normalized_ref]
    if len(remaining) != len(signals):
        return remaining
    raise ValueError(f"Signal not found: {normalized_ref}")


def _replace_group(groups: list[RuntimeSignalGroup], group_id: str, updates: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    replaced = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        updated_groups.append(_normalize_group({**group, **updates}))
        replaced = True
    if replaced:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _delete_group(groups: list[RuntimeSignalGroup], group_id: str) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    remaining = [group for group in groups if group["group_id"] != normalized_group_id]
    if len(remaining) != len(groups):
        return remaining
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_added(groups: list[RuntimeSignalGroup], group_id: str, signal: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    added = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        next_signal = _normalize_signal(signal, default_group=group["group_label"])
        updated_groups.append({**group, "signals": [*group["signals"], next_signal]})
        added = True
    if added:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_updated(groups: list[RuntimeSignalGroup], group_id: str, signal_ref: str, updates: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    replaced = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        signals = _replace_signal(group["signals"], signal_ref, updates, default_group=group["group_label"])
        updated_groups.append({**group, "signals": signals})
        replaced = True
    if replaced:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_deleted(groups: list[RuntimeSignalGroup], group_id: str, signal_ref: str) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    deleted = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        signals = _delete_signal(group["signals"], signal_ref)
        updated_groups.append({**group, "signals": signals})
        deleted = True
    if deleted:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _normalize_signal_ref(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("Signal ref is required.")
    if SIGNAL_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Signal ref must use letters, numbers, and underscores only.")


def _normalize_group_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("Extended group id is required.")
    if GROUP_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Extended group id must use letters, numbers, and underscores only.")


def _normalize_weight(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Signal weight must be a number.")
    try:
        parsed = float(str(value or "0").strip())
    except ValueError as exc:
        raise ValueError("Signal weight must be a number.") from exc
    return parsed


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


class QuestionSettingsWindow(tk.Toplevel):
    def __init__(self, app: "InterviewApp"):
        super().__init__(app)
        self.app = app
        self.title("Question Settings")
        self.geometry("1120x760")

        self.service = QuestionSettingsService(DEFAULT_RUBRIC_PATH, self.app.rubric)
        self.runtime_service = QuestionRuntimeDefinitionService(DEFAULT_BASE_DIR / "Trait-Based Scoring")
        self.track_var = StringVar(value=self.app.state.track or next(iter(self.app.rubric["tracks"].keys())))
        self.status_var = StringVar(value="")

        self.trait_list: tk.Listbox
        self.trait_id_var = StringVar(value="")
        self.trait_name_var = StringVar(value="")
        self.weight_var = StringVar(value="1")
        self.priority_var = StringVar(value="non-critical")
        self.question_text: tk.Text
        self.samples_text: tk.Text
        self.signal_definition: dict[str, Any] = default_runtime_definition("trait_1")
        self.signal_ref_var = StringVar(value="")
        self.signal_label_var = StringVar(value="")
        self.signal_weight_var = StringVar(value="1")
        self.signal_group_var = StringVar(value="Core")
        self.signal_is_critical_var = BooleanVar(value=False)
        self.group_id_var = StringVar(value="")
        self.group_label_var = StringVar(value="")
        self.core_signal_list: tk.Listbox
        self.group_list: tk.Listbox
        self.group_signal_list: tk.Listbox

        self._build()
        self.refresh_trait_list()

    def _build(self) -> None:
        head = ttk.Frame(self, padding=10)
        head.pack(fill="x")
        ttk.Label(head, text="Track:").pack(side="left")
        ttk.Combobox(
            head,
            textvariable=self.track_var,
            values=list(self.app.rubric["tracks"].keys()),
            state="readonly",
            width=24,
        ).pack(side="left", padx=8)
        ttk.Button(head, text="Refresh", command=self.refresh_trait_list).pack(side="left")
        ttk.Button(head, text="Undo Last", command=self.undo_last).pack(side="right")
        ttk.Button(head, text="Restore Defaults", command=self.restore_defaults).pack(side="right", padx=6)
        ttk.Button(head, text="Import JSON", command=self.import_json).pack(side="right", padx=6)
        ttk.Button(head, text="Export JSON", command=self.export_json).pack(side="right", padx=6)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.trait_list = tk.Listbox(left)
        self.trait_list.pack(fill="both", expand=True)
        self.trait_list.bind("<<ListboxSelect>>", lambda _e: self.load_selected_trait())

        move_row = ttk.Frame(left)
        move_row.pack(fill="x", pady=8)
        ttk.Button(move_row, text="Move Up", command=lambda: self.move_selected(-1)).pack(side="left")
        ttk.Button(move_row, text="Move Down", command=lambda: self.move_selected(1)).pack(side="left", padx=6)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self._entry(right, "Trait ID", self.trait_id_var)
        self._entry(right, "Name", self.trait_name_var)
        self._entry(right, "Weight (0-5)", self.weight_var)
        self._entry(right, "Priority", self.priority_var)

        ttk.Label(right, text="Primary Question").pack(anchor="w")
        self.question_text = tk.Text(right, height=5, wrap="word")
        self.question_text.pack(fill="x", pady=(0, 8))

        ttk.Label(right, text="Suggested Responses (JSON map keys 1..5)").pack(anchor="w")
        self.samples_text = tk.Text(right, height=12, wrap="word")
        self.samples_text.pack(fill="both", expand=True)

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Save Trait", command=self.save_trait).pack(side="right")
        ttk.Button(actions, text="Add Rated Question", command=self.add_trait).pack(side="right", padx=6)
        ttk.Button(actions, text="Delete Trait", command=self.delete_trait).pack(side="right")

        self._build_signal_panel(right)

        foot = ttk.Frame(self, padding=10)
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.status_var).pack(side="left")

    @staticmethod
    def _entry(parent: ttk.Frame, label: str, var: StringVar) -> None:
        ttk.Label(parent, text=label).pack(anchor="w")
        ttk.Entry(parent, textvariable=var).pack(fill="x", pady=(0, 8))

    def _build_signal_panel(self, parent: ttk.Frame) -> None:
        signal_box = ttk.LabelFrame(parent, text="Traits sought in answers")
        signal_box.pack(fill="both", expand=True, pady=(8, 0))

        lists = ttk.Frame(signal_box)
        lists.pack(fill="both", expand=True, padx=8, pady=8)

        core_frame = ttk.Frame(lists)
        core_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(core_frame, text="Core").pack(anchor="w")
        self.core_signal_list = tk.Listbox(core_frame, height=5)
        self.core_signal_list.pack(fill="both", expand=True)
        self.core_signal_list.bind("<<ListboxSelect>>", lambda _e: self._load_selected_core_signal())

        group_frame = ttk.Frame(lists)
        group_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(group_frame, text="Groups").pack(anchor="w")
        self.group_list = tk.Listbox(group_frame, height=5)
        self.group_list.pack(fill="both", expand=True)
        self.group_list.bind("<<ListboxSelect>>", lambda _e: self._load_selected_group())

        group_signal_frame = ttk.Frame(lists)
        group_signal_frame.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(group_signal_frame, text="Group signals").pack(anchor="w")
        self.group_signal_list = tk.Listbox(group_signal_frame, height=5)
        self.group_signal_list.pack(fill="both", expand=True)
        self.group_signal_list.bind("<<ListboxSelect>>", lambda _e: self._load_selected_group_signal())

        form = ttk.Frame(signal_box)
        form.pack(fill="x", padx=8, pady=(0, 8))
        self._entry(form, "Signal ref", self.signal_ref_var)
        self._entry(form, "Signal label", self.signal_label_var)
        self._entry(form, "Signal weight", self.signal_weight_var)
        self._entry(form, "Signal group", self.signal_group_var)
        ttk.Checkbutton(form, text="Critical", variable=self.signal_is_critical_var).pack(anchor="w", pady=(0, 8))
        self._entry(form, "Group id", self.group_id_var)
        self._entry(form, "Group label", self.group_label_var)

        buttons = ttk.Frame(signal_box)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Save Core Signal", command=self.save_core_signal).pack(side="left")
        ttk.Button(buttons, text="Delete Core Signal", command=self.delete_core_signal).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save Group", command=self.save_group).pack(side="left", padx=(10, 4))
        ttk.Button(buttons, text="Delete Group", command=self.delete_group).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save Group Signal", command=self.save_group_signal).pack(side="left", padx=(10, 4))
        ttk.Button(buttons, text="Delete Group Signal", command=self.delete_group_signal).pack(side="left", padx=4)

    def _track_traits(self) -> list[dict[str, Any]]:
        track = self.track_var.get().strip()
        return self.app.rubric_loader.get_traits_for_track(track)

    def refresh_trait_list(self) -> None:
        self.trait_list.delete(0, END)
        for trait in self._track_traits():
            self.trait_list.insert(END, f"{trait['id']} | {trait['name']} | weight={trait.get('weight', 0)}")

    def _selected_trait_id(self) -> str:
        selected = self.trait_list.curselection()
        if not selected:
            return ""
        line = self.trait_list.get(selected[0])
        return line.split("|", 1)[0].strip()

    def load_selected_trait(self) -> None:
        trait_id = self._selected_trait_id()
        if not trait_id:
            return
        trait = next((t for t in self.app.rubric.get("traits", []) if str(t.get("id")) == trait_id), None)
        if trait is None:
            return
        self.trait_id_var.set(str(trait.get("id", "")))
        self.trait_name_var.set(str(trait.get("name", "")))
        self.weight_var.set(str(trait.get("weight", 1)))
        self.priority_var.set(str(trait.get("priority", "non-critical")))
        self.question_text.delete("1.0", END)
        self.question_text.insert(END, str(trait.get("primary_question", "")))
        self.samples_text.delete("1.0", END)
        self.samples_text.insert(END, self._samples_to_json(trait.get("sample_answers", {})))
        try:
            self.signal_definition = self.runtime_service.load_definition(trait_id)
        except ValueError:
            self.signal_definition = default_runtime_definition(trait_id)
        self.refresh_signal_lists()

    @staticmethod
    def _samples_to_json(samples: dict[str, Any]) -> str:
        normalized = {str(k): str(v) for k, v in dict(samples or {}).items()}
        return json.dumps(normalized, indent=2, ensure_ascii=False)

    def _read_samples(self) -> dict[str, str]:
        raw = self.samples_text.get("1.0", END).strip() or "{}"
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise ValueError("Suggested responses must be a JSON object.")
        normalized = {str(k): str(v) for k, v in loaded.items()}
        for key in ("1", "2", "3", "4", "5"):
            normalized.setdefault(key, "")
        return normalized

    def _current_trait_descriptors(self, trait_id: str) -> dict[str, str]:
        trait = next((t for t in self.app.rubric.get("traits", []) if str(t.get("id")) == trait_id), None)
        descriptors = (trait or {}).get("descriptors", {})
        if not isinstance(descriptors, dict):
            descriptors = {}
        return {key: str(descriptors.get(key, "") or "") for key in ("1", "2", "3", "4", "5")}

    def _current_trait_tracks(self, trait_id: str) -> list[str]:
        trait = next((t for t in self.app.rubric.get("traits", []) if str(t.get("id")) == trait_id), None)
        tracks = (trait or {}).get("applicable_tracks", [])
        if isinstance(tracks, list) and tracks:
            return [str(track).strip() for track in tracks if str(track).strip()]
        return [self.track_var.get().strip()]

    def _build_trait_updates(self) -> tuple[str, dict[str, Any]]:
        trait_id = ensure_valid_trait_id(self.trait_id_var.get().strip())
        trait_name = self.trait_name_var.get().strip()
        primary_question = self.question_text.get("1.0", END).strip()
        if not trait_name:
            raise ValueError("Trait name is required.")
        if not primary_question:
            raise ValueError("Primary question is required.")
        weight = float(self.weight_var.get().strip() or "0")
        if weight < 0 or weight > 5:
            raise ValueError("Weight must be between 0 and 5.")
        updates = {
            "name": trait_name,
            "weight": weight,
            "priority": self.priority_var.get().strip() or "non-critical",
            "primary_question": primary_question,
            "descriptors": self._current_trait_descriptors(trait_id),
            "sample_answers": self._read_samples(),
            "applicable_tracks": self._current_trait_tracks(trait_id),
        }
        return trait_id, updates

    def _build_signal_payload(self, group_id: str) -> dict[str, Any]:
        default_group = "Core" if str(group_id or "").strip().lower() == "core" else str(group_id or "").strip()
        group = self.signal_group_var.get().strip() or default_group
        return {
            "ref": self.signal_ref_var.get().strip(),
            "label": self.signal_label_var.get().strip(),
            "weight": float(self.signal_weight_var.get().strip() or "0"),
            "group": group,
            "is_critical": bool(self.signal_is_critical_var.get()),
        }

    def _mutate_signal_definition(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        group_key = str(group_id or "").strip()
        definition = deepcopy(getattr(self, "signal_definition", {}) or {})
        if group_key.lower() == "core":
            return self.runtime_service.add_core_signal(definition, payload)
        return self.runtime_service.add_group_signal(definition, group_key, payload)

    def _trait_name_for_runtime(self) -> str:
        return self.trait_name_var.get().strip() or self.trait_id_var.get().strip()

    def _persist_signal_definition(self, definition: dict[str, Any]) -> None:
        self.signal_definition = self.runtime_service.save_definition(
            self.trait_id_var.get().strip(),
            self._trait_name_for_runtime(),
            definition,
        )
        self.status_var.set("Signal settings saved.")
        self.refresh_signal_lists()

    def _apply_new_rubric(self, rubric: dict[str, Any]) -> None:
        self.app.rubric = deepcopy(rubric)
        self.app.rubric_loader.data = deepcopy(rubric)
        self.service.save_rubric(rubric)
        self.refresh_trait_list()
        self.status_var.set("Question settings saved.")

    def save_trait(self) -> None:
        try:
            trait_id, updates = self._build_trait_updates()
            self.runtime_service.sync_with_trait(trait_id, updates["name"], updates["primary_question"])
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        except OSError as exc:
            messagebox.showerror("Question Settings", f"Could not save trait definition: {exc}")
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.update_trait(self.app.rubric, trait_id, updates)
        self._apply_new_rubric(rubric)

    def add_trait(self) -> None:
        track = self.track_var.get().strip()
        trait_id = self.trait_id_var.get().strip() or next_trait_id(self.app.rubric)
        self.trait_id_var.set(trait_id)
        try:
            trait_id = ensure_valid_trait_id(trait_id)
            trait_name = self.trait_name_var.get().strip() or "New Rated Question"
            primary_question = self.question_text.get("1.0", END).strip() or "New rated question"
            weight = float(self.weight_var.get().strip() or "1")
            if weight < 0 or weight > 5:
                raise ValueError("Weight must be between 0 and 5.")
            trait = {
                "id": trait_id,
                "name": trait_name,
                "priority": self.priority_var.get().strip() or "non-critical",
                "weight": weight,
                "applicable_tracks": [track],
                "primary_question": primary_question,
                "descriptors": {"1": "", "2": "", "3": "", "4": "", "5": ""},
                "sample_answers": self._read_samples(),
                "score_1_auto_no_hire": False,
            }
            self.runtime_service.create_definition(trait_id, trait_name, primary_question)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.add_trait(self.app.rubric, trait)
        self._apply_new_rubric(rubric)

    def delete_trait(self) -> None:
        trait_id = self.trait_id_var.get().strip() or self._selected_trait_id()
        if not trait_id:
            return
        if not messagebox.askyesno("Question Settings", f"Delete rated question '{trait_id}'?"):
            return
        try:
            self.runtime_service.delete_definition(trait_id)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.delete_trait(self.app.rubric, trait_id)
        self._apply_new_rubric(rubric)

    def move_selected(self, direction: int) -> None:
        selected = self.trait_list.curselection()
        if not selected:
            return
        idx = selected[0]
        target = idx + direction
        traits = self._track_traits()
        if target < 0 or target >= len(traits):
            return
        traits[idx], traits[target] = traits[target], traits[idx]
        self.app.qstore.set_trait_order(self.track_var.get().strip(), [t["id"] for t in traits])
        self.refresh_trait_list()
        self.trait_list.selection_set(target)

    def undo_last(self) -> None:
        previous = self.service.undo()
        if previous is None:
            self.status_var.set("Nothing to undo.")
            return
        self._apply_new_rubric(previous)

    def restore_defaults(self) -> None:
        if not messagebox.askyesno("Question Settings", "Restore default question settings?"):
            return
        self.service.checkpoint(self.app.rubric)
        self._apply_new_rubric(self.service.restore_defaults())

    def export_json(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export question settings",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self.service.export_questions(self.app.rubric, path)
        self.status_var.set("Exported question settings.")

    def import_json(self) -> None:
        path = filedialog.askopenfilename(
            title="Import question settings",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.import_questions(self.app.rubric, path)
        self._apply_new_rubric(rubric)

    @staticmethod
    def _signal_line(signal: dict[str, Any]) -> str:
        return f"{signal.get('ref', '')} | {signal.get('label', '')} | weight={signal.get('weight', 0)}"

    @staticmethod
    def _group_line(group: dict[str, Any]) -> str:
        return f"{group.get('group_id', '')} | {group.get('group_label', '')}"

    @staticmethod
    def _line_id(line: str) -> str:
        return str(line or "").split("|", 1)[0].strip()

    def refresh_signal_lists(self) -> None:
        if not hasattr(self, "core_signal_list"):
            return
        self.core_signal_list.delete(0, END)
        self.group_list.delete(0, END)
        self.group_signal_list.delete(0, END)
        definition = normalize_runtime_definition(getattr(self, "signal_definition", {}) or {})
        self.signal_definition = definition
        for signal in definition["core_signals"]:
            self.core_signal_list.insert(END, self._signal_line(signal))
        for group in definition["extended_signal_groups"]:
            self.group_list.insert(END, self._group_line(group))
        selected_group = self.group_id_var.get().strip()
        if not selected_group and definition["extended_signal_groups"]:
            selected_group = definition["extended_signal_groups"][0]["group_id"]
            self.group_id_var.set(selected_group)
            self.group_label_var.set(definition["extended_signal_groups"][0]["group_label"])
        for group in definition["extended_signal_groups"]:
            if group["group_id"] != selected_group:
                continue
            for signal in group["signals"]:
                self.group_signal_list.insert(END, self._signal_line(signal))

    def _load_selected_core_signal(self) -> None:
        selected = self.core_signal_list.curselection()
        if not selected:
            return
        signal_ref = self._line_id(self.core_signal_list.get(selected[0]))
        signal = next((item for item in self.signal_definition.get("core_signals", []) if item.get("ref") == signal_ref), None)
        if signal:
            self._load_signal_fields(signal)

    def _load_selected_group(self) -> None:
        selected = self.group_list.curselection()
        if not selected:
            return
        group_id = self._line_id(self.group_list.get(selected[0]))
        group = next((item for item in self.signal_definition.get("extended_signal_groups", []) if item.get("group_id") == group_id), None)
        if not group:
            return
        self.group_id_var.set(str(group.get("group_id", "")))
        self.group_label_var.set(str(group.get("group_label", "")))
        self.signal_group_var.set(str(group.get("group_label", "")))
        self.refresh_signal_lists()

    def _load_selected_group_signal(self) -> None:
        selected = self.group_signal_list.curselection()
        if not selected:
            return
        signal_ref = self._line_id(self.group_signal_list.get(selected[0]))
        group_id = self.group_id_var.get().strip()
        group = next((item for item in self.signal_definition.get("extended_signal_groups", []) if item.get("group_id") == group_id), None)
        signal = next((item for item in (group or {}).get("signals", []) if item.get("ref") == signal_ref), None)
        if signal:
            self._load_signal_fields(signal)

    def _load_signal_fields(self, signal: dict[str, Any]) -> None:
        self.signal_ref_var.set(str(signal.get("ref", "")))
        self.signal_label_var.set(str(signal.get("label", "")))
        self.signal_weight_var.set(str(signal.get("weight", 0)))
        self.signal_group_var.set(str(signal.get("group", "")))
        self.signal_is_critical_var.set(bool(signal.get("is_critical", False)))

    def save_core_signal(self) -> None:
        try:
            definition = self._mutate_signal_definition("core", self._build_signal_payload("core"))
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)

    def save_group_signal(self) -> None:
        group_id = self.group_id_var.get().strip()
        try:
            definition = self._mutate_signal_definition(group_id, self._build_signal_payload(group_id))
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)

    def delete_core_signal(self) -> None:
        signal_ref = self.signal_ref_var.get().strip()
        try:
            definition = self.runtime_service.delete_core_signal(self.signal_definition, signal_ref)
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)

    def delete_group_signal(self) -> None:
        group_id = self.group_id_var.get().strip()
        signal_ref = self.signal_ref_var.get().strip()
        try:
            definition = self.runtime_service.delete_group_signal(self.signal_definition, group_id, signal_ref)
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)

    def add_group(self) -> None:
        self.save_group()

    def save_group(self) -> None:
        group = {
            "group_id": self.group_id_var.get().strip(),
            "group_label": self.group_label_var.get().strip(),
            "signals": [],
        }
        try:
            definition = self.runtime_service.add_extended_group(self.signal_definition, group)
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)

    def delete_group(self) -> None:
        group_id = self.group_id_var.get().strip()
        try:
            definition = self.runtime_service.delete_extended_group(self.signal_definition, group_id)
        except ValueError as exc:
            messagebox.showerror("Question Settings", str(exc))
            return
        self._persist_signal_definition(definition)


class RouterState(Protocol):
    track: str | None
    current_index: int


class RouterNavigator(Protocol):
    def show_start_screen(self) -> None: ...

    def show_candidate_info(self) -> None: ...

    def show_trait_screen_by_trait_id(self, flow_idx: int, trait: dict[str, str]) -> None: ...

    def show_custom_question_item_screen(self, flow_idx: int, custom_question: dict[str, str]) -> None: ...

    def open_settings(self) -> None: ...

    def open_question_editor(self) -> None: ...

    def show_keyboard_shortcuts_help(self) -> None: ...


class RouterRenderer(Protocol):
    def bind_all(self, key_combo: str, callback: Callable[[Any], str | None]) -> None: ...

    def footer_action(self, label: str) -> Callable[[], None] | None: ...


class RouterFlowState(Protocol):
    def state(self) -> RouterState: ...

    def flow_len(self) -> int: ...

    def flow_item(self, index: int) -> dict[str, str] | None: ...

    def mark_flow_timestamp(self, flow_index: int) -> None: ...

    def start_question_recording_for_flow(self, flow_index: int) -> None: ...

    def trait_by_id(self, trait_id: str) -> dict[str, str] | None: ...

    def custom_by_id(self, custom_id: str) -> dict[str, str] | None: ...

    def build_active_flow(self, track: str) -> None: ...


class AppRouterPorts(RouterNavigator, RouterRenderer, RouterFlowState):
    def __init__(self, app: Any) -> None:
        self._app = app

    def state(self) -> RouterState:
        return self._app.state

    def bind_all(self, key_combo: str, callback: Any) -> None:
        self._app.bind_all(key_combo, callback)

    def footer_action(self, label: str) -> Any:
        return self._app._footer_actions_by_label.get(label)

    def open_settings(self) -> None:
        self._app.open_settings()

    def open_question_editor(self) -> None:
        self._app.open_question_editor()

    def show_keyboard_shortcuts_help(self) -> None:
        self._app.show_keyboard_shortcuts_help()

    def show_start_screen(self) -> None:
        self._app.show_start_screen()

    def show_candidate_info(self) -> None:
        self._app.show_candidate_info()

    def show_trait_screen_by_trait_id(self, flow_idx: int, trait: dict[str, str]) -> None:
        self._app.show_trait_screen_by_trait_id(flow_idx, trait)

    def show_custom_question_item_screen(self, flow_idx: int, custom_question: dict[str, str]) -> None:
        self._app.show_custom_question_item_screen(flow_idx, custom_question)

    def flow_len(self) -> int:
        return self._app._flow_len()

    def flow_item(self, index: int) -> dict[str, str] | None:
        return self._app._get_flow_item(index)

    def mark_flow_timestamp(self, flow_index: int) -> None:
        self._app._mark_flow_timestamp(flow_index)

    def start_question_recording_for_flow(self, flow_index: int) -> None:
        self._app._start_question_recording_for_flow(flow_index)

    def trait_by_id(self, trait_id: str) -> dict[str, str] | None:
        return self._app._trait_by_id(trait_id)

    def custom_by_id(self, custom_id: str) -> dict[str, str] | None:
        return self._app._custom_by_id(custom_id)

    def build_active_flow(self, track: str) -> None:
        self._app._build_active_flow(track)


class UiRouter:
    ROUTE_START = "start"
    ROUTE_CANDIDATE_INFO = "candidate_info"
    ROUTE_QUESTION_TRAIT = "question_trait"
    ROUTE_QUESTION_CUSTOM = "question_custom"
    ROUTE_HISTORY = "history"
    ROUTE_FALLBACK = ROUTE_CANDIDATE_INFO

    def __init__(self, navigator: RouterNavigator, renderer: RouterRenderer, flow_state: RouterFlowState) -> None:
        self.navigator = navigator
        self.renderer = renderer
        self.flow_state = flow_state

    def setup_shortcuts(self) -> None:
        bindings = self._shortcut_bindings()
        for key_combo, callback in bindings.items():
            self.renderer.bind_all(key_combo, callback)

    def _shortcut_bindings(self) -> dict[str, Any]:
        return {
            "<Control-n>": lambda _e: self._invoke_footer_action("Next"),
            "<Control-Right>": lambda _e: self._invoke_footer_action("Next"),
            "<Control-b>": lambda _e: self._invoke_footer_action("Back"),
            "<Control-Left>": lambda _e: self._invoke_footer_action("Back"),
            "<Control-s>": lambda _e: self._invoke_footer_action("Save Draft"),
            "<Control-Shift-F>": lambda _e: self._run_finalize_shortcut(),
            "<Control-,>": lambda _e: self.navigator.open_settings(),
            "<Control-e>": lambda _e: self.navigator.open_question_editor(),
            "<F1>": lambda _e: self.navigator.show_keyboard_shortcuts_help(),
        }

    def _invoke_footer_action(self, label: str) -> str | None:
        command = self.renderer.footer_action(label)
        if callable(command):
            command()
        return "break"

    def _run_finalize_shortcut(self) -> str | None:
        for label in ("Finalize", "Continue"):
            command = self.renderer.footer_action(label)
            if callable(command):
                command()
                break
        return "break"

    def flow_item_route(self, item: dict[str, Any] | None) -> str:
        if not item:
            return self.ROUTE_FALLBACK
        item_type = item.get("type")
        if item_type == "trait":
            return self.ROUTE_QUESTION_TRAIT
        if item_type == "custom":
            return self.ROUTE_QUESTION_CUSTOM
        return self.ROUTE_FALLBACK

    def route_to(self, route: str, **kwargs: Any) -> None:
        if route == self.ROUTE_START:
            self.navigator.show_start_screen()
            return
        if route == self.ROUTE_CANDIDATE_INFO:
            self.navigator.show_candidate_info()
            return
        if route == self.ROUTE_HISTORY:
            self.navigator.show_start_screen()
            return
        if route == self.ROUTE_QUESTION_TRAIT:
            self.navigator.show_trait_screen_by_trait_id(kwargs["flow_idx"], kwargs["trait"])
            return
        if route == self.ROUTE_QUESTION_CUSTOM:
            self.navigator.show_custom_question_item_screen(kwargs["flow_idx"], kwargs["custom_question"])
            return
        self.navigator.show_candidate_info()

    def show_flow_screen(self, flow_index: int) -> None:
        if not self.flow_state.state().track:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return
        if flow_index < 0:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return
        flow_len = self.flow_state.flow_len()
        if flow_index >= flow_len:
            if flow_len > 0:
                self.show_flow_screen(flow_len - 1)
                return
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return
        item = self.flow_state.flow_item(flow_index)
        if not item:
            self.route_to(self.ROUTE_CANDIDATE_INFO)
            return
        self.flow_state.mark_flow_timestamp(flow_index)
        self.flow_state.start_question_recording_for_flow(flow_index)
        self.flow_state.state().current_index = flow_index + 1
        route = self.flow_item_route(item)
        if route == self.ROUTE_QUESTION_TRAIT:
            self._route_trait(flow_index, str(item.get("id", "")))
            return
        if route == self.ROUTE_QUESTION_CUSTOM:
            self._route_custom(flow_index, str(item.get("id", "")))
            return
        self.route_to(self.ROUTE_CANDIDATE_INFO)

    def _route_trait(self, flow_index: int, trait_id: str) -> None:
        trait = self.flow_state.trait_by_id(trait_id)
        if trait:
            self.route_to(self.ROUTE_QUESTION_TRAIT, flow_idx=flow_index, trait=trait)
            return
        self._route_after_navigation_failure(flow_index)

    def _route_custom(self, flow_index: int, custom_id: str) -> None:
        custom_question = self.flow_state.custom_by_id(custom_id)
        if custom_question:
            self.route_to(self.ROUTE_QUESTION_CUSTOM, flow_idx=flow_index, custom_question=custom_question)
            return
        self._route_after_navigation_failure(flow_index)

    def _route_after_navigation_failure(self, flow_index: int) -> None:
        track = self.flow_state.state().track
        if not track:
            self.route_to(self.ROUTE_FALLBACK)
            return
        self.flow_state.build_active_flow(track)
        refreshed_item = self.flow_state.flow_item(flow_index)
        if refreshed_item:
            self.show_flow_screen(flow_index)
            return
        self.route_to(self.ROUTE_FALLBACK)


class UiShellController:
    def __init__(self, app: Any, shared_state: Any) -> None:
        self.app = app
        self.shared_state = shared_state

    def clear_page(self) -> None:
        self.app.clear_page()

    def clear_footer(self) -> None:
        self.app.clear_footer()

    def set_footer_actions(self, *, left_actions: Any = None, right_actions: Any = None) -> None:
        self.app.set_footer_actions(left_actions=left_actions, right_actions=right_actions)


_COMPAT_MODULES: tuple[str, ...] = (
    "ui_windows",
    "interview_app.ui_router",
    "interview_app.ui_shell",
    "interview_app.view_protocols",
    "interview_app.views",
    "interview_app.views.candidate_setup_view",
    "interview_app.views.signal_reference_view",
    "interview_app.views.start_screen_view",
)

_WRAPPER_POLICY = (
    "Legacy UI modules are compatibility wrappers during flattening. "
    "New production imports should prefer ui_composition."
)


def available_modules() -> tuple[str, ...]:
    return _COMPAT_MODULES


def module_ownership() -> dict[str, str]:
    return {module_name: "ui_composition" for module_name in _COMPAT_MODULES}


def wrapper_policy() -> str:
    return _WRAPPER_POLICY


def load_compat_module(module_name: str) -> ModuleType:
    if module_name not in _COMPAT_MODULES:
        raise AttributeError(f"{module_name!r} is not part of ui_composition")
    return import_module(module_name)


def public_symbols(module_name: str | None = None) -> tuple[str, ...]:
    module_names = (module_name,) if module_name is not None else _COMPAT_MODULES
    symbols: set[str] = set()
    for compat_name in module_names:
        module = load_compat_module(compat_name)
        symbols.update(name for name in dir(module) if not name.startswith("_"))
    return tuple(sorted(symbols))


def resolve_compat_symbol(symbol_name: str) -> Any:
    for module_name in _COMPAT_MODULES:
        module = import_module(module_name)
        if hasattr(module, symbol_name):
            return getattr(module, symbol_name)
    raise AttributeError(f"ui_composition has no attribute {symbol_name!r}")


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(f"ui_composition has no attribute {name!r}")
    module_key = name.replace("_", ".")
    if module_key in _COMPAT_MODULES:
        return import_module(module_key)
    return resolve_compat_symbol(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_COMPAT_MODULES) | set(public_symbols()))
