from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from onboarding_operations import EmailSettings, Employee, EmployeeTask, ReminderCadence, TaskTemplate
from onboarding_operations import parse_recipients
from onboarding_operations import OnboardingReminderRunner
from onboarding_operations import reminder_send_estimate, split_and_validate_recipients, validate_sender_email

_CONTRACT_ROOT = Path("contracts")


def _load_module(path: str):
    module_name = path.removeprefix("src/").removesuffix(".py").removesuffix(".pyw").replace("/", ".")
    if module_name == "onboarding_app":
        if module_name in sys.modules:
            return sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, Path(path))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(module_name)


def _iter_onboarding_contract_cases() -> list[tuple[str, str, str, dict[str, Any], str | None]]:
    cases: list[tuple[str, str, str, dict[str, Any], str | None]] = []
    for contract_path in sorted(_CONTRACT_ROOT.glob("onboarding_*.contract.yaml")):
        data = yaml.safe_load(contract_path.read_text())
        module_path = data["module"]["path"]
        for fn in data.get("functions", []):
            cases.append((str(contract_path), module_path, fn["name"], fn.get("inputs", {}), None))
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                cases.append((str(contract_path), module_path, method["name"], method.get("inputs", {}), cls["name"]))
    return cases


def _param_names(sig: inspect.Signature, *, method: bool) -> list[str]:
    params: list[str] = []
    for idx, param in enumerate(sig.parameters.values()):
        if method and idx == 0 and param.name == "self":
            continue
        name = param.name
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            name = f"*{name}"
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            name = f"**{name}"
        params.append(name)
    return params


@pytest.mark.parametrize(
    "contract_path,module_path,symbol_name,contract_inputs,class_name",
    _iter_onboarding_contract_cases(),
)
def test_onboarding_contract_symbol_signature_exists(
    contract_path: str,
    module_path: str,
    symbol_name: str,
    contract_inputs: dict[str, Any],
    class_name: str | None,
) -> None:
    module = _load_module(module_path)
    target = getattr(getattr(module, class_name), symbol_name) if class_name else getattr(module, symbol_name)
    signature = inspect.signature(target)
    expected_inputs = list(contract_inputs.keys())
    if class_name and expected_inputs and expected_inputs[0] == "cls":
        expected_inputs = expected_inputs[1:]
    assert _param_names(signature, method=bool(class_name)) == expected_inputs, contract_path


def test_onboarding_contract_entries_declare_return_types() -> None:
    for contract_path in sorted(_CONTRACT_ROOT.glob("onboarding_*.contract.yaml")):
        data = yaml.safe_load(contract_path.read_text())
        for fn in data.get("functions", []):
            assert isinstance(fn.get("returns", {}).get("type"), str) and fn["returns"]["type"]
        for cls in data.get("classes", []):
            for method in cls.get("methods", []):
                assert isinstance(method.get("returns", {}).get("type"), str) and method["returns"]["type"]


def test_security_input_trust_boundary_recipient_parsing_and_validation() -> None:
    assert parse_recipients(" principal@example.com, ,lead@example.com ") == ["principal@example.com", "lead@example.com"]
    valid, invalid = split_and_validate_recipients("ok@example.com, bad@@example.com,  ")
    assert valid == ["ok@example.com"]
    assert invalid == ["bad@@example.com"]


def test_security_sender_validation_and_estimates() -> None:
    allowed, reason = validate_sender_email("director@school.org")
    assert allowed is True
    assert reason is None

    denied, denied_reason = validate_sender_email("not-an-email")
    assert denied is False
    assert denied_reason == "invalid_format"

    result = type(
        "ReminderResult",
        (),
        {
            "counts": {"due_reminders": 2},
            "escalation_candidates": [{"task_id": "1"}],
            "recipients": {"reminder": ["ops@example.com"], "escalation": []},
        },
    )()
    totals = reminder_send_estimate(result)
    assert totals["email_messages"] == 1
    assert totals["in_app_messages"] == 2
    assert totals["total_messages"] == 3


def test_security_dry_run_is_idempotent_and_does_not_mutate_task_state() -> None:
    employee = Employee(
        id="e1",
        name="Jordan",
        acceptance_date="2026-01-01",
        start_date="2026-01-01",
        tasks=[EmployeeTask(id="task1", template_id="template1", title="Collect docs", due_date="2026-01-01")],
    )
    template = TaskTemplate(
        id="template1",
        title="Collect docs",
        reference="start_date",
        offset_days=0,
        cadence=ReminderCadence(mode="daily", interval_days=1),
    )
    settings = EmailSettings(reminder_recipients="ops@example.com", director_and_owners="lead@example.com")

    runner = OnboardingReminderRunner(
        employees=[employee],
        templates=[template],
        email_settings=settings,
        reminder_sender=lambda *_args, **_kwargs: "ok",
        escalation_sender=lambda *_args, **_kwargs: "ok",
    )

    first = runner.run(now_date=date(2026, 1, 2), dry_run=True)
    second = runner.run(now_date=date(2026, 1, 2), dry_run=True)

    assert first.counts["due_reminders"] == 1
    assert second.counts["due_reminders"] == 1
    assert employee.tasks[0].last_reminder_sent is None
