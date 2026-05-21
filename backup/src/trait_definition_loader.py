from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


DEFAULT_ENGINE_RUNTIME_CONTRACT = Path("Trait-Based Scoring/trait_based_scoring_contract.yaml")
RUBRIC_TRAIT_ID_PATTERN = re.compile(r"trait_(\d+)", re.IGNORECASE)
RUNTIME_TRAIT_ID_PATTERN = re.compile(r"T(\d+)(?:_[A-Za-z0-9_]+)?")


def load_trait_definitions_from_runtime_bundle(runtime_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    resolved_trait_dir = _resolve_traits_dir_from_bundle(runtime_bundle)
    if resolved_trait_dir is not None:
        return load_trait_definitions_from_dir(resolved_trait_dir)

    bundled_traits = runtime_bundle.get("traits")
    if isinstance(bundled_traits, list):
        bundled_trait_definitions = [_normalize_trait_definition(item) for item in bundled_traits if isinstance(item, dict)]
        if bundled_trait_definitions:
            return bundled_trait_definitions

    runtime_contract_path = runtime_bundle.get("runtime_contract_path")
    if runtime_contract_path:
        return load_trait_definitions_from_contract(runtime_contract_path)
    return []


def load_trait_definitions_from_contract(runtime_contract_path: str | Path) -> list[dict[str, Any]]:
    contract_payload = _load_yaml(Path(runtime_contract_path))
    trait_dir = _resolve_traits_dir_from_contract_payload(contract_payload, runtime_contract_path)
    return load_trait_definitions_from_dir(trait_dir)


def load_trait_definitions_from_dir(traits_dir: str | Path) -> list[dict[str, Any]]:
    resolved_dir = Path(traits_dir).expanduser().resolve()
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        return []

    trait_definitions: list[dict[str, Any]] = []
    for trait_path in sorted(resolved_dir.glob("T*.json")):
        trait_payload = _load_json(trait_path)
        if isinstance(trait_payload, dict):
            trait_definitions.append(_normalize_trait_definition(trait_payload))
    return trait_definitions


def canonical_trait_id(trait_id: Any) -> str:
    candidate = str(trait_id or "").strip()
    if not candidate:
        return ""
    rubric_match = RUBRIC_TRAIT_ID_PATTERN.fullmatch(candidate)
    if rubric_match:
        return f"trait_{int(rubric_match.group(1))}"
    runtime_match = RUNTIME_TRAIT_ID_PATTERN.fullmatch(candidate)
    if runtime_match:
        return f"trait_{int(runtime_match.group(1))}"
    return candidate


def trait_id_aliases(trait_id: Any) -> list[str]:
    candidate = str(trait_id or "").strip()
    if not candidate:
        return []
    canonical_id = canonical_trait_id(candidate)
    aliases = [canonical_id]
    if candidate != canonical_id:
        aliases.append(candidate)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _normalize_trait_definition(trait_definition: dict[str, Any]) -> dict[str, Any]:
    canonical_id = canonical_trait_id(trait_definition.get("trait_id"))
    normalized = dict(trait_definition)
    normalized["trait_id"] = canonical_id or str(trait_definition.get("trait_id", "") or "").strip()
    normalized["trait_aliases"] = trait_id_aliases(trait_definition.get("trait_id"))
    return normalized


def _resolve_traits_dir_from_bundle(runtime_bundle: dict[str, Any]) -> Path | None:
    resolved_paths = runtime_bundle.get("resolved_paths")
    if not isinstance(resolved_paths, dict):
        return None

    traits_dir = resolved_paths.get("traits_dir")
    if not traits_dir:
        return None
    return Path(traits_dir)


def _resolve_traits_dir_from_contract_payload(
    contract_payload: dict[str, Any],
    runtime_contract_path: str | Path,
) -> Path:
    paths = contract_payload.get("paths")
    if not isinstance(paths, dict):
        return Path(runtime_contract_path).expanduser().resolve().parent

    traits_dir = paths.get("traits_dir")
    if not isinstance(traits_dir, str) or not traits_dir.strip():
        return Path(runtime_contract_path).expanduser().resolve().parent

    base_dir = Path(runtime_contract_path).expanduser().resolve().parent
    return (base_dir / traits_dir).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _load_yaml(path: Path) -> dict[str, Any]:
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        return {}

    payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}
