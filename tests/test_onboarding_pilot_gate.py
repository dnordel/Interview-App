from __future__ import annotations

from datetime import date, timedelta
import pytest
from tools import onboarding_pilot_gate_cli

from onboarding_pilot_gate import (
    REQUIRED_PILOT_SCENARIOS,
    approve_rollout,
    enabled_director_schools,
    evaluate_pilot_gate,
    record_pilot_day,
)


def test_five_business_days_two_devices_and_all_scenarios_pass_gate(tmp_path):
    evidence_path = tmp_path / "pilot" / "evidence.jsonl"
    monday = date(2026, 7, 20)
    for offset in range(5):
        record_pilot_day(
            evidence_path,
            business_date=monday + timedelta(days=offset),
            device_id="computer-a" if offset < 3 else "computer-b",
            scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=(),
        )

    result = evaluate_pilot_gate(evidence_path)

    assert result.passed is True
    assert result.business_day_count == 5
    assert result.device_count == 2
    assert result.missing_scenarios == ()


def test_pilot_day_rejects_weekends(tmp_path):
    with pytest.raises(ValueError, match="business day"):
        record_pilot_day(
            tmp_path / "evidence.jsonl",
            business_date=date(2026, 7, 25),
            device_id="computer-a",
            scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=(),
        )


def test_pilot_day_rejects_duplicate_business_date(tmp_path):
    path = tmp_path / "evidence.jsonl"
    values = dict(
        business_date=date(2026, 7, 20), device_id="computer-a",
        scenarios=REQUIRED_PILOT_SCENARIOS, defects=(),
    )
    record_pilot_day(path, **values)
    with pytest.raises(ValueError, match="already recorded"):
        record_pilot_day(path, **values)


def test_rollout_approvals_are_explicit_and_ordered_after_passing_gate(tmp_path):
    path = tmp_path / "evidence.jsonl"
    monday = date(2026, 7, 20)
    for offset in range(5):
        record_pilot_day(
            path, business_date=monday + timedelta(days=offset),
            device_id=f"computer-{offset % 2}", scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=(),
        )
    assert enabled_director_schools(path) == ("Palmdale",)
    with pytest.raises(ValueError, match="Hawthorne must be approved first"):
        approve_rollout(
            path, school="North Long Beach", actor="admin",
            confirm_no_critical_high=True, reason="Pilot passed",
        )

    approve_rollout(
        path, school="Hawthorne", actor="admin",
        confirm_no_critical_high=True, reason="Palmdale pilot passed",
    )
    approve_rollout(
        path, school="North Long Beach", actor="admin",
        confirm_no_critical_high=True, reason="Hawthorne verified",
    )
    assert enabled_director_schools(path) == (
        "Palmdale", "Hawthorne", "North Long Beach",
    )


def test_pilot_gate_cli_records_day_and_reports_pii_safe_status(tmp_path, capsys):
    path = tmp_path / "evidence.jsonl"
    assert onboarding_pilot_gate_cli.main([
        "record-day", "--path", str(path), "--date", "2026-07-20",
        "--device", "computer-a", "--all-scenarios",
    ]) == 0
    assert onboarding_pilot_gate_cli.main(["status", "--path", str(path)]) == 1
    output = capsys.readouterr().out
    assert '"business_day_count": 1' in output
    assert "computer-a" not in path.read_text(encoding="utf-8")


def test_open_high_defect_blocks_otherwise_complete_gate(tmp_path):
    path = tmp_path / "evidence.jsonl"
    monday = date(2026, 7, 20)
    for offset in range(5):
        record_pilot_day(
            path, business_date=monday + timedelta(days=offset),
            device_id=f"device-{offset % 2}", scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=({"severity": "high", "state": "open", "category": "sync"},)
            if offset == 0 else (),
        )
    result = evaluate_pilot_gate(path)
    assert result.passed is False
    assert result.open_blocking_defects == 1


def test_corrupted_evidence_fails_evaluation_and_runtime_scope_fails_closed(tmp_path):
    path = tmp_path / "evidence.jsonl"
    record_pilot_day(
        path, business_date=date(2026, 7, 20), device_id="device-a",
        scenarios=REQUIRED_PILOT_SCENARIOS, defects=(),
    )
    path.write_text(path.read_text(encoding="utf-8").replace("hire", "tampered"), encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        evaluate_pilot_gate(path)
    assert enabled_director_schools(path) == ("Palmdale",)


def test_pilot_record_rejects_unknown_scenario_and_invalid_defect_shape(tmp_path):
    with pytest.raises(ValueError, match="scenario"):
        record_pilot_day(
            tmp_path / "unknown.jsonl", business_date=date(2026, 7, 20),
            device_id="device-a", scenarios=("employee_name",), defects=(),
        )
    with pytest.raises(ValueError, match="defect"):
        record_pilot_day(
            tmp_path / "defect.jsonl", business_date=date(2026, 7, 20),
            device_id="device-a", scenarios=REQUIRED_PILOT_SCENARIOS,
            defects=({"severity": "urgent", "state": "open", "category": "sync"},),
        )
