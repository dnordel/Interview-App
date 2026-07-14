from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from data_store import InterviewAppSettingsStore, QuestionOverridesStore, SchoolOfferSettingsStore
from interview_runtime import normalize_deepseek_prompt_templates
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
    backup_dir: Path | None = None


@dataclass(frozen=True)
class AdminSection:
    group: str
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
    rubric: dict[str, Any]
    overrides: dict[str, Any]
    school_settings: dict[str, dict[str, str]]
    prompts: dict[str, Any]
    app_settings: dict[str, Any]
    prompt_version_notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payloads(
        cls,
        *,
        rubric: dict[str, Any],
        overrides: dict[str, Any],
        school_settings: dict[str, dict[str, str]],
        prompts: dict[str, Any],
        app_settings: dict[str, Any] | None = None,
    ) -> "AdminStudioDraft":
        app_settings = app_settings or {}
        return cls(
            baseline_rubric=deepcopy(rubric),
            baseline_overrides=deepcopy(overrides),
            baseline_school_settings=deepcopy(school_settings),
            baseline_prompts=deepcopy(prompts),
            baseline_app_settings=deepcopy(app_settings),
            rubric=deepcopy(rubric),
            overrides=deepcopy(overrides),
            school_settings=deepcopy(school_settings),
            prompts=deepcopy(prompts),
            app_settings=deepcopy(app_settings),
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
        }
        for filename, (before, after) in pairs.items():
            if before != after:
                changed[filename] = (before, after)
        return changed

    def change_summary(self) -> AdminChangeSummary:
        changed = self.changed_payloads()
        lines: list[str] = []
        lines.extend(_track_change_lines(self.baseline_rubric, self.rubric))
        lines.extend(_trait_change_lines(self.baseline_rubric, self.rubric))
        lines.extend(_school_change_lines(self.baseline_school_settings, self.school_settings))
        lines.extend(_prompt_change_lines(self.baseline_prompts, self.prompts))
        lines.extend(_app_settings_change_lines(self.baseline_app_settings, self.app_settings))
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
        )

    def update_trait(self, trait_id: str, updates: dict[str, Any]) -> None:
        service = QuestionSettingsService(Path("rubric.json"), self.baseline_rubric)
        self.rubric = service.update_trait(self.rubric, trait_id, updates)

    def duplicate_trait(self, trait_id: str) -> str:
        clean_trait_id = str(trait_id or "").strip()
        traits = [trait for trait in self.rubric.get("traits", []) or [] if isinstance(trait, dict)]
        source = next((trait for trait in traits if str(trait.get("id", "")).strip() == clean_trait_id), None)
        if source is None:
            raise ValueError(f"Trait not found: {clean_trait_id}")
        existing_ids = {str(trait.get("id", "")).strip() for trait in traits}
        prefix, _, number = clean_trait_id.rpartition("_")
        if not prefix or not number.isdigit():
            raise ValueError("Trait id must end with a numeric suffix.")
        same_prefix_numbers = []
        for existing_id in existing_ids:
            existing_prefix, _, existing_number = existing_id.rpartition("_")
            if existing_prefix == prefix and existing_number.isdigit():
                same_prefix_numbers.append(int(existing_number))
        next_number = max(same_prefix_numbers or [int(number)]) + 1
        new_id = f"{prefix}_{next_number}"
        while new_id in existing_ids:
            next_number += 1
            new_id = f"{prefix}_{next_number}"
        duplicate = deepcopy(source)
        duplicate["id"] = new_id
        duplicate["name"] = f"{str(source.get('name', '')).strip()} Copy".strip()
        service = QuestionSettingsService(Path("rubric.json"), self.baseline_rubric)
        self.rubric = service.add_trait(self.rubric, duplicate)
        return new_id

    def delete_trait(self, trait_id: str) -> None:
        service = QuestionSettingsService(Path("rubric.json"), self.baseline_rubric)
        self.rubric = service.delete_trait(self.rubric, str(trait_id or "").strip())

    def add_track(self, track_key: str, label: str, description: str = "", *, active: bool = True) -> None:
        clean_key = str(track_key or "").strip().lower().replace("-", "_").replace(" ", "_")
        clean_label = str(label or "").strip()
        clean_description = str(description or "").strip()
        if not clean_key or not clean_label:
            raise ValueError("Track key and label are required.")
        if not all(ch.isalnum() or ch == "_" for ch in clean_key):
            raise ValueError("Track key may contain only letters, numbers, and underscores.")
        tracks = self.rubric.setdefault("tracks", {})
        if clean_key in tracks:
            raise ValueError("Track key already exists.")
        tracks[clean_key] = {
            "label": clean_label,
            "description": clean_description,
            "active": bool(active),
        }
        self.overrides.setdefault("track_question_flow", {}).setdefault(clean_key, [])

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

    def add_custom_question(
        self,
        track_key: str,
        question_id: str,
        label: str,
        text: str,
        *,
        section: str = "Qualification",
        position: int | None = None,
    ) -> None:
        track_key = str(track_key or "").strip()
        question_id = str(question_id or "").strip()
        clean_label = str(label or "").strip()
        clean_text = str(text or "").strip()
        clean_section = str(section or "Qualification").strip() or "Qualification"
        if not track_key or not question_id or not clean_text:
            raise ValueError("Question track, id, and text are required.")
        if not all(ch.isalnum() or ch in {"_", "-"} for ch in question_id):
            raise ValueError("Question id may contain only letters, numbers, underscores, and hyphens.")
        custom_by_track = self.overrides.setdefault("custom_questions", {}).setdefault(track_key, [])
        if any(isinstance(item, dict) and str(item.get("id")) == question_id for item in custom_by_track):
            raise ValueError("Question id already exists for this track.")
        order = int(position or (len(custom_by_track) + 1))
        custom_by_track.append({
            "id": question_id,
            "label": clean_label or question_id,
            "text": clean_text,
            "order": order,
            "section": clean_section,
        })
        flow = self.overrides.setdefault("track_question_flow", {}).setdefault(track_key, [])
        insert_at = max(0, min(order - 1, len(flow)))
        flow.insert(insert_at, {"type": "custom", "id": question_id})

    def move_question(self, track_key: str, from_index: int, to_index: int) -> None:
        track_key = str(track_key or "").strip()
        if not track_key:
            raise ValueError("Track key is required.")
        flow = self.overrides.setdefault("track_question_flow", {}).setdefault(track_key, [])
        if not isinstance(flow, list):
            raise ValueError("Track question flow must be a list.")
        source = int(from_index)
        target = int(to_index)
        if source < 0 or source >= len(flow) or target < 0 or target >= len(flow):
            raise ValueError("Question move is outside the track flow.")
        if source == target:
            return
        item = flow.pop(source)
        flow.insert(target, item)

    def delete_question(self, track_key: str, question_type: str, question_id: str) -> None:
        track_key = str(track_key or "").strip()
        question_type = str(question_type or "").strip().lower()
        question_id = str(question_id or "").strip()
        if not track_key or question_type not in {"custom", "trait"} or not question_id:
            raise ValueError("Question delete requires track, type, and id.")
        flow = self.overrides.setdefault("track_question_flow", {}).setdefault(track_key, [])
        if not isinstance(flow, list):
            raise ValueError("Track question flow must be a list.")
        original_len = len(flow)
        self.overrides["track_question_flow"][track_key] = [
            item for item in flow
            if not (
                isinstance(item, dict)
                and str(item.get("type", "")).strip().lower() == question_type
                and str(item.get("id", "")).strip() == question_id
            )
        ]
        if len(self.overrides["track_question_flow"][track_key]) == original_len:
            raise ValueError("Question was not found in the selected track flow.")
        if question_type != "custom":
            return
        custom_by_track = self.overrides.setdefault("custom_questions", {}).setdefault(track_key, [])
        remaining: list[Any] = []
        order = 1
        for item in custom_by_track:
            if isinstance(item, dict) and str(item.get("id", "")).strip() == question_id:
                continue
            if isinstance(item, dict):
                item["order"] = order
                order += 1
            remaining.append(item)
        self.overrides["custom_questions"][track_key] = remaining

    def duplicate_question(self, track_key: str, question_type: str, question_id: str) -> str:
        track_key = str(track_key or "").strip()
        question_type = str(question_type or "").strip().lower()
        question_id = str(question_id or "").strip()
        if not track_key or question_type not in {"custom", "trait"} or not question_id:
            raise ValueError("Question duplicate requires track, type, and id.")
        flow = self.overrides.setdefault("track_question_flow", {}).setdefault(track_key, [])
        if not isinstance(flow, list):
            raise ValueError("Track question flow must be a list.")
        source_index = -1
        for index, item in enumerate(flow):
            if (
                isinstance(item, dict)
                and str(item.get("type", "")).strip().lower() == question_type
                and str(item.get("id", "")).strip() == question_id
            ):
                source_index = index
                break
        if source_index < 0:
            raise ValueError("Question was not found in the selected track flow.")
        custom_by_track = self.overrides.setdefault("custom_questions", {}).setdefault(track_key, [])
        source_text = ""
        source_label = question_id
        source_section = "Qualification"
        if question_type == "custom":
            for item in custom_by_track:
                if isinstance(item, dict) and str(item.get("id", "")).strip() == question_id:
                    source_text = str(item.get("text", "")).strip()
                    source_label = str(item.get("label", question_id)).strip() or question_id
                    source_section = str(item.get("section", "Qualification")).strip() or "Qualification"
                    break
        else:
            for trait in self.rubric.get("traits", []) or []:
                if isinstance(trait, dict) and str(trait.get("id", "")).strip() == question_id:
                    source_text = str(trait.get("primary_question", "")).strip()
                    source_label = str(trait.get("name", question_id)).strip() or question_id
                    source_section = "Core Traits"
                    break
        if not source_text:
            raise ValueError("Question text is required before duplicating.")
        existing_ids = {
            str(item.get("id", "")).strip()
            for item in custom_by_track
            if isinstance(item, dict)
        }
        base_id = f"{question_id}-copy"
        new_id = base_id
        suffix = 2
        while new_id in existing_ids:
            new_id = f"{base_id}-{suffix}"
            suffix += 1
        order = source_index + 2
        custom_by_track.append({
            "id": new_id,
            "label": f"{source_label} Copy",
            "text": source_text,
            "order": order,
            "section": source_section,
        })
        flow.insert(source_index + 1, {"type": "custom", "id": new_id})
        for index, item in enumerate(custom_by_track, start=1):
            if isinstance(item, dict):
                item["order"] = index
        return new_id

    def update_school_settings(self, school: str, updates: dict[str, str]) -> None:
        school = str(school or "").strip()
        if not school:
            raise ValueError("School is required.")
        current = dict(self.school_settings.get(school, {}))
        current.setdefault("full_time_template", "")
        current.setdefault("part_time_template", "")
        current.setdefault("contractor_template", "")
        current.setdefault("offer_output_dir", "")
        current.setdefault("interview_notes_dir", "")
        current.update({str(key): str(value) for key, value in updates.items()})
        self.school_settings[school] = current

    def update_prompt(self, key: str, value: str) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("Prompt key is required.")
        self.prompts[key] = str(value)

    def update_prompt_version_note(self, key: str, note: str) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("Prompt key is required.")
        clean_note = str(note or "").strip()
        if clean_note:
            self.prompt_version_notes[key] = clean_note
            return
        self.prompt_version_notes.pop(key, None)

    def update_deepseek_model(self, model: str) -> None:
        clean_model = str(model or "").strip()
        self.app_settings["deepseek_summary_model"] = clean_model

    def validate(self) -> list[str]:
        errors: list[str] = []
        try:
            normalized = normalize_deepseek_prompt_templates(self.prompts)
            missing_prompts = [key for key, value in normalized.items() if isinstance(value, str) and not value.strip()]
            if missing_prompts:
                errors.append(f"Prompt cannot be blank: {missing_prompts[0]}")
            for key, value in normalized.items():
                if self.baseline_prompts.get(key) != value and not str(self.prompt_version_notes.get(str(key), "")).strip():
                    errors.append(f"DeepSeek prompt '{key}' requires version notes before publishing.")
                    break
        except Exception as exc:
            errors.append(f"Prompt validation failed: {exc}")
        for cfg in self.school_settings.values():
            notes_dir = str(cfg.get("interview_notes_dir", "") or "")
            if any(part.strip() == ".." for part in notes_dir.replace("/", "\\").split("\\")):
                errors.append("Interview notes folder cannot contain '..'.")
                break
            offer_paths = [
                str(cfg.get(key, "") or "")
                for key in ("full_time_template", "part_time_template", "contractor_template", "offer_output_dir")
            ]
            if any(
                part.strip() == ".."
                for path in offer_paths
                for part in path.replace("/", "\\").split("\\")
            ):
                errors.append("Offer paths cannot contain '..'.")
                break
            invalid_template = next(
                (
                    path
                    for path in offer_paths[:3]
                    if path and Path(path).suffix.casefold() not in {".docx", ".docm"}
                ),
                "",
            )
            if invalid_template:
                errors.append("Offer templates must use .docx or .docm files.")
                break
        selected_model = str(self.app_settings.get("deepseek_summary_model", "") or DEFAULT_DEEPSEEK_MODEL).strip()
        if selected_model not in DEEPSEEK_MODEL_CHOICES:
            errors.append(f"DeepSeek model must be one of: {', '.join(DEEPSEEK_MODEL_CHOICES)}.")
        question_flow = self.overrides.get("track_question_flow", {})
        if not isinstance(question_flow, dict):
            question_flow = {}
        track_keys = list((self.rubric.get("tracks") or {}).keys())
        for trait in self.rubric.get("traits", []):
            if not isinstance(trait, dict):
                continue
            trait_id = str(trait.get("id", "") or "").strip()
            if not trait_id:
                continue
            applicable_tracks = trait.get("applicable_tracks") or track_keys
            if not _trait_has_linked_question(question_flow, trait_id, applicable_tracks):
                errors.append(f"Rubric trait '{trait_id}' is missing a linked question in Questions & Flow.")
                break
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
    ) -> None:
        self.paths = _normalize_paths(paths)
        self.rubric = deepcopy(rubric)
        self.overrides = deepcopy(overrides)
        self.school_settings = deepcopy(school_settings)
        self.prompts = deepcopy(prompts)
        self.app_settings = deepcopy(app_settings)

    @classmethod
    def load(cls, paths: AdminStudioPaths | None = None) -> "AdminStudio":
        paths = _normalize_paths(paths or AdminStudioPaths())
        rubric = _read_json_object(paths.rubric_path)
        overrides_store = QuestionOverridesStore(paths.overrides_path)
        school_store = SchoolOfferSettingsStore(paths.school_settings_path)
        prompts = normalize_deepseek_prompt_templates(_read_json_object(paths.prompts_path))
        app_settings = InterviewAppSettingsStore(paths.app_settings_path).load()
        return cls(
            paths=paths,
            rubric=rubric,
            overrides=overrides_store.data,
            school_settings=school_store.load(),
            prompts=prompts,
            app_settings=app_settings,
        )

    def create_draft(self) -> AdminStudioDraft:
        return AdminStudioDraft.from_payloads(
            rubric=self.rubric,
            overrides=self.overrides,
            school_settings=self.school_settings,
            prompts=self.prompts,
            app_settings=self.app_settings,
        )

    def summary(self, draft: AdminStudioDraft | None = None) -> AdminStudioSummary:
        active = draft or self.create_draft()
        tracks = active.rubric.get("tracks", {}) if isinstance(active.rubric, dict) else {}
        traits = active.rubric.get("traits", []) if isinstance(active.rubric, dict) else []
        custom_count = sum(len(items or []) for items in (active.overrides.get("custom_questions", {}) or {}).values())
        sections = [
            AdminSection("Configuration", "dashboard", "Admin Dashboard", "Manage interview configuration, AI settings, templates, system health, and publishing status.", 0),
            AdminSection("Configuration", "questions", "Questions & Flow", "Build track-based interview flow with editable question cards.", len(traits) + custom_count),
            AdminSection("Configuration", "rubrics", "Rubrics", "Tune scored trait cards, weights, and descriptors.", len(traits)),
            AdminSection("Configuration", "signals", "Signal Hints", "Search trait signal definitions by category.", len(traits)),
            AdminSection("Configuration", "templates", "Templates & Folders", "Check school output folders and template health.", len(active.school_settings)),
            AdminSection("AI Settings", "deepseek_model", "DeepSeek Model", "Choose local model speed, quality, and hardware fit.", 1),
            AdminSection("AI Settings", "prompts", "DeepSeek Prompts", "Edit prompt templates with variables, preview, and validation.", len(active.prompts)),
            AdminSection("System", "advanced", "Advanced JSON", "Review source JSON health in a guarded read-only layout.", 5),
            AdminSection("System", "validation", "Validation", "Review blocking issues and jump to affected settings.", len(active.validate())),
            AdminSection("System", "email_settings", "Email Settings", "Configure shared company sender account for app notifications.", 1),
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
        self.rubric = deepcopy(draft.rubric)
        self.overrides = deepcopy(draft.overrides)
        self.school_settings = deepcopy(draft.school_settings)
        self.prompts = deepcopy(draft.prompts)
        self.app_settings = deepcopy(draft.app_settings)
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
        backup_dir=backup_dir,
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _trait_has_linked_question(question_flow: dict[str, Any], trait_id: str, tracks: Any) -> bool:
    track_list = tracks if isinstance(tracks, list) else []
    for track in track_list:
        for item in question_flow.get(str(track), []) or []:
            if isinstance(item, dict) and item.get("type") == "trait" and str(item.get("id", "")) == trait_id:
                return True
    return False


def _track_change_lines(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    before_tracks = before.get("tracks", {}) if isinstance(before, dict) else {}
    after_tracks = after.get("tracks", {}) if isinstance(after, dict) else {}
    for track_key, cfg in after_tracks.items():
        if track_key in before_tracks:
            continue
        label = cfg.get("label", track_key) if isinstance(cfg, dict) else track_key
        lines.append(f"Track added: {label} ({track_key})")
    return lines


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
        for field_name in ("full_time_template", "part_time_template", "contractor_template", "offer_output_dir", "interview_notes_dir"):
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
