from __future__ import annotations

from datetime import date, datetime
from time import monotonic
import argparse
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import json
from collections.abc import Callable
from tkcalendar import DateEntry

from onboarding_operations import EmailSettings, Employee, ReminderRunSummary, TaskTemplate, make_id, parse_date
from notification_service import notification_service_from_onboarding
from onboarding_operations import OnboardingReminderRunner
from onboarding_operations import evaluate_onboarding_reminder_health
from onboarding_operations import (
    apply_task_completion,
    seed_employee_tasks,
)
from onboarding_operations import (
    DEFAULT_EXPECTED_INTERVAL_HOURS,
    scheduler_command_example,
    scheduler_expected_interval_hours,
    scheduler_opt_in,
    scheduler_script_path,
    scheduler_status_text,
)
from onboarding_operations import AppState, JsonStore
from onboarding_operations import build_scheduler_status, normalize_run_source
from onboarding_operations import TASK_FILTER_OPTIONS, filtered_tasks, format_due_date_short, urgent_filter_result_count
from onboarding_operations import build_dashboard_kpi_chips, build_recommended_action, kpi_navigation_target
from onboarding_operations import build_specific_date_reference
from platform_services import EVENT_REMINDER_SENT, EVENT_TASK_COMPLETED, EVENT_TASK_CREATED, UxMetricsLogger
from onboarding_operations import (
    TASK_STATUS_BADGE_STYLE,
    TASK_STATUS_COLORS,
    build_onboarding_overview,
    scroll_widget_into_view,
    task_status,
    task_status_badge_text,
)
from onboarding_operations import build_dashboard_today_summary, critical_window_days_from_settings
from scoring_reporting import (
    insert_token_into_focused_widget,
    placeholder_picker_options,
    token_from_picker_label,
    validate_template_map,
    missing_placeholder_keys,
)
from onboarding_operations import (
    recipient_warning_text,
    reminder_send_estimate,
    split_and_validate_recipients,
    unknown_placeholder_actionable_message,
    validate_sender_email,
)
from onboarding_operations import onboarding_action_sections
from scoring_reporting import sender_email_domain_type
from ui_composition import (
    VALIDATION_SEVERITY_BLOCKING,
    VALIDATION_SEVERITY_ERROR,
    create_inline_validation_message_grid,
    should_display_modal,
    show_inline_field_error,
)
from ui_composition import KeyboardPathSession
from tk_theme import COLORS, apply_professional_ops_theme, configure_plain_button


class OnboardingTrackerApp:
    REMINDER_RUN_HISTORY_LIMIT = 50

    def __init__(self, root: tk.Tk, launch_context: dict[str, object] | None = None) -> None:
        self.root = root
        self.root.title("Employee Onboarding Task Tracker")
        self.storage_dir = Path.cwd()
        self.store = JsonStore(self.storage_dir)
        self.state = self.store.load()
        self.metrics_logger = UxMetricsLogger(self.storage_dir)

        self.selected_employee_id: str | None = None
        self.task_filter_var = tk.StringVar(value="all")
        self.compact_mode_var = tk.BooleanVar(value=False)
        self.task_vars: dict[str, tk.BooleanVar] = {}
        self._employee_lookup_by_label: dict[str, Employee] = {}
        self.launch_context = launch_context or {}
        self._active_dashboard_kpi: str | None = None
        self._pending_kpi_completion: dict[str, str] | None = None
        self._latest_dashboard_summary = None
        self._latest_recommendation = None
        self._add_employee_opened_at = monotonic()
        self._urgent_filter_started_at = monotonic()
        self._sender_email_attempt_count = 0
        self.keyboard_session = KeyboardPathSession(
            logger=self.metrics_logger,
            flow_id="onboarding_primary",
            screen_id="main_dashboard",
        )

        self._global_validation_var = tk.StringVar(value="")
        self._build_layout()
        self.refresh_employee_list()
        self._apply_launch_context()

    def _build_layout(self) -> None:
        apply_professional_ops_theme(self.root)
        self.shell = TwoPaneShell(self.root)

        # ---------------- LEFT PANEL ----------------
        self._build_reminder_health_banner(self.shell.left)
        self._build_today_dashboard(self.shell.left)
        self._build_action_sections(self.shell.left)

        ttk.Separator(self.shell.left, orient="horizontal").pack(fill="x", pady=(2, 8))
        ttk.Label(self.shell.left, text="Employees").pack(anchor="w")

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_employee_list())

        self.search_entry = ttk.Entry(self.shell.left, textvariable=self.search_var)
        self.search_entry.pack(fill="x", pady=(4, 6))

        self.employee_list = tk.Listbox(
            self.shell.left,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            selectbackground=COLORS["primary"],
            selectforeground="#ffffff",
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self.employee_list.pack(fill="both", expand=True)
        self.employee_list.bind("<<ListboxSelect>>", self._on_select_employee)
        self._install_focus_visible_behavior(self.employee_list)
        self.keyboard_session.bind(self.employee_list)
        self.keyboard_session.bind(self.search_entry)

        legend_text = " • ".join([
            "⚠ Overdue",
            "● Due today",
            "◔ Due within 3 days",
            "○ Upcoming",
            "✓ Completed",
        ])
        ttk.Label(self.shell.left, text=legend_text, foreground="#475569", wraplength=280).pack(fill="x", pady=(0, 6))

        # ---------------- RIGHT PANEL ----------------
        ttk.Label(self.shell.right, text="Tasks for selected employee")\
            .grid(row=0, column=0, sticky="w")

        self.task_canvas = tk.Canvas(self.shell.right, bg=COLORS["app_bg"], highlightthickness=0)
        self.task_canvas.grid(row=1, column=0, sticky="nsew")

        self.shell.right.rowconfigure(1, weight=1)
        self.shell.right.columnconfigure(0, weight=1)

        self.filter_bar = ttk.Frame(self.shell.right)
        self.filter_bar.grid(row=2, column=0, sticky="ew", pady=(6, 4))
        self._build_task_filter_bar()

        self.task_frame = ttk.Frame(self.task_canvas)

        self.task_window = self.task_canvas.create_window(
            (0, 0), window=self.task_frame, anchor="nw"
        )

        self.task_frame.bind("<Configure>", self._sync_canvas)
        self.task_canvas.bind("<Configure>", self._on_canvas_resize)

    def _log_onboarding_canonical_event(self, event_name: str, **fields: object) -> None:
        logger = getattr(self, "metrics_logger", None)
        if logger is None:
            return
        logger.log_onboarding_canonical_event(event_name, **fields)

    def _build_action_sections(self, parent: ttk.Frame) -> None:
        style = ttk.Style(self.root)
        style.configure("ActionPrimary.TButton", padding=(10, 8), font=("TkDefaultFont", 10, "bold"))
        style.configure("ActionSecondary.TButton", padding=(8, 4), font=("TkDefaultFont", 9))
        style.map("ActionPrimary.TButton", relief=[("focus", "solid")])
        style.map("ActionSecondary.TButton", relief=[("focus", "solid")])

        container = ttk.LabelFrame(parent, text="Actions", padding=8)
        container.pack(fill="x", pady=(0, 8), anchor="w")

        self.global_validation_label = ttk.Label(
            container,
            textvariable=self._global_validation_var,
            foreground="#b91c1c",
            wraplength=280,
            justify="left",
        )
        self.global_validation_label.pack(fill="x", pady=(0, 6), anchor="w")

        for section_spec in onboarding_action_sections():
            section = ttk.Frame(container)
            section.pack(fill="x", pady=(0, 10), anchor="w")

            header = ttk.Label(section, text=section_spec.title)
            header.pack(fill="x", anchor="w")

            helper = ttk.Label(section, text=section_spec.helper_text, foreground="#475569", wraplength=280)
            helper.pack(fill="x", pady=(0, 6), anchor="w")

            for action_spec in section_spec.actions:
                command = self._resolve_action_command(action_spec.command_name)
                if action_spec.metrics_key is None:
                    action_command = command
                else:
                    action_command = lambda c=command, key=action_spec.metrics_key: self._run_secondary_action(c, key)
                style_name = "ActionPrimary.TButton" if action_spec.emphasis == "primary" else "ActionSecondary.TButton"
                button = ttk.Button(
                    section,
                    text=action_spec.label,
                    command=action_command,
                    style=style_name,
                    takefocus=True,
                )
                button.pack(fill="x", pady=(0, 4))
                self.keyboard_session.bind(button)

                if action_spec.shortcut_hint:
                    hint = ttk.Label(section, text=action_spec.shortcut_hint, foreground="#64748b", wraplength=280)
                    hint.pack(fill="x", pady=(0, 4), anchor="w")

                if action_spec.helper_text:
                    detail = ttk.Label(section, text=action_spec.helper_text, foreground="#334155", wraplength=280)
                    detail.pack(fill="x", pady=(0, 6), anchor="w")

    def _set_global_validation_message(self, *, message: str, severity: str = VALIDATION_SEVERITY_ERROR) -> None:
        color = "#991b1b" if severity == VALIDATION_SEVERITY_BLOCKING else "#b91c1c"
        self.global_validation_label.configure(foreground=color)
        self._global_validation_var.set(message.strip())

    def _clear_global_validation_message(self) -> None:
        self._global_validation_var.set("")

    def _resolve_action_command(self, command_name: str) -> Callable[[], None]:
        command = getattr(self, command_name)
        if not callable(command):
            raise ValueError(f"Unsupported action command: {command_name}")
        return command

    def _on_primary_reminder_cta_click(self) -> None:
        self.metrics_logger.log_ux_click(app="onboarding", surface="actions_panel", target="run_reminders_now")
        self.run_reminders_now()

    def _run_reminders_dry_run_from_ui(self) -> None:
        self._run_secondary_action(self.run_reminders_dry_run, "run_reminders_dry_run")

    def _run_secondary_action(self, command: Callable[[], None], action_key: str) -> None:
        self.metrics_logger.log_ux_click(app="onboarding", surface="actions_panel", target=action_key)
        command()

    def _build_reminder_health_banner(self, parent: ttk.Frame) -> None:
        self.reminder_banner_frame = tk.Frame(parent, bd=1, relief="solid")
        self.reminder_banner_label = tk.Label(
            self.reminder_banner_frame,
            justify="left",
            anchor="w",
            wraplength=280,
            padx=8,
            pady=6,
        )
        self.reminder_banner_label.pack(fill="x")
        self._refresh_reminder_health_banner()

    def _refresh_reminder_health_banner(self) -> None:
        health = evaluate_onboarding_reminder_health(
            self.state.last_reminder_run_at,
            self.state.scheduler_settings,
        )
        if health.severity == "healthy":
            self.reminder_banner_frame.pack_forget()
            self._refresh_today_dashboard()
            return

        self.reminder_banner_label.configure(text=self._reminder_banner_text(health), **self._reminder_banner_colors(health.severity))
        self.reminder_banner_frame.configure(bg=self.reminder_banner_label.cget("bg"), highlightthickness=1, highlightbackground=self.reminder_banner_label.cget("fg"))
        self.reminder_banner_frame.pack(fill="x", pady=(0, 8), anchor="w")
        self._refresh_today_dashboard()

    def _build_today_dashboard(self, parent: ttk.Frame) -> None:
        self.today_dashboard_box = ttk.LabelFrame(parent, text="Today Dashboard", padding=6)
        self.today_dashboard_box.pack(fill="x", pady=(0, 8), anchor="w")
        self.today_kpi_buttons: dict[str, tk.Button] = {}
        self.today_kpi_labels: dict[str, tk.StringVar] = {}

        kpi_row = ttk.Frame(self.today_dashboard_box)
        kpi_row.pack(fill="x", pady=(0, 6))
        for key, label in (("overdue", "Overdue"), ("due_today", "Due Today"), ("urgent", "Urgent"), ("pending", "Pending")):
            label_var = tk.StringVar(value=f"{label}: 0")
            button = tk.Button(
                kpi_row,
                textvariable=label_var,
                command=lambda selected=key: self._on_dashboard_kpi_click(selected),
                padx=8,
                pady=4,
                takefocus=True,
            )
            configure_plain_button(button, font_size=10)
            button.pack(side="left", padx=(0, 6))
            button.bind("<Return>", lambda _event, widget=button, metric_key=key: self._invoke_button_from_keyboard(widget, "today_dashboard", metric_key))
            button.bind("<space>", lambda _event, widget=button, metric_key=key: self._invoke_button_from_keyboard(widget, "today_dashboard", metric_key))
            self._install_focus_visible_behavior(button)
            self.today_kpi_buttons[key] = button
            self.today_kpi_labels[key] = label_var

        self.today_dashboard_status_label = ttk.Label(self.today_dashboard_box, text="", justify="left", wraplength=280, foreground="#334155")
        self.today_dashboard_status_label.pack(anchor="w", pady=(0, 4))

        self.today_recommended_banner = tk.Frame(self.today_dashboard_box, bg=COLORS["surface_alt"], highlightthickness=1, highlightbackground=COLORS["primary"])
        self.today_recommended_banner.pack(fill="x", pady=(2, 6))
        self.today_recommended_banner_label = tk.Label(
            self.today_recommended_banner,
            text="",
            justify="left",
            anchor="w",
            bg=COLORS["surface_alt"],
            fg=COLORS["primary_dark"],
            wraplength=220,
            padx=8,
            pady=6,
        )
        self.today_recommended_banner_label.pack(side="left", fill="x", expand=True)
        self.today_recommended_banner_button = tk.Button(
            self.today_recommended_banner,
            text="",
            command=self._on_recommended_action_click,
            padx=8,
            pady=4,
            takefocus=True,
        )
        configure_plain_button(self.today_recommended_banner_button, role="primary", font_size=10)
        self.today_recommended_banner_button.pack(side="right", padx=6, pady=6)
        self.today_recommended_banner_button.bind("<Return>", lambda _event: self._invoke_button_from_keyboard(self.today_recommended_banner_button, "today_dashboard", "recommended_action"))
        self.today_recommended_banner_button.bind("<space>", lambda _event: self._invoke_button_from_keyboard(self.today_recommended_banner_button, "today_dashboard", "recommended_action"))
        self._install_focus_visible_behavior(self.today_recommended_banner_button)

        action_row = ttk.Frame(self.today_dashboard_box)
        action_row.pack(fill="x", pady=(2, 0))
        ttk.Button(action_row, text="Start Interview", command=self._open_interview_app).pack(side="left")
        ttk.Button(action_row, text="Next Critical", command=self._open_next_critical_from_dashboard).pack(side="left", padx=(6, 0))
        ttk.Button(action_row, text="Run Reminders", command=self.run_reminders_now).pack(side="left", padx=(6, 0))
        self._refresh_today_dashboard()

    def _refresh_today_dashboard(self) -> None:
        if not hasattr(self, "today_dashboard_status_label"):
            return
        summary = build_dashboard_today_summary(
            history_rows=[],
            employees=self.state.employees,
            scheduler_settings=self.state.scheduler_settings,
            today=date.today(),
        )
        self._latest_dashboard_summary = summary
        chips = build_dashboard_kpi_chips(summary, self.state.employees, date.today())
        for chip in chips:
            self._configure_today_kpi(chip.key, chip.count, chip.label, chip.filter_key)

        recommendation = build_recommended_action(summary, self.state.employees, date.today())
        self._latest_recommendation = recommendation
        self.today_recommended_banner_label.configure(text=f"Recommended next action: {recommendation.message}")
        self.today_recommended_banner_button.configure(text=recommendation.button_label)

    def _configure_today_kpi(self, kpi_key: str, count: int, label: str, filter_key: str) -> None:
        button = self.today_kpi_buttons[kpi_key]
        selected = self._active_dashboard_kpi == kpi_key
        if count <= 0:
            self.today_kpi_labels[kpi_key].set(f"{label}: 0")
            button.configure(state="disabled")
            if selected:
                self._active_dashboard_kpi = None
            self.today_dashboard_status_label.configure(text=f"{label} has no matching tasks right now.")
            return

        selected_text = " • selected" if selected else ""
        self.today_kpi_labels[kpi_key].set(f"{label}: {count}{selected_text}")
        button.configure(state="normal")
        if selected:
            self.today_dashboard_status_label.configure(text=f"{label} selected. Task filter is '{filter_key}'.")

    def _on_dashboard_kpi_click(self, kpi_key: str) -> None:
        summary = self._latest_dashboard_summary
        if summary is None:
            return

        chips = build_dashboard_kpi_chips(summary, self.state.employees, date.today())
        count = 0
        for chip in chips:
            if chip.key == kpi_key:
                count = chip.count
                break
        if count <= 0:
            self.today_dashboard_status_label.configure(text="No tasks available for this KPI yet.")
            return

        self.metrics_logger.log_ux_click(app="onboarding", surface="today_dashboard", target="kpi_click", kpi=kpi_key, count=count)
        navigation = self._navigate_from_kpi(kpi_key)
        if not navigation:
            self.today_dashboard_status_label.configure(text="Couldn't find a matching task. Try refreshing employee data.")
            return

        self._active_dashboard_kpi = kpi_key
        self._pending_kpi_completion = navigation
        self.today_dashboard_status_label.configure(text=f"Opened {navigation['employee_name']} and filtered matching tasks.")
        self.metrics_logger.log_ux_completion(
            app="onboarding",
            surface="today_dashboard",
            outcome="navigated",
            kpi=kpi_key,
            stage="navigated",
            employee_id=navigation["employee_id"],
            task_id=navigation["task_id"],
        )
        self._refresh_today_dashboard()

    def _navigate_from_kpi(self, kpi_key: str) -> dict[str, str] | None:
        target = kpi_navigation_target(self.state.employees, date.today(), kpi_key)
        if target is None:
            return None

        self._select_employee_by_id(target["employee_id"])
        self._set_task_filter(target["filter_key"])
        return target

    def _on_recommended_action_click(self) -> None:
        recommendation = getattr(self, "_latest_recommendation", None)
        if recommendation is None:
            return
        self.metrics_logger.log_ux_click(
            app="onboarding",
            surface="today_dashboard",
            target="recommended_action",
            action=recommendation.action_key,
        )
        if recommendation.action_key == "start_interview":
            self._open_interview_app()
            return
        if recommendation.employee_id is None or recommendation.filter_key is None:
            return
        self._select_employee_by_id(recommendation.employee_id)
        self._set_task_filter(recommendation.filter_key)
        self.today_dashboard_status_label.configure(
            text=f"Opened {recommendation.employee_name} with '{recommendation.filter_key}' tasks."
        )

    def _open_interview_app(self) -> None:
        interview_path = Path(__file__).with_name("interview_app.pyw")
        if not interview_path.exists():
            self._set_global_validation_message(
                message="Interview app could not be located. Verify your installation path, then retry.",
                severity=VALIDATION_SEVERITY_BLOCKING,
            )
            if should_display_modal(severity=VALIDATION_SEVERITY_BLOCKING):
                messagebox.showerror("Interview app missing", f"Could not find interview_app.pyw at:\n{interview_path}")
            return
        launch_error = self._launch_interview_process(interview_path)
        if launch_error is None:
            self._clear_global_validation_message()
            return
        self._set_global_validation_message(
            message="Interview app did not start. Review local setup guidance and retry.",
            severity=VALIDATION_SEVERITY_BLOCKING,
        )
        if should_display_modal(severity=VALIDATION_SEVERITY_BLOCKING):
            messagebox.showerror("Interview app failed to start", launch_error)

    def _launch_interview_process(self, interview_path: Path) -> str | None:
        app_root = interview_path.parent.parent
        log_path = self._interview_launch_log_path(app_root)
        try:
            process = subprocess.Popen(
                [sys.executable, str(interview_path)],
                cwd=app_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            diagnostics = self._launch_diagnostics_text(
                interview_path=interview_path,
                app_root=app_root,
                return_code=None,
                stdout_text="",
                stderr_text=f"{type(exc).__name__}: {exc}",
            )
            self._write_interview_launch_log(log_path, diagnostics)
            return self._format_launch_error(log_path)

        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return None

        stdout_text, stderr_text = process.communicate()
        diagnostics = self._launch_diagnostics_text(
            interview_path=interview_path,
            app_root=app_root,
            return_code=process.returncode,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
        )
        self._write_interview_launch_log(log_path, diagnostics)
        return self._format_launch_error(log_path)

    def _interview_launch_log_path(self, app_root: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return app_root / "logs" / f"interview-launch-{stamp}.log"

    def _write_interview_launch_log(self, log_path: Path, diagnostics: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(diagnostics, encoding="utf-8")

    def _launch_diagnostics_text(
        self,
        interview_path: Path,
        app_root: Path,
        return_code: int | None,
        stdout_text: str,
        stderr_text: str,
    ) -> str:
        return "\n".join([
            f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
            f"python: {sys.executable}",
            f"script: {interview_path}",
            f"cwd: {app_root}",
            f"return_code: {return_code}",
            "--- stdout ---",
            stdout_text.strip() or "<empty>",
            "--- stderr ---",
            stderr_text.strip() or "<empty>",
        ])

    def _format_launch_error(self, log_path: Path) -> str:
        return (
            "The interview app closed immediately after launch. "
            "Please verify your local setup and then retry.\n\n"
            f"Diagnostic log: {log_path}"
        )

    def _open_next_critical_from_dashboard(self) -> None:
        summary = build_dashboard_today_summary(
            history_rows=[],
            employees=self.state.employees,
            scheduler_settings=self.state.scheduler_settings,
            today=date.today(),
        )
        next_item = summary.onboarding.next_critical
        if next_item is None:
            messagebox.showinfo("Onboarding", "There are no critical onboarding tasks due right now.")
            return
        self._select_employee_by_id(next_item.employee_id)
        self._set_task_filter("urgent")

    @staticmethod
    def _reminder_banner_text(health) -> str:
        if health.hours_or_days_late is None:
            return health.message
        return f"{health.message} (lateness value: {health.hours_or_days_late})"

    @staticmethod
    def _reminder_banner_colors(severity: str) -> dict[str, str]:
        if severity == "overdue":
            return {"fg": "#991B1B", "bg": "#FEF2F2"}
        return {"fg": "#92400E", "bg": "#FFFBEB"}


    def _apply_launch_context(self) -> None:
        if bool(self.launch_context.get("urgent_only", False)):
            self._set_task_filter("urgent")

        employee_id = str(self.launch_context.get("employee_id") or "").strip()
        if not employee_id:
            return

        self._select_employee_by_id(employee_id)

    def _select_employee_by_id(self, employee_id: str) -> None:
        employee = self._employee_by_id(employee_id)
        if not employee:
            return

        label_match = self._label_for_employee(employee)
        if label_match is None:
            return

        self.employee_list.selection_clear(0, tk.END)
        self.employee_list.selection_set(label_match)
        self.employee_list.see(label_match)
        self.selected_employee_id = employee.id
        self.render_tasks_for_selected(employee)

    def _label_for_employee(self, employee: Employee) -> int | None:
        for idx, candidate in enumerate(self._filtered_employees):
            if candidate.id == employee.id:
                return idx
        return None

    def _build_task_filter_bar(self) -> None:
        ttk.Label(self.filter_bar, text="Filter:").pack(side="left", padx=(0, 6))
        for option in TASK_FILTER_OPTIONS:
            text = f"{option.label} ({option.hotkey})"
            button = ttk.Radiobutton(
                self.filter_bar,
                text=text,
                value=option.key,
                variable=self.task_filter_var,
                command=lambda key=option.key: self._set_task_filter(key, source="radio"),
            )
            button.pack(side="left", padx=(0, 4))
            self.root.bind_all(
                f"<Alt-Key-{option.hotkey}>",
                lambda _event, key=option.key: self._set_task_filter(key, source="hotkey"),
            )

        ttk.Checkbutton(
            self.filter_bar,
            text="Compact rows",
            variable=self.compact_mode_var,
            command=self._toggle_compact_mode,
        ).pack(side="right", padx=(8, 0))

    def _set_task_filter(self, filter_key: str, source: str = "programmatic") -> None:
        previous_filter = self.task_filter_var.get()
        self.task_filter_var.set(filter_key)
        if previous_filter != filter_key:
            self.metrics_logger.log_ux_view(
                app="onboarding",
                surface="task_filter",
                target=filter_key,
                source=source,
                previous_filter=previous_filter,
            )
        self._refresh_selected_tasks()
        if filter_key == "urgent":
            employee = self._employee_by_id(self.selected_employee_id)
            visible_count = urgent_filter_result_count(employee.tasks, date.today()) if employee else 0
            self.metrics_logger.log_onboarding_canonical_event(
                "ux.onboarding.urgent_filter.click",
                time_to_filter_ms=int(max(0, (monotonic() - self._urgent_filter_started_at) * 1000)),
                result_count=visible_count,
            )
        self._urgent_filter_started_at = monotonic()

    def _toggle_compact_mode(self) -> None:
        compact_mode = self.compact_mode_var.get()
        self.metrics_logger.log_ux_click(app="onboarding", surface="task_list", target="compact_mode_toggle", compact_mode=compact_mode)
        self._refresh_selected_tasks()

    def _refresh_selected_tasks(self) -> None:
        employee = self._employee_by_id(self.selected_employee_id)
        self.render_tasks_for_selected(employee)

    def _sync_canvas(self, _event: tk.Event) -> None:
        self.task_canvas.configure(scrollregion=self.task_canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.task_canvas.itemconfigure(self.task_window, width=event.width)

    def _bind_task_widget_visibility(self, widget: tk.Misc) -> None:
        widget.bind(
            "<FocusIn>",
            lambda event: scroll_widget_into_view(self.task_canvas, event.widget),
            add="+",
        )

    def change_storage_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose Dropbox folder for onboarding files")
        if not selected:
            return
        self.storage_dir = Path(selected)
        self.store = JsonStore(self.storage_dir)
        self.state = self.store.load()
        self.metrics_logger = UxMetricsLogger(self.storage_dir)
        self._refresh_reminder_health_banner()
        self.refresh_employee_list()
        messagebox.showinfo("Folder updated", f"Using folder:\n{self.storage_dir}")

    def refresh_employee_list(self) -> None:
        self.employee_list.delete(0, tk.END)

        query = self.search_var.get().lower().strip()

        self._filtered_employees = []
        self._employee_lookup_by_label.clear()
        today = date.today()

        for employee in self.state.employees:
            summary = self._employee_summary_label(employee, today)
            label = f"{employee.name} ({employee.school or 'No school'}) | start {employee.start_date}{summary}"

            if query and query not in label.lower():
                continue

            self._filtered_employees.append(employee)
            self.employee_list.insert(tk.END, label)
            self._employee_lookup_by_label[label] = employee

        self.render_tasks_for_selected(None)
        self._log_overdue_events()
        self._refresh_today_dashboard()
    
    def _on_select_employee(self, _event: tk.Event) -> None:
        index = self.employee_list.curselection()
        if not index:
            self.render_tasks_for_selected(None)
            self._refresh_today_dashboard()
            return
        label = self.employee_list.get(index[0])
        employee = self._employee_lookup_by_label.get(label)
        if not employee:
            self.render_tasks_for_selected(None)
            self._refresh_today_dashboard()
            return
        self.selected_employee_id = employee.id
        self.render_tasks_for_selected(employee)
        self._refresh_today_dashboard()

    def render_tasks_for_selected(self, employee: Employee | None) -> None:
        for widget in self.task_frame.winfo_children():
            widget.destroy()
        self.task_vars.clear()
        if not employee:
            ttk.Label(self.task_frame, text="Select an employee to view tasks.").grid(row=0, column=0, sticky="w")
            return

        ttk.Label(
            self.task_frame,
            text="Tip: Prioritize Overdue and Due today badges first to keep onboarding on time.",
            foreground="#475569",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        today = date.today()
        active_filter = self.task_filter_var.get()
        compact_mode = self.compact_mode_var.get()
        visible_tasks = filtered_tasks(employee.tasks, today, active_filter)
        if not visible_tasks:
            ttk.Label(self.task_frame, text="No tasks match this filter.").grid(row=1, column=0, sticky="w")
            return

        for idx, task in enumerate(visible_tasks, start=1):
            var = tk.BooleanVar(value=task.completed)
            self.task_vars[task.id] = var
            status = task_status(task, today)
            due = format_due_date_short(task.due_date)
            countdown = self._task_countdown_text(task, today)
            deadline_text = task.deadline_label or ""

            row = ttk.Frame(self.task_frame)
            row.grid(row=idx, column=0, sticky="ew", pady=2)
            row.columnconfigure(1, weight=1)

            badge_style = TASK_STATUS_BADGE_STYLE.get(status, {"bg": "#334155", "fg": "#FFFFFF"})
            badge = tk.Label(
                row,
                text=task_status_badge_text(status),
                bg=badge_style["bg"],
                fg=badge_style["fg"],
                padx=8,
                pady=2,
            )
            badge.grid(row=0, column=0, sticky="w", padx=(0, 8))

            task_prefix = "⚠ CRITICAL " if task.critical else ""
            title_wrap = self._task_title_wraplength(compact_mode)
            checkbox = tk.Checkbutton(
                row,
                text=f"{task_prefix}{task.title}",
                variable=var,
                selectcolor="#FFFFFF",
                command=lambda t=task, v=var: self._toggle_task(t, v),
                justify="left",
                anchor="w",
                wraplength=title_wrap,
                takefocus=True,
            )
            checkbox.grid(row=0, column=1, sticky="ew")
            self._install_focus_visible_behavior(checkbox)

            suffix_parts = [f"Due {due}"]
            if deadline_text:
                suffix_parts.append(deadline_text)
            if countdown:
                suffix_parts.append(countdown)
            due_label = ttk.Label(row, text=" • ".join(suffix_parts))
            due_label_column = 2
            due_label_row = 0
            due_label_sticky = "e"
            if not compact_mode:
                due_label_column = 1
                due_label_row = 1
                due_label_sticky = "w"
            due_label.grid(row=due_label_row, column=due_label_column, sticky=due_label_sticky, padx=(8, 0))

    def _task_title_wraplength(self, compact_mode: bool) -> int:
        canvas_width = self.task_canvas.winfo_width()
        if canvas_width <= 1:
            return 520 if compact_mode else 640
        wrap_offset = 240 if compact_mode else 120
        return max(320, canvas_width - wrap_offset)


    def _invoke_button_from_keyboard(self, button: tk.Widget, surface: str, target: str) -> str:
        self.keyboard_session.mark_step()
        self.metrics_logger.log_ux_completion(
            app="onboarding",
            surface=surface,
            outcome="keyboard_only_success",
            target=target,
            input_method="keyboard",
        )
        self.keyboard_session.complete(
            abandoned=False,
            screen_id=f"{surface}_{target}",
        )
        button.invoke()
        return "break"

    @staticmethod
    def _install_focus_visible_behavior(widget: tk.Widget) -> None:
        widget.configure(highlightthickness=0, highlightbackground=COLORS["focus"], highlightcolor=COLORS["focus"])
        widget.bind("<FocusIn>", lambda _event: widget.configure(highlightthickness=2), add="+")
        widget.bind("<FocusOut>", lambda _event: widget.configure(highlightthickness=0), add="+")

    def _task_countdown_text(self, task, today: date) -> str:
        if not task.due_date or task.completed:
            return ""
        due_date = parse_date(task.due_date)
        day_delta = (due_date - today).days
        if day_delta > 0:
            noun = "day" if day_delta == 1 else "days"
            return f"Due in {day_delta} {noun}"
        if day_delta < 0:
            overdue_days = abs(day_delta)
            noun = "day" if overdue_days == 1 else "days"
            return f"Overdue by {overdue_days} {noun}"
        return "Due today"

    def _toggle_task(self, task, var: tk.BooleanVar) -> None:
        apply_task_completion(task, var.get(), date.today())
        if var.get():
            self.metrics_logger.log_event(
                EVENT_TASK_COMPLETED,
                task_id=task.id,
                task_type=task.template_id or task.title,
                employee_id=self.selected_employee_id,
                due_date=task.due_date,
                completed_at=task.completed_at,
            )
            self._log_kpi_completion_if_applicable(task)
            self._emit_onboarding_task_completed(task)
        self.save_state()
        employee = self._employee_by_id(self.selected_employee_id)
        self.render_tasks_for_selected(employee)
        self.refresh_employee_list()

    def _log_kpi_completion_if_applicable(self, task) -> None:
        context = self._pending_kpi_completion
        if context is None:
            return
        if context["task_id"] != task.id:
            return

        self.metrics_logger.log_ux_completion(
            app="onboarding",
            surface="today_dashboard",
            outcome="task_completed",
            kpi=self._active_dashboard_kpi or "unknown",
            stage="task_completed",
            employee_id=context["employee_id"],
            task_id=context["task_id"],
        )
        self._pending_kpi_completion = None

    def _emit_onboarding_task_completed(self, task) -> None:
        employee = self._employee_by_id(self.selected_employee_id)
        if employee is None:
            return
        payload = {
            "employee_name": employee.name,
            "school": employee.school,
            "task_title": task.title,
            "task_id": task.id,
            "completed_at": task.completed_at or "",
        }
        try:
            notification_service_from_onboarding(root_dir=self.storage_dir).emit_event(
                "onboarding.task.completed",
                payload,
                f"{employee.id}:{task.id}:onboarding.task.completed:{task.completed_at or ''}",
            )
        except Exception:
            return

    def _employee_by_id(self, employee_id: str | None) -> Employee | None:
        if not employee_id:
            return None
        for employee in self.state.employees:
            if employee.id == employee_id:
                return employee
        return None

    def _employee_summary_label(self, employee: Employee, today: date) -> str:
        overview = build_onboarding_overview([employee], today)
        if not overview.employee_summaries:
            return ""
        summary = overview.employee_summaries[0]
        parts: list[str] = []
        if summary.overdue:
            parts.append(f"{summary.overdue} overdue")
        if summary.critical_overdue:
            parts.append(f"{summary.critical_overdue} critical overdue")
        if summary.due_today:
            parts.append(f"{summary.due_today} today")
        if summary.due_soon:
            parts.append(f"{summary.due_soon} soon")
        if not parts:
            return ""
        return f" | {' / '.join(parts)}"

    def open_add_employee_dialog(self) -> None:
        self.metrics_logger.log_onboarding_canonical_event(
            "ux.onboarding.add_employee_form.view",
            entry_point="actions_panel",
            time_from_screen_open_ms=int(max(0, (monotonic() - self._add_employee_opened_at) * 1000)),
        )
        self._add_employee_opened_at = monotonic()
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Employee")
        fields: dict[str, tk.Entry] = {}
        specs = [
            ("Name", ""),
            ("School", ""),
            ("Acceptance date (YYYY-MM-DD)", date.today().strftime("%Y-%m-%d")),
            ("Start date (YYYY-MM-DD)", date.today().strftime("%Y-%m-%d")),
        ]
        for row, (label, default) in enumerate(specs):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            entry = ttk.Entry(dialog, width=30)
            entry.insert(0, default)
            entry.grid(row=row, column=1, padx=8, pady=4)
            fields[label] = entry

        inline_validation = create_inline_validation_message_grid(dialog, row=4, column=0, columnspan=2)
        cancel_action = lambda: dialog.destroy()
        button_bar = ttk.Frame(dialog)
        button_bar.grid(row=5, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        button_bar.columnconfigure(0, weight=1)
        ttk.Button(
            button_bar,
            text="Cancel",
            command=cancel_action,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            button_bar,
            text="Save",
            command=lambda: self._save_employee(dialog, fields, inline_validation),
        ).grid(row=0, column=1, sticky="e")
        self._prepare_modal_dialog(dialog, first_widget=fields["Name"], on_close=cancel_action)

    def _save_employee(
        self,
        dialog: tk.Toplevel,
        fields: dict[str, tk.Entry],
        inline_validation,
    ) -> None:
        inline_validation.clear()
        name = fields["Name"].get().strip()
        school = fields["School"].get().strip()
        acceptance = fields["Acceptance date (YYYY-MM-DD)"].get().strip()
        start = fields["Start date (YYYY-MM-DD)"].get().strip()
        if not name:
            self._log_onboarding_canonical_event(
                "ux.onboarding.add_employee_form.validation_error",
                error_type="missing_name",
                required_fields_missing_count=1,
            )
            show_inline_field_error(
                inline_validation,
                field_label="Name",
                cause="a value is required.",
                corrective_action="Enter a name, then save again.",
                focus_widget=fields["Name"],
            )
            return

        if not self._is_valid_date(acceptance):
            self._log_onboarding_canonical_event(
                "ux.onboarding.add_employee_form.validation_error",
                error_type="invalid_acceptance_date",
                required_fields_missing_count=0,
            )
            show_inline_field_error(
                inline_validation,
                field_label="Acceptance date",
                cause="the date format is invalid.",
                corrective_action="Use YYYY-MM-DD, then save again.",
                focus_widget=fields["Acceptance date (YYYY-MM-DD)"],
            )
            return

        if not self._is_valid_date(start):
            self._log_onboarding_canonical_event(
                "ux.onboarding.add_employee_form.validation_error",
                error_type="invalid_start_date",
                required_fields_missing_count=0,
            )
            show_inline_field_error(
                inline_validation,
                field_label="Start date",
                cause="the date format is invalid.",
                corrective_action="Use YYYY-MM-DD, then save again.",
                focus_widget=fields["Start date (YYYY-MM-DD)"],
            )
            return

        employee = Employee(id=make_id("emp"), name=name, acceptance_date=acceptance, start_date=start, school=school)
        seed_employee_tasks(employee, self.state.templates)
        self.state.employees.append(employee)
        self._log_task_created_events(employee)
        self.save_state()
        self.refresh_employee_list()
        dialog.destroy()

    def open_custom_template_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Custom Task Template")
        vars_map = {
            "title": tk.StringVar(value=""),
            "reference": tk.StringVar(value="start_date"),
            "offset": tk.StringVar(value="0"),
            "cadence": tk.StringVar(value="daily"),
            "interval": tk.StringVar(value="1"),
        }

        ttk.Label(dialog, text="Task title").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        title_entry = ttk.Entry(dialog, textvariable=vars_map["title"], width=36)
        title_entry.grid(row=0, column=1, padx=8, pady=4)
        ttk.Label(dialog, text="Reference").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        reference_input = ttk.Combobox(
            dialog,
            textvariable=vars_map["reference"],
            values=["start_date", "acceptance_date", "monthly", "specific_date"],
            state="readonly",
        )
        reference_input.grid(row=1, column=1, padx=8, pady=4)
        ttk.Label(dialog, text="Specific date").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        specific_date_picker = DateEntry(dialog, date_pattern="yyyy-mm-dd", width=16)
        specific_date_picker.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(dialog, text="Offset days (+/- integer)").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        offset_entry = ttk.Entry(dialog, textvariable=vars_map["offset"], width=12)
        offset_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(dialog, text="Cadence").grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(dialog, textvariable=vars_map["cadence"], values=["once", "daily", "weekly", "custom", "monthly"], state="readonly").grid(row=4, column=1, padx=8, pady=4)
        ttk.Label(dialog, text="Interval days").grid(row=5, column=0, sticky="w", padx=8, pady=4)
        interval_entry = ttk.Entry(dialog, textvariable=vars_map["interval"], width=12)
        interval_entry.grid(row=5, column=1, sticky="w", padx=8, pady=4)

        self._toggle_specific_date_picker(reference_input, specific_date_picker)
        reference_input.bind(
            "<<ComboboxSelected>>",
            lambda *_: self._toggle_specific_date_picker(reference_input, specific_date_picker),
        )

        inline_validation = create_inline_validation_message_grid(dialog, row=6, column=0, columnspan=2)
        controls = {"title": title_entry, "offset": offset_entry, "interval": interval_entry}
        cancel_action = lambda: dialog.destroy()
        button_bar = ttk.Frame(dialog)
        button_bar.grid(row=7, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        button_bar.columnconfigure(0, weight=1)
        ttk.Button(button_bar, text="Cancel", command=cancel_action).grid(row=0, column=0, sticky="w")
        ttk.Button(
            button_bar,
            text="Add template",
            command=lambda: self._save_custom_template(dialog, vars_map, specific_date_picker, inline_validation, controls),
        ).grid(row=0, column=1, sticky="e")
        self._prepare_modal_dialog(dialog, first_widget=title_entry, on_close=cancel_action)

    @staticmethod
    def _toggle_specific_date_picker(reference_input: ttk.Combobox, specific_date_picker: DateEntry) -> None:
        is_specific_date = str(reference_input.get()).strip() == "specific_date"
        picker_state = "normal" if is_specific_date else "disabled"
        specific_date_picker.configure(state=picker_state)

    def _save_custom_template(
        self,
        dialog: tk.Toplevel,
        vars_map: dict[str, tk.StringVar],
        specific_date_picker: DateEntry,
        inline_validation,
        controls: dict[str, tk.Widget],
    ) -> None:
        inline_validation.clear()
        title = vars_map["title"].get().strip()
        if not title:
            show_inline_field_error(
                inline_validation,
                field_label="Task title",
                cause="a value is required.",
                corrective_action="Enter a task title, then add the template again.",
                focus_widget=controls["title"],
            )
            return

        try:
            offset = int(vars_map["offset"].get().strip())
            interval = int(vars_map["interval"].get().strip())
        except ValueError:
            show_inline_field_error(
                inline_validation,
                field_label="Offset/interval",
                cause="one or more values are not valid integers.",
                corrective_action="Enter whole numbers for offset and interval, then add the template again.",
                focus_widget=controls["offset"],
            )
            return

        reference = vars_map["reference"].get().strip()
        if reference == "specific_date":
            reference = build_specific_date_reference(specific_date_picker.get_date())

        template = TaskTemplate.from_dict(
            {
                "id": make_id("tmpl"),
                "title": title,
                "reference": reference,
                "offset_days": offset,
                "cadence": {
                    "mode": vars_map["cadence"].get().strip(),
                    "interval_days": max(1, interval),
                },
            }
        )
        self.state.templates.append(template)
        for employee in self.state.employees:
            before_ids = {item.id for item in employee.tasks}
            seed_employee_tasks(employee, [template])
            self._log_new_tasks_for_employee(employee, before_ids)
        self.save_state()
        self.refresh_employee_list()
        dialog.destroy()

    def open_email_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Email / Reminder Settings")
        email = self.state.email_settings
        scheduler_settings = dict(self.state.scheduler_settings)
        vars_map = {
            "sender_email": tk.StringVar(value=email.sender_email),
            "smtp_host": tk.StringVar(value=email.smtp_host),
            "smtp_port": tk.StringVar(value=str(email.smtp_port)),
            "smtp_username": tk.StringVar(value=email.smtp_username),
            "smtp_password": tk.StringVar(value=email.smtp_password),
            "use_tls": tk.BooleanVar(value=email.use_tls),
            "imap_or_pop_host": tk.StringVar(value=email.imap_or_pop_host),
            "imap_or_pop_port": tk.StringVar(value=str(email.imap_or_pop_port)),
            "director_and_owners": tk.StringVar(value=email.director_and_owners),
            "reminder_recipients": tk.StringVar(value=email.reminder_recipients),
            "reminder_subject_template": tk.StringVar(value=email.reminder_subject_template),
            "escalation_subject_template": tk.StringVar(value=email.escalation_subject_template),
            "scheduler_enabled": tk.BooleanVar(value=scheduler_opt_in(scheduler_settings)),
            "expected_interval_hours": tk.StringVar(value=str(scheduler_expected_interval_hours(scheduler_settings))),
            "critical_window_days": tk.StringVar(value=str(critical_window_days_from_settings(scheduler_settings))),
            "metrics_export_dir": tk.StringVar(value=str(scheduler_settings.get("metrics_export_dir") or (self.storage_dir / "exports"))),
        }
        content = ttk.Frame(dialog, padding=10)
        content.grid(row=0, column=0, sticky="nsew")
        dialog.columnconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        email_frame = ttk.LabelFrame(content, text="Email settings", padding=8)
        email_frame.grid(row=0, column=0, sticky="ew")
        template_widgets = self._build_email_settings_fields(email_frame, vars_map)

        scheduler_frame = ttk.LabelFrame(content, text="Local scheduler", padding=8)
        scheduler_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._build_scheduler_settings_fields(scheduler_frame, vars_map)

        inline_validation = create_inline_validation_message_grid(content, row=2, column=0, columnspan=1, padx=0)
        cancel_action = lambda: dialog.destroy()
        button_bar = ttk.Frame(content)
        button_bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        button_bar.columnconfigure(0, weight=1)
        ttk.Button(button_bar, text="Cancel", command=cancel_action).grid(row=0, column=0, sticky="w")
        ttk.Button(
            button_bar,
            text="Save",
            command=lambda: self._save_email_settings(dialog, vars_map, template_widgets, inline_validation),
        ).grid(row=0, column=1, sticky="e")
        first_widget = self._email_settings_controls.get("sender_email")
        self._prepare_modal_dialog(dialog, first_widget=first_widget, on_close=cancel_action)

    def _build_email_settings_fields(self, parent: ttk.LabelFrame, vars_map: dict[str, tk.Variable]) -> dict[str, tk.Text]:
        labels = [
            ("Sender email", "sender_email", False),
            ("SMTP host", "smtp_host", False),
            ("SMTP port", "smtp_port", False),
            ("SMTP username", "smtp_username", False),
            ("SMTP password", "smtp_password", True),
            ("IMAP/POP host", "imap_or_pop_host", False),
            ("IMAP/POP port", "imap_or_pop_port", False),
            ("Director + owners recipients (comma-separated)", "director_and_owners", False),
            ("General reminder recipients (comma-separated)", "reminder_recipients", False),
        ]
        self._recipient_warning_vars: dict[str, tk.StringVar] = {}
        self._email_settings_controls: dict[str, tk.Widget] = {}
        for row, (label, key, is_secret) in enumerate(labels):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            entry = ttk.Entry(parent, textvariable=vars_map[key], width=46)
            if is_secret:
                entry.configure(show="•")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self._email_settings_controls[key] = entry

        self._recipient_warning_vars["director_and_owners"] = tk.StringVar(value="")
        ttk.Label(
            parent,
            textvariable=self._recipient_warning_vars["director_and_owners"],
            foreground="#b91c1c",
            wraplength=420,
            justify="left",
        ).grid(row=7, column=1, sticky="w", pady=(0, 2))
        self._recipient_warning_vars["reminder_recipients"] = tk.StringVar(value="")
        ttk.Label(
            parent,
            textvariable=self._recipient_warning_vars["reminder_recipients"],
            foreground="#b91c1c",
            wraplength=420,
            justify="left",
        ).grid(row=8, column=1, sticky="w", pady=(0, 2))
        vars_map["director_and_owners"].trace_add("write", lambda *_: self._refresh_recipient_warnings(vars_map))
        vars_map["reminder_recipients"].trace_add("write", lambda *_: self._refresh_recipient_warnings(vars_map))
        self._refresh_recipient_warnings(vars_map)

        parent.columnconfigure(1, weight=1)
        row_index = len(labels)
        ttk.Checkbutton(parent, text="Use TLS", variable=vars_map["use_tls"]).grid(row=row_index, column=1, sticky="w", pady=4)
        row_index += 1

        template_specs = [
            ("Reminder subject template", "reminder_subject_template", False),
            ("Reminder body template", "reminder_body_template", True),
            ("Escalation subject template", "escalation_subject_template", False),
            ("Escalation body template", "escalation_body_template", True),
        ]
        widgets: dict[str, tk.Text] = {}
        self._email_template_entries: dict[str, tk.Misc] = {}
        for label, key, multiline in template_specs:
            ttk.Label(parent, text=label).grid(row=row_index, column=0, sticky="nw", padx=(0, 8), pady=4)
            if multiline:
                text_widget = tk.Text(parent, width=46, height=6, wrap="word")
                text_widget.insert("1.0", getattr(self.state.email_settings, key, ""))
                text_widget.grid(row=row_index, column=1, sticky="ew", pady=4)
                widgets[key] = text_widget
                self._email_template_entries[key] = text_widget
                self._email_settings_controls[key] = text_widget
            else:
                entry = ttk.Entry(parent, textvariable=vars_map[key], width=46)
                entry.grid(row=row_index, column=1, sticky="ew", pady=4)
                self._email_template_entries[key] = entry
                self._email_settings_controls[key] = entry
            row_index += 1
        self._build_onboarding_placeholder_picker(parent, row_index)
        for key in ["expected_interval_hours", "critical_window_days"]:
            if hasattr(self, "scheduler_entries") and key in self.scheduler_entries:
                self._email_settings_controls[key] = self.scheduler_entries[key]
        return widgets

    def _build_onboarding_placeholder_picker(self, parent: ttk.LabelFrame, row_index: int) -> None:
        options = placeholder_picker_options(["onboarding_reminder", "escalation"])
        self._onboarding_placeholder_var = tk.StringVar(value=options[0] if options else "")
        picker_row = ttk.Frame(parent)
        picker_row.grid(row=row_index, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        picker_row.columnconfigure(1, weight=1)
        ttk.Label(picker_row, text="Placeholders:", foreground="#475569").grid(row=0, column=0, sticky="w")
        picker = ttk.Combobox(picker_row, state="readonly", values=options, textvariable=self._onboarding_placeholder_var)
        picker.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(picker_row, text="Insert", command=self._insert_onboarding_placeholder).grid(row=0, column=2, sticky="e")

    def _insert_onboarding_placeholder(self) -> None:
        token = token_from_picker_label(self._onboarding_placeholder_var.get())
        if not token:
            return
        allowed = list(self._email_template_entries.values())
        inserted = insert_token_into_focused_widget(self.root, token, allowed)
        if inserted:
            return
        messagebox.showinfo("Template placeholders", "Focus a template field, then click Insert.")

    def _refresh_recipient_warnings(self, vars_map: dict[str, tk.Variable]) -> None:
        if not hasattr(self, "_recipient_warning_vars"):
            return
        reminder_text = recipient_warning_text(
            str(vars_map["reminder_recipients"].get()),
            channel_label="Reminder recipients",
        )
        escalation_text = recipient_warning_text(
            str(vars_map["director_and_owners"].get()),
            channel_label="Escalation recipients",
        )
        self._recipient_warning_vars["reminder_recipients"].set(reminder_text)
        self._recipient_warning_vars["director_and_owners"].set(escalation_text)

    def _recipient_validation_errors(self, vars_map: dict[str, tk.Variable]) -> list[str]:
        errors: list[str] = []
        for key, label in [
            ("reminder_recipients", "Reminder recipients"),
            ("director_and_owners", "Escalation recipients"),
        ]:
            warning = recipient_warning_text(str(vars_map[key].get()), channel_label=label)
            if warning:
                errors.append(warning)
        return errors

    def _build_scheduler_settings_fields(self, parent: ttk.LabelFrame, vars_map: dict[str, tk.Variable]) -> None:
        ttk.Checkbutton(parent, text="Enable local scheduler", variable=vars_map["scheduler_enabled"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(parent, text="Expected interval (hours)").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        expected_interval_entry = ttk.Entry(parent, textvariable=vars_map["expected_interval_hours"], width=12)
        expected_interval_entry.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(parent, text="Critical window (days)").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
        critical_window_entry = ttk.Entry(parent, textvariable=vars_map["critical_window_days"], width=12)
        critical_window_entry.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(parent, text="Metrics export folder").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        metrics_export_entry = ttk.Entry(parent, textvariable=vars_map["metrics_export_dir"], width=52)
        metrics_export_entry.grid(row=3, column=1, sticky="ew", pady=2)
        self.scheduler_entries = {
            "expected_interval_hours": expected_interval_entry,
            "critical_window_days": critical_window_entry,
            "metrics_export_dir": metrics_export_entry,
        }

        status_rows = [
            ("Scheduler state", self._scheduler_enabled_status_label(vars_map)),
            ("Last reminder run at", self.state.last_reminder_run_at or "Never"),
            ("Last scheduler-triggered result", scheduler_status_text(self.state.scheduler_status)),
        ]
        self._render_scheduler_status_rows(parent, start_row=4, rows=status_rows)

        ttk.Label(parent, text="Windows Task Scheduler setup", font=("TkDefaultFont", 9, "bold")).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self._render_scheduler_setup_steps(parent, start_row=9)
        parent.columnconfigure(1, weight=1)

    @staticmethod
    def _render_scheduler_status_rows(parent: ttk.LabelFrame, start_row: int, rows: list[tuple[str, str]]) -> None:
        for offset, (label, value) in enumerate(rows):
            row = start_row + offset
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
            ttk.Label(parent, text=value, foreground="#475569", wraplength=420, justify="left").grid(row=row, column=1, sticky="w", pady=2)

    def _render_scheduler_setup_steps(self, parent: ttk.LabelFrame, start_row: int) -> None:
        steps = self._scheduler_setup_steps()
        for offset, step in enumerate(steps):
            ttk.Label(parent, text=f"{offset + 1}. {step}", wraplength=500, justify="left").grid(row=start_row + offset, column=0, columnspan=2, sticky="w", pady=1)
        command_row = start_row + len(steps)
        ttk.Label(parent, text="Example command:").grid(row=command_row, column=0, sticky="w", pady=(4, 2))
        ttk.Entry(parent, state="readonly", width=90, textvariable=tk.StringVar(value=scheduler_command_example(scheduler_script_path(Path(__file__).resolve())))).grid(row=command_row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 2))

    @staticmethod
    def _scheduler_setup_steps() -> list[str]:
        return [
            "Open Task Scheduler and choose Create Task.",
            "Name the task 'OnboardingReminderRunner' and choose whether to run when user is logged in.",
            "Add a trigger to repeat every 1 hour indefinitely.",
            "Set Program/script to pythonw and pass this app path with --run-reminders.",
            "Save the task and run it once manually to confirm reminders execute.",
        ]

    @staticmethod
    def _scheduler_enabled_status_label(vars_map: dict[str, tk.Variable]) -> str:
        return "Enabled" if bool(vars_map["scheduler_enabled"].get()) else "Disabled"

    def _save_email_settings(
        self,
        dialog: tk.Toplevel,
        vars_map: dict[str, tk.Variable],
        template_widgets: dict[str, tk.Text],
        inline_validation,
    ) -> None:
        inline_validation.clear()
        try:
            smtp_port = int(str(vars_map["smtp_port"].get()).strip())
            imap_or_pop_port = int(str(vars_map["imap_or_pop_port"].get()).strip())
            expected_interval_hours = int(str(vars_map["expected_interval_hours"].get()).strip())
            critical_window_days = int(str(vars_map["critical_window_days"].get()).strip())
        except ValueError:
            show_inline_field_error(
                inline_validation,
                field_label="Port/scheduler values",
                cause="one or more values are not valid integers.",
                corrective_action="Enter whole numbers for SMTP, IMAP/POP, expected interval, and critical window.",
                focus_widget=self._email_settings_controls.get("smtp_port"),
            )
            return

        if expected_interval_hours <= 0:
            expected_interval_hours = DEFAULT_EXPECTED_INTERVAL_HOURS
        if critical_window_days <= 0:
            critical_window_days = 3

        sender_email = str(vars_map["sender_email"].get()).strip()
        sender_valid, sender_error_reason = validate_sender_email(sender_email)
        if not sender_valid:
            self._sender_email_attempt_count += 1
            self.metrics_logger.log_onboarding_canonical_event(
                "ux.onboarding.sender_email.validation_error",
                error_reason=sender_error_reason or "invalid",
                attempt_count=self._sender_email_attempt_count,
            )
            show_inline_field_error(
                inline_validation,
                field_label="Sender email",
                cause="sender email is invalid.",
                corrective_action="Enter a valid sender email address, then save again.",
                focus_widget=self._email_settings_controls.get("sender_email"),
            )
            return

        recipient_errors = self._recipient_validation_errors(vars_map)
        if recipient_errors:
            self.metrics_logger.log_ux_validation_error(
                app="onboarding",
                surface="reminders",
                error_type="invalid_recipients",
                issue_count=len(recipient_errors),
            )
            show_inline_field_error(
                inline_validation,
                field_label="Recipients",
                cause="one or more recipient addresses are invalid.",
                corrective_action="Correct recipient formats, then save again.",
                focus_widget=self._email_settings_controls.get("reminder_recipients"),
            )
            return

        templates = self._onboarding_template_values(vars_map, template_widgets)
        unknown = validate_template_map(templates, self._onboarding_template_contexts())
        if unknown:
            self.metrics_logger.log_ux_validation_error(
                app="onboarding",
                surface="reminders",
                error_type="unknown_placeholders",
                issue_count=sum(len(values) for values in unknown.values()),
            )
            show_inline_field_error(
                inline_validation,
                field_label="Template placeholders",
                cause="one or more placeholders are unsupported.",
                corrective_action=unknown_placeholder_actionable_message(unknown),
                focus_widget=self._email_settings_controls.get("reminder_subject_template"),
            )
            return

        self.state.email_settings = EmailSettings(
            sender_email=sender_email,
            smtp_host=str(vars_map["smtp_host"].get()).strip(),
            smtp_port=smtp_port,
            smtp_username=str(vars_map["smtp_username"].get()).strip(),
            smtp_password=str(vars_map["smtp_password"].get()).strip(),
            use_tls=bool(vars_map["use_tls"].get()),
            imap_or_pop_host=str(vars_map["imap_or_pop_host"].get()).strip(),
            imap_or_pop_port=imap_or_pop_port,
            director_and_owners=str(vars_map["director_and_owners"].get()).strip(),
            reminder_recipients=str(vars_map["reminder_recipients"].get()).strip(),
            reminder_subject_template=templates["reminder_subject_template"],
            reminder_body_template=templates["reminder_body_template"],
            escalation_subject_template=templates["escalation_subject_template"],
            escalation_body_template=templates["escalation_body_template"],
        )
        self.state.scheduler_settings = {
            "enabled": bool(vars_map["scheduler_enabled"].get()),
            "opt_in": bool(vars_map["scheduler_enabled"].get()),
            "expected_interval_hours": expected_interval_hours,
            "critical_window_days": critical_window_days,
            "metrics_export_dir": str(vars_map["metrics_export_dir"].get()).strip(),
        }
        if self._sender_email_attempt_count > 0:
            self.metrics_logger.log_onboarding_canonical_event(
                "ux.onboarding.sender_email.completion",
                attempts_before_success=self._sender_email_attempt_count,
                domain_type=sender_email_domain_type(sender_email),
            )
            self._sender_email_attempt_count = 0
        self.save_state()
        self._refresh_reminder_health_banner()
        dialog.destroy()

    @staticmethod
    def _onboarding_template_contexts() -> dict[str, str]:
        return {
            "reminder_subject_template": "onboarding_reminder",
            "reminder_body_template": "onboarding_reminder",
            "escalation_subject_template": "escalation",
            "escalation_body_template": "escalation",
        }

    @staticmethod
    def _onboarding_template_values(vars_map: dict[str, tk.Variable], template_widgets: dict[str, tk.Text]) -> dict[str, str]:
        return {
            "reminder_subject_template": str(vars_map["reminder_subject_template"].get()).strip(),
            "reminder_body_template": template_widgets["reminder_body_template"].get("1.0", "end").strip(),
            "escalation_subject_template": str(vars_map["escalation_subject_template"].get()).strip(),
            "escalation_body_template": template_widgets["escalation_body_template"].get("1.0", "end").strip(),
        }

    def _validate_placeholder_templates_for_run(self) -> bool:
        templates = {
            "reminder_subject_template": self.state.email_settings.reminder_subject_template,
            "reminder_body_template": self.state.email_settings.reminder_body_template,
            "escalation_subject_template": self.state.email_settings.escalation_subject_template,
            "escalation_body_template": self.state.email_settings.escalation_body_template,
        }
        unknown = validate_template_map(templates, self._onboarding_template_contexts())
        if not unknown:
            return True
        self.metrics_logger.log_ux_validation_error(
            app="onboarding",
            surface="reminders",
            error_type="unknown_placeholders",
            issue_count=sum(len(values) for values in unknown.values()),
            phase="send",
        )
        self._set_global_validation_message(
            message=unknown_placeholder_actionable_message(unknown),
            severity=VALIDATION_SEVERITY_ERROR,
        )
        return False

    def _collect_runtime_placeholder_values(self, runner: OnboardingReminderRunner) -> dict[str, str] | None:
        context = runner._build_run_context(now_date=None, include_escalation=True)
        templates = [
            self.state.email_settings.reminder_subject_template,
            self.state.email_settings.reminder_body_template,
            self.state.email_settings.escalation_subject_template,
            self.state.email_settings.escalation_body_template,
        ]
        values = dict(context.runtime_values)
        for template, ctx in [
            (templates[0], "onboarding_reminder"),
            (templates[1], "onboarding_reminder"),
            (templates[2], "escalation"),
            (templates[3], "escalation"),
        ]:
            missing = missing_placeholder_keys(template, values, ctx)
            if not self._prompt_for_missing_values(missing, values):
                return None
        return values

    def _prompt_for_missing_values(self, missing: list[str], values: dict[str, str]) -> bool:
        for key in missing:
            if str(values.get(key, "")).strip():
                continue
            prompt = f"Enter value for placeholder [{key}]"
            entered = simpledialog.askstring("Missing placeholder value", prompt, parent=self.root)
            if entered is None:
                return False
            text = entered.strip()
            if not text:
                messagebox.showerror("Missing value", f"Placeholder [{key}] requires a value.")
                return False
            values[key] = text
        return True

    def _runtime_recipient_validation_errors(self) -> list[str]:
        errors: list[str] = []
        reminder_valid, reminder_invalid = split_and_validate_recipients(self.state.email_settings.reminder_recipients)
        escalation_valid, escalation_invalid = split_and_validate_recipients(self.state.email_settings.director_and_owners)
        if not reminder_valid and not reminder_invalid:
            errors.append("Reminder recipients: add at least one recipient email address.")
        if reminder_invalid:
            errors.append("Reminder recipients: malformed addresses: " + ", ".join(reminder_invalid))
        if not escalation_valid and not escalation_invalid:
            errors.append("Escalation recipients: add at least one recipient email address.")
        if escalation_invalid:
            errors.append("Escalation recipients: malformed addresses: " + ", ".join(escalation_invalid))
        return errors

    def run_reminders_now(self) -> None:
        self._clear_global_validation_message()
        if not self._validate_placeholder_templates_for_run():
            return

        recipient_errors = self._runtime_recipient_validation_errors()
        if recipient_errors:
            self.metrics_logger.log_ux_validation_error(
                app="onboarding",
                surface="reminders",
                error_type="invalid_recipients",
                issue_count=len(recipient_errors),
                phase="send",
            )
            self._set_global_validation_message(
                message="Recipients are invalid. Update reminder and escalation addresses in Email Settings, then run again.",
                severity=VALIDATION_SEVERITY_ERROR,
            )
            return

        runner = self._build_reminder_runner()
        preview = runner.preview()
        action = self._show_presend_reminder_dialog(preview)
        if action == "cancel":
            self.metrics_logger.log_ux_completion(app="onboarding", surface="reminders", outcome="cancelled", mode="live")
            return

        if action == "dry_run":
            self.metrics_logger.log_onboarding_canonical_event(
                "ux.onboarding.reminder_mode.click",
                mode="dry_run",
                time_to_mode_select_ms=0,
                changed_from_default=True,
            )
            self.metrics_logger.log_ux_completion(app="onboarding", surface="reminders", outcome="confirmed", mode="dry_run", source="live_dialog")
            self._finalize_reminder_run(preview, run_source="manual")
            self._show_reminder_run_message(preview)
            return

        self.metrics_logger.log_onboarding_canonical_event(
            "ux.onboarding.reminder_mode.click",
            mode="live",
            time_to_mode_select_ms=0,
            changed_from_default=False,
        )
        self.metrics_logger.log_ux_completion(app="onboarding", surface="reminders", outcome="confirmed", mode="live", source="live_dialog")
        runtime_values = self._collect_runtime_placeholder_values(runner)
        if runtime_values is None:
            self.metrics_logger.log_ux_completion(app="onboarding", surface="reminders", outcome="cancelled", mode="live", reason="missing_runtime_value")
            return

        runner.runtime_values = runtime_values
        result = runner.run(dry_run=False)
        self._finalize_reminder_run(result, run_source="manual")
        self._show_reminder_run_message(result)

    def run_reminders_dry_run(self) -> None:
        if not self._validate_placeholder_templates_for_run():
            return
        runner = self._build_reminder_runner()
        result = runner.preview()
        self.metrics_logger.log_onboarding_canonical_event(
            "ux.onboarding.reminder_mode.click",
            mode="dry_run",
            time_to_mode_select_ms=0,
            changed_from_default=True,
        )
        self.metrics_logger.log_ux_completion(app="onboarding", surface="reminders", outcome="confirmed", mode="dry_run", source="dry_run_button")
        self._finalize_reminder_run(result, run_source="manual")
        self._show_reminder_run_message(result)

    def _build_reminder_runner(self) -> OnboardingReminderRunner:
        return OnboardingReminderRunner(
            employees=self.state.employees,
            templates=self.state.templates,
            email_settings=self.state.email_settings,
            metrics_logger=self.metrics_logger,
        )

    def _show_presend_reminder_dialog(self, result) -> str:
        dialog = tk.Toplevel(self.root)
        dialog.title("Reminder Pre-Send Review")

        chosen_action = tk.StringVar(value="cancel")
        warning = tk.Label(
            dialog,
            text="LIVE MODE WARNING: Choosing 'Send now' sends real emails immediately.",
            bg="#FEE2E2",
            fg="#991B1B",
            anchor="w",
            justify="left",
            padx=10,
            pady=8,
        )
        warning.pack(fill="x", padx=12, pady=(12, 6))

        text = tk.Text(dialog, width=90, height=24, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=6)
        text.insert("1.0", self._presend_dialog_text(result))
        text.configure(state="disabled")

        button_bar = ttk.Frame(dialog)
        button_bar.pack(fill="x", padx=12, pady=(0, 12))
        dry_button = ttk.Button(button_bar, text="Dry-run only", command=lambda: self._close_reminder_dialog(dialog, chosen_action, "dry_run"))
        dry_button.pack(side="left")
        send_button = ttk.Button(button_bar, text="Send now", command=lambda: self._close_reminder_dialog(dialog, chosen_action, "send"))
        send_button.pack(side="left", padx=8)
        cancel_button = ttk.Button(button_bar, text="Cancel", command=lambda: self._close_reminder_dialog(dialog, chosen_action, "cancel"))
        cancel_button.pack(side="right")

        close_action = lambda: self._close_reminder_dialog(dialog, chosen_action, "cancel")
        self._prepare_modal_dialog(dialog, first_widget=dry_button, on_close=close_action)
        dialog.bind("<Return>", lambda _event: self._invoke_button_from_keyboard(send_button, "reminders", "send_now"))
        dialog.bind("<KP_Enter>", lambda _event: self._invoke_button_from_keyboard(send_button, "reminders", "send_now"))
        dialog.wait_window()
        return chosen_action.get()

    def _prepare_modal_dialog(
        self,
        dialog: tk.Toplevel,
        *,
        first_widget: tk.Widget | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        dialog.transient(self.root)
        dialog.grab_set()

        close_action = on_close or dialog.destroy
        first_actionable = first_widget or dialog
        self._enable_modal_keyboard_navigation(dialog, first_widget=first_actionable)
        dialog.bind("<Escape>", lambda _event: close_action())
        dialog.protocol("WM_DELETE_WINDOW", close_action)

    def _enable_modal_keyboard_navigation(self, dialog: tk.Toplevel, first_widget: tk.Widget) -> None:
        focusables = self._focusable_children(dialog)
        if first_widget in focusables:
            first_widget.focus_set()
        elif focusables:
            focusables[0].focus_set()

        dialog.bind("<Tab>", lambda event: self._cycle_modal_focus(event, dialog, forward=True))
        dialog.bind("<Shift-Tab>", lambda event: self._cycle_modal_focus(event, dialog, forward=False))

    def _cycle_modal_focus(self, event: tk.Event, dialog: tk.Toplevel, forward: bool) -> str:
        focusables = self._focusable_children(dialog)
        if not focusables:
            return "break"
        current = dialog.focus_get()
        if current not in focusables:
            focusables[0].focus_set()
            return "break"
        index = focusables.index(current)
        next_index = (index + 1) % len(focusables) if forward else (index - 1) % len(focusables)
        focusables[next_index].focus_set()
        return "break"

    def _focusable_children(self, root: tk.Misc) -> list[tk.Widget]:
        children: list[tk.Widget] = []
        for widget in root.winfo_children():
            try:
                can_focus = bool(int(widget.cget("takefocus")))
            except (tk.TclError, ValueError):
                can_focus = widget.winfo_class() in {"TButton", "Button", "Entry", "Text", "TCombobox"}
            state = "normal"
            try:
                state = str(widget.cget("state"))
            except tk.TclError:
                state = "normal"
            if can_focus and state != "disabled":
                children.append(widget)
            children.extend(self._focusable_children(widget))
        return children

    @staticmethod
    def _close_reminder_dialog(dialog: tk.Toplevel, chosen_action: tk.StringVar, action: str) -> None:
        chosen_action.set(action)
        dialog.destroy()

    def _presend_dialog_text(self, result) -> str:
        estimate = reminder_send_estimate(result)
        lines = [
            "Pre-send reminder review",
            "",
            "Mode summary:",
            "- Dry run: no emails are sent.",
            "- Live run: real reminder/escalation emails are sent.",
            "",
            "Recipients:",
            f"- Email reminder recipients: {', '.join(result.recipients.get('reminder', [])) or 'None'}",
            f"- Email escalation recipients: {', '.join(result.recipients.get('escalation', [])) or 'None'}",
            "- In-app recipients: all users viewing onboarding tracker",
            "",
            "Expected message count:",
            f"- Email messages: {estimate['email_messages']}",
            f"- In-app notifications: {estimate['in_app_messages']}",
            f"- Total expected messages: {estimate['total_messages']}",
            "",
            "Tasks grouped by employee:",
        ]
        if not result.task_breakdown:
            lines.append("- None")
        for employee_name, tasks in result.task_breakdown.items():
            lines.append(f"- {employee_name}")
            lines.extend([f"  • {item['title']} (due {item['due_date']})" for item in tasks])

        lines.extend([
            "",
            "Totals:",
            f"- Due reminders: {result.counts.get('due_reminders', 0)}",
            f"- Escalation candidates: {len(result.escalation_candidates)}",
            f"- Monthly summary lines: {result.counts.get('monthly_lines', 0)}",
        ])
        if result.escalation_candidates:
            lines.append("Escalation candidates:")
            lines.extend([f"- {item['employee_name']}: {item['title']}" for item in result.escalation_candidates])
        return "\n".join(lines)

    def _finalize_reminder_run(self, result, run_source: str = "manual") -> None:
        self._record_reminder_run_metadata(result, run_source=run_source)
        self._refresh_reminder_health_banner()
        self.save_state()

    def _show_reminder_run_message(self, result) -> None:
        mode_label = self._reminder_mode_label(dry_run=result.dry_run)
        if result.counts["due_reminders"] == 0:
            messagebox.showinfo(f"Reminders: {mode_label}", f"{mode_label}: no reminders due right now.")
            return

        lines = [
            f"Mode: {mode_label}",
            "Due reminders:",
            *[f"- {item['employee_name']}: {item['title']}" for item in result.tasks],
            "",
        ]
        for outcome in result.outcomes:
            if outcome.message:
                lines.append(outcome.message)
            if outcome.error:
                lines.append(f"{outcome.phase.title()} error: {outcome.error}")
        msg = "\n".join(lines).strip()

        messagebox.showinfo(f"Reminder run complete ({mode_label})", msg)

    @staticmethod
    def _reminder_mode_label(dry_run: bool) -> str:
        if dry_run:
            return "Dry run (no emails sent)"
        return "Actual send"

    def _record_reminder_run_metadata(self, result, run_source: str = "manual") -> bool:
        summary_obj = ReminderRunSummary.from_dict(result.to_dict())
        summary = summary_obj.to_dict()
        if not result.dry_run:
            self._log_reminder_events(result.tasks)
        summary["run_mode"] = "dry_run" if result.dry_run else "send"
        self.state.reminder_run_history.append(summary)

        if self._should_update_last_reminder_run_at(result):
            ran_at = summary_obj.ran_at
            if isinstance(ran_at, str):
                self.state.last_reminder_run_at = ran_at

        scheduler_enabled = scheduler_opt_in(self.state.scheduler_settings)
        normalized_source = normalize_run_source(run_source)
        self.state.scheduler_status = build_scheduler_status(
            summary_obj,
            scheduler_enabled=scheduler_enabled,
            run_source=normalized_source,
        )
        return True

    @staticmethod
    def _should_update_last_reminder_run_at(result) -> bool:
        if result.dry_run:
            return False
        return any(outcome.success for outcome in result.outcomes)

    def _log_task_created_events(self, employee: Employee) -> None:
        for task in employee.tasks:
            self.metrics_logger.log_event(
                EVENT_TASK_CREATED,
                task_id=task.id,
                task_type=task.template_id or task.title,
                employee_id=employee.id,
                employee_name=employee.name,
                due_date=task.due_date,
                created_at=task.created_at,
            )

    def _log_new_tasks_for_employee(self, employee: Employee, before_ids: set[str]) -> None:
        for task in employee.tasks:
            if task.id in before_ids:
                continue
            self.metrics_logger.log_event(
                EVENT_TASK_CREATED,
                task_id=task.id,
                task_type=task.template_id or task.title,
                employee_id=employee.id,
                employee_name=employee.name,
                due_date=task.due_date,
                created_at=task.created_at,
            )

    def _log_reminder_events(self, tasks: list[dict[str, str | None]]) -> None:
        for item in tasks:
            self.metrics_logger.log_event(
                EVENT_REMINDER_SENT,
                task_id=item.get("task_id") or "",
                task_type=item.get("title") or "",
                employee_id=item.get("employee_id") or "",
                employee_name=item.get("employee_name") or "",
                due_date=item.get("due_date") or "",
            )

    def _log_overdue_events(self) -> None:
        today_iso = date.today().isoformat()
        for employee in self.state.employees:
            for task in employee.tasks:
                if task.completed or not task.due_date:
                    continue
                if task.due_date >= today_iso:
                    continue
                self.metrics_logger.log_overdue_once(
                    task_id=task.id,
                    due_date=task.due_date,
                    task_type=task.template_id or task.title,
                    employee_id=employee.id,
                    employee_name=employee.name,
                )

    def _is_valid_date(self, value: str) -> bool:
        try:
            parse_date(value)
        except ValueError:
            return False
        return True

    def save_state(self) -> None:
        self._log_overdue_events()
        self.store.save(self.state)
        self._refresh_today_dashboard()

class WindowState:
    def __init__(self, root, path="window_state.json"):
        self.root = root
        self.path = Path(path)

    def load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.root.geometry(data["geometry"])

    def save(self):
        data = {"geometry": self.root.geometry()}
        self.path.write_text(json.dumps(data))

class TwoPaneShell(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=14)
        self.grid(row=0, column=0, sticky="nsew")

        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # LEFT
        self.left = ttk.Frame(self)
        self.left.grid(row=0, column=0, sticky="nsw", padx=(0, 14))

        # RIGHT
        self.right = ttk.Frame(self)
        self.right.grid(row=0, column=1, sticky="nsew")

        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(0, weight=1)



def _load_launch_context(args: argparse.Namespace) -> dict[str, object]:
    context = {
        "employee_id": _sanitize_employee_id(args.employee_id),
        "urgent_only": bool(args.urgent_only),
    }
    state_context = _read_launch_context_file(args.state_file)
    if not state_context:
        return context

    if not context["employee_id"]:
        context["employee_id"] = _sanitize_employee_id(state_context.get("employee_id"))

    context["urgent_only"] = context["urgent_only"] or bool(state_context.get("urgent_only", False))
    return context


def _read_launch_context_file(path_value: str | None) -> dict[str, object]:
    if not path_value:
        return {}

    path = Path(path_value)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


def _sanitize_employee_id(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""

    if all(ch.isalnum() or ch in {'-', '_'} for ch in candidate):
        return candidate
    return ""


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--employee-id", default="")
    parser.add_argument("--urgent-only", action="store_true")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--run-reminders", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-source", default="manual")
    return parser

def _run_reminders_from_cli(args: argparse.Namespace) -> int:
    store = JsonStore(Path.cwd())
    state = store.load()
    runner = OnboardingReminderRunner(
        employees=state.employees,
        templates=state.templates,
        email_settings=state.email_settings,
    )
    dry_run = bool(args.dry_run)
    if dry_run:
        result = runner.preview()
    else:
        result = runner.run(dry_run=False)

    summary_obj = ReminderRunSummary.from_dict(result.to_dict())
    summary = summary_obj.to_dict()
    summary["run_mode"] = "dry_run" if result.dry_run else "send"
    state.reminder_run_history.append(summary)
    if (not result.dry_run) and any(outcome.success for outcome in result.outcomes):
        state.last_reminder_run_at = summary_obj.ran_at

    run_source = normalize_run_source(args.run_source)
    scheduler_enabled = scheduler_opt_in(state.scheduler_settings)
    state.scheduler_status = build_scheduler_status(summary_obj, scheduler_enabled=scheduler_enabled, run_source=run_source)

    metadata_changed = True
    run_had_success = any(item.success for item in result.outcomes)
    if metadata_changed or run_had_success:
        store.save(state)
    return 0


def main() -> None:
    args = _build_arg_parser().parse_args()
    if args.run_reminders:
        _run_reminders_from_cli(args)
        return

    launch_context = _load_launch_context(args)
    root = tk.Tk()
    win_state = WindowState(root)
    win_state.load()
    app = OnboardingTrackerApp(root, launch_context=launch_context)
    root.protocol("WM_DELETE_WINDOW", lambda: (win_state.save(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
