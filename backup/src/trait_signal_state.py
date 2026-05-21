from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from reporting import ReportingValidationError
from trait_definition_loader import canonical_trait_id, trait_id_aliases
from trait_signal_schema import (
    build_signal_dictionary_index,
    normalize_core_signals,
    normalize_extended_signal_groups,
)
from trait_scoring_adapter import (
    DEFAULT_ENGINE_MODULE_CONTRACT,
    DEFAULT_ENGINE_RUNTIME_CONTRACT,
    load_module_contract_runtime_bundle,
    normalize_trait_state_item,
    validate_runtime_bundle_metadata,
)


SignalUIDefinition = dict[str, Any]
DEFAULT_CORE_DISPLAY_STYLE = "checkbox"
DEFAULT_EXTENDED_DISPLAY_STYLE = "checkbox"
DEFAULT_CORE_SECTION_LABEL = "Core Signals (Most Important)"
DEFAULT_EXTENDED_SECTION_LABEL = "Additional Observations"
DEFAULT_EXTENDED_COLLAPSIBLE = True
DEFAULT_EXTENDED_DEFAULT_COLLAPSED = True


def default_signal_ui_definition(trait_id: str) -> SignalUIDefinition:
    return {
        "trait_id": trait_id,
        "core_display_style": DEFAULT_CORE_DISPLAY_STYLE,
        "extended_display_style": DEFAULT_EXTENDED_DISPLAY_STYLE,
        "core_section_label": DEFAULT_CORE_SECTION_LABEL,
        "extended_section_label": DEFAULT_EXTENDED_SECTION_LABEL,
        "extended_collapsible": DEFAULT_EXTENDED_COLLAPSIBLE,
        "extended_default_collapsed": DEFAULT_EXTENDED_DEFAULT_COLLAPSED,
        "core_signals": [],
        "extended_groups": [],
        "valid_signal_ids": [],
    }


def load_trait_signal_ui_definition(
    trait_id: str,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> SignalUIDefinition:
    runtime_bundle = load_module_contract_runtime_bundle(
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    validate_runtime_bundle_metadata(runtime_bundle)
    trait_definition = _find_trait_definition(runtime_bundle.get("trait_definitions", []), trait_id)
    if not trait_definition:
        return _empty_signal_ui_definition(trait_id)
    return _build_signal_ui_definition(runtime_bundle, trait_definition)


def ensure_trait_signal_ui_definition(
    trait_id: str,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> SignalUIDefinition:
    definition = load_trait_signal_ui_definition(
        trait_id,
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    if definition.get("valid_signal_ids"):
        return definition
    raise ReportingValidationError(
        f"Trait scoring configuration mismatch: rubric trait '{trait_id}' is missing a runtime signal definition."
    )


def normalize_trait_signal_selection_state(trait_state: dict[str, Any] | None, valid_signal_ids: list[str]) -> list[str]:
    normalized_state = normalize_trait_state_item(trait_state)
    allowed = set(valid_signal_ids)
    selected_signal_ids = normalized_state.get("selected_signal_ids", []) or []
    if not allowed:
        return []
    return [signal_id for signal_id in selected_signal_ids if signal_id in allowed]



def write_canonical_selected_signal_ids(trait_state: dict[str, Any], selected_signal_ids: list[str]) -> None:
    canonical_ids = list(dict.fromkeys(signal_id for signal_id in selected_signal_ids if str(signal_id).strip()))
    trait_state["selected_signal_ids"] = canonical_ids
    trait_state.pop("selected_signals", None)
    trait_state.pop("signal_selections", None)





TRAIT_SELECTION_FIELDS = ("selected_signal_ids", "selected_signals", "signal_selections")
SELECTION_COLLECTION_TYPES = (list, tuple, set)


def count_selected_trait_checkbox_entries(state: dict[str, Any], trait_id: str) -> int:
    selection_value = resolve_trait_selection_value(state)
    if selection_value is None:
        return 0
    return _count_selected_entries(selection_value, trait_id)


def resolve_trait_selection_value(state: dict[str, Any]) -> Any:
    for field_name in TRAIT_SELECTION_FIELDS:
        if field_name in state:
            return state[field_name]
    return None


def trait_requires_signal_selection(raw_state: dict[str, Any], normalized_trait_state: dict[str, Any], trait_id: str) -> bool:
    if normalized_trait_state["skipped"]:
        return False
    if normalized_trait_state["absolute_disqualifier"]:
        return False
    return count_selected_trait_checkbox_entries(raw_state, trait_id) == 0


def _count_selected_entries(selection_value: Any, trait_id: str) -> int:
    if isinstance(selection_value, dict):
        return _count_selected_mapping_entries(selection_value, trait_id)
    if isinstance(selection_value, SELECTION_COLLECTION_TYPES):
        return _count_selected_sequence_entries(selection_value, trait_id)
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: expected mapping or list-like value."
    )


def _count_selected_mapping_entries(selection_value: dict[str, Any], trait_id: str) -> int:
    if _is_boolean_mapping(selection_value):
        return sum(1 for is_selected in selection_value.values() if is_selected)
    if _is_grouped_selection_mapping(selection_value):
        return sum(_count_selected_sequence_entries(group_value, trait_id) for group_value in selection_value.values())
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: mapping entries must be booleans or list-like groups."
    )


def _count_selected_sequence_entries(selection_value: Any, trait_id: str) -> int:
    count = 0
    for item in selection_value:
        count += _count_selected_sequence_item(item, trait_id)
    return count


def _count_selected_sequence_item(item: Any, trait_id: str) -> int:
    if isinstance(item, bool):
        return int(item)
    if isinstance(item, str):
        if item.strip():
            return 1
        raise ValueError(f"Trait '{trait_id}' has malformed trait checkbox selections: blank signal reference.")
    if isinstance(item, dict):
        return _count_selected_item_mapping(item, trait_id)
    raise ValueError(
        f"Trait '{trait_id}' has malformed trait checkbox selections: list items must be strings, booleans, or mappings."
    )


def _count_selected_item_mapping(item: dict[str, Any], trait_id: str) -> int:
    if "selected" not in item:
        raise ValueError(
            f"Trait '{trait_id}' has malformed trait checkbox selections: mapping items must include 'selected'."
        )
    if not isinstance(item.get("selected"), bool):
        raise ValueError(
            f"Trait '{trait_id}' has malformed trait checkbox selections: 'selected' must be a boolean."
        )
    return int(item["selected"])


def _is_boolean_mapping(selection_value: dict[str, Any]) -> bool:
    return bool(selection_value) and all(isinstance(value, bool) for value in selection_value.values())


def _is_grouped_selection_mapping(selection_value: dict[str, Any]) -> bool:
    return bool(selection_value) and all(
        isinstance(value, dict) or isinstance(value, SELECTION_COLLECTION_TYPES)
        for value in selection_value.values()
    )

def _find_trait_definition(trait_definitions: list[dict[str, Any]], trait_id: str) -> dict[str, Any]:
    candidate_ids = set(trait_id_aliases(trait_id))
    canonical_id = canonical_trait_id(trait_id)
    if canonical_id:
        candidate_ids.add(canonical_id)
    if not candidate_ids:
        return {}
    for trait_definition in trait_definitions:
        definition_ids = set(trait_id_aliases(trait_definition.get("trait_id")))
        definition_ids.update(str(alias).strip() for alias in trait_definition.get("trait_aliases", []) or [])
        if candidate_ids.intersection(definition_ids):
            return trait_definition
    runtime_prefix = _runtime_trait_id_alias(str(trait_id or ""))
    if not runtime_prefix:
        return {}
    for trait_definition in trait_definitions:
        candidate = str(trait_definition.get("trait_id", "") or "").strip()
        if candidate.startswith(runtime_prefix):
            return trait_definition
    return {}



def _runtime_trait_id_alias(trait_id: str) -> str:
    match = re.fullmatch(r"trait_(\d+)", trait_id.strip().lower())
    if not match:
        return ""
    numeric_prefix = f"T{int(match.group(1))}_"
    return numeric_prefix



def _empty_signal_ui_definition(trait_id: str) -> SignalUIDefinition:
    return default_signal_ui_definition(trait_id)



def _build_signal_ui_definition(runtime_bundle: dict[str, Any], trait_definition: dict[str, Any]) -> SignalUIDefinition:
    ui_config = runtime_bundle.get("config", {}).get("ui", {})
    core_config = ui_config.get("core_signals", {})
    extended_config = ui_config.get("extended_signals", {})
    signal_dictionary_index = build_signal_dictionary_index(runtime_bundle.get("signal_dictionary", {}))
    core_signals = normalize_core_signals(trait_definition.get("core_signals", []))
    extended_groups = normalize_extended_signal_groups(
        trait_definition,
        signal_dictionary_index=signal_dictionary_index,
    )
    core_signals = [_normalize_ui_signal(signal) for signal in core_signals]
    extended_groups = [_normalize_ui_group(group) for group in extended_groups]
    valid_signal_ids = _collect_valid_signal_ids(core_signals)
    for group in extended_groups:
        valid_signal_ids.extend(_collect_valid_signal_ids(group.get("signals", [])))
    return {
        "trait_id": str(trait_definition.get("trait_id", "") or ""),
        "core_display_style": str(core_config.get("display_style", DEFAULT_CORE_DISPLAY_STYLE) or DEFAULT_CORE_DISPLAY_STYLE),
        "extended_display_style": str(
            extended_config.get("display_style", DEFAULT_EXTENDED_DISPLAY_STYLE) or DEFAULT_EXTENDED_DISPLAY_STYLE
        ),
        "core_section_label": str(core_config.get("section_label", DEFAULT_CORE_SECTION_LABEL) or DEFAULT_CORE_SECTION_LABEL),
        "extended_section_label": str(
            extended_config.get("section_label", DEFAULT_EXTENDED_SECTION_LABEL) or DEFAULT_EXTENDED_SECTION_LABEL
        ),
        "extended_collapsible": bool(extended_config.get("collapsible", DEFAULT_EXTENDED_COLLAPSIBLE)),
        "extended_default_collapsed": bool(
            extended_config.get("default_collapsed", DEFAULT_EXTENDED_DEFAULT_COLLAPSED)
        ),
        "core_signals": core_signals,
        "extended_groups": extended_groups,
        "valid_signal_ids": list(dict.fromkeys(valid_signal_ids)),
    }


def _normalize_ui_group(group: dict[str, Any]) -> dict[str, Any]:
    normalized_group = dict(group)
    normalized_group["signals"] = [_normalize_ui_signal(signal) for signal in group.get("signals", [])]
    return normalized_group


def _normalize_ui_signal(signal: dict[str, Any]) -> dict[str, Any]:
    normalized_signal = dict(signal)
    aliases = [str(alias).strip() for alias in signal.get("selection_aliases", []) if str(alias).strip()]
    if aliases:
        normalized_signal["signal_id"] = _preferred_ui_signal_id(aliases)
        normalized_signal["selection_aliases"] = aliases
        return normalized_signal
    signal_id = str(signal.get("signal_id", "") or "").strip()
    normalized_signal["selection_aliases"] = [signal_id] if signal_id else []
    return normalized_signal


def _preferred_ui_signal_id(aliases: list[str]) -> str:
    if len(aliases) == 1:
        return aliases[0]
    return aliases[1]


def _collect_valid_signal_ids(signals: list[dict[str, Any]]) -> list[str]:
    valid_signal_ids: list[str] = []
    for signal in signals:
        aliases = signal.get("selection_aliases", [])
        if aliases:
            valid_signal_ids.extend(str(alias).strip() for alias in aliases if str(alias).strip())
            continue
        signal_id = str(signal.get("signal_id", "") or "").strip()
        if signal_id:
            valid_signal_ids.append(signal_id)
    return valid_signal_ids

