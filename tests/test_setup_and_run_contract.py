from __future__ import annotations

import re
from pathlib import Path

import yaml


SETUP_CONTRACT = Path("contracts/setup_and_run.contract.yaml")
SETUP_SCRIPT = Path("setup_and_run.ps1")
FUNCTION_PATTERN = re.compile(r"(?ms)^function\s+([A-Za-z0-9_-]+)\s*(?:\((.*?)\))?\s*\{(.*?)^}\s*$")
PARAM_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
POWERSHELL_LITERALS = {"false", "null", "true"}


def _script_functions() -> dict[str, tuple[str | None, str]]:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
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
    assert 'Run-Proc -File $PyExe -Args @("-m","venv","--system-site-packages",$VenvDir)' in script_text
    assert "Test-VenvUsesSystemSitePackages" in {item["name"] for item in contract["functions"]}
    assert "Existing venv is isolated; recreating to reuse system site packages." in script_text
    assert 'Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$req)' in script_text
    stale_names = {"Get-UvCommand", "Initialize-UvEnvironment", "Invoke-Uv", "Resolve-PythonSelector", "Sync-Project", "Start-InterviewApp"}

    assert stale_names.isdisjoint({item["name"] for item in contract["functions"]})
    assert "per-user virtual environment" in contract["module"]["description"]
    assert "system site packages" in descriptions


def test_setup_and_run_launches_runtime_wrapper_with_venv_python() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'Join-Path (Join-Path $AppDir "src") "runtime_wrapper.py"' in script_text
    assert '$wrapperArgs = @("--target", $appFull, "--app-root", $AppDir)' in script_text
    assert '-PythonExe $venvPy' in script_text


def test_setup_and_run_does_not_execute_legacy_vbcable_installer_tail() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    modern_dialog_index = script_text.index("[void]$form.ShowDialog()")
    legacy_tail_index = script_text.index("LPL SETUP AND RUN SCRIPT (FULL INTEGRATION)")

    assert modern_dialog_index < legacy_tail_index
    assert "return" in script_text[modern_dialog_index:legacy_tail_index]


def test_setup_and_run_describes_first_run_audio_routing_steps() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}

    assert "Show-AudioRoutingInstructions" in function_names
    assert "Open Windows sound settings." in script_text
    assert "Open VB-CABLE Input device properties." in script_text
    assert "Enable listening to this device." in script_text
    assert "Set the listen output device, preferably a headset." in script_text
    assert "Select VB-CABLE as Windows sound output before recording." in script_text
    assert "AudioRoutingInstructionsShown" in script_text
    assert (
        '$cfg.VBCable | Add-Member -NotePropertyName AudioRoutingInstructionsShown'
        in script_text
    )
    assert (
        '$Cfg.VBCable | Add-Member -NotePropertyName AudioRoutingInstructionsShown'
        in script_text
    )


def test_setup_and_run_installs_local_deepseek_with_ollama() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "Ensure-Ollama" in function_names
    assert "Ensure-DeepSeekModel" in function_names
    assert "Enable-LocalDeepSeekForLaunchedApp" in function_names
    assert '$LocalDeepSeekModel = "deepseek-r1:8b"' in script_text
    assert '$LocalDeepSeekModel = $env:DEEPSEEK_SUMMARY_MODEL.Trim()' in script_text
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Ollama\\ollama.exe"' in script_text
    assert 'Join-Path $env:LOCALAPPDATA "Ollama\\ollama.exe"' in script_text
    assert 'Microsoft\\WinGet\\Packages' in script_text
    assert '"--id", "Ollama.Ollama"' in script_text
    assert "foreach ($localModel in @($tags.models))" in script_text
    assert "[string]$localModel.name -ieq $Model" in script_text
    assert "[string]$localModel.model -ieq $Model" in script_text
    assert "foreach ($model in @($tags.models))" not in script_text
    assert 'Run-Proc -File $OllamaExe -Args @("pull", $Model)' in script_text
    assert "for ($i = 0; $i -lt 10; $i++)" in script_text
    assert '$env:DEEPSEEK_API_BASE_URL = "$OllamaBaseUrl/v1"' in script_text
    assert '$env:DEEPSEEK_API_KEY = "ollama"' in script_text
    assert "retry local registry checks" in descriptions


def test_setup_and_run_shows_visible_setup_details() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "$details = New-Object System.Windows.Forms.TextBox" in script_text
    assert "$details.ReadOnly = $true" in script_text
    assert '$details.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text))' in script_text
    assert "Checking local DeepSeek through Ollama" in script_text
    assert "append visible setup details" in descriptions
