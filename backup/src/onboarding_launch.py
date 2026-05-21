from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from onboarding_models import LaunchEmployeeSeed


def build_launch_context(
    *,
    employee_id: str | None = None,
    urgent_only: bool = False,
    employee_seed: LaunchEmployeeSeed | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "employee_id": str(employee_id or "").strip(),
        "urgent_only": bool(urgent_only),
    }
    if employee_seed is None:
        return context
    if not employee_seed.has_prefill():
        return context
    context["employee_seed"] = employee_seed.to_dict()
    return context


def write_launch_context_file(payload: dict[str, object]) -> Path | None:
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="onboarding_launch_",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump(payload, handle)
            return Path(handle.name)
    except OSError:
        return None


def read_launch_context_file(path_value: str | None) -> dict[str, object]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return dict(payload)


def extract_employee_seed(payload: dict[str, object] | None) -> LaunchEmployeeSeed | None:
    if not isinstance(payload, dict):
        return None
    seed = LaunchEmployeeSeed.from_dict(payload.get("employee_seed"))
    if not seed.has_prefill():
        return None
    return seed
