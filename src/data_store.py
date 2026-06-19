from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from platform_services import (
    ConfigValidationError,
    atomic_write_json,
    load_json_dict,
    normalize_question_overrides_config,
    validate_disqualifier_config,
    validate_rubric_config,
)

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
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

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

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_write_json(self.path, items, indent=2, ensure_ascii=False)


class SchoolOfferSettingsStore:
    """Per-school templates and output folder for offer generation."""

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
