from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "full-test-suite.yml"
CONTRACT_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "contract-quality.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _load_contract_workflow() -> dict:
    return yaml.safe_load(CONTRACT_WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_full_test_suite_ci_runs_pytest_on_pull_requests_and_main_pushes() -> None:
    workflow = _load_workflow()

    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["main"]

    all_run_commands = "\n".join(
        str(step.get("run", ""))
        for job in workflow["jobs"].values()
        for step in job["steps"]
    )

    assert "python -m pytest" in all_run_commands
    assert "tests/" not in all_run_commands


def test_contract_review_workflows_invoke_section_checks_through_bash() -> None:
    workflows = [_load_workflow(), _load_contract_workflow()]

    all_run_commands = "\n".join(
        str(step.get("run", ""))
        for workflow in workflows
        for job in workflow["jobs"].values()
        for step in job["steps"]
    )

    assert all_run_commands.count("bash scripts/contract_review/run_section_checks.sh") == 2
    assert "\nscripts/contract_review/run_section_checks.sh\n" not in (
        f"\n{all_run_commands}\n"
    )
