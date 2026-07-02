from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from data_store import InterviewAppSettingsStore, QuestionOverridesStore, SchoolOfferSettingsStore
from email_security import is_valid_email_address
from interview_runtime import normalize_deepseek_prompt_templates
from notification_models import NotificationRecipient, NotificationRule
from notification_service import NOTIFICATION_RULES_PATH
from notification_store import NotificationStore
from platform_services import (
    DEFAULT_RUBRIC_PATH,
    INTERVIEW_APP_SETTINGS_PATH,
    QUESTIONS_OVERRIDE_PATH,
    SCHOOL_OFFER_SETTINGS_PATH,
    atomic_write_json,
)
from question_settings_service import QuestionSettingsService


DEFAULT_PROMPTS_PATH = Path(__file__).resolve().parent.parent / "config" / "deepseek_prompts.json"
DEEPSEEK_MODEL_CHOICES = ("deepseek-r1:1.5b", "deepseek-r1:8b", "deepseek-r1:14b")
DEFAULT_DEEPSEEK_MODEL = "deepseek-r1:8b"


@dataclass(frozen=True)
class AdminStudioPaths:
    rubric_path: Path = DEFAULT_RUBRIC_PATH
    overrides_path: Path = QUESTIONS_OVERRIDE_PATH
    school_settings_path: Path = SCHOOL_OFFER_SETTINGS_PATH
    prompts_path: Path = DEFAULT_PROMPTS_PATH
    app_settings_path: Path = INTERVIEW_APP_SETTINGS_PATH
    notification_rules_path: Path = NOTIFICATION_RULES_PATH
    backup_dir: Path | None = None


@dataclass(frozen=True)
class AdminSection:
    key: str
    title: str
    description: str
    item_count: int


@dataclass(frozen=True)
class AdminStudioSummary:
    sections: list[AdminSection]
    track_count: int
    question_count: int
    dirty_count: int
    validation_errors: list[str]


@dataclass(frozen=True)
class AdminChangeSummary:
    changed_files: list[str]
    lines: list[str]


@dataclass(frozen=True)
class AdminApplyResult:
    applied: bool
    changed_files: list[str] = field(default_factory=list)
    backup_paths: list[Path] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class AdminStudioDraft:
    baseline_rubric: dict[str, Any]
    baseline_overrides: dict[str, Any]
    baseline_school_settings: dict[str, dict[str, str]]
    baseline_prompts: dict[str, Any]
    baseline_app_settings: dict[str, Any]
    baseline_notification_rules: list[NotificationRule]
    rubric: dict[str, Any]
    overrides: dict[str, Any]
    school_settings: dict[str, dict[str, str]]
    prompts: dict[str, Any]
    app_settings: dict[str, Any]
    notification_rules: list[NotificationRule]

    @classmethod
    def from_payloads(
        cls,
        *,
        rubric: dict[str, Any],
        overrides: dict[str, Any],
        school_settings: dict[str, dict[str, str]],
        prompts: dict[str, Any],
        app_settings: dict[str, Any] | None = None,
        notification_rules: list[NotificationRule] | None = None,
    ) -> "AdminStudioDraft":
        app_settings = app_settings or {}
        return cls(
            baseline_rubric=deepcopy(rubric),
            baseline_overrides=deepcopy(overrides),
            baseline_school_settings=deepcopy(school_settings),
            baseline_prompts=deepcopy(prompts),
            baseline_app_settings=deepcopy(app_settings),
            baseline_notification_rules=deepcopy(notification_rules or []),
            rubric=deepcopy(rubric),
            overrides=deepcopy(overrides),
            school_settings=deepcopy(school_settings),
            prompts=deepcopy(prompts),
            app_settings=deepcopy(app_settings),
            notification_rules=deepcopy(notification_rules or []),
        )

    @property
    def is_dirty(self) -> bool:
        return bool(self.changed_payloads())

    def changed_payloads(self) -> dict[str, tuple[Any, Any]]:
        changed: dict[str, tuple[Any, Any]] = {}
        pairs = {
            "rubric.json": (self.baseline_rubric, self.rubric),
            "question_overrides.json": (self.baseline_overrides, self.overrides),
            "school_offer_settings.json": (self.baseline_school_settings, self.school_settings),
            "deepseek_prompts.json": (self.baseline_prompts, self.prompts),
            "interview_app_settings.json": (self.baseline_app_settings, self.app_settings),
            "notification_rules.sqlite3": (
                _notification_rule_snapshot(self.baseline_notification_rules),
                _notification_rule_snapshot(self.notification_rules),
            ),
        }
        for filename, (before, after) in pairs.items():
            if before != after:
                changed[filename] = (before, after)
        return changed

    def change_summary(self) -> AdminChangeSummary:
        changed = self.changed_payloads()
        lines: list[str] = []
        lines.extend(_trait_change_lines(self.baseline_rubric, self.rubric))
        lines.extend(_school_change_lines(self.baseline_school_settings, self.school_settings))
        lines.extend(_prompt_change_lines(self.baseline_prompts, self.prompts))
        lines.extend(_app_settings_change_lines(self.baseline_app_settings, self.app_settings))
        lines.extend(_notification_change_lines(self.baseline_notification_rules, self.notification_rules))
        if self.baseline_overrides != self.overrides:
            lines.append("Question flow or custom question settings changed.")
        return AdminChangeSummary(changed_files=list(changed), lines=lines)

    def discard(self) -> "AdminStudioDraft":
        return AdminStudioDraft.from_payloads(
            rubric=self.baseline_rubric,
            overrides=self.baseline_overrides,
            school_settings=self.baseline_school_settings,
            prompts=self.baseline_prompts,
            app_settings=self.baseline_app_settings,
            notification_rules=self.baseline_notification_rules,
        )

    def update_trait(self, trait_id: str, updates: dict[str, Any]) -> None:
        service = QuestionSettingsService(Path("rubric.json"), self.baseline_rubric)
        self.rubric = service.update_trait(self.rubric, trait_id, updates)

    def update_question_text(self, track_key: str, question_type: str, question_id: str, text: str) -> None:
        track_key = str(track_key or "").strip()
        question_type = str(question_type or "").strip().lower()
        question_id = str(question_id or "").strip()
        text = str(text or "").strip()
        if not track_key or question_type not in {"custom", "trait"} or not question_id:
            raise ValueError("Question update requires track, type, and id.")
        if not text:
            raise ValueError("Question text is required.")
        if question_type == "trait":
            self.overrides.setdefault("trait_question_overrides", {})[question_id] = text
            return
        custom_by_track = self.overrides.setdefault("custom_questions", {}).setdefault(track_key, [])
        for item in custom_by_track:
            if isinstance(item, dict) and str(item.get("id")) == question_id:
                item["text"] = text
                return
        custom_by_track.append({"id": question_id, "text": text, "order": len(custom_by_track) + 1})

    def update_school_settings(self, school: str, updates: dict[str, str]) -> None:
        school = str(school or "").strip()
        if not school:
            raise ValueError("School is required.")
        current = dict(self.school_settings.get(school, {}))
        current.setdefault("full_time_template", "")
        current.setdefault("part_time_template", "")
        current.setdefault("offer_output_dir", "")
        current.setdefault("interview_notes_dir", "")
        current.update({str(key): str(value) for key, value in updates.items()})
        self.school_settings[school] = current

    def update_prompt(self, key: str, value: str) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("Prompt key is required.")
        self.prompts[key] = str(value)

    def update_deepseek_model(self, model: str) -> None:
        clean_model = str(model or "").strip()
        self.app_settings["deepseek_summary_model"] = clean_model

    def update_notification_rule(self, event_type: str, updates: dict[str, str]) -> None:
        event_type = str(event_type or "").strip()
        if not event_type:
            raise ValueError("Notification event type is required.")
        current: NotificationRule | None = None
        for rule in self.notification_rules:
            if rule.event_type == event_type:
                current = rule
                break
        recipients_text = str(updates.get("recipients", "")).strip()
        recipients = [
            NotificationRecipient(email=email.strip())
            for email in recipients_text.split(",")
            if email.strip()
        ]
        active_text = str(updates.get("active", "true")).strip().lower()
        replacement = NotificationRule(
            id=current.id if current else None,
            event_type=event_type,
            label=str(updates.get("label", current.label if current else event_type)).strip(),
            subject_template=str(updates.get("subject_template", current.subject_template if current else "")).strip(),
            body_template=str(updates.get("body_template", current.body_template if current else "")).strip(),
            recipients=recipients if recipients_text else (current.recipients if current else []),
            active=active_text not in {"0", "false", "no", "off"},
            created_at=current.created_at if current else "",
            updated_at=current.updated_at if current else "",
        )
        self.notification_rules = [
            rule for rule in self.notification_rules
            if not (rule.event_type == event_type and rule.id == replacement.id)
        ]
        self.notification_rules.append(replacement)

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            normalized = normalize_deepseek_prompt_templates(self.prompts)
            missing_prompts = [key for key, value in normalized.items() if isinstance(value, str) and not value.strip()]
            if missing_prompts:
                errors.append(f"Prompt cannot be blank: {missing_prompts[0]}")
        except Exception as exc:
            errors.append(f"Prompt validation failed: {exc}")
        for cfg in self.school_settings.values():
            notes_dir = str(cfg.get("interview_notes_dir", "") or "")
            if any(part.strip() == ".." for part in notes_dir.replace("/", "\\").split("\\")):
                errors.append("Interview notes folder cannot contain '..'.")
                break
        selected_model = str(self.app_settings.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
        if selected_model not in DEEPSEEK_MODEL_CHOICES:
            errors.append(f"DeepSeek model must be one of: {', '.join(DEEPSEEK_MODEL_CHOICES)}.")
        for rule in self.notification_rules:
            if not rule.event_type.strip():
                errors.append("Notification event type is required.")
                break
            if not rule.label.strip():
                errors.append("Notification label is required.")
                break
            for recipient in rule.recipients:
                if not is_valid_email_address(recipient.email):
                    errors.append("Invalid notification recipient email.")
                    return errors
        return errors


class AdminStudio:
    def __init__(
        self,
        *,
        paths: AdminStudioPaths,
        rubric: dict[str, Any],
        overrides: dict[str, Any],
        school_settings: dict[str, dict[str, str]],
        prompts: dict[str, Any],
        app_settings: dict[str, Any],
        notification_rules: list[NotificationRule],
    ) -> None:
        self.paths = _normalize_paths(paths)
        self.rubric = deepcopy(rubric)
        self.overrides = deepcopy(overrides)
        self.school_settings = deepcopy(school_settings)
        self.prompts = deepcopy(prompts)
        self.app_settings = deepcopy(app_settings)
        self.notification_rules = deepcopy(notification_rules)

    @classmethod
    def load(cls, paths: AdminStudioPaths | None = None) -> "AdminStudio":
        paths = _normalize_paths(paths or AdminStudioPaths())
        rubric = _read_json_object(paths.rubric_path)
        overrides_store = QuestionOverridesStore(paths.overrides_path)
        school_store = SchoolOfferSettingsStore(paths.school_settings_path)
        prompts = normalize_deepseek_prompt_templates(_read_json_object(paths.prompts_path))
        app_settings = InterviewAppSettingsStore(paths.app_settings_path).load()
        notification_store = NotificationStore(paths.notification_rules_path)
        notification_store.ensure_default_rules()
        notification_rules = notification_store.list_rules()
        return cls(
            paths=paths,
            rubric=rubric,
            overrides=overrides_store.data,
            school_settings=school_store.load(),
            prompts=prompts,
            app_settings=app_settings,
            notification_rules=notification_rules,
        )

    def create_draft(self) -> AdminStudioDraft:
        return AdminStudioDraft.from_payloads(
            rubric=self.rubric,
            overrides=self.overrides,
            school_settings=self.school_settings,
            prompts=self.prompts,
            app_settings=self.app_settings,
            notification_rules=self.notification_rules,
        )

    def summary(self, draft: AdminStudioDraft | None = None) -> AdminStudioSummary:
        active = draft or self.create_draft()
        tracks = active.rubric.get("tracks", {}) if isinstance(active.rubric, dict) else {}
        traits = active.rubric.get("traits", []) if isinstance(active.rubric, dict) else []
        custom_count = sum(len(items or []) for items in (active.overrides.get("custom_questions", {}) or {}).values())
        sections = [
            AdminSection("questions", "Questions & Flow", "Edit interview flow and custom questions.", len(traits) + custom_count),
            AdminSection("rubrics", "Rubrics", "Edit scored traits and descriptors.", len(traits)),
            AdminSection("signals", "Signal Hints", "Review runtime signal definitions.", len(traits)),
            AdminSection("templates", "Templates & Folders", "Edit school templates and output folders.", len(active.school_settings)),
            AdminSection("notifications", "Notifications", "Edit checkpoint email rules and recipients.", len(active.notification_rules)),
            AdminSection("deepseek_model", "DeepSeek Model", "Choose local model speed and quality.", 1),
            AdminSection("prompts", "DeepSeek Prompts", "Edit local prompt templates.", len(active.prompts)),
            AdminSection("advanced", "Advanced JSON", "Review source JSON files with safeguards.", 5),
        ]
        return AdminStudioSummary(
            sections=sections,
            track_count=len(tracks),
            question_count=len(traits) + custom_count,
            dirty_count=len(active.changed_payloads()),
            validation_errors=active.validate(),
        )

    def apply_draft(self, draft: AdminStudioDraft, *, confirm: bool) -> AdminApplyResult:
        errors = draft.validate()
        if errors:
            return AdminApplyResult(applied=False, validation_errors=errors)
        changed = draft.changed_payloads()
        if not confirm or not changed:
            return AdminApplyResult(applied=False)
        backup_paths = self._backup_changed_files(changed)
        if "rubric.json" in changed:
            QuestionSettingsService(self.paths.rubric_path, draft.baseline_rubric).save_rubric(draft.rubric)
        if "question_overrides.json" in changed:
            atomic_write_json(self.paths.overrides_path, draft.overrides, indent=2, ensure_ascii=False)
        if "school_offer_settings.json" in changed:
            SchoolOfferSettingsStore(self.paths.school_settings_path).save(draft.school_settings)
        if "deepseek_prompts.json" in changed:
            atomic_write_json(self.paths.prompts_path, normalize_deepseek_prompt_templates(draft.prompts), indent=2, ensure_ascii=False)
        if "interview_app_settings.json" in changed:
            InterviewAppSettingsStore(self.paths.app_settings_path).save(draft.app_settings)
        if "notification_rules.sqlite3" in changed:
            store = NotificationStore(self.paths.notification_rules_path)
            for rule in draft.notification_rules:
                store.save_rule(rule)
        self.rubric = deepcopy(draft.rubric)
        self.overrides = deepcopy(draft.overrides)
        self.school_settings = deepcopy(draft.school_settings)
        self.prompts = deepcopy(draft.prompts)
        self.app_settings = deepcopy(draft.app_settings)
        self.notification_rules = deepcopy(draft.notification_rules)
        return AdminApplyResult(applied=True, changed_files=list(changed), backup_paths=backup_paths)

    def _backup_changed_files(self, changed: dict[str, tuple[Any, Any]]) -> list[Path]:
        backup_dir = self.paths.backup_dir or (self.paths.school_settings_path.parent / "admin_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        source_by_name = {
            "rubric.json": self.paths.rubric_path,
            "question_overrides.json": self.paths.overrides_path,
            "school_offer_settings.json": self.paths.school_settings_path,
            "deepseek_prompts.json": self.paths.prompts_path,
            "interview_app_settings.json": self.paths.app_settings_path,
            "notification_rules.sqlite3": self.paths.notification_rules_path,
        }
        backups: list[Path] = []
        for filename in changed:
            source = source_by_name[filename]
            backup = backup_dir / f"{Path(filename).stem}.{stamp}.bak.json"
            if source.exists():
                shutil.copy2(source, backup)
            else:
                atomic_write_json(backup, {}, indent=2, ensure_ascii=False)
            backups.append(backup)
        return backups


def _normalize_paths(paths: AdminStudioPaths) -> AdminStudioPaths:
    backup_dir = Path(paths.backup_dir) if paths.backup_dir is not None else None
    return AdminStudioPaths(
        rubric_path=Path(paths.rubric_path),
        overrides_path=Path(paths.overrides_path),
        school_settings_path=Path(paths.school_settings_path),
        prompts_path=Path(paths.prompts_path),
        app_settings_path=Path(paths.app_settings_path),
        notification_rules_path=Path(paths.notification_rules_path),
        backup_dir=backup_dir,
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _trait_change_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_traits = {str(item.get("id")): item for item in before.get("traits", []) if isinstance(item, dict)}
    after_traits = {str(item.get("id")): item for item in after.get("traits", []) if isinstance(item, dict)}
    lines: list[str] = []
    for trait_id, after_trait in after_traits.items():
        before_trait = before_traits.get(trait_id, {})
        for field_name in ("name", "primary_question", "priority", "weight"):
            old = before_trait.get(field_name)
            new = after_trait.get(field_name)
            if old != new:
                lines.append(f"{trait_id} {field_name}: {old} -> {new}")
    return lines


def _school_change_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for school, cfg in after.items():
        old_cfg = before.get(school, {})
        for field_name in ("full_time_template", "part_time_template", "offer_output_dir", "interview_notes_dir"):
            old = old_cfg.get(field_name, "")
            new = cfg.get(field_name, "")
            if old != new:
                lines.append(f"{school} {field_name}: {old} -> {new}")
    return lines


def _prompt_change_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, new in after.items():
        if before.get(key) != new:
            lines.append(f"{key} prompt changed.")
    return lines


def _app_settings_change_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old_model = str(before.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
    new_model = str(after.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
    if old_model != new_model:
        return [f"DeepSeek model: {old_model} -> {new_model}"]
    return []


def _notification_rule_snapshot(rules: list[NotificationRule]) -> list[dict[str, Any]]:
    return [
        {
            "id": rule.id,
            "event_type": rule.event_type,
            "label": rule.label,
            "active": rule.active,
            "subject_template": rule.subject_template,
            "body_template": rule.body_template,
            "recipients": [
                {
                    "email": recipient.email,
                    "name": recipient.name,
                    "role_label": recipient.role_label,
                    "active": recipient.active,
                }
                for recipient in rule.recipients
            ],
        }
        for rule in sorted(rules, key=lambda item: (item.event_type, item.id or 0, item.label))
    ]


def _notification_change_lines(before: list[NotificationRule], after: list[NotificationRule]) -> list[str]:
    if _notification_rule_snapshot(before) == _notification_rule_snapshot(after):
        return []
    return ["Notification rules changed."]
