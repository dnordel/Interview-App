from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import uuid

from onboarding_models import EmailSettings, Employee
from onboarding_notifier import parse_recipients, reminder_run_telemetry_counts, send_escalation_email, send_reminder_email
from onboarding_scheduler import ReminderItem, collect_due_reminders, mark_reminder_sent
from ux_metrics import UxMetricsLogger


@dataclass(slots=True)
class SendOutcome:
    phase: str
    attempted: bool
    success: bool
    recipients: list[str]
    item_count: int
    message: str = ""
    error: str = ""


@dataclass(slots=True)
class ReminderRunResult:
    run_id: str
    ran_at: str
    dry_run: bool
    recipients: dict[str, list[str]]
    tasks: list[dict[str, str | None]]
    counts: dict[str, int]
    outcomes: list[SendOutcome]
    task_breakdown: dict[str, list[dict[str, str | None]]]
    escalation_candidates: list[dict[str, str | None]]
    channel_results: dict[str, dict[str, int]]
    error_summaries: list[str]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["outcomes"] = [asdict(outcome) for outcome in self.outcomes]
        return payload


@dataclass(slots=True)
class ReminderRunContext:
    run_id: str
    now_date: date
    reminders: list[ReminderItem]
    monthly_lines: list[str]
    escalation_lines: list[str]
    recipients: dict[str, list[str]]
    school: str
    runtime_values: dict[str, str]


class OnboardingReminderRunner:
    def __init__(
        self,
        employees: list[Employee],
        templates,
        email_settings: EmailSettings,
        reminder_sender=send_reminder_email,
        escalation_sender=send_escalation_email,
        runtime_values: dict[str, str] | None = None,
        metrics_logger: UxMetricsLogger | None = None,
    ) -> None:
        self.employees = employees
        self.templates = templates
        self.email_settings = email_settings
        self.reminder_sender = reminder_sender
        self.escalation_sender = escalation_sender
        self.runtime_values = dict(runtime_values or {})
        self.metrics_logger = metrics_logger

    def preview(self, now_date: date | None = None, include_escalation: bool = True) -> ReminderRunResult:
        return self.run(now_date=now_date, dry_run=True, include_escalation=include_escalation)

    def run(
        self,
        now_date: date | None = None,
        dry_run: bool = False,
        include_escalation: bool = True,
    ) -> ReminderRunResult:
        context = self._build_run_context(now_date=now_date, include_escalation=include_escalation)
        if not context.reminders:
            result = self._build_result(context=context, dry_run=dry_run, outcomes=[])
            self._emit_canonical_completion(dry_run=dry_run, outcomes=[])
            return result

        outcomes = self._send_phase(context=context, dry_run=dry_run)
        self._apply_state_updates(context=context, dry_run=dry_run, outcomes=outcomes)
        result = self._build_result(context=context, dry_run=dry_run, outcomes=outcomes)
        self._emit_canonical_completion(dry_run=dry_run, outcomes=outcomes)
        return result

    def _emit_canonical_completion(self, dry_run: bool, outcomes: list[SendOutcome]) -> None:
        if not self.metrics_logger:
            return
        counts = reminder_run_telemetry_counts(outcomes, dry_run)
        self.metrics_logger.log_onboarding_canonical_event(
            "ux.onboarding.reminder_run.completion",
            mode="dry_run" if dry_run else "live",
            recipient_count=counts["recipient_count"],
            skipped_count=counts["skipped_count"],
            warning_count=counts["warning_count"],
            sent_count=counts["sent_count"],
            failed_count=counts["failed_count"],
            blocked_count=counts["blocked_count"],
        )

    def _build_run_context(self, now_date: date | None, include_escalation: bool) -> ReminderRunContext:
        run_date = now_date or date.today()
        reminders = collect_due_reminders(self.employees, self.templates, run_date)
        monthly_lines = self._monthly_outstanding_lines(run_date)
        escalation_lines = self._collect_escalation_lines(reminders) if include_escalation else []
        recipients = {
            "reminder": parse_recipients(self.email_settings.reminder_recipients),
            "escalation": parse_recipients(self.email_settings.director_and_owners),
        }
        return ReminderRunContext(
            run_id=f"reminder_run_{uuid.uuid4().hex[:12]}",
            now_date=run_date,
            reminders=reminders,
            monthly_lines=monthly_lines,
            escalation_lines=escalation_lines,
            recipients=recipients,
            school=self._school_for_reminders(reminders),
            runtime_values=dict(self.runtime_values),
        )

    def _send_phase(self, context: ReminderRunContext, dry_run: bool) -> list[SendOutcome]:
        if dry_run:
            return self._dry_run_outcomes(context)

        outcomes = [
            self._send_reminders(context),
        ]
        if context.escalation_lines:
            outcomes.append(self._send_escalation(context))
        return outcomes

    def _dry_run_outcomes(self, context: ReminderRunContext) -> list[SendOutcome]:
        outcomes = [
            SendOutcome(
                phase="reminder",
                attempted=False,
                success=True,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                message="Dry-run: reminder send skipped.",
            )
        ]
        if context.escalation_lines:
            outcomes.append(
                SendOutcome(
                    phase="escalation",
                    attempted=False,
                    success=True,
                    recipients=context.recipients["escalation"],
                    item_count=len(context.escalation_lines),
                    message="Dry-run: escalation send skipped.",
                )
            )
        return outcomes

    def _send_reminders(self, context: ReminderRunContext) -> SendOutcome:
        if not context.recipients["reminder"]:
            return SendOutcome(
                phase="reminder",
                attempted=False,
                success=False,
                recipients=[],
                item_count=len(context.reminders),
                error="No reminder recipients configured.",
            )

        try:
            message = self.reminder_sender(
                self.email_settings,
                context.reminders,
                context.monthly_lines,
                context.school,
                context.runtime_values,
            )
            return SendOutcome(
                phase="reminder",
                attempted=True,
                success=True,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                message=message,
            )
        except Exception as exc:
            return SendOutcome(
                phase="reminder",
                attempted=True,
                success=False,
                recipients=context.recipients["reminder"],
                item_count=len(context.reminders),
                error=str(exc),
            )

    def _send_escalation(self, context: ReminderRunContext) -> SendOutcome:
        if not context.recipients["escalation"]:
            return SendOutcome(
                phase="escalation",
                attempted=False,
                success=False,
                recipients=[],
                item_count=len(context.escalation_lines),
                error="Escalation recipients are not configured.",
            )

        try:
            message = self.escalation_sender(
                self.email_settings,
                context.escalation_lines,
                context.reminders,
                context.school,
                context.runtime_values,
            )
            return SendOutcome(
                phase="escalation",
                attempted=True,
                success=True,
                recipients=context.recipients["escalation"],
                item_count=len(context.escalation_lines),
                message=message,
            )
        except Exception as exc:
            return SendOutcome(
                phase="escalation",
                attempted=True,
                success=False,
                recipients=context.recipients["escalation"],
                item_count=len(context.escalation_lines),
                error=str(exc),
            )

    def _apply_state_updates(self, context: ReminderRunContext, dry_run: bool, outcomes: list[SendOutcome]) -> None:
        if dry_run:
            return
        reminder_outcome = self._outcome_for_phase(outcomes, "reminder")
        if not reminder_outcome or not reminder_outcome.success:
            return

        lookup = {employee.id: employee for employee in self.employees}
        for reminder in context.reminders:
            employee = lookup.get(reminder.employee_id)
            if not employee:
                continue
            self._mark_task_sent(employee, reminder.task_id, context.now_date)

    def _outcome_for_phase(self, outcomes: list[SendOutcome], phase: str) -> SendOutcome | None:
        for outcome in outcomes:
            if outcome.phase == phase:
                return outcome
        return None

    def _mark_task_sent(self, employee: Employee, task_id: str, run_date: date) -> None:
        for task in employee.tasks:
            if task.id != task_id:
                continue
            mark_reminder_sent(task, run_date)
            return


    def _school_for_reminders(self, reminders: list[ReminderItem]) -> str:
        employees = self._employees_for_reminders(reminders)
        for employee in employees:
            school = str(getattr(employee, "school", "")).strip()
            if school:
                return school
        return ""

    def _employees_for_reminders(self, reminders: list[ReminderItem]) -> list[Employee]:
        lookup = {employee.id: employee for employee in self.employees}
        employees: list[Employee] = []
        for reminder in reminders:
            employee = lookup.get(reminder.employee_id)
            if not employee:
                continue
            employees.append(employee)
        return employees
    def _collect_escalation_lines(self, reminders: list[ReminderItem]) -> list[str]:
        escalation_items = [item for item in reminders if "Escalation:" in item.title]
        if not escalation_items:
            return []

        lines = ["The following employees still have incomplete permit or LiveScan tasks:"]
        lines.extend([f"- {item.employee_name} ({item.title})" for item in escalation_items])
        return lines

    def _monthly_outstanding_lines(self, run_date: date) -> list[str]:
        if run_date.day != 1:
            return []

        rows: list[str] = []
        for employee in self.employees:
            pending = [task.title for task in employee.tasks if not task.completed]
            if not pending:
                continue
            rows.append(f"- {employee.name}: {', '.join(pending)}")
        return rows

    def _build_result(self, context: ReminderRunContext, dry_run: bool, outcomes: list[SendOutcome]) -> ReminderRunResult:
        tasks = [
            {
                "employee_id": item.employee_id,
                "employee_name": item.employee_name,
                "task_id": item.task_id,
                "title": item.title,
                "due_date": item.due_date,
            }
            for item in context.reminders
        ]
        counts = self._build_counts(context, outcomes)
        task_breakdown = self._build_task_breakdown(tasks)
        escalation_candidates = self._escalation_candidates(context.reminders)
        channel_results = self._build_channel_results(counts, outcomes)
        error_summaries = [outcome.error for outcome in outcomes if outcome.error]
        return ReminderRunResult(
            run_id=context.run_id,
            ran_at=datetime.combine(context.now_date, datetime.min.time()).isoformat(),
            dry_run=dry_run,
            recipients=context.recipients,
            tasks=tasks,
            counts=counts,
            outcomes=outcomes,
            task_breakdown=task_breakdown,
            escalation_candidates=escalation_candidates,
            channel_results=channel_results,
            error_summaries=error_summaries,
        )

    @staticmethod
    def _build_task_breakdown(tasks: list[dict[str, str | None]]) -> dict[str, list[dict[str, str | None]]]:
        grouped: dict[str, list[dict[str, str | None]]] = {}
        for task in tasks:
            employee_name = str(task.get("employee_name") or "Unknown")
            grouped.setdefault(employee_name, []).append(task)
        return grouped

    @staticmethod
    def _escalation_candidates(reminders: list[ReminderItem]) -> list[dict[str, str | None]]:
        return [
            {
                "employee_id": item.employee_id,
                "employee_name": item.employee_name,
                "task_id": item.task_id,
                "title": item.title,
                "due_date": item.due_date,
            }
            for item in reminders
            if "Escalation:" in item.title
        ]

    @staticmethod
    def _build_channel_results(counts: dict[str, int], outcomes: list[SendOutcome]) -> dict[str, dict[str, int]]:
        email_attempted = sum(1 for outcome in outcomes if outcome.attempted)
        email_sent = sum(1 for outcome in outcomes if outcome.success)
        email_failed = sum(1 for outcome in outcomes if outcome.attempted and not outcome.success)
        return {
            "email": {
                "attempted": email_attempted,
                "sent": email_sent,
                "failed": email_failed,
            },
            "in_app": {
                "attempted": 0,
                "sent": counts["due_reminders"],
                "failed": 0,
            },
        }

    def _build_counts(self, context: ReminderRunContext, outcomes: list[SendOutcome]) -> dict[str, int]:
        attempted = sum(1 for item in outcomes if item.attempted)
        successful = sum(1 for item in outcomes if item.success)
        failed = sum(1 for item in outcomes if item.attempted and not item.success)
        return {
            "due_reminders": len(context.reminders),
            "escalation_lines": len(context.escalation_lines),
            "monthly_lines": len(context.monthly_lines),
            "send_attempts": attempted,
            "successful_sends": successful,
            "failed_sends": failed,
        }
