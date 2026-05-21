from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

MAX_CONFIG_BYTES = 2_000_000

CONFIG_ASSET_REGISTRY: dict[str, dict[str, Any]] = {
    "rubric.json": {
        "owner": "interview_runtime_service",
        "consumers": ["data_store", "question_settings_service", "interview_app"],
        "schema": "object(metadata, scoring, tracks, traits[], absolute_disqualifiers[])",
    },
    "disqualifier_signals.json": {
        "owner": "interview_runtime_service",
        "consumers": ["data_store", "interview_app"],
        "schema": "object(questions[])",
    },
    "question_overrides.json": {
        "owner": "interview_runtime_service",
        "consumers": ["data_store", "interview_app", "question_settings_window"],
        "schema": "object(track_trait_order, trait_question_overrides, custom_questions, track_question_flow)",
    },
    "interview_output.schema.json": {
        "owner": "interview_runtime_service",
        "consumers": ["integration_export", "reporting", "interview_session_store"],
        "schema": "json-schema draft 2020-12",
    },
    "cues.json": {
        "owner": "interview_runtime_service",
        "consumers": [],
        "schema": "object(version, scoring_scale, behavior_flags, final_outcomes, cases[])",
    },
    "sample_draft.json": {
        "owner": "interview_runtime_service",
        "consumers": [],
        "schema": "object(candidate, current_index, trait_inputs)",
    },
}


class ConfigValidationError(ValueError):
    """Validation failed for untrusted config payloads."""


def inventory_config_assets(config_dir: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for name, metadata in CONFIG_ASSET_REGISTRY.items():
        path = Path(config_dir) / name
        assets.append(
            {
                "asset": name,
                "path": str(path),
                "exists": path.exists(),
                "owner": metadata["owner"],
                "consumers": list(metadata["consumers"]),
                "schema": metadata["schema"],
            }
        )
    return assets


def load_json_dict(
    path: Path,
    *,
    required: bool,
    context: str,
    default: dict[str, Any] | None = None,
    max_bytes: int = MAX_CONFIG_BYTES,
) -> dict[str, Any]:
    source_path = Path(path)
    if not source_path.exists():
        if required:
            raise FileNotFoundError(f"{context} file not found")
        return deepcopy(default or {})

    size = source_path.stat().st_size
    if size > max_bytes:
        raise ConfigValidationError(f"{context} exceeds the safe size limit")

    try:
        with source_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigValidationError(f"{context} is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise ConfigValidationError(f"{context} must be a JSON object")
    return payload


def validate_rubric_config(payload: dict[str, Any]) -> None:
    _require_keys(
        payload,
        required=["metadata", "scoring", "tracks", "traits", "absolute_disqualifiers"],
        context="rubric.json",
    )
    _expect_type(payload["metadata"], dict, "metadata")
    _expect_type(payload["scoring"], dict, "scoring")
    _expect_type(payload["tracks"], dict, "tracks")
    _expect_type(payload["absolute_disqualifiers"], list, "absolute_disqualifiers")

    traits = payload["traits"]
    _expect_type(traits, list, "traits")
    if not traits:
        raise ConfigValidationError("rubric.json field 'traits' must not be empty")

    for index, trait in enumerate(traits):
        _expect_type(trait, dict, f"traits[{index}]")
        _require_keys(
            trait,
            required=[
                "id",
                "name",
                "priority",
                "weight",
                "primary_question",
                "descriptors",
                "sample_answers",
                "applicable_tracks",
            ],
            context=f"traits[{index}]",
        )
        _expect_non_empty_str(trait["id"], f"traits[{index}].id")
        _expect_non_empty_str(trait["name"], f"traits[{index}].name")
        _expect_non_empty_str(trait["primary_question"], f"traits[{index}].primary_question")
        _expect_type(trait["priority"], str, f"traits[{index}].priority")
        _expect_type(trait["descriptors"], dict, f"traits[{index}].descriptors")
        _expect_type(trait["sample_answers"], dict, f"traits[{index}].sample_answers")
        _expect_str_list(trait["applicable_tracks"], f"traits[{index}].applicable_tracks")

        weight = trait["weight"]
        if not isinstance(weight, (int, float)):
            raise ConfigValidationError(f"traits[{index}].weight must be numeric")
        if weight <= 0 or weight > 10:
            raise ConfigValidationError(f"traits[{index}].weight must be between 0 and 10")


def validate_disqualifier_config(payload: dict[str, Any]) -> None:
    questions = payload.get("questions", [])
    _expect_type(questions, list, "questions")
    for index, item in enumerate(questions):
        _expect_type(item, dict, f"questions[{index}]")
        trait_id = str(item.get("trait_id", "")).strip()
        if not trait_id:
            raise ConfigValidationError(f"questions[{index}].trait_id is required")


def normalize_question_overrides_config(payload: dict[str, Any]) -> dict[str, Any]:
    top_level = {
        "track_trait_order": _normalize_track_trait_order(payload.get("track_trait_order", {})),
        "trait_question_overrides": _normalize_trait_overrides(payload.get("trait_question_overrides", {})),
        "custom_questions": _normalize_custom_questions(payload.get("custom_questions", {})),
        "track_question_flow": _normalize_question_flow(payload.get("track_question_flow", {})),
    }
    return top_level


def _normalize_track_trait_order(value: Any) -> dict[str, list[str]]:
    _expect_type(value, dict, "track_trait_order")
    normalized: dict[str, list[str]] = {}
    for track, trait_ids in value.items():
        track_key = str(track).strip()
        if not track_key:
            continue
        _expect_type(trait_ids, list, f"track_trait_order.{track_key}")
        normalized[track_key] = [str(item).strip() for item in trait_ids if str(item).strip()]
    return normalized


def _normalize_trait_overrides(value: Any) -> dict[str, str]:
    _expect_type(value, dict, "trait_question_overrides")
    normalized: dict[str, str] = {}
    for trait_id, text in value.items():
        clean_trait = str(trait_id).strip()
        clean_text = str(text).strip()
        if clean_trait and clean_text:
            normalized[clean_trait] = clean_text
    return normalized


def _normalize_custom_questions(value: Any) -> dict[str, list[dict[str, Any]]]:
    _expect_type(value, dict, "custom_questions")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for track, items in value.items():
        track_key = str(track).strip()
        if not track_key:
            continue
        _expect_type(items, list, f"custom_questions.{track_key}")
        normalized_items: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            _expect_type(item, dict, f"custom_questions.{track_key}[{idx}]")
            item_id = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            order = item.get("order", idx)
            if not item_id or not text:
                continue
            if not isinstance(order, int) or order < 0:
                raise ConfigValidationError(f"custom_questions.{track_key}[{idx}].order must be >= 0")
            normalized_items.append({"id": item_id, "text": text, "order": order})
        normalized[track_key] = normalized_items
    return normalized


def _normalize_question_flow(value: Any) -> dict[str, list[dict[str, str]]]:
    _expect_type(value, dict, "track_question_flow")
    normalized: dict[str, list[dict[str, str]]] = {}
    for track, items in value.items():
        track_key = str(track).strip()
        if not track_key:
            continue
        _expect_type(items, list, f"track_question_flow.{track_key}")
        cleaned_items: list[dict[str, str]] = []
        for idx, item in enumerate(items):
            _expect_type(item, dict, f"track_question_flow.{track_key}[{idx}]")
            kind = str(item.get("type", "")).strip().lower()
            item_id = str(item.get("id", "")).strip()
            if kind not in {"trait", "custom"}:
                continue
            if not item_id:
                continue
            cleaned_items.append({"type": kind, "id": item_id})
        normalized[track_key] = cleaned_items
    return normalized


def _expect_str_list(value: Any, field: str) -> None:
    _expect_type(value, list, field)
    for item in value:
        if not isinstance(item, str):
            raise ConfigValidationError(f"{field} must contain only strings")


def _expect_non_empty_str(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"{field} must be a non-empty string")


def _expect_type(value: Any, expected_type: type, field: str) -> None:
    if not isinstance(value, expected_type):
        raise ConfigValidationError(f"{field} must be of type {expected_type.__name__}")


def _require_keys(payload: dict[str, Any], *, required: list[str], context: str) -> None:
    for key in required:
        if key not in payload:
            raise ConfigValidationError(f"{context} missing required key: {key}")
