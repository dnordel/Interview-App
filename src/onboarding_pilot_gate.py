from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable, Mapping
from uuid import uuid4


REQUIRED_PILOT_SCENARIOS = (
    "hire", "offer", "tasks", "reminders", "package", "separation",
    "bidirectional_sync", "conflict_handling", "restart_recovery",
    "encrypted_access", "tombstone",
)


@dataclass(frozen=True)
class PilotGateResult:
    passed: bool
    business_day_count: int
    device_count: int
    missing_scenarios: tuple[str, ...]
    open_blocking_defects: int


def record_pilot_day(
    path: Path,
    *,
    business_date: date,
    device_id: str,
    scenarios: Iterable[str],
    defects: Iterable[Mapping[str, str]],
) -> None:
    if business_date.weekday() >= 5:
        raise ValueError("Pilot evidence date must be a business day.")
    existing = _load_records(path)
    if any(
        item.get("event_type") == "pilot_day"
        and item.get("business_date") == business_date.isoformat()
        for item in existing
    ):
        raise ValueError("Pilot evidence for this business day is already recorded.")
    clean_device = str(device_id or "").strip()
    if not clean_device:
        raise ValueError("Pilot device identifier is required.")
    clean_scenarios = tuple(sorted(set(str(value or "").strip() for value in scenarios)))
    if not clean_scenarios or not set(clean_scenarios).issubset(REQUIRED_PILOT_SCENARIOS):
        raise ValueError("Pilot evidence contains an unsupported scenario.")
    clean_defects: list[dict[str, str]] = []
    for item in defects:
        severity = str(item.get("severity") or "").strip().casefold()
        state = str(item.get("state") or "").strip().casefold()
        category = str(item.get("category") or "").strip().casefold()
        if (
            severity not in {"low", "medium", "high", "critical"}
            or state not in {"open", "closed"}
            or re.fullmatch(r"[a-z0-9_.-]{1,64}", category) is None
        ):
            raise ValueError("Pilot defect severity, state, or category is invalid.")
        clean_defects.append({"category": category, "severity": severity, "state": state})
    record = {
        "event_type": "pilot_day",
        "business_date": business_date.isoformat(),
        "device_id": hashlib.sha256(
            f"onboarding-pilot:{clean_device}".encode("utf-8")
        ).hexdigest(),
        "scenarios": clean_scenarios,
        "defects": clean_defects,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _append_record(path, record)


def evaluate_pilot_gate(path: Path) -> PilotGateResult:
    records = [item for item in _load_records(path) if item.get("event_type") == "pilot_day"]
    days = {str(item["business_date"]) for item in records}
    devices = {str(item["device_id"]) for item in records}
    covered = {
        str(scenario)
        for item in records
        for scenario in item.get("scenarios", ())
    }
    missing = tuple(sorted(set(REQUIRED_PILOT_SCENARIOS) - covered))
    blockers = sum(
        str(defect.get("severity", "")) in {"critical", "high"}
        and str(defect.get("state", "")) != "closed"
        for item in records
        for defect in item.get("defects", ())
    )
    return PilotGateResult(
        passed=len(days) >= 5 and len(devices) >= 2 and not missing and blockers == 0,
        business_day_count=len(days),
        device_count=len(devices),
        missing_scenarios=missing,
        open_blocking_defects=blockers,
    )


def approve_rollout(
    path: Path,
    *,
    school: str,
    actor: str,
    confirm_no_critical_high: bool,
    reason: str,
) -> None:
    target_school = str(school or "").strip()
    if target_school not in {"Hawthorne", "North Long Beach"}:
        raise ValueError("Rollout approval school is invalid.")
    if not confirm_no_critical_high:
        raise ValueError("Rollout approval requires explicit no-critical/high confirmation.")
    clean_actor = str(actor or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_actor or not clean_reason:
        raise ValueError("Rollout approval actor and reason are required.")
    gate = evaluate_pilot_gate(path)
    if not gate.passed:
        raise ValueError("Palmdale pilot gate has not passed.")
    enabled = enabled_director_schools(path)
    if target_school == "North Long Beach" and "Hawthorne" not in enabled:
        raise ValueError("Hawthorne must be approved first.")
    if target_school in enabled:
        raise ValueError(f"{target_school} is already approved.")
    _append_record(path, {
        "event_type": "rollout_approval",
        "school": target_school,
        "actor_id": hashlib.sha256(
            f"onboarding-rollout:{clean_actor}".encode("utf-8")
        ).hexdigest(),
        "reason_sha256": hashlib.sha256(clean_reason.encode("utf-8")).hexdigest(),
        "confirmed_no_critical_high": True,
        "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })


def enabled_director_schools(path: Path) -> tuple[str, ...]:
    order = ("Palmdale", "Hawthorne", "North Long Beach")
    try:
        records = _load_records(path)
    except ValueError:
        return ("Palmdale",)
    approved = {
        str(item.get("school") or "")
        for item in records
        if item.get("event_type") == "rollout_approval"
        and item.get("confirmed_no_critical_high") is True
    }
    return tuple(school for school in order if school == "Palmdale" or school in approved)


def _append_record(path: Path, record: dict[str, object]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_records(target) if target.exists() else []
    previous_hash = str(existing[-1]["record_hash"]) if existing else ""
    payload = dict(record, previous_hash=previous_hash)
    payload["record_hash"] = _record_hash(payload)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            for item in (*existing, payload):
                file.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_records(path: Path) -> list[dict[str, object]]:
    target = Path(path).resolve()
    if not target.exists():
        return []
    records: list[dict[str, object]] = []
    previous_hash = ""
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        for line in lines:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError
            record_hash = str(item.get("record_hash") or "")
            unsigned = {key: value for key, value in item.items() if key != "record_hash"}
            if str(item.get("previous_hash") or "") != previous_hash or record_hash != _record_hash(unsigned):
                raise ValueError
            records.append(item)
            previous_hash = record_hash
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError("Pilot evidence is corrupted or has been modified.") from exc
    return records


def _record_hash(record: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
