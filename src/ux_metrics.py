from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import csv
import json
from pathlib import Path
from statistics import median
from typing import Any


EVENT_TASK_CREATED = "task_created"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TASK_OVERDUE = "task_overdue"
EVENT_REMINDER_SENT = "reminder_sent"
EVENT_INTERVIEW_FINALIZED = "interview_finalized"

UX_EVENT_KINDS = ("view", "click", "validation_error", "completion")
UX_REQUIRED_FIELDS_BY_KIND = {"view": ("target",), "click": ("target",), "validation_error": ("error_type",), "completion": ("outcome",)}
_BLOCKED_UX_FIELDS = {
    "candidate_name",
    "employee_name",
    "notes",
    "free_text",
    "email",
    "phone",
    "address",
    "resume_path",
}
_EMAIL_LIKE_VALUE_MARKERS = ("@",)

ONBOARDING_EVENT_REGISTRY: dict[str, dict[str, type[Any]]] = {
    "ux.onboarding.add_employee_form.view": {
        "entry_point": str,
        "time_from_screen_open_ms": int,
    },
    "ux.onboarding.add_employee_form.validation_error": {
        "error_type": str,
        "required_fields_missing_count": int,
    },
    "ux.onboarding.urgent_filter.click": {
        "time_to_filter_ms": int,
        "result_count": int,
    },
    "ux.onboarding.reminder_mode.click": {
        "mode": str,
        "time_to_mode_select_ms": int,
        "changed_from_default": bool,
    },
    "ux.onboarding.reminder_run.completion": {
        "mode": str,
        "recipient_count": int,
        "skipped_count": int,
        "warning_count": int,
        "sent_count": int,
        "failed_count": int,
        "blocked_count": int,
    },
    "ux.onboarding.sender_email.validation_error": {
        "error_reason": str,
        "attempt_count": int,
    },
    "ux.onboarding.sender_email.completion": {
        "attempts_before_success": int,
        "domain_type": str,
    },
}

LEGACY_TO_CANONICAL_UX_EVENTS = {
    "onboarding_add_employee_opened": "ux.onboarding.add_employee_form.view",
    "onboarding_employee_save_error": "ux.onboarding.add_employee_form.validation_error",
    "onboarding_urgent_filter_applied": "ux.onboarding.urgent_filter.click",
    "onboarding_reminder_mode_selected": "ux.onboarding.reminder_mode.click",
    "onboarding_reminder_dry_run_completed": "ux.onboarding.reminder_run.completion",
    "onboarding_reminder_live_run_completed": "ux.onboarding.reminder_run.completion",
    "onboarding_sender_email_validation_error": "ux.onboarding.sender_email.validation_error",
    "onboarding_sender_email_updated": "ux.onboarding.sender_email.completion",
}

SCOPE_EVENT_MONTH = "event_month"
SCOPE_CREATED_MONTH = "created_month"
SUMMARY_SCOPES = (SCOPE_EVENT_MONTH, SCOPE_CREATED_MONTH)


@dataclass(slots=True)
class MonthlyMetricsSummary:
    month_key: str
    scope: str
    on_time_completion_pct: float
    overdue_count: int
    previous_overdue_count: int
    overdue_change_pct: float | None
    median_days_by_task_type: dict[str, float]


class UxMetricsLogger:
    def __init__(self, root_dir: str | Path, filename: str = "ux_metrics.jsonl") -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.root_dir / filename
        self._overdue_event_keys = self._load_overdue_event_keys()

    def log_event(self, event_type: str, **fields: Any) -> None:
        canonical_type = LEGACY_TO_CANONICAL_UX_EVENTS.get(event_type, event_type)
        if not canonical_type:
            return
        safe_fields = dict(fields)
        if canonical_type.startswith("ux."):
            safe_fields = _sanitize_ux_payload(safe_fields)
        if canonical_type in ONBOARDING_EVENT_REGISTRY:
            safe_fields = _coerce_canonical_onboarding_fields(canonical_type, safe_fields)
        payload = {
            "event_type": canonical_type,
            "timestamp": _utc_now_iso(),
            **safe_fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def log_ux_view(self, *, app: str, surface: str, target: str = "", **fields: Any) -> None:
        self._log_ux_event("view", app=app, surface=surface, target=target, **fields)

    def log_ux_click(self, *, app: str, surface: str, target: str, **fields: Any) -> None:
        self._log_ux_event("click", app=app, surface=surface, target=target, **fields)

    def log_ux_validation_error(
        self,
        *,
        app: str,
        surface: str,
        error_type: str,
        field_name: str = "",
        required_fields_present: bool = True,
        **fields: Any,
    ) -> None:
        self._log_ux_event(
            "validation_error",
            app=app,
            surface=surface,
            error_type=error_type,
            field_name=field_name,
            required_fields_present=required_fields_present,
            **fields,
        )

    def log_ux_completion(self, *, app: str, surface: str, outcome: str, **fields: Any) -> None:
        self._log_ux_event("completion", app=app, surface=surface, outcome=outcome, **fields)

    def log_onboarding_canonical_event(self, event_type: str, **fields: Any) -> None:
        canonical_type = LEGACY_TO_CANONICAL_UX_EVENTS.get(event_type, event_type)
        if canonical_type not in ONBOARDING_EVENT_REGISTRY:
            return
        payload = _coerce_canonical_onboarding_fields(canonical_type, _sanitize_ux_payload(fields))
        self.log_event(canonical_type, **payload)

    def _log_ux_event(self, kind: str, **fields: Any) -> None:
        if kind not in UX_EVENT_KINDS:
            return
        app = _sanitize_event_token(fields.pop("app", ""))
        surface = _sanitize_event_token(fields.pop("surface", ""))
        if not app or not surface:
            return
        payload = _sanitize_ux_payload(fields)
        required = UX_REQUIRED_FIELDS_BY_KIND.get(kind, ())
        for field in required:
            value = str(payload.get(field, "")).strip()
            if not value:
                return
        event_type = f"ux.{app}.{surface}.{kind}"
        self.log_event(event_type, **payload)


    def log_keyboard_path_completed(
        self,
        *,
        screen_id: str,
        flow_id: str,
        completed_via_keyboard: bool,
        keyboard_step_count: int,
        abandoned: bool,
    ) -> None:
        clean_screen_id = _sanitize_event_token(screen_id)
        clean_flow_id = _sanitize_event_token(flow_id)
        if not clean_screen_id or not clean_flow_id:
            return
        safe_step_count = max(0, int(keyboard_step_count))
        self.log_event(
            "ux.keyboard_path_completed",
            screen_id=clean_screen_id,
            flow_id=clean_flow_id,
            completed_via_keyboard=bool(completed_via_keyboard),
            keyboard_step_count=safe_step_count,
            abandoned=bool(abandoned),
        )

    def log_overdue_once(self, *, task_id: str, due_date: str | None, **fields: Any) -> bool:
        event_key = f"{task_id}|{due_date or ''}|{EVENT_TASK_OVERDUE}"
        if not task_id or event_key in self._overdue_event_keys:
            return False
        self._overdue_event_keys.add(event_key)
        self.log_event(EVENT_TASK_OVERDUE, task_id=task_id, due_date=due_date, event_key=event_key, **fields)
        return True

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_clean = line.strip()
                if not line_clean:
                    continue
                try:
                    payload = json.loads(line_clean)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    events.append(payload)
        return events

    def export_events_csv(self, export_dir: str | Path) -> Path:
        destination = Path(export_dir)
        destination.mkdir(parents=True, exist_ok=True)
        export_path = destination / f"ux_metrics_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        events = self.read_events()
        fieldnames = _collect_fieldnames(events)

        with export_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for event in events:
                writer.writerow({name: event.get(name, "") for name in fieldnames})

        return export_path

    def _load_overdue_event_keys(self) -> set[str]:
        keys: set[str] = set()
        for event in self.read_events():
            if str(event.get("event_type") or "") != EVENT_TASK_OVERDUE:
                continue
            key = str(event.get("event_key") or "").strip()
            if key:
                keys.add(key)
        return keys



def build_monthly_summary(
    *,
    month: date,
    scope: str,
    employees: list[Any],
    events: list[dict[str, Any]],
    grace_hours: int = 24,
) -> MonthlyMetricsSummary:
    selected_month = date(month.year, month.month, 1)
    previous_month = _prev_month(selected_month)
    records = _build_task_records(employees)

    selected = _records_for_scope(records, selected_month, scope)
    on_time_pct = _on_time_completion_pct(selected, grace_hours=grace_hours)
    median_days = _median_days_by_type(selected)

    overdue_count = _overdue_count(selected_month, events, records)
    previous_overdue = _overdue_count(previous_month, events, records)
    change_pct = _pct_change(overdue_count, previous_overdue)

    return MonthlyMetricsSummary(
        month_key=selected_month.strftime("%Y-%m"),
        scope=scope,
        on_time_completion_pct=on_time_pct,
        overdue_count=overdue_count,
        previous_overdue_count=previous_overdue,
        overdue_change_pct=change_pct,
        median_days_by_task_type=median_days,
    )


def _build_task_records(employees: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for employee in employees:
        for task in getattr(employee, "tasks", []):
            created = _parse_datetime(getattr(task, "created_at", None))
            completed = _parse_datetime(getattr(task, "completed_at", None))
            due = _parse_datetime(getattr(task, "due_date", None))
            if created is None:
                created = due or completed
            records.append(
                {
                    "task_id": getattr(task, "id", ""),
                    "task_type": str(getattr(task, "template_id", "") or getattr(task, "title", "unknown")),
                    "created_at": created,
                    "completed_at": completed,
                    "due_at": due,
                    "completed": bool(getattr(task, "completed", False)),
                }
            )
    return records


def _records_for_scope(records: list[dict[str, Any]], month: date, scope: str) -> list[dict[str, Any]]:
    if scope == SCOPE_CREATED_MONTH:
        return [item for item in records if _in_month(item.get("created_at"), month)]
    return [item for item in records if _in_month(item.get("completed_at"), month)]


def _on_time_completion_pct(records: list[dict[str, Any]], grace_hours: int) -> float:
    completed_records = [item for item in records if item.get("completed_at")]
    if not completed_records:
        return 0.0

    grace_delta = timedelta(hours=grace_hours)
    on_time_count = 0
    for item in completed_records:
        due_at = item.get("due_at")
        completed_at = item.get("completed_at")
        if due_at is None or completed_at is None:
            continue
        if completed_at <= due_at + grace_delta:
            on_time_count += 1

    return round((on_time_count / len(completed_records)) * 100.0, 1)


def _median_days_by_type(records: list[dict[str, Any]]) -> dict[str, float]:
    by_type: dict[str, list[float]] = {}
    for item in records:
        created = item.get("created_at")
        completed = item.get("completed_at")
        task_type = str(item.get("task_type") or "unknown")
        if created is None or completed is None:
            continue
        day_span = (completed - created).total_seconds() / 86400.0
        by_type.setdefault(task_type, []).append(day_span)

    medians: dict[str, float] = {}
    for task_type, values in by_type.items():
        if not values:
            continue
        medians[task_type] = round(float(median(values)), 1)
    return medians


def _overdue_count(month: date, events: list[dict[str, Any]], records: list[dict[str, Any]]) -> int:
    logged_count = sum(1 for item in events if _event_matches_month(item, EVENT_TASK_OVERDUE, month))
    if logged_count:
        return logged_count

    fallback = 0
    for item in records:
        due_at = item.get("due_at")
        completed_at = item.get("completed_at")
        if due_at is None:
            continue
        if not _in_month(due_at, month):
            continue
        if completed_at is None or completed_at > due_at:
            fallback += 1
    return fallback


def _event_matches_month(event: dict[str, Any], event_type: str, month: date) -> bool:
    if str(event.get("event_type") or "") != event_type:
        return False
    ts = _parse_datetime(event.get("timestamp"))
    return _in_month(ts, month)


def _pct_change(current: int, previous: int) -> float | None:
    if previous == 0:
        if current == 0:
            return 0.0
        return None
    return round(((current - previous) / previous) * 100.0, 1)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if len(raw) == 10:
        raw = f"{raw}T00:00:00"
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_month(value: datetime | None, month: date) -> bool:
    if value is None:
        return False
    return value.year == month.year and value.month == month.month


def _prev_month(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _collect_fieldnames(events: list[dict[str, Any]]) -> list[str]:
    ordered = ["timestamp", "event_type", "task_id", "task_type", "employee_id", "employee_name", "due_date"]
    dynamic = set()
    for item in events:
        dynamic.update(item.keys())
    for field in ordered:
        dynamic.discard(field)
    return ordered + sorted(dynamic)


def _sanitize_event_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip().lower().replace(" ", "_")
    return "".join(char for char in cleaned if char.isalnum() or char in {"_", "-"})


def _sanitize_ux_payload(fields: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _BLOCKED_UX_FIELDS or key.endswith("_notes"):
            continue
        if key.endswith("_name"):
            continue
        normalized_key = _sanitize_event_token(key)
        if not normalized_key:
            continue
        safe_value = _sanitize_ux_value(value)
        if safe_value is None and value is not None:
            continue
        if safe_value is None:
            sanitized[normalized_key] = None
            continue
        sanitized[normalized_key] = safe_value
    return sanitized


def _sanitize_ux_value(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    if any(marker in text for marker in _EMAIL_LIKE_VALUE_MARKERS):
        return None
    if len(text) > 120:
        return text[:120]
    return text


def _coerce_canonical_onboarding_fields(event_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    schema = ONBOARDING_EVENT_REGISTRY.get(event_type, {})
    payload: dict[str, Any] = {}
    for key, expected_type in schema.items():
        value = fields.get(key)
        coerced = _coerce_value(value, expected_type)
        if coerced is None:
            continue
        payload[key] = coerced
    return payload


def _coerce_value(value: Any, expected_type: type[Any]) -> Any:
    if value is None:
        return None
    if expected_type is bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None
    if expected_type is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if expected_type is str:
        text = str(value).strip().lower()
        return _sanitize_event_token(text)
    return value
