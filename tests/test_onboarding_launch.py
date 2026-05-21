from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from onboarding_models import LaunchEmployeeSeed

loader = importlib.machinery.SourceFileLoader("onboarding_app_runtime", "src/onboarding_app.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
onboarding_app = importlib.util.module_from_spec(spec)
loader.exec_module(onboarding_app)


def test_load_launch_context_recovers_employee_seed_from_state_file(tmp_path) -> None:
    state_path = tmp_path / "launch.json"
    state_path.write_text(
        json.dumps(
            {
                "employee_seed": {
                    "name": "Taylor Teacher",
                    "school": "North Long Beach",
                    "acceptance_date": "2026-03-20",
                    "start_date": "",
                }
            }
        ),
        encoding="utf-8",
    )

    context = onboarding_app._load_launch_context(
        Namespace(employee_id="", urgent_only=False, state_file=str(state_path))
    )

    assert context["employee_seed"]["name"] == "Taylor Teacher"
    assert context["employee_seed"]["start_date"] == ""


def test_apply_launch_context_opens_prefilled_add_employee_dialog() -> None:
    app = onboarding_app.OnboardingTrackerApp.__new__(onboarding_app.OnboardingTrackerApp)
    app.launch_context = {"employee_seed": {"name": "Taylor Teacher"}}
    app._pending_launch_employee_seed = LaunchEmployeeSeed(name="Taylor Teacher", school="North Long Beach")
    calls: list[LaunchEmployeeSeed] = []
    app._set_task_filter = lambda _key: None
    app._select_employee_by_id = lambda _employee_id: (_ for _ in ()).throw(AssertionError("should not select by id"))
    app.open_add_employee_dialog = lambda prefill=None: calls.append(prefill)

    onboarding_app.OnboardingTrackerApp._apply_launch_context(app)

    assert len(calls) == 1
    assert calls[0].name == "Taylor Teacher"
    assert calls[0].school == "North Long Beach"
    assert app._pending_launch_employee_seed is None
