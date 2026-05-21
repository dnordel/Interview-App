from __future__ import annotations

from datetime import date
from typing import Any

from tkinter import ttk

from app_content import APP_TITLE
from dashboard_today import build_dashboard_today_summary
from onboarding_ui_helpers import TASK_STATUS_COLORS, TASK_STATUS_LABELS
from ux_metrics import SUMMARY_SCOPES


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

        ttk.Label(
            frm,
            text=APP_TITLE,
            font=("TkDefaultFont", self.controller.settings["font_size"] + 6, "bold"),
        ).pack(pady=10)
        ttk.Label(
            frm,
            text="Run interviews in a consistent flow, capture evidence quickly, and finalize to a DOCX report.",
            foreground="#475569",
            wraplength=950,
            justify="left",
        ).pack(fill="x", pady=(0, 12))

        core_box = ttk.LabelFrame(frm, text="START INTERVIEW WORKFLOW", padding=12)
        core_box.pack(fill="x", pady=(0, 12))
        ttk.Label(
            core_box,
            text="Begin a new session or resume draft work.",
            foreground="#475569",
        ).pack(anchor="w", pady=(0, 8))

        core_row = ttk.Frame(core_box)
        core_row.pack(anchor="w")
        new_btn = ttk.Button(core_row, text="New Interview", command=self.controller.new_interview)
        new_btn.pack(side="left", padx=(0, 8))
        ttk.Button(core_row, text="Open Draft", command=self.controller.open_draft).pack(side="left", padx=(0, 8))

        continue_label = "Continue Last Draft" if latest_draft_path else "Continue Last Draft (Unavailable)"
        continue_btn = ttk.Button(
            core_row,
            text=continue_label,
            command=self.controller.continue_last_draft,
            state="normal" if latest_draft_path else "disabled",
        )
        continue_btn.pack(side="left")
        if latest_draft_path:
            ttk.Label(core_box, text=f"Latest draft: {latest_draft_path.name}", foreground="#475569").pack(anchor="w", pady=(8, 0))

        tools_box = ttk.LabelFrame(frm, text="TOOLS & ADMIN", padding=12)
        tools_box.pack(fill="x", pady=(0, 12))
        ttk.Label(
            tools_box,
            text="Secondary setup and support actions.",
            foreground="#475569",
        ).pack(anchor="w", pady=(0, 8))

        tools_row_one = ttk.Frame(tools_box)
        tools_row_one.pack(anchor="w", pady=(0, 6))
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
        tools_row_two.pack(anchor="w")
        ttk.Button(tools_row_two, text="Refer Director", command=self.controller.open_director_referral_email_draft).pack(side="left", padx=(0, 8))
        ttk.Button(tools_row_two, text="Exit", command=self.controller.destroy).pack(side="left")

        self.render_today_dashboard(frm)

        search_row = ttk.Frame(frm)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Search history:").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.controller.history_search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(search_row, text="Clear", command=lambda: self.controller._set_history_search("")).pack(side="left")
        if self.controller.history_search_trace_id:
            self.controller.history_search_var.trace_remove("write", self.controller.history_search_trace_id)
        self.controller.history_search_trace_id = self.controller.history_search_var.trace_add("write", lambda *_: self.controller._refresh_history_tree())

        self.controller._build_history_table(frm)
        self.controller._refresh_history_tree()

        self.controller.set_footer_actions()
        self.controller.after_idle(new_btn.focus_set)

    def render_today_dashboard(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Today at a glance")
        box.pack(fill="x", pady=(0, 12))

        onboarding_state = self.controller._load_onboarding_state()
        summary = build_dashboard_today_summary(
            history_rows=self.controller.history_store.load(),
            employees=onboarding_state.employees,
            scheduler_settings=onboarding_state.scheduler_settings,
            today=date.today(),
        )
        self.render_interview_dashboard_card(box, summary)
        self.render_onboarding_dashboard_card(box, summary)
        self.render_monthly_metrics_card(box, onboarding_state)
        self.render_dashboard_actions(box, summary)

    def render_interview_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        card = ttk.Frame(parent)
        card.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(card, text="Interviews", font=("TkDefaultFont", self.controller.settings["font_size"], "bold")).pack(anchor="w")
        ttk.Label(
            card,
            text=f"Pending: {summary.interviews.pending}   Follow-up: {summary.interviews.follow_up}",
            foreground="#475569",
        ).pack(anchor="w")

    def render_onboarding_dashboard_card(self, parent: ttk.LabelFrame, summary: Any) -> None:
        onboarding = summary.onboarding
        card = ttk.Frame(parent)
        card.pack(fill="x", padx=8, pady=(2, 2))
        ttk.Label(card, text="Onboarding", font=("TkDefaultFont", self.controller.settings["font_size"], "bold")).pack(anchor="w")

        due_today_label = TASK_STATUS_LABELS["due_today"]
        critical_text = f"Critical (≤{onboarding.critical_window_days} days): {onboarding.critical}"
        totals = f"Overdue: {onboarding.overdue}   {due_today_label}: {onboarding.due_today}   {critical_text}"
        ttk.Label(card, text=totals, foreground=TASK_STATUS_COLORS["due_soon"]).pack(anchor="w")

        if onboarding.next_critical is None:
            ttk.Label(card, text="Next critical task: none", foreground="#166534").pack(anchor="w")
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
