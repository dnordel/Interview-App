from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NotificationRecipient:
    email: str = ""
    name: str = ""
    role_label: str = ""
    active: bool = True
    id: int | None = None
    recipient_type: str = "email"
    role_key: str = ""


@dataclass(frozen=True)
class NotificationCondition:
    field: str
    operator: str = "equals"
    value: str = ""
    id: int | None = None


@dataclass(frozen=True)
class NotificationRule:
    event_type: str
    label: str
    subject_template: str
    body_template: str
    recipients: list[NotificationRecipient] = field(default_factory=list)
    conditions: list[NotificationCondition] = field(default_factory=list)
    active: bool = True
    trigger_timing: str = "event"
    date_field: str = ""
    offset_days: int = 0
    sender_account: str = "default"
    required_attachment_key: str = ""
    system_rule: bool = False
    user_disabled: bool = False
    id: int | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class NotificationSendResult:
    event_type: str
    rule_id: int | None
    status: str
    recipient_count: int = 0
    error: str = ""


@dataclass(frozen=True)
class NotificationTestPayload:
    label: str
    event_type: str
    payload: dict[str, str]
    source_kind: str = "manual"
