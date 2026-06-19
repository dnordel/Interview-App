from __future__ import annotations

from datetime import date
from typing import Any

from tkinter import ttk

from onboarding_operations import build_dashboard_today_summary
from onboarding_operations import TASK_STATUS_COLORS, TASK_STATUS_LABELS
from platform_services import APP_TITLE, SUMMARY_SCOPES
from tk_theme import COLORS


class StartScreenView:
    """Renders the interview app start screen and dashboard cards."""

    def __init__(self, parent: Any, controller: Any) -> None:
        self.parent = parent
        self.controller = controller

    def render(self) -> None:
        self.controller.clear_page()

        frm = ttk.Frame(self.parent, padding=20)
        frm.pack(fill="both", expand=True)

        latest_draft_path = self.controller._latest_draft_path()

        header = ttk.Frame(frm)
        header.pack(fill="x", pady=(0, 12))
        title_block = ttk.Frame(header)
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(
            title_block,
            text=APP_TITLE,
            font=("TkDefaultFont", self.controller.settings["font_size"] + 5, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            title_block,
            text="Interviews, onboarding signals, and offer follow-through in one operating view.",
            foreground=COLORS["muted"],
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        actions = ttk.Frame(header)
        actions.pack(side="right", anchor="ne")
        new_btn = ttk.Button(actions, text="New Interview", command=self.controller.new_interview, style="Primary.TButton")
        new_btn.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Open Draft", command=self.controller.open_draft, style="Secondary.TButton").pack(side="left", padx=(0, 8))

        continue_label = "Continue Last Draft" if latest_draft_path else "Continue Last Draft (Unavailable)"
        continue_btn = ttk.Button(
            actions,
            text=continue_label,
            command=self.controller.continue_last_draft,
            state="normal" if latest_draft_path else "disabled",
            style="Secondary.TButton",
        )
        continue_btn.pack(side="left")
        if latest_draft_path:
            ttk.Label(frm, text=f"Latest draft: {latest_draft_path.name}", foreground=COLORS["muted"]).pack(anchor="w", pady=(0, 8))

        self.render_today_dashboard(frm)

        workbench = ttk.Frame(frm)
        workbench.pack(fill="x", pady=(0, 12))
        tools_box = ttk.LabelFrame(workbench, text="Tools & Admin", padding=10)
        tools_box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Label(
            tools_box,
            text="Question setup, settings, onboarding, and referral support.",
            foreground=COLORS["muted"],
        ).pack(anchor="w", pady=(0, 8))

        tools_row_one = ttk.Frame(tools_box)
        tools_row_one.pack(fill="x", pady=(0, 6))
        ttk.Button(tools_row_one, text="Edit Questions", command=self.controller.open_question_editor).pack(side="left", padx=(0, 8))
        ttk.Button(tools_row_one, text="Question Settings", command=self.controller.open_question_settings).pack(side="left", padx=(0, 8))
        ttk.Button(tools_row_one, text="Settings", command=self.controller.open_settings).pack(side="left", padx=(0, 8))
        ttk.Button(tools_row_one, text="Open Onboarding Tracker", command=self.controller.open_onboarding_tracker).pack(side="left", padx=(0, 8))
        ttk.Button(
            tools_row_one,
            text="View only urgent onboarding tasks",
            command=lambda: self.controller.open_onboarding_tracker(urgent_only=True),
        ).pack(side="left")

        tools_row_two = ttk.Frame(tools_box)
        tools_row_two.pack(fill="x")
        ttk.Button(tools_row_two, text="Refer Director", command=self.controller.open_director_referral_email_draft).pack(side="left", padx=(0, 8))
        ttk.Button(tools_row_two, text="Exit", command=self.controller.destroy).pack(side="left")

        search_box = ttk.LabelFrame(frm, text="Interview History", padding=10)
        search_box.pack(fill="both", expand=True)
        search_row = ttk.Frame(search_box)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Search").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.controller.history_search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(search_row, text="Clear", command=lambda: self.controller._set_history_search("")).pack(side="left")
        if self.controller.history_search_trace_id:
            self.controller.history_search_var.trace_remove("write", self.controller.history_search_trace_id)
        self.controller.history_search_trace_id = self.controller.history_search_var.trace_add("write", lambda *_: self.controller._refresh_history_tree())

        self.controller._build_history_table(search_box)
        self.controller._refresh_history_tree()

        self.controller.set_footer_actions()
        self.controller.after_idle(new_btn.focus_set)

    def render_today_dashboard(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Today at a glance", padding=10)
        box.pack(fill="x", pady=(0, 12))

        onboarding_state = self.controller._load_onboarding_state()
        summary = build_dashboard_today_summary(
            history_rows=self.controller.history_store.load(),
            employees=onboarding_state.employees,
            scheduler_settings=onboarding_state.scheduler_settings,
            today=date.today(),
        )
        kpi_row = ttk.Frame(box)
        kpi_row.pack(fill="x")
        self.render_interview_dashboard_card(kpi_row, summary)
        self.render_onboarding_dashboard_card(kpi_row, summary)
        self.render_monthly_metrics_card(box, onboarding_state)
        self.render_dashboard_actions(box, summary)

    def render_interview_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        card = ttk.Frame(parent, style="Surface.TFrame")
        card.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=(0, 6))
        ttk.Label(card, text="Interviews", font=("TkDefaultFont", self.controller.settings["font_size"], "bold")).pack(anchor="w")
        ttk.Label(
            card,
            text=f"Pending: {summary.interviews.pending}   Follow-up: {summary.interviews.follow_up}",
            foreground=COLORS["muted"],
        ).pack(anchor="w")

    def render_onboarding_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        onboarding = summary.onboarding
        card = ttk.Frame(parent, style="Surface.TFrame")
        card.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(0, 6))
        ttk.Label(card, text="Onboarding", font=("TkDefaultFont", self.controller.settings["font_size"], "bold")).pack(anchor="w")

        due_today_label = TASK_STATUS_LABELS["due_today"]
        critical_text = f"Critical (≤{onboarding.critical_window_days} days): {onboarding.critical}"
        totals = f"Overdue: {onboarding.overdue}   {due_today_label}: {onboarding.due_today}   {critical_text}"
        ttk.Label(card, text=totals, foreground=TASK_STATUS_COLORS["due_soon"]).pack(anchor="w")

        if onboarding.next_critical is None:
            ttk.Label(card, text="Next critical task: none", foreground=COLORS["success"]).pack(anchor="w")
            return

        next_item = onboarding.next_critical
        next_text = f"Next critical: {next_item.employee_name} • {next_item.title} (due {next_item.due_date})"
        color = TASK_STATUS_COLORS.get(next_item.status, TASK_STATUS_COLORS["due_soon"])
        ttk.Label(card, text=next_text, foreground=color).pack(anchor="w")

    def render_monthly_metrics_card(self, parent: ttk.LabelFrame, onboarding_state: Any) -> None:
        card = ttk.Frame(parent)
        card.pack(fill="x", padx=8, pady=(2, 2))
        ttk.Label(card, text="Monthly Onboarding Metrics", font=("TkDefaultFont", self.controller.settings["font_size"], "bold")).pack(anchor="w")

        controls = ttk.Frame(card)
        controls.pack(anchor="w", pady=(2, 4))
        ttk.Label(controls, text="Scope:").pack(side="left")
        scope_box = ttk.Combobox(
            controls,
            textvariable=self.controller.monthly_metrics_scope_var,
            values=list(SUMMARY_SCOPES),
            state="readonly",
            width=15,
        )
        scope_box.pack(side="left", padx=(4, 10))

        month_values = self.controller._monthly_metric_month_choices()
        if self.controller.monthly_metrics_month_var.get() not in month_values:
            self.controller.monthly_metrics_month_var.set(month_values[0])
        ttk.Label(controls, text="Month:").pack(side="left")
        month_box = ttk.Combobox(
            controls,
            textvariable=self.controller.monthly_metrics_month_var,
            values=month_values,
            state="readonly",
            width=10,
        )
        month_box.pack(side="left", padx=(4, 10))

        ttk.Button(controls, text="Refresh", command=lambda: self.controller._refresh_monthly_metrics_text(onboarding_state)).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Export Metrics", command=self.controller._export_metrics_log).pack(side="left")

        self.controller.monthly_metrics_label = ttk.Label(card, text="", foreground="#475569", justify="left")
        self.controller.monthly_metrics_label.pack(anchor="w")

        scope_box.bind("<<ComboboxSelected>>", lambda _e: self.controller._refresh_monthly_metrics_text(onboarding_state))
        month_box.bind("<<ComboboxSelected>>", lambda _e: self.controller._refresh_monthly_metrics_text(onboarding_state))
        self.controller._refresh_monthly_metrics_text(onboarding_state)

    def render_dashboard_actions(self, parent: ttk.LabelFrame, summary: Any) -> None:
        actions = ttk.Frame(parent)
        actions.pack(anchor="w", padx=8, pady=(4, 8))
        ttk.Button(actions, text="Start Interview", command=self.controller.new_interview).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="Open Next Critical Onboarding Task",
            command=lambda: self.controller._open_next_critical_task(summary),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Run Reminders", command=self.controller._run_onboarding_reminders_now).pack(side="left")
