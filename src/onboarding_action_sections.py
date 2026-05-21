from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ActionEmphasis = Literal["primary", "secondary"]


@dataclass(frozen=True)
class ActionItemSpec:
    label: str
    command_name: str
    emphasis: ActionEmphasis
    metrics_key: str | None = None
    helper_text: str = ""
    shortcut_hint: str = ""


@dataclass(frozen=True)
class ActionSectionSpec:
    title: str
    helper_text: str
    actions: tuple[ActionItemSpec, ...]


def onboarding_action_sections() -> tuple[ActionSectionSpec, ...]:
    return (
        ActionSectionSpec(
            title="Daily workflow",
            helper_text="Start here each day to execute the most time-sensitive reminders.",
            actions=(
                ActionItemSpec(
                    label="Run Reminders Now",
                    command_name="_on_primary_reminder_cta_click",
                    emphasis="primary",
                    helper_text="Sends due reminders and escalations using current settings.",
                    shortcut_hint="Tab to focus, Enter/Space to run",
                ),
                ActionItemSpec(
                    label="Run Reminders (Dry Run)",
                    command_name="_run_reminders_dry_run_from_ui",
                    emphasis="secondary",
                    metrics_key="run_reminders_dry_run",
                    helper_text="Preview recipients and message content without sending.",
                    shortcut_hint="Tab to focus, Enter/Space to preview",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Candidate management",
            helper_text="Create and maintain onboarding records and task templates.",
            actions=(
                ActionItemSpec(
                    label="Add Employee",
                    command_name="open_add_employee_dialog",
                    emphasis="secondary",
                    metrics_key="add_employee",
                    helper_text="Create a new onboarding plan for a newly hired teacher.",
                ),
                ActionItemSpec(
                    label="Add Custom Template",
                    command_name="open_custom_template_dialog",
                    emphasis="secondary",
                    metrics_key="add_custom_template",
                    helper_text="Save reusable onboarding task sets for future hires.",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Communications",
            helper_text="Configure outbound email behavior used by reminder automation.",
            actions=(
                ActionItemSpec(
                    label="Email Settings",
                    command_name="open_email_settings",
                    emphasis="secondary",
                    metrics_key="open_email_settings",
                    helper_text="Update sender identity, recipients, and reminder templates.",
                ),
            ),
        ),
        ActionSectionSpec(
            title="Admin & advanced",
            helper_text="Less frequent settings for storage and environment setup.",
            actions=(
                ActionItemSpec(
                    label="Use Dropbox Folder",
                    command_name="change_storage_folder",
                    emphasis="secondary",
                    metrics_key="change_storage_folder",
                    helper_text="Change where onboarding data files are stored.",
                ),
            ),
        ),
    )
