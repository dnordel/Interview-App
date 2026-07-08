from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from platform_services import atomic_write_json
from question_runtime_definition_service import ensure_valid_trait_id


class QuestionSettingsService:
    def __init__(self, rubric_path: Path, rubric_data: dict[str, Any]):
        self.rubric_path = Path(rubric_path)
        self._defaults = deepcopy(rubric_data)
        self._undo_stack: list[dict[str, Any]] = []

    @staticmethod
    def _trait_index(rubric: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(t.get("id")): t for t in rubric.get("traits", [])}

    def checkpoint(self, rubric: dict[str, Any]) -> None:
        self._undo_stack.append(deepcopy(rubric))

    def undo(self) -> dict[str, Any] | None:
        if not self._undo_stack:
            return None
        return self._undo_stack.pop()

    def restore_defaults(self) -> dict[str, Any]:
        return deepcopy(self._defaults)

    def save_rubric(self, rubric: dict[str, Any]) -> None:
        atomic_write_json(self.rubric_path, rubric, indent=2, ensure_ascii=False)

    def export_questions(self, rubric: dict[str, Any], path: Path) -> None:
        payload = {
            "tracks": rubric.get("tracks", {}),
            "traits": rubric.get("traits", []),
        }
        atomic_write_json(path, payload, indent=2, ensure_ascii=False)

    def import_questions(self, rubric: dict[str, Any], path: Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle) or {}
        traits = payload.get("traits")
        if not isinstance(traits, list) or not traits:
            raise ValueError("Imported file must include a non-empty 'traits' list.")

        merged = deepcopy(rubric)
        tracks = payload.get("tracks")
        if isinstance(tracks, dict) and tracks:
            merged["tracks"] = tracks
        merged["traits"] = [self._validated_trait(merged, trait) for trait in traits]
        return merged

    def update_trait(self, rubric: dict[str, Any], trait_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        trait_id = ensure_valid_trait_id(trait_id)
        updated = deepcopy(rubric)
        by_id = self._trait_index(updated)
        trait = by_id.get(trait_id)
        if trait is None:
            raise ValueError(f"Trait not found: {trait_id}")
        trait.update(updates)
        self._validated_trait(updated, trait)
        return updated

    def add_trait(self, rubric: dict[str, Any], trait: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(rubric)
        traits = list(updated.get("traits", []))
        trait_id = ensure_valid_trait_id(str(trait.get("id") or "").strip())
        if trait_id in {str(t.get("id")) for t in traits}:
            raise ValueError(f"Trait id already exists: {trait_id}")
        trait = {**trait, "id": trait_id}
        trait = self._validated_trait(updated, trait)
        traits.append(trait)
        updated["traits"] = traits
        return updated

    def delete_trait(self, rubric: dict[str, Any], trait_id: str) -> dict[str, Any]:
        trait_id = ensure_valid_trait_id(trait_id)
        updated = deepcopy(rubric)
        traits = [t for t in updated.get("traits", []) if str(t.get("id")) != trait_id]
        if len(traits) == len(updated.get("traits", [])):
            raise ValueError(f"Trait not found: {trait_id}")
        updated["traits"] = traits
        return updated

    @classmethod
    def _validated_trait(cls, rubric: dict[str, Any], trait: dict[str, Any]) -> dict[str, Any]:
        source = dict(trait or {})
        source["id"] = ensure_valid_trait_id(str(source.get("id") or "").strip())
        source["name"] = cls._require_text(source.get("name"), "Trait name is required.")
        source["primary_question"] = cls._require_text(source.get("primary_question"), "Primary question is required.")
        source["priority"] = str(source.get("priority") or "non-critical").strip() or "non-critical"
        source["weight"] = cls._validated_weight(source.get("weight"))
        source["descriptors"] = cls._validated_anchor_map(source.get("descriptors"), "descriptors")
        source["sample_answers"] = cls._validated_anchor_map(source.get("sample_answers"), "sample_answers")
        source["applicable_tracks"] = cls._validated_applicable_tracks(source.get("applicable_tracks"), rubric)
        return source

    @staticmethod
    def _require_text(value: Any, message: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(message)
        return text

    @staticmethod
    def _validated_weight(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("Weight must be between 0 and 5.")
        try:
            weight = float(str(value or "0").strip())
        except ValueError as exc:
            raise ValueError("Weight must be between 0 and 5.") from exc
        if weight < 0 or weight > 5:
            raise ValueError("Weight must be between 0 and 5.")
        return weight

    @staticmethod
    def _validated_anchor_map(value: Any, field_name: str) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} must be a JSON object with keys 1 through 5.")
        normalized = {str(key): str(item or "").strip() for key, item in value.items()}
        missing = [key for key in ("1", "2", "3", "4", "5") if key not in normalized]
        if missing:
            raise ValueError(f"{field_name} must include keys 1 through 5.")
        return {key: normalized[key] for key in ("1", "2", "3", "4", "5")}

    @staticmethod
    def _validated_applicable_tracks(value: Any, rubric: dict[str, Any]) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("Applicable tracks must be a list.")
        tracks = [str(item).strip() for item in value if str(item).strip()]
        if not tracks:
            raise ValueError("At least one applicable track is required.")
        if "all" in tracks:
            return ["all"]
        known_tracks = set((rubric.get("tracks") or {}).keys())
        unknown_tracks = [track for track in tracks if track not in known_tracks]
        if unknown_tracks:
            raise ValueError(f"Unknown applicable track: {unknown_tracks[0]}")
        return tracks


__all__ = ["QuestionSettingsService"]
