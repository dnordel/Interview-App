from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any, Optional

from platform_services import (
    ConfigValidationError,
    atomic_write_json,
    load_json_dict,
    normalize_question_overrides_config,
    validate_disqualifier_config,
    validate_rubric_config,
)

DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS: dict[str, str] = {
    "Hawthorne": r"\LPL HAW Office Shared Docs\Staff\Candidates",
    "Long Beach": r"\Dropbox\LPL NLB Office Shared\Staff\Candidates",
    "North Long Beach": r"\Dropbox\LPL NLB Office Shared\Staff\Candidates",
    "Palmdale": r"\Dropbox\LPL PMD Office Shared\Staff\Candidates",
}


def default_school_offer_settings() -> dict[str, dict[str, str]]:
    return {
        school: {
            "full_time_template": "",
            "part_time_template": "",
            "offer_output_dir": "",
            "interview_notes_dir": notes_dir,
        }
        for school, notes_dir in DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS.items()
    }


def resolve_interview_notes_output_dir(
    base_dir: Path,
    school: str,
    settings: dict[str, dict[str, str]] | None = None,
) -> Path:
    school_name = str(school or "").strip()
    configured = _interview_notes_dir_for_school(school_name, settings or {})
    if not configured:
        return Path(base_dir) / "Indeed Interview Notes"

    configured_path = PureWindowsPath(configured)
    if configured_path.drive:
        _reject_unsafe_path_parts(configured_path.parts)
        return Path(configured).expanduser()

    parts = _portable_dropbox_path_parts(configured)
    if not parts:
        return Path(base_dir) / "Indeed Interview Notes"
    dropbox_root = _find_dropbox_root(Path(base_dir))
    return dropbox_root.joinpath(*parts)


def _interview_notes_dir_for_school(
    school: str,
    settings: dict[str, dict[str, str]],
) -> str:
    cfg = settings.get(school, {}) if isinstance(settings, dict) else {}
    if isinstance(cfg, dict):
        configured = str(cfg.get("interview_notes_dir", "")).strip()
        if configured:
            return configured
    return DEFAULT_SCHOOL_INTERVIEW_NOTES_DIRS.get(school, "")


def _find_dropbox_root(base_dir: Path) -> Path:
    resolved = Path(base_dir).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if "dropbox" in candidate.name.casefold():
            return candidate
    return resolved


def _portable_dropbox_path_parts(path_text: str) -> list[str]:
    text = str(path_text or "").strip()
    parts = [
        part.strip()
        for part in re.split(r"[\\/]+", text)
        if part.strip() and part.strip() != "."
    ]
    if parts and parts[0].casefold() == "dropbox":
        parts = parts[1:]
    _reject_unsafe_path_parts(parts)
    return parts


def _reject_unsafe_path_parts(parts: tuple[str, ...] | list[str]) -> None:
    if any(str(part).strip() == ".." for part in parts):
        raise ValueError("Interview notes folder cannot contain parent-directory segments.")

class RubricLoader:
    """Loads rubric.json and validates required structure."""

    def __init__(self, rubric_path: Path):
        self.rubric_path = Path(rubric_path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        data = load_json_dict(
            self.rubric_path,
            required=True,
            context="rubric.json",
        )
        self.validate(data)
        return data

    @staticmethod
    def validate(data: dict[str, Any]) -> None:
        try:
            validate_rubric_config(data)
        except ConfigValidationError as exc:
            raise ValueError(f"Invalid rubric configuration: {exc}") from exc

    def get_traits_for_track(self, track_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in self.data["traits"]:
            applicable = t.get("applicable_tracks", [])
            if "all" in applicable or track_key in applicable:
                out.append(t)
        return out


class DisqualifierSignalLibrary:
    """Loads disqualifier_signals.json (optional) and indexes it by trait_id."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()
        self.by_trait_id = self._build_index()

    def _load(self) -> dict[str, Any]:
        try:
            data = load_json_dict(
                self.path,
                required=False,
                context="disqualifier_signals.json",
                default={"questions": []},
            )
            validate_disqualifier_config(data)
            return data
        except ConfigValidationError:
            return {"questions": []}

    def _build_index(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for q in self.data.get("questions", []):
            raw_trait = str(q.get("trait_id", "")).strip()
            if raw_trait.isdigit():
                out[f"trait_{raw_trait}"] = q
            elif raw_trait:
                out[raw_trait] = q
        return out

    def get_for_trait(self, trait_id: str) -> Optional[dict[str, Any]]:
        return self.by_trait_id.get(trait_id)


# =========================
# Question overrides + custom questions + mixed flow
# =========================

class QuestionOverridesStore:
    """
    Persists GUI-edited question configuration without touching rubric.json.

    question_overrides.json:
    {
      "track_trait_order": {
        "<track_key>": ["trait_3", "trait_1", ...]
      },
      "trait_question_overrides": {
        "trait_1": "Overridden primary question text..."
      },
      "custom_questions": {
        "<track_key>": [
          {"id": "cq_...", "text": "Custom question?", "order": 1},
          ...
        ]
      },
      "track_question_flow": {
        "<track_key>": [
          {"type": "trait", "id": "trait_3"},
          {"type": "custom", "id": "cq_20260101_120000"},
          ...
        ]
      }
    }
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.load()

    @staticmethod
    def _default_data() -> dict[str, Any]:
        return {
            "track_trait_order": {},
            "trait_question_overrides": {},
            "custom_questions": {},
            "track_question_flow": {},
        }

    def _archive_corrupt_file(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        corrupt_path = self.path.with_name(f"{self.path.stem}.corrupt-{timestamp}{self.path.suffix}")
        try:
            self.path.replace(corrupt_path)
        except OSError:
            return

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_data()

        try:
            data = load_json_dict(
                self.path,
                required=False,
                context="question_overrides.json",
                default=self._default_data(),
            )
            return normalize_question_overrides_config(data)
        except (ConfigValidationError, OSError):
            self._archive_corrupt_file()
            return self._default_data()

    def save(self) -> None:
        atomic_write_json(self.path, self.data, indent=2, ensure_ascii=False)

    # ---- Trait ordering (legacy per track) ----
    def get_trait_order(self, track_key: str) -> list[str]:
        return list(self.data.get("track_trait_order", {}).get(track_key, []) or [])

    def set_trait_order(self, track_key: str, trait_ids: list[str]) -> None:
        self.data.setdefault("track_trait_order", {})[track_key] = list(trait_ids)
        self.save()

    # ---- Trait question text overrides ----
    def get_trait_question_override(self, trait_id: str) -> str | None:
        v = (self.data.get("trait_question_overrides", {}) or {}).get(trait_id)
        if isinstance(v, str) and v.strip():
            return v
        return None

    def set_trait_question_override(self, trait_id: str, text: str) -> None:
        self.data.setdefault("trait_question_overrides", {})[trait_id] = text.strip()
        self.save()

    def clear_trait_question_override(self, trait_id: str) -> None:
        overrides = self.data.setdefault("trait_question_overrides", {})
        if trait_id in overrides:
            del overrides[trait_id]
            self.save()

    # ---- Custom questions (CRUD per track) ----
    def list_custom_questions(self, track_key: str) -> list[dict[str, Any]]:
        items = list((self.data.get("custom_questions", {}) or {}).get(track_key, []) or [])
        return sorted(items, key=lambda x: (int(x.get("order", 999999)), str(x.get("text", "")).lower()))

    def upsert_custom_question(self, track_key: str, q: dict[str, Any]) -> None:
        self.data.setdefault("custom_questions", {}).setdefault(track_key, [])
        items = self.data["custom_questions"][track_key]

        qid = str(q.get("id") or "").strip()
        if not qid:
            raise ValueError("Custom question requires an id")

        for i, it in enumerate(items):
            if str(it.get("id")) == qid:
                items[i] = q
                self.save()
                return

        items.append(q)
        self.save()

    def delete_custom_question(self, track_key: str, qid: str) -> None:
        items = self.data.setdefault("custom_questions", {}).setdefault(track_key, [])
        self.data["custom_questions"][track_key] = [it for it in items if str(it.get("id")) != str(qid)]
        self.save()

    # ---- Mixed interview flow (per track) ----
    @staticmethod
    def _clean_flow_items(flow: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for it in flow or []:
            t = str(it.get("type", "")).strip().lower()
            qid = str(it.get("id", "")).strip()
            if t in {"trait", "custom"} and qid:
                cleaned.append({"type": t, "id": qid})
        return cleaned

    def get_question_flow_raw(self, track_key: str) -> list[dict[str, Any]]:
        flow = list((self.data.get("track_question_flow", {}) or {}).get(track_key, []) or [])
        return self._clean_flow_items(flow)

    def set_question_flow(self, track_key: str, flow: list[dict[str, Any]]) -> None:
        self.data.setdefault("track_question_flow", {})[track_key] = self._clean_flow_items(flow)
        self.save()

    def ensure_flow(
        self,
        track_key: str,
        valid_trait_ids_in_order: list[str],
        valid_custom_ids_in_order: list[str],
    ) -> list[dict[str, Any]]:
        """
        Returns a safe flow that includes:
        - all valid traits exactly once (non-deletable)
        - valid customs if referenced
        - any missing customs appended at the end
        - any missing traits appended at the end
        """
        raw = self.get_question_flow_raw(track_key)

        valid_trait_set = set(valid_trait_ids_in_order)
        valid_custom_set = set(valid_custom_ids_in_order)

        out: list[dict[str, Any]] = []
        seen_traits: set[str] = set()
        seen_customs: set[str] = set()

        for it in raw:
            t = it["type"]
            qid = it["id"]
            if t == "trait":
                if qid in valid_trait_set and qid not in seen_traits:
                    out.append({"type": "trait", "id": qid})
                    seen_traits.add(qid)
                continue
            if qid in valid_custom_set and qid not in seen_customs:
                out.append({"type": "custom", "id": qid})
                seen_customs.add(qid)

        for tid in valid_trait_ids_in_order:
            if tid not in seen_traits:
                out.append({"type": "trait", "id": tid})
                seen_traits.add(tid)

        for cid in valid_custom_ids_in_order:
            if cid not in seen_customs:
                out.append({"type": "custom", "id": cid})
                seen_customs.add(cid)

        if out != raw:
            self.set_question_flow(track_key, out)

        return out

    def remove_custom_from_flow(self, track_key: str, qid: str) -> None:
        flow = self.get_question_flow_raw(track_key)
        flow = [it for it in flow if not (it.get("type") == "custom" and str(it.get("id")) == str(qid))]
        self.set_question_flow(track_key, flow)

    def insert_custom_into_flow(self, track_key: str, qid: str, index: int) -> None:
        flow = self.get_question_flow_raw(track_key)
        if any(it.get("type") == "custom" and it.get("id") == qid for it in flow):
            return
        index = max(0, min(index, len(flow)))
        flow.insert(index, {"type": "custom", "id": qid})
        self.set_question_flow(track_key, flow)


# =========================
# Scoring and decisions
# =========================


class InterviewHistoryStore:
    """Persists finalized interview history to a dedicated JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        rows = self._load_from_path(self.path)
        legacy_rows = self._load_from_path(self._legacy_root_path())
        if not rows:
            return legacy_rows
        if not legacy_rows:
            return rows
        return self._merge_history_rows(legacy_rows, rows)

    def _load_from_path(self, path: Path | None) -> list[dict[str, Any]]:
        if path is None or not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _legacy_root_path(self) -> Path | None:
        if self.path.parent.name != "user_artifacts":
            return None
        legacy_path = self.path.parent.parent / self.path.name
        if legacy_path == self.path:
            return None
        return legacy_path

    @classmethod
    def _merge_history_rows(
        cls,
        legacy_rows: list[dict[str, Any]],
        canonical_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in [*legacy_rows, *canonical_rows]:
            key = cls.build_row_key(row)
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            merged.append(row)
        return merged

    def append(self, entry: dict[str, Any]) -> None:
        items = self.load()
        items.append(entry)
        self._save(items)

    @staticmethod
    def build_row_key(entry: dict[str, Any]) -> str:
        row_id = str(entry.get("history_id", "")).strip()
        if row_id:
            return row_id
        candidate = str(entry.get("candidate_name", "")).strip().lower()
        interview_date = str(entry.get("interview_date", "")).strip()
        saved_at = str(entry.get("saved_at", "")).strip()
        if not saved_at:
            return ""
        return f"{candidate}|{interview_date}|{saved_at}"

    def update_row(self, row_key: str, updates: dict[str, Any]) -> bool:
        key = str(row_key).strip()
        if not key or not updates:
            return False
        items = self.load()
        for item in items:
            if self.build_row_key(item) != key:
                continue
            item.update(updates)
            self._save(items)
            return True
        return False

    def delete_row(self, row_key: str) -> bool:
        key = str(row_key).strip()
        if not key:
            return False
        items = self.load()
        kept = [item for item in items if self.build_row_key(item) != key]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    def update_offer_state(self, row_key: str, offer_status: str, offer_letter_path: str = "") -> bool:
        key = str(row_key).strip()
        if not key:
            return False
        payload: dict[str, Any] = {"offer_status": str(offer_status).strip().lower()}
        if offer_letter_path:
            path_text = str(offer_letter_path).strip()
            payload["offer_letter_path"] = path_text
            payload["offer_path"] = path_text
        return self.update_row(key, payload)

    def repair_interview_notes_links(self, notes_dir: Path) -> int:
        rows = self._load_from_path(self.path)
        if not rows:
            return 0
        notes_index = self._interview_notes_index(Path(notes_dir))
        if not notes_index:
            return 0
        repaired = 0
        for row in rows:
            if self._has_existing_notes_link(row):
                continue
            candidate = self._normalize_match_text(str(row.get("candidate_name", "") or row.get("candidate", "")))
            interview_date = str(row.get("interview_date", "") or "").strip()
            school = self._normalize_match_text(str(row.get("school", "") or ""))
            notes_path = notes_index.get((interview_date, candidate, school)) or notes_index.get((interview_date, candidate, ""))
            if notes_path is None:
                continue
            path_text = str(notes_path)
            row["interview_notes_path"] = path_text
            row["saved_report_path"] = path_text
            row["notes_path"] = path_text
            row["report_path"] = path_text
            repaired += 1
        if repaired:
            self._save(rows)
        return repaired

    @classmethod
    def _interview_notes_index(cls, notes_dir: Path) -> dict[tuple[str, str, str], Path]:
        if not notes_dir.exists() or not notes_dir.is_dir():
            return {}
        index: dict[tuple[str, str, str], Path] = {}
        pattern = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}) - (?P<school>.+?) - (?P<candidate>.+?) - Interview\.docx$", re.IGNORECASE)
        for path in notes_dir.glob("*.docx"):
            match = pattern.match(path.name)
            if match is None:
                continue
            interview_date = match.group("date")
            school = cls._normalize_match_text(match.group("school"))
            candidate = cls._normalize_match_text(match.group("candidate"))
            index.setdefault((interview_date, candidate, school), path)
            index.setdefault((interview_date, candidate, ""), path)
        return index

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

    @staticmethod
    def _has_existing_notes_link(row: dict[str, Any]) -> bool:
        for key in ("interview_notes_path", "saved_report_path", "notes_path", "report_path"):
            path_text = str(row.get(key, "") or "").strip()
            if path_text and Path(path_text).exists():
                return True
        return False

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_write_json(self.path, items, indent=2, ensure_ascii=False)


class SchoolOfferSettingsStore:
    """Per-school templates and output folders for offer and interview-note generation."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        output: dict[str, dict[str, str]] = {}
        for school, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            output[str(school)] = {
                "full_time_template": str(cfg.get("full_time_template", "")).strip(),
                "part_time_template": str(cfg.get("part_time_template", "")).strip(),
                "offer_output_dir": str(cfg.get("offer_output_dir", "")).strip(),
                "interview_notes_dir": str(cfg.get("interview_notes_dir", "")).strip(),
            }
        return output

    def save(self, data: dict[str, dict[str, str]]) -> None:
        atomic_write_json(self.path, data, indent=2, ensure_ascii=False)


class SchoolEmailTemplateStore:
    """Per-school email template and recipient overrides."""

    TEMPLATE_KEYS = (
        "director_referral_subject_template",
        "director_referral_body_template",
        "director_email_to",
        "offer_approval_subject_template",
        "offer_approval_body_template",
        "offer_acceptance_subject_template",
        "offer_acceptance_body_template",
        "offer_email_to",
        "welcome_email_subject_template",
        "welcome_email_body_template",
    )

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def _sanitize_config(cls, cfg: dict[str, Any]) -> dict[str, str]:
        return {key: str(cfg.get(key, "")).strip() for key in cls.TEMPLATE_KEYS}

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}

        output: dict[str, dict[str, str]] = {}
        for school, cfg in data.items():
            if not isinstance(cfg, dict):
                continue
            output[str(school)] = self._sanitize_config(cfg)
        return output

    def save(self, data: dict[str, dict[str, str]]) -> None:
        payload = {str(school): self._sanitize_config(cfg) for school, cfg in data.items() if isinstance(cfg, dict)}
        atomic_write_json(self.path, payload, indent=2, ensure_ascii=False)


class InterviewAppSettingsStore:
    """Persists cross-launch interview app UI/runtime settings."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def save(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("Interview app settings must be a dictionary")
        atomic_write_json(self.path, data, indent=2, ensure_ascii=False)
