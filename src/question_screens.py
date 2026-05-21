from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import BooleanVar, END, IntVar, StringVar, messagebox, ttk

from app_content import NEVER_HAPPENED_BY_TRAIT, NEVER_HAPPENED_GLOBAL_SCRIPT, text_suggests_no_example
from candidate_profile import CANONICAL_DEGREE_TYPES, parse_yes_no, validate_candidate_qualification
from keyboard_telemetry import KeyboardPathSession
from reporting import DraftManager
from ui_feedback import (
    VALIDATION_SEVERITY_ERROR,
    VALIDATION_SEVERITY_WARNING,
    append_error_log,
    associate_label_with_control,
    create_inline_validation_message,
    format_guidance,
    show_actionable_error,
)


SCORE_PANEL_BG = "#FEF3C7"
DQ_PANEL_BG = "#FECACA"
FOCUS_RING = "#1D4ED8"


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
        self.dq_required_bg = "#FCA5A5"
        self._descriptor_body: ttk.Frame | None = None
        self._samples_body: ttk.Frame | None = None
        self.inline_validation = None
        self.keyboard_session = KeyboardPathSession(
            logger=getattr(self.app, "metrics_logger", None),
            flow_id="interview_question",
            screen_id=f"trait_{self.tid}",
        )

    def render(self) -> None:
        frm = ttk.Frame(self.app.page_frame, padding=12)
        frm.pack(fill="both", expand=True)
        self.keyboard_session.bind(frm)

        if self.flow_idx == 0:
            self.qualification_vars = render_qualification_box(frm, self.app)

        self.app.render_progress_strip(frm, self.flow_idx, is_scored=True)
        self._render_header(frm)
        primary_frame, secondary_frame = self._render_container_frames(frm)
        self._render_primary_viewport(primary_frame)
        self._render_secondary_context(secondary_frame)

        self._sync_dq_dependent_ui()
        self._render_footer_actions()
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
        self._render_ladders(parent)
        self._render_no_example_addendum(parent)
        self.app._render_signal_examples(parent, self.tid)

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

        for n in [5, 4, 3, 2, 1]:
            ttk.Label(section_body, text=f"{n}: {line_values[str(n)]}", wraplength=1030).pack(anchor="w")
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
        score_box = tk.Frame(parent, bg=SCORE_PANEL_BG, bd=2, relief="solid", highlightthickness=2, highlightbackground="#D97706")
        score_box.pack(fill="x", pady=(8, 6))

        score_label = tk.Label(
            score_box,
            text="Select raw score (required unless absolute disqualifier is checked)",
            bg=SCORE_PANEL_BG,
            fg="#111827",
            font=("TkDefaultFont", self.app.settings["font_size"] + 4, "bold"),
        )
        score_label.pack(anchor="w", padx=12, pady=(10, 6))

        tk.Button(
            score_box,
            text="Help",
            padx=10,
            pady=4,
            highlightthickness=2,
            highlightcolor=FOCUS_RING,
            command=lambda: self._show_help_definition(
                "Raw score",
                "Raw score is the 1-5 rating for this scored competency before weighting.\n\n"
                "If an absolute disqualifier is checked, leave raw score unselected and document verbatim evidence.",
            ),
        ).pack(anchor="e", padx=12, pady=(0, 6))

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
            )
            rb.pack(side="left", padx=(0, 14))
            self.score_widgets.append(rb)
        if self.score_widgets:
            associate_label_with_control(score_label, self.score_widgets[0])

        self.inline_validation = create_inline_validation_message(score_box, pady=(0, 10))

    def _render_disqualifier_box(self, parent: ttk.Frame) -> None:
        dq_wrap = tk.Frame(parent, bg=DQ_PANEL_BG, bd=2, relief="solid", highlightthickness=2, highlightbackground="#B91C1C")
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
        )
        dq_toggle.pack(side="left")

        tk.Button(
            dq_inner,
            text="Help",
            padx=10,
            pady=4,
            highlightthickness=2,
            highlightcolor=FOCUS_RING,
            command=lambda: self._show_help_definition(
                "Absolute disqualifier",
                "An absolute disqualifier is a behavior or statement that requires an automatic no-hire recommendation.\n\n"
                "When checked, include a verbatim quote or specific notes that justify the flag.",
            ),
        ).pack(side="left", padx=(8, 0))

        tk.Button(
            dq_inner,
            text="View Global Disqualifiers",
            padx=10,
            pady=4,
            highlightthickness=2,
            highlightcolor=FOCUS_RING,
            command=self.app.show_disqualifier_reference,
        ).pack(side="right")

        verbatim_wrap = tk.Frame(dq_wrap, bg=DQ_PANEL_BG)
        verbatim_wrap.pack(fill="x", padx=10, pady=(6, 8))

        self.v_label = tk.Label(
            verbatim_wrap,
            text="Verbatim quote/notes (required when absolute disqualifier is checked)",
            bg=DQ_PANEL_BG,
            fg="#111827",
            font=("TkDefaultFont", self.app.settings["font_size"], "bold"),
        )

        self.v_text = tk.Text(verbatim_wrap, height=6, wrap="word", highlightthickness=2, highlightcolor=FOCUS_RING)
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
        self.q_text.configure(highlightthickness=2, highlightcolor=FOCUS_RING)
        self.q_text.pack(fill="x", pady=4)
        self.q_text.insert(END, self.state.get("question_notes", ""))
        self.keyboard_session.bind(self.q_text)

        ttk.Label(notes_frame, text="Scored competency notes (evaluation summary for final report)").pack(anchor="w", pady=(4, 0))
        self.t_text = tk.Text(notes_frame, height=7, wrap="word")
        self.t_text.configure(highlightthickness=2, highlightcolor=FOCUS_RING)
        self.t_text.pack(fill="x", pady=4)
        self.t_text.insert(END, self.state.get("trait_notes", ""))
        self.keyboard_session.bind(self.t_text)

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

        self.app.state.current_index = self.flow_idx + 1
        persist_snapshot = getattr(self.app, "_persist_interview_session_snapshot", None)
        if callable(persist_snapshot):
            persist_snapshot(self.flow_idx)
        return True

    def _render_footer_actions(self) -> None:
        self.app.set_footer_actions(
            left_actions=[
                ("Back", self.go_back),
                ("Next", self.go_next),
                ("Skip", self.skip_question),
                ("Save Draft", self.save_draft),
            ],
            right_actions=[
                ("Play Audio", lambda: self.app.play_flow_question_audio(self.flow_idx)),
                ("Finalize" if self._is_last() else "Continue", self.finalize_or_continue),
                ("Exit", self.app.destroy),
            ],
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

    def skip_question(self) -> None:
        t = self.app.state.trait_inputs[self.tid]
        t["raw_score"] = None
        t["absolute_disqualifier"] = False
        t["verbatim_notes"] = ""
        t["skipped"] = True
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
        self.text_box.pack(fill="x", padx=10, pady=10)
        self.keyboard_session.bind(self.text_box)

        existing = (self.app.state.custom_inputs.get(self.qid, {}) or {}).get("answer", "")
        if existing:
            self.text_box.insert(END, existing)

        self._render_footer_actions()
        self.text_box.configure(highlightthickness=2, highlightcolor=FOCUS_RING)
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
        self.app.set_footer_actions(
            left_actions=[
                ("Back", self.go_back),
                ("Next", self.go_next),
                ("Skip", self.skip_question),
                ("Save Draft", self.save_draft),
            ],
            right_actions=[
                ("Play Audio", lambda: self.app.play_flow_question_audio(self.flow_idx)),
                ("Finalize" if self._is_last() else "Continue", self.finalize_or_continue),
                ("Exit", self.app.destroy),
            ],
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
