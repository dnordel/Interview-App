from __future__ import annotations

from typing import Any


DEFAULT_EXTENDED_GROUP_LABEL = "Extended Signals"


CanonicalSignal = dict[str, Any]
CanonicalSignalGroup = dict[str, Any]


CANONICAL_SIGNAL_COMPARISON_KEYS = (
    "signal_id",
    "label",
    "group_label",
    "weight",
    "is_critical",
)


class CanonicalSignalRecord(dict[str, Any]):
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        return _comparison_view(self) == _comparison_view(other)


class CanonicalSignalGroupRecord(dict[str, Any]):
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        return {
            "group_id": self.get("group_id"),
            "group_label": self.get("group_label"),
            "signals": self.get("signals", []),
        } == {
            "group_id": other.get("group_id"),
            "group_label": other.get("group_label"),
            "signals": other.get("signals", []),
        }


def _comparison_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in CANONICAL_SIGNAL_COMPARISON_KEYS}


def build_signal_dictionary_index(signal_dictionary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    signals = signal_dictionary.get("signals", []) if isinstance(signal_dictionary, dict) else []
    index: dict[str, dict[str, Any]] = {}
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        signal_id = str(signal.get("id", "") or "").strip()
        if signal_id:
            index[signal_id] = signal
    return index


def normalize_trait_signal(signal: dict[str, Any], *, default_group_label: str) -> CanonicalSignal:
    signal_id = resolve_trait_signal_selection_id(signal)
    return CanonicalSignalRecord(
        {
            "signal_id": signal_id,
            "runtime_signal_id": resolve_trait_signal_runtime_id(signal),
            "selection_aliases": signal_selection_aliases(signal),
            "label": resolve_trait_signal_label(signal, fallback=signal_id),
            "group_label": str(signal.get("group", "") or default_group_label),
            "weight": resolve_trait_signal_weight(signal),
            "is_critical": bool(signal.get("is_critical", False)),
        }
    )


def resolve_trait_signal_selection_id(signal: dict[str, Any]) -> str:
    if str(signal.get("ref", "") or "").strip():
        return str(signal.get("ref") or "").strip()
    return resolve_trait_signal_runtime_id(signal)


def resolve_trait_signal_runtime_id(signal: dict[str, Any]) -> str:
    return str(signal.get("ref") or signal.get("id") or "").strip()


def signal_selection_aliases(signal: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    runtime_signal_id = resolve_trait_signal_runtime_id(signal)
    if runtime_signal_id:
        aliases.append(runtime_signal_id)
    for mapped_signal_id in signal.get("maps_to", []) or []:
        alias = str(mapped_signal_id or "").strip()
        if alias:
            aliases.append(alias)
    return list(dict.fromkeys(aliases))


def resolve_trait_signal_label(signal: dict[str, Any], *, fallback: str = "") -> str:
    return str(signal.get("label", "") or fallback).strip()


def resolve_trait_signal_weight(signal: dict[str, Any]) -> float:
    raw_weight = signal.get("weight", signal.get("base_weight", signal.get("default_weight", 0)))
    return float(raw_weight or 0)


def normalize_core_signals(core_signals: list[dict[str, Any]]) -> list[CanonicalSignal]:
    return _normalize_signal_collection(core_signals, default_group_label="Core")


def normalize_extended_signal_groups(
    trait_definition: dict[str, Any],
    *,
    signal_dictionary_index: dict[str, dict[str, Any]] | None = None,
) -> list[CanonicalSignalGroup]:
    explicit_groups = trait_definition.get("extended_signal_groups", []) or []
    if explicit_groups:
        return _normalize_explicit_extended_groups(explicit_groups)
    return _normalize_runtime_extended_signals(
        trait_definition.get("extended_signals", []) or [],
        signal_dictionary_index=signal_dictionary_index or {},
    )


def iter_trait_schema_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    signals = list(trait_definition.get("core_signals", []) or [])
    for group in trait_definition.get("extended_signal_groups", []) or []:
        if isinstance(group, dict):
            signals.extend(group.get("signals", []) or [])
    signals.extend(trait_definition.get("extended_signals", []) or [])
    return [signal for signal in signals if isinstance(signal, dict)]


def _normalize_signal_collection(signals: list[dict[str, Any]], *, default_group_label: str) -> list[CanonicalSignal]:
    normalized_signals: list[CanonicalSignal] = []
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        normalized = normalize_trait_signal(signal, default_group_label=default_group_label)
        if normalized["signal_id"]:
            normalized_signals.append(normalized)
    return normalized_signals


def _normalize_explicit_extended_groups(groups: list[dict[str, Any]]) -> list[CanonicalSignalGroup]:
    normalized_groups: list[CanonicalSignalGroup] = []
    for index, group in enumerate(groups or [], start=1):
        if not isinstance(group, dict):
            continue
        group_label = str(group.get("group_label", "") or f"Group {index}").strip()
        normalized_groups.append(
            CanonicalSignalGroupRecord(
                {
                    "group_id": str(group.get("group_id", "") or f"group_{index}").strip(),
                    "group_label": group_label,
                    "signals": _normalize_signal_collection(
                        group.get("signals", []) or [],
                        default_group_label=group_label,
                    ),
                }
            )
        )
    return normalized_groups


def _normalize_runtime_extended_signals(
    signals: list[dict[str, Any]],
    *,
    signal_dictionary_index: dict[str, dict[str, Any]],
) -> list[CanonicalSignalGroup]:
    grouped: dict[str, list[CanonicalSignal]] = {}
    ordered_labels: list[str] = []
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        group_label = _runtime_extended_group_label(signal, signal_dictionary_index)
        if group_label not in grouped:
            grouped[group_label] = []
            ordered_labels.append(group_label)
        normalized = normalize_trait_signal(signal, default_group_label=group_label)
        if normalized["signal_id"]:
            grouped[group_label].append(normalized)
    return [
        CanonicalSignalGroupRecord(
            {
                "group_id": _group_id_from_label(group_label),
                "group_label": group_label,
                "signals": grouped[group_label],
            }
        )
        for group_label in ordered_labels
        if grouped[group_label]
    ]


def _runtime_extended_group_label(signal: dict[str, Any], signal_dictionary_index: dict[str, dict[str, Any]]) -> str:
    explicit_group = str(signal.get("group", "") or "").strip()
    if explicit_group:
        return explicit_group
    for mapped_signal_id in signal.get("maps_to", []) or []:
        dictionary_signal = signal_dictionary_index.get(str(mapped_signal_id).strip(), {})
        category = str(dictionary_signal.get("category", "") or "").strip()
        if category:
            return category
    return DEFAULT_EXTENDED_GROUP_LABEL


def _group_id_from_label(group_label: str) -> str:
    words = [part.lower() for part in str(group_label).split() if part.strip()]
    if not words:
        return "extended_signals"
    return "_".join(words)
