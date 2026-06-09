from __future__ import annotations

from typing import Any

from reporting import ReportingValidationError, ScoringEngine as ReportingScoringEngine
from trait_scoring_adapter import build_trait_scoring_payload

_SIGNAL_SELECTION_FIELDS = ("selected_signal_ids", "selected_signals", "signal_selections")


def score_interview(rubric: dict[str, Any], track_key: Any, trait_inputs: dict[str, Any]) -> dict[str, Any]:
    """Score interview trait inputs through the active scoring seam."""
    _validate_score_inputs(rubric, track_key, trait_inputs)
    if _has_selected_signal_scoring_inputs(trait_inputs):
        return build_trait_scoring_payload(rubric, track_key, trait_inputs)
    return ReportingScoringEngine.evaluate(rubric, track_key, trait_inputs)


def _validate_score_inputs(rubric: dict[str, Any], track_key: Any, trait_inputs: dict[str, Any]) -> None:
    if not isinstance(rubric, dict):
        raise ReportingValidationError("Scoring rubric must be a dictionary.")
    if not isinstance(trait_inputs, dict):
        raise ReportingValidationError("Trait inputs must be a dictionary.")
    tracks = rubric.get("tracks")
    if not isinstance(tracks, dict) or not tracks:
        raise ReportingValidationError("Scoring rubric is missing tracks.")
    if isinstance(track_key, str) and track_key in tracks:
        return
    raise ReportingValidationError("Invalid track key in draft. This track is missing from the current rubric.")


def _has_selected_signal_scoring_inputs(trait_inputs: dict[str, Any]) -> bool:
    for state in trait_inputs.values():
        if _state_has_selected_signal_scoring_inputs(state):
            return True
    return False


def _state_has_selected_signal_scoring_inputs(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    return any(_has_non_empty_signal_selection(state.get(field_name)) for field_name in _SIGNAL_SELECTION_FIELDS)


def _has_non_empty_signal_selection(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        if not value.strip():
            return False
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_non_empty_signal_selection(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_non_empty_signal_selection(item) for item in value)
    return True
