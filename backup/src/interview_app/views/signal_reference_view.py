from __future__ import annotations

from typing import Any

import tkinter as tk
from tkinter import END, ttk


class SignalReferenceView:
    """Renders disqualifier reference and signal example UI blocks."""

    def __init__(self, parent: Any, controller: Any) -> None:
        self.parent = parent
        self.controller = controller

    def show_disqualifier_reference(self) -> None:
        top = tk.Toplevel(self.controller)
        top.title("Absolute Disqualifiers")
        top.geometry("760x360")

        text = tk.Text(top, wrap="word")
        text.pack(fill="both", expand=True)

        text.insert(END, "Absolute Disqualifiers (Global)\n\n")
        for item in self.controller.rubric["absolute_disqualifiers"]:
            text.insert(END, f"- {item}\n")

        text.config(state="disabled")

    def render_signal_examples(self, parent: ttk.Frame, trait_id: str) -> None:
        data = self.controller.signals.get_for_trait(trait_id)

        box = ttk.LabelFrame(parent, text="Disqualifier Signal Examples")
        box.pack(fill="both", pady=8, expand=True)

        ttk.Label(
            box,
            text="Use these as probe prompts; they are examples to help pattern-match risk signals.",
            wraplength=1020,
            foreground="#334155",
        ).pack(anchor="w", padx=10, pady=(8, 0))

        text = tk.Text(
            box,
            height=18,
            wrap="word",
            relief="flat",
            bg="#f8fafc",
            padx=12,
            pady=10,
            font=("TkDefaultFont", self.controller.settings["font_size"]),
        )

        ybar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ybar.set)

        ybar.pack(side="right", fill="y", pady=8)
        text.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        text.tag_configure("header", font=("TkDefaultFont", self.controller.settings["font_size"] + 1, "bold"), foreground="#0f172a")
        text.tag_configure("meta", foreground="#334155")
        text.tag_configure("signal", font=("TkDefaultFont", self.controller.settings["font_size"], "bold"), foreground="#1d4ed8")
        text.tag_configure("probe", foreground="#7c2d12")

        if not data:
            text.insert(END, "No signal examples configured for this trait.", "meta")
            text.config(state="disabled")
            return

        text.insert(END, "Question Context\n", "header")
        text.insert(END, f"Question ID: {data.get('question_id', '')}\n", "meta")
        text.insert(END, f"Primary question: {data.get('primary_question', '')}\n\n", "meta")

        signal_items = data.get("disqualifier_signals") or data.get("signals") or []
        if not signal_items:
            text.insert(END, "No signal examples configured for this trait.", "meta")
            text.config(state="disabled")
            return

        for idx, item in enumerate(signal_items, start=1):
            raw_type = item.get("disqualifier_type", "")
            friendly_type = raw_type.replace("_", " ").title() if raw_type else "Unspecified"
            auto = "Yes" if item.get("auto_disqualify_if_confirmed") else "No"

            text.insert(END, f"Signal {idx}: {friendly_type}\n", "signal")
            text.insert(END, f"Auto disqualify if confirmed: {auto}\n", "meta")

            examples = item.get("examples", [])
            if examples:
                for ex in examples:
                    text.insert(END, f"• {ex}\n")
            else:
                text.insert(END, "• No examples listed.\n", "meta")

            probe = item.get("probe_to_confirm", "")
            if probe:
                text.insert(END, f"Probe to confirm: {probe}\n", "probe")

            text.insert(END, "\n")

        text.config(state="disabled")

    def render_progress_strip(self, parent: ttk.Frame, flow_idx: int, *, is_scored: bool) -> None:
        progress = ttk.LabelFrame(parent, text="Interview Progress")
        progress.pack(fill="x", pady=(0, 8))

        total = max(1, self.controller._flow_len())
        current = max(1, min(total, flow_idx + 1))
        percent = int((current / total) * 100)
        kind = "Scored competency" if is_scored else "Custom question"

        ttk.Label(
            progress,
            text=f"{kind} • Question {current} of {total}",
            foreground="#334155",
        ).pack(anchor="w", padx=10, pady=(8, 4))

        bar = ttk.Progressbar(progress, mode="determinate", maximum=100, value=percent)
        bar.pack(fill="x", padx=10, pady=(0, 8))
