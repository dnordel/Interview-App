from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml


ROOT = Path(".")
BAT = ROOT / "..START DIRECTOR STAFFING DASHBOARD.bat"
SCRIPT = ROOT / "setup_director_staffing.ps1"
CONTRACT = ROOT / "contracts" / "setup_director_staffing.contract.yaml"
APP = ROOT / "src" / "staffing_dashboard_app.py"
APP_CONTRACT = ROOT / "contracts" / "staffing_dashboard_app.contract.yaml"
DIRECTOR_REQUIREMENTS = ROOT / "requirements-director.txt"


FUNCTION_PATTERN = re.compile(r"(?ms)^function\s+([A-Za-z0-9_-]+)\s*(?:\((.*?)\))?\s*\{(.*?)^}\s*$")
PARAM_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
POWERSHELL_LITERALS = {"false", "null", "true"}


def _script_functions() -> dict[str, tuple[str | None, str]]:
    script_text = SCRIPT.read_text(encoding="utf-8")
    functions: dict[str, tuple[str | None, str]] = {}
    for name, inline_params, body in FUNCTION_PATTERN.findall(script_text):
        functions.setdefault(name, (inline_params or None, body))
    return functions


def _param_names(function_definition: tuple[str | None, str]) -> list[str]:
    inline_params, function_body = function_definition
    param_source = inline_params
    if param_source is None:
        param_start = function_body.find("param(")
        if param_start == -1:
            return []
        body_start = param_start + len("param(")
        depth = 1
        for idx in range(body_start, len(function_body)):
            char = function_body[idx]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    param_source = function_body[body_start:idx]
                    break
        if param_source is None:
            return []

    param_source = re.sub(r"=\s*\$[A-Za-z_][A-Za-z0-9_]*", "", param_source)
    return [
        name
        for name in PARAM_PATTERN.findall(param_source)
        if name.lower() not in POWERSHELL_LITERALS
    ]


def test_director_bat_runs_light_staffing_setup() -> None:
    text = BAT.read_text(encoding="utf-8")

    assert "setup_director_staffing.ps1" in text
    assert "setup_and_run.ps1" not in text
    assert 'cd /d "%~dp0"' in text


def test_director_setup_contract_signatures_match_script() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    functions = _script_functions()

    for item in contract["functions"]:
        assert item["name"] in functions
        assert _param_names(functions[item["name"]]) == list(item["inputs"].keys())


def test_director_setup_skips_interview_runtime_dependencies() -> None:
    script_text = SCRIPT.read_text(encoding="utf-8")
    req_text = DIRECTOR_REQUIREMENTS.read_text(encoding="utf-8")

    assert "Checking Python 3.11 install" in script_text
    assert "Checking director staffing packages" in script_text
    assert "Launching staffing dashboard" in script_text
    assert "staffing_dashboard_app.py" in script_text
    assert "requirements-director.txt" in script_text
    assert "PySide6==6.8.1.1" in req_text

    forbidden = [
        "VB-CABLE",
        "Ensure-LocalDeepSeek",
        "Ensure-Ollama",
        "Ollama",
        "deepseek-r1",
        "faster-whisper",
        "transformers",
        "requirements.txt",
        "ffmpeg",
    ]
    for token in forbidden:
        assert token not in script_text
    assert "transformers" not in req_text
    assert "faster-whisper" not in req_text


def test_staffing_dashboard_app_is_staffing_only_entrypoint() -> None:
    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    forbidden = {
        "admin_studio",
        "data_store",
        "interview_runtime",
        "scoring_reporting",
        "transformers",
        "deepseek_finalize_worker",
        "interview_audio_recorder",
        "docx",
    }
    assert forbidden.isdisjoint(imported_modules)
    assert {"staffing_service", "staffing_store", "PySide6"}.issubset(imported_modules)


def test_staffing_dashboard_app_contract_mentions_director_entrypoint() -> None:
    contract = yaml.safe_load(APP_CONTRACT.read_text(encoding="utf-8"))
    system = yaml.safe_load((ROOT / "contracts" / "system.contract.yaml").read_text(encoding="utf-8"))
    architecture = yaml.safe_load((ROOT / "contracts" / "architecture.contract.yaml").read_text(encoding="utf-8"))

    assert contract["module"]["path"] == "src/staffing_dashboard_app.py"
    assert "director" in contract["module"]["description"].lower()
    assert "staffing_dashboard_app" in system["modules"]
    assert "director_staffing_launcher" in system["modules"]
    assert "director_staffing_dashboard_service" in architecture["services"]
