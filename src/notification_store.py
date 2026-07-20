from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from email_security import is_valid_email_address
from notification_models import NotificationCondition, NotificationRecipient, NotificationRule
from notification_templates import validate_notification_rule


STAFFING_NOTIFICATION_ROLE_RECIPIENTS = [
    NotificationRecipient(
        email="",
        name="Hiring Manager",
        role_label="Hiring Manager",
        recipient_type="role",
        role_key="hiring_manager",
    ),
    NotificationRecipient(
        email="",
        name="Director",
        role_label="Director",
        recipient_type="role",
        role_key="director",
    ),
]
HIRING_MANAGER_NOTIFICATION_ROLE_RECIPIENTS = [
    NotificationRecipient(
        email="",
        name="Hiring Manager",
        role_label="Hiring Manager",
        recipient_type="role",
        role_key="hiring_manager",
    )
]
OFFER_APPROVED_NOTIFICATION_ROLE_RECIPIENTS = [
    NotificationRecipient(
        name="Candidate",
        role_label="Candidate",
        recipient_type="role",
        role_key="candidate",
    )
]
OFFER_ACCEPTED_NOTIFICATION_ROLE_RECIPIENTS = [
    NotificationRecipient(
        name="Candidate",
        role_label="Candidate",
        recipient_type="role",
        role_key="candidate",
    ),
    NotificationRecipient(
        name="Director",
        role_label="Director",
        recipient_type="role",
        role_key="director",
    ),
    NotificationRecipient(
        name="Executive Director",
        role_label="Executive Director",
        recipient_type="role",
        role_key="executive_director",
    ),
]


def _roles(*keys: str) -> list[NotificationRecipient]:
    labels = {
        "candidate": "Candidate",
        "director": "School Director",
        "executive_director": "Executive Director",
        "hiring_manager": "Hiring Manager",
        "hr_manager": "HR Manager",
        "payroll": "Payroll",
        "office_manager": "Office Manager",
    }
    return [
        NotificationRecipient(
            name=labels[key], role_label=labels[key], recipient_type="role", role_key=key
        )
        for key in keys
    ]


def _system_workflow_default_rules() -> list[NotificationRule]:
    signature = (
        "Best Regards,\nDavid Nordel\nDirector, Recruiting & Community Relations\n"
        "Launch Pad Learning\n(310) 347-8694\ndavidn@launchpadpreschool.com"
    )
    return [
        NotificationRule(
            event_type="interview.rating.qualified",
            label="System: initial interview qualified",
            subject_template="New Candidate for You - {position} - {candidate_name}",
            body_template=(
                "Hi {director_name},\n\nThere is a new candidate for you to review; below you will find a snapshot of them:\n\n"
                "Name: {candidate_name}\nInterview Score: {interview_score}\nDegree: {degree_display}"
                "{degree_in_ece_display}\nYears of Experience: {experience}\n\n"
                "You may view transcripts from their interview on your staffing dashboard.\n\n"
                "{candidate_name} will be emailing you their resume and transcripts. Please verify their transcripts/units "
                "prior to scheduling your interview with them.\n\nThank you,\nLPL HR System"
            ),
            recipients=_roles("director"), active=False, system_rule=True,
        ),
        NotificationRule(
            event_type="director.interview.hire",
            label="System: director Hire submitted",
            subject_template="Please Approve the Offer for {candidate_name}",
            body_template=(
                "Hi,\n\n{director_name} approved hiring {candidate_name}; below you will find a snapshot of them:\n\n"
                "Name: {candidate_name}\nInterview Score: {interview_score}\nDirector Rating: {director_interview_score}\n"
                "Degree: {degree_display}{degree_in_ece_display}\nYears of Experience: {experience}\n"
                "Requested Pay: {requested_pay}\nOffer Amount: {offer_amount}\nClassroom: {proposed_classroom}\n"
                "Hours: {shift_start} to {shift_end}\n\nYou may view this candidate on your staffing dashboard.\n\n"
                "Thank you,\nLPL HR System"
            ),
            recipients=_roles("hr_manager", "executive_director"), active=False, system_rule=True,
        ),
        NotificationRule(
            event_type="offer.approved",
            label="System: approved offer to candidate",
            subject_template="Offer - Launch Pad Learning {school}",
            body_template=(
                "Hello {honorific} {candidate_name},\n\nI hope you are doing well. We are thrilled to offer you a teacher position "
                "at Launch Pad Learning in {school}.\n\nThe official offer letter detailing your compensation, benefits, and other "
                "employment terms is attached to this email.\n\nPlease reply with your signed offer letter by 5:00 PM on "
                "{reply_by_date}, to confirm your acceptance.\n\nIf you have questions or need clarification, please email or call "
                "Ms. Deidre at deidre@launchpadpreschool.com or (310) 977-6133.\n\nWe look forward to having you join our "
                "team and working together to provide our students with a nurturing and enriching environment.\n\n" + signature
            ),
            recipients=_roles("candidate", "executive_director"), active=False, system_rule=True,
            sender_account="hiring_manager", required_attachment_key="offer_pdf_path",
        ),
        NotificationRule(
            event_type="offer.accepted",
            label="System: offer accepted onboarding",
            subject_template="New Candidate for You - {position} - {candidate_name}",
            body_template=(
                "Hello {honorific} {candidate_name},\n\nThank you for accepting the offer for a teacher position at Launch Pad Learning. "
                "Please coordinate with Ms. {director_name} on your official start date (all paperwork must be completed before your "
                "first day). I've attached our onboarding guide for new employees.\n\nYou will need to have completed a physical "
                "examination and a TB test within the past year. If you have not had an appointment within the past year, please call "
                "your doctor to schedule one as soon as possible.\n\nYou will be separately emailed the employment documents to complete.\n\n"
                "Welcome to the team! We are excited to work with you.\n\n" + signature
            ),
            recipients=_roles("candidate", "director", "office_manager", "executive_director"),
            active=False, system_rule=True, sender_account="hiring_manager",
            required_attachment_key="onboarding_guide_path",
        ),
        NotificationRule(
            event_type="employment.start.today", label="System: employee starts today",
            subject_template="{candidate_name} Is Starting Today",
            body_template=("{candidate_name} starts today. This is a reminder to set up their payroll, benefits, and email.\n\n"
                           "If they completed the new employee bio survey, please add the bio to the website.\n\nThank you,\nLPL HR System"),
            recipients=_roles("hr_manager"), active=False, system_rule=True,
        ),
        NotificationRule(
            event_type="permit.eligible.50d", label="System: permit eligibility",
            subject_template="{candidate_name} Is Now Eligible to Apply for Their Permit",
            body_template=("Hello {director_name},\n\n{candidate_name} is now eligible to apply for their permit - please print and sign "
                           "the verification of experience letter and provide it to {candidate_name}.\nIf they have already applied for or "
                           "obtained their permit, please update their profile on the staffing dashboard.\n\nThank you,\nLPL HR System"),
            recipients=_roles("director"), active=False, system_rule=True,
            trigger_timing="date_offset", date_field="start_date", offset_days=50,
            conditions=[
                NotificationCondition("permit_status", "in", "unknown, no_permit_or_application"),
                NotificationCondition("position_type", "in", "teacher, aide, assistant director"),
            ],
        ),
        NotificationRule(
            event_type="permit.escalation.90d", label="System: permit escalation",
            subject_template="{candidate_name}'s Permit Status",
            body_template=("Hello {honorific} {director_name},\n\n{candidate_name} is eligible to apply for their permit; however, our staffing "
                           "system does not show that they have applied or been approved for their permit. Please either follow up with "
                           "{candidate_name} or update their profile on the staffing dashboard.\n\nThank you,\nLPL HR System"),
            recipients=_roles("director", "candidate", "executive_director"), active=False, system_rule=True,
            trigger_timing="date_offset", date_field="start_date", offset_days=90,
            conditions=[
                NotificationCondition("permit_status", "in", "unknown, no_permit_or_application"),
                NotificationCondition("position_type", "in", "teacher, aide, assistant director"),
            ],
            sender_account="hiring_manager",
        ),
        NotificationRule(
            event_type="employment.notice.given", label="System: employee gave notice",
            subject_template="{candidate_name} Gave Notice Today",
            body_template=("Hi,\n\n{candidate_name} gave notice today. Their last day is {final_working_day}. You will receive another "
                           "reminder on their last day to process and provide their final paycheck.\n\nThank you,\nLPL HR System"),
            recipients=_roles("director", "office_manager", "executive_director", "payroll", "hr_manager"),
            active=False, system_rule=True,
        ),
        NotificationRule(
            event_type="employment.last_day", label="System: employee last day",
            subject_template="LAST DAY: {candidate_name} - Please Issue Paycheck",
            body_template=("Hi,\n\n{candidate_name}'s last day is today. After they clock back in after lunch, please close out their "
                           "timecard and issue their final paycheck.\n\nThank you,\nLPL HR System"),
            recipients=_roles("director", "payroll", "hr_manager"), active=False, system_rule=True,
        ),
        NotificationRule(
            event_type="staffing.assignment.need_now", label="System: position needed now",
            subject_template="{school} Needs to Hire!",
            body_template=("Hi,\n\n{school} needs a {position_title} for {classroom}. Please open a job listing.\n\nThank you,\nLPL HR System"),
            recipients=_roles("hr_manager"), active=False, system_rule=True,
        ),
    ]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class NotificationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def list_rules(self, event_type: str | None = None) -> list[NotificationRule]:
        with self._connect() as conn:
            if event_type is None:
                rows = conn.execute(
                    """
                    SELECT id, event_type, label, active, subject_template, body_template,
                           trigger_timing, date_field, offset_days, sender_account,
                           required_attachment_key, system_rule, user_disabled, created_at, updated_at
                    FROM notification_rules
                    ORDER BY event_type, label, id
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, event_type, label, active, subject_template, body_template,
                           trigger_timing, date_field, offset_days, sender_account,
                           required_attachment_key, system_rule, user_disabled, created_at, updated_at
                    FROM notification_rules
                    WHERE event_type = ?
                    ORDER BY label, id
                    """,
                    (str(event_type).strip(),),
                ).fetchall()
            return [self._rule_from_row(conn, row) for row in rows]

    def ensure_default_rules(self) -> None:
        system_defaults = _system_workflow_default_rules()
        defaults = [
            NotificationRule(
                event_type="staffing.assignment.created",
                label="Hiring manager: position created",
                subject_template="Position created: {position_name}",
                body_template="{school} created {position_name} for {classroom}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.assignment.need_now",
                label="Hiring manager: position needed now",
                subject_template="Position needed now: {position_name}",
                body_template="{school} needs {position_name} for {classroom}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.assignment.coming",
                label="Director: candidate marked coming",
                subject_template="Candidate coming: {person_name}",
                body_template="{person_name} is marked coming for {position_name} in {classroom}. Start date: {start_date}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.assignment.filled",
                label="Leadership: position filled",
                subject_template="Position filled: {position_name}",
                body_template="{position_name} in {classroom} has been filled by {person_name}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.assignment.replace",
                label="Director: replacement needed",
                subject_template="Replacement needed: {position_name}",
                body_template="{person_name} needs replacement for {position_name}. Final working day: {final_working_day}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.assignment.not_needed",
                label="Leadership: position no longer needed",
                subject_template="Position not needed: {position_name}",
                body_template="{position_name} in {classroom} is no longer needed. Status: {assignment_status}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="staffing.permit.updated",
                label="Director: permit updated",
                subject_template="Permit updated: {person_name}",
                body_template="{person_name} permit status changed to {permit_status}.",
                recipients=STAFFING_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="offer.accepted",
                label="Leadership: offer accepted",
                subject_template="RE: Offer of Employment - Launch Pad Learning {school_code}",
                body_template=(
                    "Hello {candidate},\n\n"
                    "Thank you for accepting the offer for a teacher position at Launch Pad Learning. "
                    "Please coordinate with your director on your official start date (all paperwork must be completed before your first day). "
                    "I've attached our onboarding guide for new employees.\n\n"
                    "You will need to have completed a physical examination and a TB test within the past year. "
                    "If you have not had an appointment within the past year, please call your doctor to schedule one as soon as possible.\n"
                    "You will be separately emailed the employment documents to complete.\n"
                    "Welcome to the team! We are excited to work with you.\n"
                    "Best Regards,\n"
                    "David Nordel\n"
                    "Director, Recruiting & Community Relations\n"
                    "Launch Pad Learning\n"
                    "(310)347-8694\n"
                    "davidn@launchpadpreschool.com"
                ),
                recipients=OFFER_ACCEPTED_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="offer.approved",
                label="Leadership: offer approved",
                subject_template="Offer of Employment - Launch Pad Learning {school_code}",
                body_template=(
                    "Hello {candidate},\n\n"
                    "I hope you are doing well. We are thrilled to offer you a {position} position at Launch Pad Learning in {school_location}.\n\n"
                    "The official offer letter detailing your compensation, benefits, and other employment terms is attached to this email.\n\n"
                    "Please reply with your signed offer letter by 5:00 PM on {reply_by_date}, to confirm your acceptance.\n\n"
                    "If you have questions or need clarification, please email or call Ms. Deidre at deidre@launchpadpreschool.com or (310) 977-6133.\n\n"
                    "We look forward to having you join our team and working together to provide our students with a nurturing and enriching environment.\n\n"
                    "Best Regards,\n"
                    "David Nordel\n"
                    "Director, Recruiting & Community Relations\n"
                    "Launch Pad Learning\n"
                    "(310)347-8694\n"
                    "davidn@launchpadpreschool.com"
                ),
                recipients=OFFER_APPROVED_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="offer.generated",
                label="Leadership: offer generated",
                subject_template="Offer generated: {candidate_name}",
                body_template="{candidate_name}'s {position} offer was generated for {school}. Start date: {start_date}.",
                recipients=HIRING_MANAGER_NOTIFICATION_ROLE_RECIPIENTS,
                active=False,
            ),
            NotificationRule(
                event_type="onboarding.task.created",
                label="Onboarding: task assigned",
                subject_template="Onboarding task assigned: {task_title}",
                body_template=(
                    "A new onboarding task is assigned to {owner_role} for {school}. "
                    "Due date: {due_date}. Open the Onboarding Tasks page for details."
                ),
                recipients=_roles("director"),
                active=False,
            ),
            NotificationRule(
                event_type="onboarding.task.completed",
                label="Onboarding: task completed",
                subject_template="Onboarding task completed: {task_title}",
                body_template=(
                    "An onboarding task was completed for {school}. "
                    "Open the Onboarding Tasks page for current status."
                ),
                recipients=_roles("director"),
                active=False,
            ),
            NotificationRule(
                event_type="onboarding.task.overdue",
                label="Onboarding: task overdue",
                subject_template="Onboarding task overdue: {task_title}",
                body_template=(
                    "An actionable onboarding task is overdue for {school}. "
                    "Open the Onboarding Tasks page for details."
                ),
                recipients=_roles("director"),
                active=False,
            ),
            NotificationRule(
                event_type="onboarding.digest.due",
                label="Onboarding: due and overdue digest",
                subject_template="{school} onboarding tasks due — {owner_role}",
                body_template=(
                    "{task_count} onboarding tasks require attention. "
                    "Open the Onboarding Tasks page for authorized details."
                ),
                recipients=_roles("director"),
                active=False,
            ),
            NotificationRule(
                event_type="custom.reminder",
                label="Custom reminder",
                subject_template="Reminder: {position_name}",
                body_template="This is a reminder for {position_name} at {school}.",
                active=False,
                trigger_timing="date_offset",
                date_field="start_date",
                offset_days=1,
            ),
        ]
        existing_rules = self.list_rules()
        for rule in system_defaults:
            existing_system_rule = next(
                (
                    existing
                    for existing in existing_rules
                    if existing.event_type == rule.event_type and existing.system_rule
                ),
                None,
            )
            if existing_system_rule is not None:
                if rule.conditions and not existing_system_rule.conditions:
                    existing_system_rule = self.save_rule(
                        replace(
                            existing_system_rule,
                            trigger_timing=rule.trigger_timing,
                            date_field=rule.date_field,
                            offset_days=rule.offset_days,
                            conditions=list(rule.conditions),
                        )
                    )
                self._backfill_default_recipients(rule)
                continue
            saved = self.save_rule(rule)
            existing_rules.append(saved)
        existing = {rule.event_type for rule in existing_rules}
        for rule in defaults:
            if rule.event_type not in existing:
                self.save_rule(rule)
                existing.add(rule.event_type)
                continue
            self._backfill_default_recipients(rule)

    def _backfill_default_recipients(self, default_rule: NotificationRule) -> None:
        existing_rules = self.list_rules(default_rule.event_type)
        if len(existing_rules) != 1:
            return
        existing_rule = existing_rules[0]
        if existing_rule.recipients:
            return
        if existing_rule.label != default_rule.label:
            return
        if existing_rule.subject_template != default_rule.subject_template:
            return
        if existing_rule.body_template != default_rule.body_template:
            return
        self.save_rule(
            NotificationRule(
                id=existing_rule.id,
                event_type=existing_rule.event_type,
                label=existing_rule.label,
                subject_template=existing_rule.subject_template,
                body_template=existing_rule.body_template,
                recipients=list(default_rule.recipients),
                active=existing_rule.active,
                trigger_timing=existing_rule.trigger_timing,
                date_field=existing_rule.date_field,
                offset_days=existing_rule.offset_days,
                sender_account=default_rule.sender_account,
                required_attachment_key=default_rule.required_attachment_key,
                system_rule=default_rule.system_rule,
                user_disabled=existing_rule.user_disabled,
                conditions=list(existing_rule.conditions),
            )
        )

    def save_rule(self, rule: NotificationRule) -> NotificationRule:
        if rule.id is not None:
            existing_rule = self.get_rule(rule.id)
            rule = replace(
                rule,
                sender_account=(
                    existing_rule.sender_account
                    if rule.sender_account == "default" and existing_rule.sender_account != "default"
                    else rule.sender_account
                ),
                required_attachment_key=(
                    rule.required_attachment_key or existing_rule.required_attachment_key
                ),
                system_rule=rule.system_rule or existing_rule.system_rule,
                user_disabled=rule.user_disabled or existing_rule.user_disabled,
            )
        blocking_issues = [issue for issue in validate_notification_rule(rule) if issue.blocking]
        if blocking_issues:
            raise ValueError(blocking_issues[0].message)
        event_type = str(rule.event_type or "").strip()
        label = str(rule.label or "").strip()
        if not event_type:
            raise ValueError("Notification event type is required.")
        if not label:
            raise ValueError("Notification label is required.")
        trigger_timing = str(rule.trigger_timing or "event").strip() or "event"
        date_field = str(rule.date_field or "").strip()
        offset_days = int(rule.offset_days)
        if trigger_timing not in {"event", "date_offset"}:
            raise ValueError("Notification trigger timing must be event or date_offset.")
        if trigger_timing == "date_offset" and not date_field:
            raise ValueError("Date-offset notification requires a date field.")
        for recipient in rule.recipients:
            recipient_type = str(recipient.recipient_type or "email").strip() or "email"
            role_key = str(recipient.role_key or "").strip()
            email = str(recipient.email or "").strip()
            if recipient_type not in {"email", "role"}:
                raise ValueError("Notification recipient type must be email or role.")
            if recipient_type == "role" and role_key not in {
                "candidate", "director", "executive_director", "hiring_manager",
                "hr_manager", "payroll", "office_manager",
            }:
                raise ValueError("Unknown notification recipient role.")
            if recipient_type == "email" and not is_valid_email_address(email):
                raise ValueError("Invalid recipient email.")

        now = utc_now_iso()
        with self._connect() as conn:
            with conn:
                if rule.id is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO notification_rules
                            (event_type, label, active, subject_template, body_template,
                             trigger_timing, date_field, offset_days, sender_account,
                             required_attachment_key, system_rule, user_disabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_type,
                            label,
                            1 if rule.active else 0,
                            str(rule.subject_template or ""),
                            str(rule.body_template or ""),
                            trigger_timing,
                            date_field,
                            offset_days,
                            str(rule.sender_account or "default"),
                            str(rule.required_attachment_key or ""),
                            1 if rule.system_rule else 0,
                            1 if rule.user_disabled else 0,
                            now,
                            now,
                        ),
                    )
                    rule_id = int(cursor.lastrowid)
                else:
                    rule_id = int(rule.id)
                    conn.execute(
                        """
                        UPDATE notification_rules
                        SET event_type = ?, label = ?, active = ?, subject_template = ?, body_template = ?,
                            trigger_timing = ?, date_field = ?, offset_days = ?, sender_account = ?,
                            required_attachment_key = ?, system_rule = ?, user_disabled = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            event_type,
                            label,
                            1 if rule.active else 0,
                            str(rule.subject_template or ""),
                            str(rule.body_template or ""),
                            trigger_timing,
                            date_field,
                            offset_days,
                            str(rule.sender_account or "default"),
                            str(rule.required_attachment_key or ""),
                            1 if rule.system_rule else 0,
                            1 if rule.user_disabled else 0,
                            now,
                            rule_id,
                        ),
                    )
                    conn.execute("DELETE FROM notification_recipients WHERE rule_id = ?", (rule_id,))

                for recipient in rule.recipients:
                    recipient_type = str(recipient.recipient_type or "email").strip() or "email"
                    conn.execute(
                        """
                        INSERT INTO notification_recipients
                            (rule_id, name, email, role_label, recipient_type, role_key, active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rule_id,
                            str(recipient.name or "").strip(),
                            str(recipient.email or "").strip(),
                            str(recipient.role_label or "").strip(),
                            recipient_type,
                            str(recipient.role_key or "").strip(),
                            1 if recipient.active else 0,
                            now,
                            now,
                        ),
                    )
                conn.execute("DELETE FROM notification_conditions WHERE rule_id = ?", (rule_id,))
                for sort_order, condition in enumerate(rule.conditions):
                    conn.execute(
                        """
                        INSERT INTO notification_conditions
                            (rule_id, field_key, operator, expected_value, sort_order)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            rule_id,
                            str(condition.field or "").strip(),
                            str(condition.operator or "equals").strip(),
                            str(condition.value or "").strip(),
                            sort_order,
                        ),
                    )
        return self.get_rule(rule_id)

    def get_rule(self, rule_id: int) -> NotificationRule:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, event_type, label, active, subject_template, body_template,
                       trigger_timing, date_field, offset_days, sender_account,
                       required_attachment_key, system_rule, user_disabled, created_at, updated_at
                FROM notification_rules
                WHERE id = ?
                """,
                (int(rule_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Notification rule not found.")
            return self._rule_from_row(conn, row)

    def delete_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute("DELETE FROM notification_recipients WHERE rule_id = ?", (int(rule_id),))
                conn.execute("DELETE FROM notification_conditions WHERE rule_id = ?", (int(rule_id),))
                conn.execute("DELETE FROM notification_rules WHERE id = ?", (int(rule_id),))

    def set_rule_active(self, rule_id: int, active: bool) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE notification_rules SET active = ?, user_disabled = ?, updated_at = ? WHERE id = ?",
                    (1 if active else 0, 0 if active else 1, utc_now_iso(), int(rule_id)),
                )

    def activate_system_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE notification_rules SET active = 1, updated_at = ? "
                    "WHERE id = ? AND system_rule = 1 AND user_disabled = 0",
                    (utc_now_iso(), int(rule_id)),
                )

    def has_send_attempt(self, rule_id: int, idempotency_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM notification_audit
                WHERE rule_id = ? AND idempotency_key = ? AND status = 'sent'
                LIMIT 1
                """,
                (int(rule_id), str(idempotency_key)),
            ).fetchone()
            return row is not None

    def record_send_attempt(
        self,
        *,
        event_type: str,
        rule_id: int | None,
        idempotency_key: str,
        recipient_count: int,
        status: str,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO notification_audit
                        (event_type, rule_id, idempotency_key, recipient_count, status, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_type).strip(),
                        rule_id,
                        str(idempotency_key),
                        int(recipient_count),
                        str(status).strip(),
                        str(error or "").strip(),
                        utc_now_iso(),
                    ),
                )

    def list_audit(self, rule_id: int | None = None, limit: int = 25) -> list[dict[str, Any]]:
        limited = max(1, min(int(limit), 200))
        with self._connect() as conn:
            if rule_id is None:
                rows = conn.execute(
                    """
                    SELECT id, event_type, rule_id, idempotency_key, recipient_count, status, error, created_at
                    FROM notification_audit
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limited,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, event_type, rule_id, idempotency_key, recipient_count, status, error, created_at
                    FROM notification_audit
                    WHERE rule_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(rule_id), limited),
                ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "event_type": str(row["event_type"]),
                    "rule_id": int(row["rule_id"]) if row["rule_id"] is not None else None,
                    "idempotency_key": str(row["idempotency_key"]),
                    "recipient_count": int(row["recipient_count"]),
                    "status": str(row["status"]),
                    "error": str(row["error"]),
                    "created_at": str(row["created_at"]),
                }
                for row in rows
            ]

    def schedule_notification(
        self,
        *,
        event_type: str,
        rule_id: int,
        idempotency_key: str,
        due_date: date,
        payload: dict[str, Any],
    ) -> None:
        payload_json = json.dumps({str(key): str(value) for key, value in payload.items()}, sort_keys=True)
        now = utc_now_iso()
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO notification_schedule
                        (event_type, rule_id, idempotency_key, due_date, payload_json, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(rule_id, idempotency_key) DO UPDATE SET
                        due_date = excluded.due_date,
                        payload_json = excluded.payload_json,
                        status = CASE
                            WHEN notification_schedule.status = 'sent' THEN notification_schedule.status
                            ELSE 'pending'
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(event_type).strip(),
                        int(rule_id),
                        str(idempotency_key),
                        due_date.isoformat(),
                        payload_json,
                        now,
                        now,
                    ),
                )

    def list_due_scheduled_notifications(self, current_date: date) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event_type, rule_id, idempotency_key, due_date, payload_json, status
                FROM notification_schedule
                WHERE status = 'pending' AND due_date <= ?
                ORDER BY due_date, id
                """,
                (current_date.isoformat(),),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "event_type": str(row["event_type"]),
                    "rule_id": int(row["rule_id"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "due_date": str(row["due_date"]),
                    "payload": json.loads(str(row["payload_json"] or "{}")),
                    "status": str(row["status"]),
                }
                for row in rows
            ]

    def list_scheduled_notifications(
        self,
        rule_id: int | None = None,
        status: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        limited = max(1, min(int(limit), 200))
        clauses: list[str] = []
        params: list[Any] = []
        if rule_id is not None:
            clauses.append("rule_id = ?")
            params.append(int(rule_id))
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status).strip())
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, event_type, rule_id, idempotency_key, due_date, payload_json, status, created_at, updated_at
                FROM notification_schedule
                {where}
                ORDER BY due_date DESC, id DESC
                LIMIT ?
                """,
                (*params, limited),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "event_type": str(row["event_type"]),
                    "rule_id": int(row["rule_id"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "due_date": str(row["due_date"]),
                    "payload": json.loads(str(row["payload_json"] or "{}")),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
                for row in rows
            ]

    def mark_scheduled_notification(self, schedule_id: int, status: str) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "UPDATE notification_schedule SET status = ?, updated_at = ? WHERE id = ?",
                    (str(status).strip(), utc_now_iso(), int(schedule_id)),
                )

    def get_or_create_rollout_date(self, today: date) -> date:
        if not isinstance(today, date):
            raise TypeError("Rollout date must be a date.")
        with self._connect() as conn:
            with conn:
                row = conn.execute(
                    "SELECT value FROM notification_metadata WHERE key = 'system_rollout_date'"
                ).fetchone()
                if row is None:
                    value = today.isoformat()
                    conn.execute(
                        "INSERT INTO notification_metadata(key, value) VALUES ('system_rollout_date', ?)",
                        (value,),
                    )
                else:
                    value = str(row["value"])
        return date.fromisoformat(value)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_rules (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        label TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        subject_template TEXT NOT NULL DEFAULT '',
                        body_template TEXT NOT NULL DEFAULT '',
                        trigger_timing TEXT NOT NULL DEFAULT 'event',
                        date_field TEXT NOT NULL DEFAULT '',
                        offset_days INTEGER NOT NULL DEFAULT 0,
                        sender_account TEXT NOT NULL DEFAULT 'default',
                        required_attachment_key TEXT NOT NULL DEFAULT '',
                        system_rule INTEGER NOT NULL DEFAULT 0,
                        user_disabled INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                _ensure_column(conn, "notification_rules", "trigger_timing", "TEXT NOT NULL DEFAULT 'event'")
                _ensure_column(conn, "notification_rules", "date_field", "TEXT NOT NULL DEFAULT ''")
                _ensure_column(conn, "notification_rules", "offset_days", "INTEGER NOT NULL DEFAULT 0")
                _ensure_column(conn, "notification_rules", "sender_account", "TEXT NOT NULL DEFAULT 'default'")
                _ensure_column(conn, "notification_rules", "required_attachment_key", "TEXT NOT NULL DEFAULT ''")
                _ensure_column(conn, "notification_rules", "system_rule", "INTEGER NOT NULL DEFAULT 0")
                _ensure_column(conn, "notification_rules", "user_disabled", "INTEGER NOT NULL DEFAULT 0")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_recipients (
                        id INTEGER PRIMARY KEY,
                        rule_id INTEGER NOT NULL REFERENCES notification_rules(id) ON DELETE CASCADE,
                        name TEXT NOT NULL DEFAULT '',
                        email TEXT NOT NULL,
                        role_label TEXT NOT NULL DEFAULT '',
                        recipient_type TEXT NOT NULL DEFAULT 'email',
                        role_key TEXT NOT NULL DEFAULT '',
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                _ensure_column(conn, "notification_recipients", "recipient_type", "TEXT NOT NULL DEFAULT 'email'")
                _ensure_column(conn, "notification_recipients", "role_key", "TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_conditions (
                        id INTEGER PRIMARY KEY,
                        rule_id INTEGER NOT NULL REFERENCES notification_rules(id) ON DELETE CASCADE,
                        field_key TEXT NOT NULL,
                        operator TEXT NOT NULL,
                        expected_value TEXT NOT NULL DEFAULT '',
                        sort_order INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_audit (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        rule_id INTEGER,
                        idempotency_key TEXT NOT NULL,
                        recipient_count INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_schedule (
                        id INTEGER PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        rule_id INTEGER NOT NULL REFERENCES notification_rules(id) ON DELETE CASCADE,
                        idempotency_key TEXT NOT NULL,
                        due_date TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_rules_event_type ON notification_rules(event_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_recipients_rule_id ON notification_recipients(rule_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_conditions_rule_id ON notification_conditions(rule_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_schedule_due ON notification_schedule(status, due_date)")
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_schedule_dedupe
                    ON notification_schedule(rule_id, idempotency_key)
                    """
                )
                conn.execute("DROP INDEX IF EXISTS idx_notification_audit_dedupe")
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_audit_dedupe
                    ON notification_audit(rule_id, idempotency_key)
                    WHERE rule_id IS NOT NULL AND status = 'sent'
                    """
                )

    def _rule_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> NotificationRule:
        recipients = [
            NotificationRecipient(
                id=int(recipient["id"]),
                name=str(recipient["name"]),
                email=str(recipient["email"]),
                role_label=str(recipient["role_label"]),
                active=bool(recipient["active"]),
                recipient_type=str(recipient["recipient_type"] or "email"),
                role_key=str(recipient["role_key"] or ""),
            )
            for recipient in conn.execute(
                """
                SELECT id, name, email, role_label, recipient_type, role_key, active
                FROM notification_recipients
                WHERE rule_id = ?
                ORDER BY id
                """,
                (int(row["id"]),),
            ).fetchall()
        ]
        conditions = [
            NotificationCondition(
                id=int(condition["id"]),
                field=str(condition["field_key"]),
                operator=str(condition["operator"]),
                value=str(condition["expected_value"]),
            )
            for condition in conn.execute(
                """
                SELECT id, field_key, operator, expected_value
                FROM notification_conditions
                WHERE rule_id = ?
                ORDER BY sort_order, id
                """,
                (int(row["id"]),),
            ).fetchall()
        ]
        return NotificationRule(
            id=int(row["id"]),
            event_type=str(row["event_type"]),
            label=str(row["label"]),
            active=bool(row["active"]),
            subject_template=str(row["subject_template"]),
            body_template=str(row["body_template"]),
            trigger_timing=str(row["trigger_timing"]),
            date_field=str(row["date_field"]),
            offset_days=int(row["offset_days"]),
            sender_account=str(row["sender_account"] or "default"),
            required_attachment_key=str(row["required_attachment_key"] or ""),
            system_rule=bool(row["system_rule"]),
            user_disabled=bool(row["user_disabled"]),
            recipients=recipients,
            conditions=conditions,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
