from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from platform_services import atomic_write_json
from scoring_reporting import load_trait_signal_ui_definition

RuntimeSignalDefinition = dict[str, Any]
RuntimeSignalRecord = dict[str, Any]
RuntimeSignalGroup = dict[str, Any]
TRAIT_FILE_PATTERN = "T*.json"
TRAIT_ID_ALIAS_PATTERN = re.compile(r"trait_(\d+)", re.IGNORECASE)
PREFIXED_TRAIT_ID_ALIAS_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9]*)_trait_(\d+)", re.IGNORECASE)
BSS_TRAIT_ID_ALIAS_PATTERN = PREFIXED_TRAIT_ID_ALIAS_PATTERN
RUNTIME_TRAIT_ID_PATTERN = re.compile(r"(?:[A-Z][A-Z0-9]*_)?T\d+(?:_[A-Za-z0-9_]+)?")
SIGNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
GROUP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


class QuestionRuntimeDefinitionService:
    def __init__(self, traits_dir: Path):
        self.traits_dir = Path(traits_dir)

    def load_definition(self, trait_id: str) -> RuntimeSignalDefinition:
        definition = self._load_definition_or_empty(trait_id)
        return normalize_runtime_definition(definition, trait_id=trait_id)

    def save_definition(
        self,
        trait_id: str,
        trait_name: str,
        definition: dict[str, Any],
    ) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition, trait_id=trait_id, trait_name=trait_name)
        target_path = self._target_path(trait_id, trait_name)
        existing_path = self._find_trait_file(trait_id)
        atomic_write_json(target_path, normalized, indent=2, ensure_ascii=False)
        if existing_path and existing_path != target_path and existing_path.exists():
            existing_path.unlink()
        return normalized

    def create_definition(self, trait_id: str, trait_name: str, question: str) -> RuntimeSignalDefinition:
        definition = default_runtime_definition(trait_id, question=question)
        return self.save_definition(trait_id, trait_name, definition)

    def delete_definition(self, trait_id: str) -> None:
        existing_path = self._find_trait_file(trait_id)
        if existing_path and existing_path.exists():
            existing_path.unlink()

    def sync_with_trait(self, trait_id: str, trait_name: str, question: str) -> RuntimeSignalDefinition:
        existing = self._load_definition_or_empty(trait_id)
        definition = normalize_runtime_definition(existing, trait_id=trait_id, trait_name=trait_name)
        definition["question"] = question.strip()
        return self.save_definition(trait_id, trait_name, definition)

    def add_core_signal(self, definition: dict[str, Any], signal: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["core_signals"].append(_normalize_signal(signal, default_group="Core"))
        return _finalize_definition(normalized)

    def update_core_signal(self, definition: dict[str, Any], signal_ref: str, updates: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        updated_signals = _replace_signal(normalized["core_signals"], signal_ref, updates, default_group="Core")
        normalized["core_signals"] = updated_signals
        return _finalize_definition(normalized)

    def delete_core_signal(self, definition: dict[str, Any], signal_ref: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["core_signals"] = _delete_signal(normalized["core_signals"], signal_ref)
        return _finalize_definition(normalized)

    def add_extended_group(self, definition: dict[str, Any], group: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"].append(_normalize_group(group))
        return _finalize_definition(normalized)

    def update_extended_group(self, definition: dict[str, Any], group_id: str, updates: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _replace_group(normalized["extended_signal_groups"], group_id, updates)
        return _finalize_definition(normalized)

    def delete_extended_group(self, definition: dict[str, Any], group_id: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _delete_group(normalized["extended_signal_groups"], group_id)
        return _finalize_definition(normalized)

    def add_group_signal(self, definition: dict[str, Any], group_id: str, signal: dict[str, Any]) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_added(normalized["extended_signal_groups"], group_id, signal)
        return _finalize_definition(normalized)

    def update_group_signal(
        self,
        definition: dict[str, Any],
        group_id: str,
        signal_ref: str,
        updates: dict[str, Any],
    ) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_updated(
            normalized["extended_signal_groups"],
            group_id,
            signal_ref,
            updates,
        )
        return _finalize_definition(normalized)

    def delete_group_signal(self, definition: dict[str, Any], group_id: str, signal_ref: str) -> RuntimeSignalDefinition:
        normalized = normalize_runtime_definition(definition)
        normalized["extended_signal_groups"] = _with_group_signal_deleted(normalized["extended_signal_groups"], group_id, signal_ref)
        return _finalize_definition(normalized)

    def _load_definition_or_empty(self, trait_id: str) -> RuntimeSignalDefinition:
        existing_path = self._find_trait_file(trait_id)
        if not existing_path:
            return self._load_weighted_definition_or_empty(trait_id)
        payload = json.loads(existing_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        return self._load_weighted_definition_or_empty(trait_id)

    def _load_weighted_definition_or_empty(self, trait_id: str) -> RuntimeSignalDefinition:
        try:
            signal_definition = load_trait_signal_ui_definition(trait_id)
        except Exception:
            return default_runtime_definition(trait_id)
        if not signal_definition.get("valid_signal_ids"):
            return default_runtime_definition(trait_id)
        return {
            "trait_id": build_runtime_trait_id(trait_id, trait_name=""),
            "question": "",
            "core_signals": [
                {
                    "ref": signal.get("signal_id", ""),
                    "label": signal.get("label", ""),
                    "weight": signal.get("weight", 0),
                    "group": "Core",
                    "is_critical": signal.get("is_critical", False),
                }
                for signal in signal_definition.get("core_signals", []) or []
            ],
            "extended_signal_groups": [
                {
                    "group_id": group.get("group_id", ""),
                    "group_label": group.get("group_label", ""),
                    "signals": [
                        {
                            "ref": signal.get("signal_id", ""),
                            "label": signal.get("label", ""),
                            "weight": signal.get("weight", 0),
                            "group": group.get("group_label", ""),
                            "is_critical": signal.get("is_critical", False),
                        }
                        for signal in group.get("signals", []) or []
                    ],
                }
                for group in signal_definition.get("extended_groups", []) or []
            ],
        }

    def _find_trait_file(self, trait_id: str) -> Path | None:
        runtime_trait_id = runtime_trait_id_for_rubric_trait(trait_id)
        for candidate in sorted(self.traits_dir.glob(TRAIT_FILE_PATTERN)):
            if candidate.name == "trait_based_scoring_contract.yaml":
                continue
            payload = _read_json(candidate)
            if str(payload.get("trait_id", "") or "").strip() == runtime_trait_id:
                return candidate
        prefix = runtime_trait_id_prefix(trait_id)
        if not prefix:
            return None
        matches = sorted(self.traits_dir.glob(f"{prefix}*.json"))
        return matches[0] if matches else None

    def _target_path(self, trait_id: str, trait_name: str) -> Path:
        runtime_trait_id = build_runtime_trait_id(trait_id, trait_name=trait_name)
        return self.traits_dir / f"{runtime_trait_id}.json"


def default_runtime_definition(trait_id: str, *, question: str = "") -> RuntimeSignalDefinition:
    return {
        "trait_id": build_runtime_trait_id(trait_id, trait_name=""),
        "question": str(question or "").strip(),
        "core_signals": [],
        "extended_signal_groups": [],
    }


def normalize_runtime_definition(
    definition: dict[str, Any] | None,
    *,
    trait_id: str | None = None,
    trait_name: str = "",
) -> RuntimeSignalDefinition:
    source = definition if isinstance(definition, dict) else {}
    resolved_trait_id = build_runtime_trait_id(
        trait_id or source.get("trait_id", ""),
        trait_name=trait_name,
        existing_runtime_trait_id=source.get("trait_id", ""),
    )
    normalized = {
        "trait_id": resolved_trait_id,
        "question": str(source.get("question", "") or "").strip(),
        "core_signals": [_normalize_signal(signal, default_group="Core") for signal in _as_dict_list(source.get("core_signals"))],
        "extended_signal_groups": [_normalize_group(group) for group in _as_dict_list(source.get("extended_signal_groups"))],
    }
    return _finalize_definition(normalized)


def normalize_runtime_signal(signal: dict[str, Any], *, default_group: str) -> RuntimeSignalRecord:
    return _normalize_signal(signal, default_group=default_group)


def normalize_runtime_group(group: dict[str, Any]) -> RuntimeSignalGroup:
    return _normalize_group(group)


def runtime_trait_id_for_rubric_trait(trait_id: str) -> str:
    candidate = str(trait_id or "").strip()
    if not candidate:
        raise ValueError("Trait id is required.")
    match = TRAIT_ID_ALIAS_PATTERN.fullmatch(candidate)
    if match:
        return f"T{int(match.group(1))}"
    prefixed_match = PREFIXED_TRAIT_ID_ALIAS_PATTERN.fullmatch(candidate)
    if prefixed_match:
        prefix = prefixed_match.group(1).upper()
        return f"{prefix}_T{int(prefixed_match.group(2))}"
    if RUNTIME_TRAIT_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Trait id must use the 'trait_<number>' or '<prefix>_trait_<number>' format.")


def build_runtime_trait_id(trait_id: str, *, trait_name: str, existing_runtime_trait_id: Any = "") -> str:
    existing_candidate = str(existing_runtime_trait_id or "").strip()
    prefix = runtime_trait_id_for_rubric_trait(trait_id)
    if existing_candidate.startswith(f"{prefix}_"):
        return existing_candidate
    suffix = slugify_trait_name(trait_name)
    if suffix:
        return f"{prefix}_{suffix}"
    return prefix


def runtime_trait_id_prefix(trait_id: str) -> str:
    return f"{runtime_trait_id_for_rubric_trait(trait_id)}_"


def slugify_trait_name(trait_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(trait_name or "").strip())
    return cleaned.strip("_")


def ensure_valid_trait_id(trait_id: str) -> str:
    normalized = str(trait_id or "").strip()
    if not normalized:
        raise ValueError("Trait id is required.")
    runtime_trait_id_for_rubric_trait(normalized)
    return normalized


def next_trait_id(rubric: dict[str, Any]) -> str:
    numeric_ids: list[int] = []
    for trait in rubric.get("traits", []):
        match = TRAIT_ID_ALIAS_PATTERN.fullmatch(str(trait.get("id", "") or "").strip())
        if match:
            numeric_ids.append(int(match.group(1)))
    return f"trait_{max(numeric_ids, default=0) + 1}"


def list_signal_refs(definition: dict[str, Any]) -> list[str]:
    normalized = normalize_runtime_definition(definition)
    refs = [signal["ref"] for signal in normalized["core_signals"]]
    for group in normalized["extended_signal_groups"]:
        refs.extend(signal["ref"] for signal in group["signals"])
    return refs


def _normalize_signal(signal: dict[str, Any], *, default_group: str) -> RuntimeSignalRecord:
    source = signal if isinstance(signal, dict) else {}
    normalized = {
        "ref": _normalize_signal_ref(source.get("ref")),
        "label": str(source.get("label", "") or "").strip(),
        "weight": _normalize_weight(source.get("weight")),
        "group": str(source.get("group", "") or default_group).strip() or default_group,
        "is_critical": bool(source.get("is_critical", False)),
    }
    if not normalized["label"]:
        normalized["label"] = normalized["ref"]
    return normalized


def _normalize_group(group: dict[str, Any]) -> RuntimeSignalGroup:
    source = group if isinstance(group, dict) else {}
    group_label = str(source.get("group_label", "") or "").strip() or "Extended Group"
    signals = [_normalize_signal(signal, default_group=group_label) for signal in _as_dict_list(source.get("signals"))]
    return {"group_id": _normalize_group_id(source.get("group_id")), "group_label": group_label, "signals": signals}


def _finalize_definition(definition: RuntimeSignalDefinition) -> RuntimeSignalDefinition:
    _validate_runtime_definition(definition)
    return deepcopy(definition)


def _validate_runtime_definition(definition: RuntimeSignalDefinition) -> None:
    seen_refs: set[str] = set()
    for signal in definition["core_signals"]:
        _ensure_unique_ref(seen_refs, signal["ref"])
    for group in definition["extended_signal_groups"]:
        for signal in group["signals"]:
            _ensure_unique_ref(seen_refs, signal["ref"])


def _ensure_unique_ref(seen_refs: set[str], signal_ref: str) -> None:
    if signal_ref not in seen_refs:
        seen_refs.add(signal_ref)
        return
    raise ValueError(f"Duplicate signal ref: {signal_ref}")


def _replace_signal(signals: list[RuntimeSignalRecord], signal_ref: str, updates: dict[str, Any], *, default_group: str) -> list[RuntimeSignalRecord]:
    normalized_ref = _normalize_signal_ref(signal_ref)
    updated_signals: list[RuntimeSignalRecord] = []
    replaced = False
    for signal in signals:
        if signal["ref"] != normalized_ref:
            updated_signals.append(signal)
            continue
        updated_signals.append(_normalize_signal({**signal, **updates}, default_group=default_group))
        replaced = True
    if replaced:
        return updated_signals
    raise ValueError(f"Signal not found: {normalized_ref}")


def _delete_signal(signals: list[RuntimeSignalRecord], signal_ref: str) -> list[RuntimeSignalRecord]:
    normalized_ref = _normalize_signal_ref(signal_ref)
    remaining = [signal for signal in signals if signal["ref"] != normalized_ref]
    if len(remaining) != len(signals):
        return remaining
    raise ValueError(f"Signal not found: {normalized_ref}")


def _replace_group(groups: list[RuntimeSignalGroup], group_id: str, updates: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    replaced = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        updated_groups.append(_normalize_group({**group, **updates}))
        replaced = True
    if replaced:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _delete_group(groups: list[RuntimeSignalGroup], group_id: str) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    remaining = [group for group in groups if group["group_id"] != normalized_group_id]
    if len(remaining) != len(groups):
        return remaining
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_added(groups: list[RuntimeSignalGroup], group_id: str, signal: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    added = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        next_signal = _normalize_signal(signal, default_group=group["group_label"])
        updated_groups.append({**group, "signals": [*group["signals"], next_signal]})
        added = True
    if added:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_updated(groups: list[RuntimeSignalGroup], group_id: str, signal_ref: str, updates: dict[str, Any]) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    replaced = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        signals = _replace_signal(group["signals"], signal_ref, updates, default_group=group["group_label"])
        updated_groups.append({**group, "signals": signals})
        replaced = True
    if replaced:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _with_group_signal_deleted(groups: list[RuntimeSignalGroup], group_id: str, signal_ref: str) -> list[RuntimeSignalGroup]:
    normalized_group_id = _normalize_group_id(group_id)
    updated_groups: list[RuntimeSignalGroup] = []
    deleted = False
    for group in groups:
        if group["group_id"] != normalized_group_id:
            updated_groups.append(group)
            continue
        signals = _delete_signal(group["signals"], signal_ref)
        updated_groups.append({**group, "signals": signals})
        deleted = True
    if deleted:
        return updated_groups
    raise ValueError(f"Extended group not found: {normalized_group_id}")


def _normalize_signal_ref(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("Signal ref is required.")
    if SIGNAL_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Signal ref must use letters, numbers, and underscores only.")


def _normalize_group_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("Extended group id is required.")
    if GROUP_ID_PATTERN.fullmatch(candidate):
        return candidate
    raise ValueError("Extended group id must use letters, numbers, and underscores only.")


def _normalize_weight(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Signal weight must be a number.")
    try:
        parsed = float(str(value or "0").strip())
    except ValueError as exc:
        raise ValueError("Signal weight must be a number.") from exc
    return parsed


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}
