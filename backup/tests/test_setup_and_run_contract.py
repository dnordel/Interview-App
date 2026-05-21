from __future__ import annotations

import re
from pathlib import Path

import yaml


SETUP_CONTRACT = Path("contracts/setup_and_run.contract.yaml")
SETUP_SCRIPT = Path("setup_and_run.ps1")
FUNCTION_PATTERN = re.compile(r"(?ms)^function\s+([A-Za-z0-9_-]+)\s*\{(.*?)^}\s*$")
PARAM_PATTERN = re.compile(r"\]\s*\$([A-Za-z_][A-Za-z0-9_]*)")


def _script_functions() -> dict[str, str]:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    return {name: body for name, body in FUNCTION_PATTERN.findall(script_text)}


def _param_names(function_body: str) -> list[str]:
    param_start = function_body.find("param(")
    if param_start == -1:
        return []

    body = function_body[param_start:]
    return PARAM_PATTERN.findall(body)


def test_setup_and_run_contract_signatures_match_script() -> None:
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    functions = _script_functions()

    for item in contract["functions"]:
        assert item["name"] in functions
        assert _param_names(functions[item["name"]]) == list(item["inputs"].keys())


def test_setup_and_run_contract_describes_current_python_and_venv_flow() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "python-3.11.9-amd64.exe" in script_text
    assert 'Run-Proc -File $PyExe -Args @("-m","venv",$VenvDir)' in script_text
    assert 'Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$req)' in script_text
    stale_names = {"Get-UvCommand", "Initialize-UvEnvironment", "Invoke-Uv", "Resolve-PythonSelector", "Sync-Project", "Start-InterviewApp"}

    assert stale_names.isdisjoint({item["name"] for item in contract["functions"]})
    assert "per-user virtual environment" in contract["module"]["description"]


def test_setup_and_run_launches_runtime_wrapper_with_venv_python() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'Join-Path (Join-Path $AppDir "src") "runtime_wrapper.py"' in script_text
    assert '$wrapperArgs = @("--target", $appFull, "--app-root", $AppDir)' in script_text
    assert '-PythonExe $venvPy' in script_text
