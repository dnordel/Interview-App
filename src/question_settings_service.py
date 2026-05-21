from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from storage_utils import atomic_write_json


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
        merged["traits"] = traits
        tracks = payload.get("tracks")
        if isinstance(tracks, dict) and tracks:
            merged["tracks"] = tracks
        return merged

    def update_trait(self, rubric: dict[str, Any], trait_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(rubric)
        by_id = self._trait_index(updated)
        trait = by_id.get(trait_id)
        if trait is None:
            raise ValueError(f"Trait not found: {trait_id}")
        trait.update(updates)
        return updated

    def add_trait(self, rubric: dict[str, Any], trait: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(rubric)
        traits = list(updated.get("traits", []))
        trait_id = str(trait.get("id") or "").strip()
        if not trait_id:
            raise ValueError("Trait id is required")
        if trait_id in {str(t.get('id')) for t in traits}:
            raise ValueError(f"Trait id already exists: {trait_id}")
        traits.append(trait)
        updated["traits"] = traits
        return updated

    def delete_trait(self, rubric: dict[str, Any], trait_id: str) -> dict[str, Any]:
        updated = deepcopy(rubric)
        traits = [t for t in updated.get("traits", []) if str(t.get("id")) != trait_id]
        if len(traits) == len(updated.get("traits", [])):
            raise ValueError(f"Trait not found: {trait_id}")
        updated["traits"] = traits
        return updated
