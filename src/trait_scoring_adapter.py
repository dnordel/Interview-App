from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import yaml

from reporting import ReportingValidationError, ScoringEngine as ReportingScoringEngine
from trait_definition_loader import (
    canonical_trait_id,
    load_trait_definitions_from_runtime_bundle,
    trait_id_aliases,
)
from trait_signal_schema import (
    iter_trait_schema_signals,
    resolve_trait_signal_runtime_id,
    resolve_trait_signal_selection_id,
    resolve_trait_signal_weight,
    signal_selection_aliases,
)

DEFAULT_ENGINE_MODULE_CONTRACT = Path("contracts/trait_based_scoring_engine.contract.yaml")
DEFAULT_ENGINE_RUNTIME_CONTRACT = Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
VALID_RAW_SCORES = {1, 2, 3, 4, 5}
CANONICAL_TRAIT_STATE_SCHEMA_VERSION = "1.2.0"
OUTPUT_SCHEMA_VERSION = "1.0.0"
DECISION_LABELS = {
    "strong_hire": "Hire",
    "hire": "Hire",
    "borderline": "Borderline",
    "no_hire": "No Hire",
}


def build_trait_scoring_payload(
    rubric: dict[str, Any],
    track_key: Any,
    trait_state: dict[str, dict[str, Any]] | None,
    *,
    engine_module_contract_path: str | Path = DEFAULT_ENGINE_MODULE_CONTRACT,
    engine_runtime_contract_path: str | Path = DEFAULT_ENGINE_RUNTIME_CONTRACT,
) -> dict[str, Any]:
    runtime_bundle = load_module_contract_runtime_bundle(
        engine_module_contract_path=engine_module_contract_path,
        engine_runtime_contract_path=engine_runtime_contract_path,
    )
    normalized_state = normalize_app_trait_state(trait_state)
    validate_normalized_state(normalized_state)
    engine_output = invoke_scoring_engine(
        rubric,
        track_key,
        normalized_state,
        runtime_bundle=runtime_bundle,
        engine_runtime_contract_path=runtime_bundle["runtime_contract_path"],
    )
    scoring_state = _filter_normalized_state_for_scoring_output(normalized_state, engine_output)
    return map_engine_output_to_normalized_shape(
        rubric=rubric,
        track_key=track_key,
        normalized_state=scoring_state,
        engine_output=engine_output,
        runtime_bundle=runtime_bundle,
    )


def _filter_normalized_state_for_scoring_output(
    normalized_state: dict[str, dict[str, Any]],
    engine_output: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    row_trait_ids = {
        canonical_trait_id(row.get("trait_id"))
        for row in engine_output.get("rows", []) or []
        if canonical_trait_id(row.get("trait_id"))
    }
    return {trait_id: state for trait_id, state in normalized_state.items() if trait_id in row_trait_ids}


def load_module_contract_runtime_bundle(
    *,
    engine_module_contract_path: str | Path,
    engine_runtime_contract_path: str | Path,
) -> dict[str, Any]:
    module_contract_path = _resolve_contract_path(engine_module_contract_path)
    module_contract = _load_yaml(module_contract_path)
    runtime_contract_path = _resolve_contract_path(engine_runtime_contract_path)
    runtime_bundle = _load_runtime_bundle(runtime_contract_path)
    runtime_bundle_with_path = {**runtime_bundle, "runtime_contract_path": str(runtime_contract_path)}
    trait_definitions = load_trait_definitions(runtime_bundle_with_path)
    runtime_error = runtime_bundle.get("runtime_error")
    metadata = {
        "module_contract": module_contract,
        "runtime_contract_path": str(runtime_contract_path),
        "runtime_bundle_loaded": bool(runtime_bundle) and not runtime_error,
        "runtime_error": runtime_error,
        "trait_definitions": trait_definitions,
    }
    return {**runtime_bundle, **metadata}


def load_trait_definitions(runtime_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return load_trait_definitions_from_runtime_bundle(runtime_bundle)


def validate_runtime_bundle_metadata(runtime_bundle: dict[str, Any]) -> None:
    runtime_error = str(runtime_bundle.get("runtime_error", "") or "").strip()
    if runtime_error:
        raise ReportingValidationError(f"Unable to load scoring runtime bundle: {runtime_error}")
    if runtime_bundle.get("runtime_bundle_loaded"):
        return
    runtime_contract_path = runtime_bundle.get("runtime_contract_path", "runtime bundle")
    raise ReportingValidationError(f"Unable to load scoring runtime bundle: {runtime_contract_path}")


def normalize_skipped(value: Any) -> bool:
    return _normalize_bool(value)


def normalize_absolute_disqualifier(value: Any) -> bool:
    return _normalize_bool(value)


def coerce_raw_score(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in VALID_RAW_SCORES:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            if parsed in VALID_RAW_SCORES:
                return parsed
        return None
    return None


def normalize_verbatim_notes(value: Any) -> str:
    return str(value or "").strip()


def normalize_trait_state_item(state: dict[str, Any] | None) -> dict[str, Any]:
    source = state if isinstance(state, dict) else {}
    skipped = normalize_skipped(source.get("skipped", False))
    raw_score = coerce_raw_score(source.get("raw_score"))
    selected_signal_ids = _normalize_selected_signal_ids(source)
    normalized = {
        "schema_version": CANONICAL_TRAIT_STATE_SCHEMA_VERSION,
        "raw_score": raw_score,
        "raw_score_invalid": _is_invalid_raw_score_input(source.get("raw_score")),
        "selected_signal_ids": selected_signal_ids,
        "skipped": skipped,
        "absolute_disqualifier": normalize_absolute_disqualifier(source.get("absolute_disqualifier", False)),
        "no_example_after_followups": _normalize_bool(source.get("no_example_after_followups", False)),
        "verbatim_notes": normalize_verbatim_notes(source.get("verbatim_notes")),
    }
    if skipped:
        normalized["selected_signal_ids"] = []
        normalized["raw_score"] = None
    return normalized


def normalize_app_trait_state(trait_state: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(trait_state, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for trait_id, state in trait_state.items():
        if not isinstance(state, dict):
            continue
        normalized[canonical_trait_id(trait_id)] = normalize_trait_state_item(state)
    return normalized


def validate_normalized_state(normalized_state: dict[str, dict[str, Any]]) -> None:
    for trait_id, state in normalized_state.items():
        if state.get("raw_score_invalid"):
            raise ReportingValidationError(f"Trait '{trait_id}' has invalid raw_score '{state.get('raw_score')}'.")
        raw_score = state.get("raw_score")
        if raw_score is not None and raw_score not in VALID_RAW_SCORES:
            raise ReportingValidationError(f"Trait '{trait_id}' has invalid raw_score '{raw_score}'.")
        if state.get("skipped"):
            continue
        if state.get("absolute_disqualifier") and not state.get("verbatim_notes"):
            raise ReportingValidationError(
                f"Trait '{trait_id}' has disqualifier checked but no verbatim notes."
            )


def invoke_scoring_engine(
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    *,
    runtime_bundle: dict[str, Any],
    engine_runtime_contract_path: str | Path,
) -> dict[str, Any]:
    runtime_contract_path = _resolve_contract_path(engine_runtime_contract_path)
    trait_definitions = load_trait_definitions(runtime_bundle)
    validate_runtime_bundle_metadata(
        {
            "runtime_bundle_loaded": bool(runtime_bundle) and not runtime_bundle.get("runtime_error"),
            "runtime_error": runtime_bundle.get("runtime_error"),
            "runtime_contract_path": str(runtime_contract_path),
        }
    )
    _validate_trait_scoring_configuration(rubric, track_key, trait_definitions, normalized_state)
    active_trait_definitions = _filter_trait_definitions_for_track(rubric, track_key, trait_definitions)
    active_state = _filter_normalized_state_for_trait_definitions(normalized_state, active_trait_definitions)

    engine = _build_trait_engine(runtime_bundle, runtime_contract_path)
    selections = _build_trait_selections(active_trait_definitions, active_state)
    session_result = engine.score_session(active_trait_definitions, selections)
    return _build_compatibility_engine_output(
        rubric=rubric,
        track_key=track_key,
        normalized_state=active_state,
        trait_definitions=active_trait_definitions,
        session_result=session_result,
    )


def _validate_trait_scoring_configuration(
    rubric: dict[str, Any],
    track_key: Any,
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> None:
    runtime_trait_ids = _trait_ids_from_runtime_definitions(trait_definitions)
    input_trait_ids = _trait_ids_from_normalized_state(normalized_state)
    resolved_track_key = ReportingScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    rubric_trait_ids = _trait_ids_from_rubric(rubric, resolved_track_key)

    _raise_for_missing_trait_overlap(input_trait_ids, runtime_trait_ids)
    _raise_for_rubric_runtime_mismatch(resolved_track_key, rubric_trait_ids, runtime_trait_ids)


def _filter_trait_definitions_for_track(
    rubric: dict[str, Any],
    track_key: Any,
    trait_definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved_track_key = ReportingScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    active_trait_ids = _trait_ids_from_rubric(rubric, resolved_track_key)
    return [
        trait_definition
        for trait_definition in trait_definitions
        if canonical_trait_id(trait_definition.get("trait_id")) in active_trait_ids
    ]


def _filter_normalized_state_for_trait_definitions(
    normalized_state: dict[str, dict[str, Any]],
    trait_definitions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    active_trait_ids = _trait_ids_from_runtime_definitions(trait_definitions)
    return {trait_id: state for trait_id, state in normalized_state.items() if trait_id in active_trait_ids}


def _trait_ids_from_runtime_definitions(trait_definitions: list[dict[str, Any]]) -> set[str]:
    return {
        canonical_trait_id(item.get("trait_id"))
        for item in trait_definitions
        if canonical_trait_id(item.get("trait_id"))
    }


def _trait_ids_from_normalized_state(normalized_state: dict[str, dict[str, Any]]) -> set[str]:
    return {canonical_trait_id(trait_id) for trait_id in normalized_state if canonical_trait_id(trait_id)}


def _trait_ids_from_rubric(rubric: dict[str, Any], resolved_track_key: str) -> set[str]:
    rubric_traits = _rubric_trait_map(rubric, resolved_track_key)
    return {trait_id for trait_id in rubric_traits if trait_id}


def _raise_for_missing_trait_overlap(input_trait_ids: set[str], runtime_trait_ids: set[str]) -> None:
    overlap = sorted(input_trait_ids.intersection(runtime_trait_ids))
    if overlap:
        return
    input_list = ", ".join(sorted(input_trait_ids)) or "<none>"
    runtime_list = ", ".join(sorted(runtime_trait_ids)) or "<none>"
    raise ReportingValidationError(
        "Trait scoring configuration mismatch: finalized trait inputs do not overlap "
        f"the trait runtime bundle. Input traits: {input_list}. Runtime traits: {runtime_list}."
    )


def _raise_for_rubric_runtime_mismatch(
    resolved_track_key: str,
    rubric_trait_ids: set[str],
    runtime_trait_ids: set[str],
) -> None:
    if not rubric_trait_ids:
        raise ReportingValidationError(
            "Trait scoring configuration mismatch: rubric track "
            f"'{resolved_track_key}' does not define any trait-scoring entries."
        )
    missing_runtime_traits = sorted(rubric_trait_ids.difference(runtime_trait_ids))
    if not missing_runtime_traits:
        return
    runtime_list = ", ".join(sorted(runtime_trait_ids)) or "<none>"
    missing_list = ", ".join(missing_runtime_traits)
    raise ReportingValidationError(
        "Trait scoring configuration mismatch: rubric track "
        f"'{resolved_track_key}' includes traits missing from the runtime bundle: {missing_list}. "
        f"Runtime traits: {runtime_list}."
    )


def map_engine_output_to_normalized_shape(
    *,
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    engine_output: dict[str, Any],
    runtime_bundle: dict[str, Any],
) -> dict[str, Any]:
    rows = list(engine_output.get("rows", []) or [])
    traits = [_map_trait_row(row) for row in rows]
    resolved_track_key = ReportingScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    summary = _build_summary(engine_output)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "track_key": resolved_track_key,
        "summary": summary,
        "traits": traits,
        "rows": rows,
        "normalized_state": normalized_state,
        "engine_metadata": runtime_bundle,
        **summary,
    }


def _build_summary(engine_output: dict[str, Any]) -> dict[str, Any]:
    configured_max = int(engine_output.get("configured_max_weighted_total", 0) or 0)
    included_max = int(engine_output.get("max_weighted_total_included_traits", 0) or 0)
    denominator = _resolve_scoring_denominator(engine_output, included_max, configured_max)
    percent_label = _resolve_percent_label(engine_output, denominator)
    return {
        "weighted_total": int(engine_output.get("weighted_total", 0) or 0),
        "configured_max_weighted_total": configured_max,
        "max_weighted_total": int(engine_output.get("max_weighted_total", denominator) or denominator),
        "max_weighted_total_included_traits": included_max,
        "percent_denominator": denominator,
        "percent_of_max": float(engine_output.get("percent_of_max", 0.0) or 0.0),
        "percent_of_max_label": percent_label,
        "percent_label": percent_label,
        "outcome": str(engine_output.get("outcome", "") or ""),
        "critical_eq_1": bool(engine_output.get("critical_eq_1", False)),
        "critical_lt_3": bool(engine_output.get("critical_lt_3", False)),
        "any_critical_selected": bool(engine_output.get("any_critical_selected", False)),
        "disqualifier_present": bool(engine_output.get("disqualifier_present", False)),
        "triggered_critical": bool(engine_output.get("triggered_critical", False)),
        "locked_rule": engine_output.get("locked_rule"),
        "override_rationale": engine_output.get("override_rationale"),
        "skipped_traits_count": int(engine_output.get("skipped_traits_count", 0) or 0),
        "scored_traits_count": int(engine_output.get("scored_traits_count", 0) or 0),
    }


def _resolve_scoring_denominator(engine_output: dict[str, Any], included_max: int, configured_max: int) -> int:
    explicit_denominator = engine_output.get("percent_denominator")
    if explicit_denominator is not None:
        return int(explicit_denominator or 0)
    if included_max > 0:
        return included_max
    return configured_max


def _resolve_percent_label(engine_output: dict[str, Any], denominator: int) -> str:
    label = str(engine_output.get("percent_of_max_label", "") or "").strip()
    if label:
        return label
    if denominator <= 0:
        return "N/A (all questions skipped)"
    percent_value = float(engine_output.get("percent_of_max", 0.0) or 0.0)
    return f"{percent_value}%"


def _map_trait_row(row: dict[str, Any]) -> dict[str, Any]:
    signal_counts = row.get("signal_counts", {}) or {}
    return {
        "trait_id": str(row.get("trait_id", "") or ""),
        "trait_name": str(row.get("trait_name", "") or ""),
        "priority": row.get("priority"),
        "weight": int(row.get("weight", 0) or 0),
        "primary_question": str(row.get("primary_question", "") or ""),
        "score": {
            "raw": row.get("raw_score"),
            "raw_for_math": int(row.get("raw_score_math", 0) or 0),
            "weighted": int(row.get("weighted_score", 0) or 0),
            "skipped": bool(row.get("skipped", False)),
        },
        "flags": {
            "absolute_disqualifier": bool(row.get("absolute_disqualifier", False)),
            "no_example_after_followups": bool(row.get("no_example_after_followups", False)),
        },
        "notes": {
            "verbatim": str(row.get("verbatim_notes", "") or ""),
            "question_notes": str(row.get("question_notes", "") or ""),
            "trait_notes": str(row.get("trait_notes", "") or ""),
        },
        "signal_counts": {
            "core": int(signal_counts.get("core", 0) or 0),
            "extended": int(signal_counts.get("extended", 0) or 0),
        },
        "session_trait_outcome": str(row.get("session_trait_outcome", "") or ""),
    }


def _is_invalid_raw_score_input(value: Any) -> bool:
    if value in {None, ""}:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return coerce_raw_score(value) is None


def _normalize_selected_signal_ids(source: dict[str, Any]) -> list[str]:
    for field_name in ("selected_signal_ids", "selected_signals", "signal_selections"):
        if field_name not in source:
            continue
        normalized = _normalize_selection_variant(source[field_name])
        return normalized
    return []


def _normalize_selection_variant(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return _normalize_selection_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return _normalize_selection_sequence(value)
    return []


def _normalize_selection_mapping(value: dict[str, Any]) -> list[str]:
    if not value:
        return []
    if all(isinstance(item, bool) for item in value.values()):
        return [key.strip() for key, is_selected in value.items() if is_selected and str(key).strip()]

    selected_signal_ids: list[str] = []
    for group_value in value.values():
        selected_signal_ids.extend(_normalize_selection_variant(group_value))
    return _dedupe_signal_ids(selected_signal_ids)


def _normalize_selection_sequence(value: list[Any] | tuple[Any, ...] | set[Any]) -> list[str]:
    selected_signal_ids: list[str] = []
    for item in value:
        signal_id = _normalize_selection_item(item)
        if signal_id:
            selected_signal_ids.append(signal_id)
    return _dedupe_signal_ids(selected_signal_ids)


def _normalize_selection_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    if not item.get("selected"):
        return ""
    for field_name in ("signal_id", "ref", "id", "value"):
        candidate = str(item.get(field_name, "") or "").strip()
        if candidate:
            return candidate
    return ""


def _dedupe_signal_ids(signal_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(signal_id for signal_id in signal_ids if signal_id))


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in {"true", "1", "yes", "on"}:
            return True
        if stripped in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


def _contract_resolution_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_contract_path_from_base(path: str | Path, *, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _resolve_contract_path(path: str | Path) -> Path:
    return _resolve_contract_path_from_base(path, base_dir=_contract_resolution_base_dir())


def _load_yaml(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {}
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _load_runtime_bundle(runtime_contract_path: Path) -> dict[str, Any]:
    resolved_path = runtime_contract_path.expanduser().resolve()
    if not resolved_path.exists():
        return {}

    try:
        engine_class = _load_trait_engine_class(resolved_path)
        config, signal_dictionary, traits, resolved_paths = engine_class.load_runtime_bundle(resolved_path)
    except (FileNotFoundError, ImportError, KeyError, PermissionError, TypeError, ValueError) as exc:
        return {"runtime_error": str(exc), "traits": []}

    return {
        "config": config,
        "signal_dictionary": signal_dictionary,
        "traits": traits,
        "resolved_paths": {key: str(value) for key, value in resolved_paths.items()},
    }


def _load_trait_engine_class(runtime_contract_path: Path) -> type[Any]:
    engine_module_path = runtime_contract_path.resolve().parent / "trait_based_scoring_engine.py"
    spec = importlib.util.spec_from_file_location("trait_based_scoring_engine_adapter", engine_module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load scoring engine module from {engine_module_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScoringEngine


def _has_trait_definition_overlap(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> bool:
    trait_ids = {canonical_trait_id(item.get("trait_id")) for item in trait_definitions if canonical_trait_id(item.get("trait_id"))}
    normalized_ids = {canonical_trait_id(trait_id) for trait_id in normalized_state if canonical_trait_id(trait_id)}
    return bool(trait_ids.intersection(normalized_ids))


def _build_trait_engine(runtime_bundle: dict[str, Any], runtime_contract_path: Path) -> Any:
    engine_class = _load_trait_engine_class(Path(runtime_contract_path))
    return engine_class(runtime_bundle["config"], runtime_bundle["signal_dictionary"])


def _build_trait_selections(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    selections: dict[str, list[str]] = {}
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        if not trait_id:
            continue
        state = normalized_state.get(trait_id) or {}
        selections[trait_id] = _select_signal_refs_for_state(trait_definition, state)
    return selections


def _select_signal_refs_for_state(trait_definition: dict[str, Any], state: dict[str, Any]) -> list[str]:
    if state.get("skipped"):
        return []
    selected_signal_ids = {
        str(signal_id).strip()
        for signal_id in state.get("selected_signal_ids", []) or []
        if str(signal_id).strip()
    }
    resolved_refs: list[str] = []
    for signal in _iter_trait_signals(trait_definition):
        runtime_signal_id = resolve_trait_signal_runtime_id(signal)
        if not runtime_signal_id:
            continue
        if resolve_trait_signal_weight(signal) == 0:
            continue
        if not selected_signal_ids.intersection(signal_selection_aliases(signal)):
            continue
        resolved_refs.append(runtime_signal_id)
    return resolved_refs


def _positive_signal_refs(trait_definition: dict[str, Any]) -> list[str]:
    return _signal_refs_by_weight(trait_definition, positive=True)


def _negative_signal_refs(trait_definition: dict[str, Any]) -> list[str]:
    return _signal_refs_by_weight(trait_definition, positive=False)


def _signal_refs_by_weight(trait_definition: dict[str, Any], *, positive: bool) -> list[str]:
    refs: list[str] = []
    for signal in _iter_trait_signals(trait_definition):
        weight = resolve_trait_signal_weight(signal)
        signal_id = resolve_trait_signal_selection_id(signal)
        if positive and weight > 0 and signal_id:
            refs.append(signal_id)
        if (not positive) and weight < 0 and signal_id:
            refs.append(signal_id)
    return refs


def _iter_trait_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    return iter_trait_schema_signals(trait_definition)


def _build_compatibility_engine_output(
    *,
    rubric: dict[str, Any],
    track_key: Any,
    normalized_state: dict[str, dict[str, Any]],
    trait_definitions: list[dict[str, Any]],
    session_result: dict[str, Any],
) -> dict[str, Any]:
    session_traits = {
        canonical_trait_id(item.get("trait_id")): item
        for item in session_result.get("traits", []) or []
        if canonical_trait_id(item.get("trait_id"))
    }
    resolved_track_key = ReportingScoringEngine._resolve_track_key_for_scoring(rubric, track_key)
    rubric_trait_map = _rubric_trait_map(rubric, resolved_track_key)
    rows = _build_rows(trait_definitions, rubric_trait_map, normalized_state, session_traits, session_result)
    weighted_total = int(round(float(session_result.get("totals", {}).get("final", 0) or 0)))
    configured_max_weighted_total = _configured_max_weighted_total(rubric, resolved_track_key, trait_definitions)
    included_max_weighted_total = _max_weighted_total(trait_definitions, normalized_state)
    percent_denominator = _resolve_percent_denominator(included_max_weighted_total, configured_max_weighted_total)
    percent_of_max = _percent_of_max(weighted_total, percent_denominator)
    percent_label = _percent_label(percent_of_max, included_max_weighted_total)
    outcome = DECISION_LABELS.get(str(session_result.get("decision", "") or "").strip().lower(), "Borderline")
    any_critical_selected = bool(session_result.get("any_critical_selected", False))
    triggered_critical = bool(session_result.get("triggered_critical", False))
    critical_eq_1 = any((state.get("raw_score") == 1) and not state.get("skipped") for state in normalized_state.values())
    critical_lt_3 = any_critical_selected
    disqualifier_present = any(bool(state.get("absolute_disqualifier")) for state in normalized_state.values())
    locked_rule = session_result.get("locked_rule")
    override_rationale = session_result.get("override_rationale")
    return {
        "rows": rows,
        "weighted_total": weighted_total,
        "configured_max_weighted_total": configured_max_weighted_total,
        "max_weighted_total": percent_denominator,
        "max_weighted_total_included_traits": included_max_weighted_total,
        "percent_denominator": percent_denominator,
        "percent_of_max": percent_of_max,
        "percent_of_max_label": percent_label,
        "percent_label": percent_label,
        "skipped_traits_count": sum(1 for state in normalized_state.values() if state.get("skipped")),
        "scored_traits_count": sum(1 for state in normalized_state.values() if not state.get("skipped")),
        "critical_eq_1": critical_eq_1,
        "critical_lt_3": critical_lt_3,
        "any_critical_selected": any_critical_selected,
        "disqualifier_present": disqualifier_present,
        "triggered_critical": triggered_critical,
        "locked_rule": locked_rule,
        "override_rationale": override_rationale,
        "outcome": outcome,
        "session_result": session_result,
    }


def _rubric_trait_map(rubric: dict[str, Any], resolved_track_key: str) -> dict[str, dict[str, Any]]:
    rubric_traits = rubric.get("traits", []) or []
    mapping: dict[str, dict[str, Any]] = {}
    for trait in rubric_traits:
        tracks = trait.get("applicable_tracks", []) or []
        if tracks and "all" not in tracks and resolved_track_key not in tracks:
            continue
        trait_id = canonical_trait_id(trait.get("id"))
        if trait_id:
            mapping[trait_id] = trait
    return mapping


def _build_rows(
    trait_definitions: list[dict[str, Any]],
    rubric_trait_map: dict[str, dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
    session_traits: dict[str, dict[str, Any]],
    session_result: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        if not trait_id:
            continue
        state = normalized_state.get(trait_id) or {}
        session_trait = session_traits.get(trait_id, {})
        rubric_trait = rubric_trait_map.get(trait_id, {})
        rows.append(_build_trait_row(trait_definition, rubric_trait, state, session_trait, session_result))
    return rows


def _build_trait_row(
    trait_definition: dict[str, Any],
    rubric_trait: dict[str, Any],
    state: dict[str, Any],
    session_trait: dict[str, Any],
    session_result: dict[str, Any],
) -> dict[str, Any]:
    raw_score = state.get("raw_score")
    weighted_score = int(round(float(session_trait.get("final_score", 0) or 0)))
    canonical_id = canonical_trait_id(trait_definition.get("trait_id"))
    trait_label = str(rubric_trait.get("id") or canonical_id or "")
    return {
        "trait_id": canonical_id,
        "trait_name": str(rubric_trait.get("name") or trait_label),
        "priority": rubric_trait.get("priority"),
        "weight": int(rubric_trait.get("weight", 0) or 0),
        "skipped": bool(state.get("skipped", False)),
        "raw_score": raw_score,
        "raw_score_math": int(raw_score or 0),
        "weighted_score": weighted_score,
        "verbatim_notes": str(state.get("verbatim_notes", "") or ""),
        "no_example_after_followups": bool(state.get("no_example_after_followups", False)),
        "absolute_disqualifier": bool(state.get("absolute_disqualifier", False)),
        "primary_question": str(rubric_trait.get("primary_question") or trait_definition.get("question", "") or ""),
        "question_notes": "",
        "trait_notes": "",
        "signal_counts": {
            "core": len(session_trait.get("selected_core", []) or []),
            "extended": len(session_trait.get("selected_extended", []) or []),
        },
        "session_trait_outcome": str(session_result.get("decision", "") or ""),
        "trait_aliases": trait_id_aliases(trait_definition.get("trait_id")),
    }


def _max_weighted_total(
    trait_definitions: list[dict[str, Any]],
    normalized_state: dict[str, dict[str, Any]],
) -> int:
    total = 0.0
    for trait_definition in trait_definitions:
        trait_id = canonical_trait_id(trait_definition.get("trait_id"))
        state = normalized_state.get(trait_id) or {}
        if state.get("skipped"):
            continue
        total += _max_trait_final_score(trait_definition)
    return int(round(total))


def _max_trait_final_score(trait_definition: dict[str, Any]) -> float:
    core_total = sum(
        max(resolve_trait_signal_weight(signal), 0.0)
        for signal in trait_definition.get("core_signals", []) or []
        if isinstance(signal, dict)
    )
    extended_total = sum(max(resolve_trait_signal_weight(signal), 0.0) for signal in _iter_extended_trait_signals(trait_definition))
    return (core_total * 1.5) + extended_total


def _iter_extended_trait_signals(trait_definition: dict[str, Any]) -> list[dict[str, Any]]:
    return iter_trait_schema_signals(
        {
            "extended_signal_groups": trait_definition.get("extended_signal_groups", []),
            "extended_signals": trait_definition.get("extended_signals", []),
        }
    )


def _configured_max_weighted_total(
    rubric: dict[str, Any],
    resolved_track_key: str,
    trait_definitions: list[dict[str, Any]],
) -> int:
    track_cfg = ((rubric.get("tracks") or {}).get(resolved_track_key) or {})
    configured_value = track_cfg.get("max_weighted_total")
    if configured_value is not None:
        return int(configured_value or 0)
    return int(round(sum(_max_trait_final_score(trait_definition) for trait_definition in trait_definitions)))


def _resolve_percent_denominator(included_max_weighted_total: int, configured_max_weighted_total: int) -> int:
    if included_max_weighted_total > 0:
        return included_max_weighted_total
    return configured_max_weighted_total


def _percent_of_max(weighted_total: int, max_weighted_total: int) -> float:
    if max_weighted_total <= 0:
        return 0.0
    return round((float(weighted_total) * 100.0) / float(max_weighted_total), 2)


def _percent_label(percent_of_max: float, included_max_weighted_total: int) -> str:
    if included_max_weighted_total <= 0:
        return "N/A (all questions skipped)"
    return f"{percent_of_max}%"
