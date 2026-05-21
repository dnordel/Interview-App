from __future__ import annotations

from datetime import date
from typing import Any

import tkinter as tk
from tkinter import StringVar, messagebox, ttk


class CandidateSetupView:
    """Renders the candidate setup screen and delegates flow logic to the controller."""

    def __init__(self, parent: Any, controller: Any) -> None:
        self.parent = parent
        self.controller = controller

    def render(self) -> None:
        self.controller.clear_page()

        frm = ttk.Frame(self.parent, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Step 1: Candidate setup",
            font=("TkDefaultFont", self.controller.settings["font_size"] + 4, "bold"),
        ).pack(anchor="w", pady=8)

        name_var = StringVar(value=self.controller.state.candidate_name)
        school_var = StringVar(value=self.controller.state.school)
        track_var = StringVar(value=self.controller.state.track)

        name_error_var = StringVar(value="")
        school_error_var = StringVar(value="")
        track_error_var = StringVar(value="")

        basics = ttk.LabelFrame(frm, text="Step A: Candidate basics")
        basics.pack(fill="x", pady=(2, 10))

        ttk.Label(basics, text="Candidate Name (Required)").pack(anchor="w", padx=10, pady=(10, 0))
        name_entry = ttk.Entry(basics, textvariable=name_var)
        name_entry.pack(fill="x", padx=10, pady=4)
        ttk.Label(basics, textvariable=name_error_var, foreground="#b91c1c").pack(anchor="w", padx=10)

        ttk.Label(basics, text="School (Required)").pack(anchor="w", padx=10, pady=(6, 0))
        school_row = ttk.Frame(basics)
        school_row.pack(fill="x", pady=4)

        school_combo = ttk.Combobox(school_row, textvariable=school_var, values=self.controller.school_options)
        school_combo.pack(side="left", fill="x", expand=True, padx=(10, 0))
        school_combo.bind("<Button-1>", lambda _e: self.controller._open_combo_dropdown(school_combo))

        ttk.Button(
            school_row,
            text="Add School (Optional)",
            command=lambda: self.controller._add_school(school_var, school_combo),
        ).pack(side="left", padx=6)

        ttk.Label(
            basics,
            text="Choose a configured school or type to add a custom one.",
            foreground="#475569",
        ).pack(anchor="w", padx=10)
        ttk.Label(basics, textvariable=school_error_var, foreground="#b91c1c").pack(anchor="w", padx=10)

        track_hdr = ttk.Frame(frm)
        track_hdr.pack(fill="x", pady=(10, 0))
        ttk.Label(track_hdr, text="Role track (required)").pack(side="left")
        ttk.Button(
            track_hdr,
            text="Help",
            padding=6,
            command=lambda: messagebox.showinfo(
                "Role track",
                "Role track selects the scored competencies used for this interview.\n\n"
                "Choose the track that matches the classroom role being hired.",
            ),
        ).pack(side="left", padx=(8, 0))

        track_box = ttk.LabelFrame(frm, text="Choose track")
        track_box.pack(fill="x", pady=(4, 0))
        tracks = [(k, self.controller.rubric["tracks"][k]["label"]) for k in self.controller.rubric["tracks"].keys()]
        for k, label in tracks:
            ttk.Radiobutton(track_box, text=label, variable=track_var, value=k).pack(anchor="w", padx=10, pady=2)
        ttk.Label(frm, textvariable=track_error_var, foreground="#b91c1c").pack(anchor="w")

        intro_box = ttk.LabelFrame(frm, text="Step B: Intro script preview")
        intro_box.pack(fill="both", expand=False, pady=(12, 12))

        intro_text = tk.Text(
            intro_box,
            height=16,
            wrap="word",
            relief="flat",
            bg="#f8fafc",
            padx=12,
            pady=10,
            font=("TkDefaultFont", self.controller.settings["font_size"]),
        )
        intro_y = ttk.Scrollbar(intro_box, orient="vertical", command=intro_text.yview)
        intro_text.configure(yscrollcommand=intro_y.set)

        intro_y.pack(side="right", fill="y", pady=8)
        intro_text.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        school_combo.bind("<<ComboboxSelected>>", lambda _e: self.controller._refresh_intro_script(school_var, intro_text))
        school_var.trace_add("write", lambda *_: self.controller._refresh_intro_script(school_var, intro_text))
        self.controller._refresh_intro_script(school_var, intro_text)

        def apply_inline_errors() -> bool:
            errors = {
                "name": "Candidate Name is required." if not name_var.get().strip() else "",
                "school": "School selection is required." if not school_var.get().strip() else "",
                "track": "Track selection is required." if not track_var.get().strip() else "",
            }
            name_error_var.set(errors["name"])
            school_error_var.set(errors["school"])
            track_error_var.set(errors["track"])
            return any(bool(v) for v in errors.values())

        def go_next() -> None:
            has_inline_errors = apply_inline_errors()
            ok, msg = self.controller._validate_candidate_vars(name_var, school_var, track_var)
            if not ok:
                if not has_inline_errors:
                    apply_inline_errors()
                messagebox.showerror("Validation", msg)
                return

            self.controller.state.candidate_name = name_var.get().strip()
            self.controller.state.interview_date = date.today().isoformat()
            self.controller.state.school = school_var.get().strip()
            self.controller.state.track = track_var.get().strip()
            self.controller.state.referral_packet = {
                "interview_notes_path": "",
                "transcript_path": "",
            }

            self.controller._build_active_flow(self.controller.state.track)
            if not self.controller.active_flow:
                messagebox.showerror("Configuration", "No questions configured for this track.")
                return

            if not self.controller._initialize_interview_runtime():
                return
            self.controller.state.current_index = 1
            try:
                self.controller.show_flow_screen(0)
            except Exception as exc:
                self.controller._handle_start_interview_navigation_failure(exc)

        self.controller.set_footer_actions(
            left_actions=[("Back to Start", self.controller.show_start_screen)],
            right_actions=[("Start Interview", go_next)],
        )
        self.controller.after_idle(name_entry.focus_set)
