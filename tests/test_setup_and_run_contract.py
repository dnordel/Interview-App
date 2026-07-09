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
    assert '$VenvBase = Join-Path (Get-ConfigBaseDir) "py311"' in script_text
    assert 'Run-Proc -File $PyExe -Args @("-m","venv","--system-site-packages",$VenvDir)' in script_text
    assert "Test-VenvUsesSystemSitePackages" in {item["name"] for item in contract["functions"]}
    assert "Existing venv is isolated; recreating to reuse system site packages." in script_text
    assert 'Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$req)' in script_text
    stale_names = {"Get-UvCommand", "Initialize-UvEnvironment", "Invoke-Uv", "Resolve-PythonSelector", "Sync-Project", "Start-InterviewApp"}

    assert stale_names.isdisjoint({item["name"] for item in contract["functions"]})
    assert "per-user virtual environment" in contract["module"]["description"]
    assert "system site packages" in descriptions


def test_setup_and_run_drains_process_streams_without_pipe_deadlock() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "ReadToEndAsync()" in script_text
    assert ".add_OutputDataReceived" not in script_text
    assert ".add_ErrorDataReceived" not in script_text
    assert "BeginOutputReadLine()" not in script_text
    assert "BeginErrorReadLine()" not in script_text
    assert ".StandardOutput.ReadToEnd()" not in script_text
    assert ".StandardError.ReadToEnd()" not in script_text
    assert "prevent pipe deadlocks" in descriptions


def test_windows_launchers_do_not_use_repo_root_venv() -> None:
    launcher_paths = [
        Path("Start Interview Assistant PySide6.bat"),
        Path("scripts/windows/requirements.bat"),
    ]

    for launcher_path in launcher_paths:
        launcher_text = launcher_path.read_text(encoding="utf-8")
        assert '".venv\\Scripts\\python.exe"' not in launcher_text
        assert "%APP_DIR%.venv" not in launcher_text

    requirements_text = Path("scripts/windows/requirements.bat").read_text(encoding="utf-8")
    pyside_launcher_text = Path("Start Interview Assistant PySide6.bat").read_text(
        encoding="utf-8"
    )

    assert "%LOCALAPPDATA%\\LPL_InterviewTool\\py311" in requirements_text
    assert "venv --system-site-packages" in requirements_text
    assert '-File "%RUNNER%" -UiMode pyside' in pyside_launcher_text


def test_primary_setup_launchers_force_pyside_mode() -> None:
    launcher_paths = [
        Path("..START PROGRAM.bat"),
        Path("Start Preschool Teacher Interview Guide.bat"),
        Path("start.bat"),
    ]

    for launcher_path in launcher_paths:
        launcher_text = launcher_path.read_text(encoding="utf-8")
        assert "-UiMode pyside" in launcher_text


def test_setup_and_run_launches_runtime_wrapper_with_venv_python() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'Join-Path (Join-Path $AppDir "src") "runtime_wrapper.py"' in script_text
    assert '$wrapperArgs = @("--target", $appFull, "--app-root", $AppDir)' in script_text
    assert '-PythonExe $venvPy' in script_text


def test_setup_and_run_defaults_to_pyside_and_versions_ui_mode() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert '$DefaultUiMode = "pyside"' in script_text
    assert '$DefaultInterviewAppFile = "pyside_interview_app.py"' in script_text
    assert '$PySideInterviewAppFile = "pyside_interview_app.py"' in script_text
    assert 'PreferredUiMode = $DefaultUiMode' in script_text
    assert 'Resolve-PreferredInterviewAppFile -Cfg $Cfg' in script_text
    assert 'switch ($uiMode)' not in script_text
    assert 'return $PySideInterviewAppFile' in script_text


def test_setup_and_run_invalidates_cached_path_against_selected_ui_mode() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert '$preferredAppFile = Resolve-PreferredInterviewAppFile -Cfg $Cfg' in script_text
    assert '$Cfg.App.PreferredInterviewAppFile = $preferredAppFile' in script_text
    assert 'Split-Path $Cfg.App.InterviewAppPath -Leaf' in script_text
    assert '$candidates = @("pyside_interview_app.py")' in script_text
    assert '"interview_app' + '.pyw"' not in script_text
    assert '$dlg.Filter = "Python GUI (*.py;*.pyw)|*.py;*.pyw"' in script_text


def test_setup_and_run_versions_requirements_and_checks_pyside_dependency() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}

    assert "Get-RequirementsFingerprint" in function_names
    assert "RequirementsFingerprint" in script_text
    assert 'Get-FileHash -Algorithm SHA256 -Path $RequirementsPath' in script_text
    assert '@{ Package = "PySide6"; Module = "PySide6" }' in script_text


def test_setup_and_run_skips_dependency_installs_when_fingerprints_are_unchanged() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "Requirements fingerprint unchanged and venv healthy; skipping base dependency install." in script_text
    assert "GPU requirements fingerprint unchanged and venv healthy; skipping GPU dependency install." in script_text
    assert "OpenVINO requirements fingerprint unchanged and venv healthy; skipping OpenVINO dependency install." in script_text
    assert "$baseDepsNeedInstall = $needRecreate -or ($cfg.Tools.RequirementsFingerprint -ne $requirementsFingerprint)" in script_text
    assert "$gpuDepsNeedInstall = $needRecreate -or ($cfg.Tools.GpuRequirementsFingerprint -ne $gpuRequirementsFingerprint)" in script_text
    assert "$openVinoDepsNeedInstall = $needRecreate -or ($cfg.Tools.OpenVinoRequirementsFingerprint -ne $openVinoRequirementsFingerprint)" in script_text
    assert "skip unchanged dependency installs on healthy cached environments" in descriptions


def test_setup_and_run_keeps_nvidia_packages_out_of_base_requirements() -> None:
    requirements_text = Path("requirements.txt").read_text(encoding="utf-8").lower()
    gpu_requirements_text = Path("requirements-gpu.txt").read_text(encoding="utf-8").lower()

    assert "nvidia-" not in requirements_text
    assert "nvidia-cublas-cu12" in gpu_requirements_text
    assert "nvidia-cudnn-cu12" in gpu_requirements_text


def test_setup_and_run_cleans_up_nvidia_packages_without_nvidia_gpu() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "Get-GpuVendorProfile" in function_names
    assert "Remove-NvidiaGpuPackagesWhenUnsupported" in function_names
    assert "Set-GpuVendorEnvironment" in function_names
    assert "Remove-NvidiaGpuPackagesWhenUnsupported -VenvPy $VenvPy" in script_text
    assert "Set-GpuVendorEnvironment -Profile $gpuProfile" in script_text
    assert "INTERVIEW_GPU_VENDOR=$vendor" in script_text
    assert '"pip","uninstall","-y","nvidia-cublas-cu12","nvidia-cudnn-cu12"' in script_text
    assert "Skipping GPU dependency install because no NVIDIA GPU was detected." in script_text
    assert "remove NVIDIA-only Python wheels" in descriptions


def test_setup_and_run_detects_amd_intel_without_installing_nvidia_wheels() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert '$profile = [PSCustomObject]@{' in script_text
    assert "Amd = $false" in script_text
    assert "Intel = $false" in script_text
    assert '$name -match "AMD|Radeon|Advanced Micro Devices"' in script_text
    assert '$name -match "Intel|Arc|Iris|UHD Graphics"' in script_text
    assert (
        "AMD GPU detected. Ollama may use supported ROCm/Vulkan acceleration; "
        "Whisper transcription will use OpenVINO GenAI unless an external Vulkan whisper.cpp backend is configured."
    ) in script_text
    assert "Intel GPU detected. OpenVINO GenAI will be used for Whisper transcription" in script_text


def test_setup_and_run_installs_openvino_packages_for_transcription_default() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    openvino_requirements = Path("requirements-openvino.txt").read_text(encoding="utf-8")

    assert "openvino-genai" in openvino_requirements
    assert "openvino-tokenizers" in openvino_requirements
    assert '$openVinoReq = Join-Path $AppDir "requirements-openvino.txt"' in script_text
    assert 'if (Test-Path $openVinoReq)' in script_text
    assert 'Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$openVinoReq)' in script_text
    assert '$env:INTERVIEW_WHISPER_BACKEND = "openvino_genai"' in script_text
    assert '$env:INTERVIEW_OPENVINO_WHISPER_MODEL = "OpenVINO/whisper-small-int8-ov"' in script_text


def test_setup_and_run_configures_whisper_cpp_for_amd_when_present() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}

    assert "Find-WhisperCppCli" in function_names
    assert "Find-WhisperCppModel" in function_names
    assert '$env:INTERVIEW_WHISPER_BACKEND = "whisper_cpp"' in script_text
    assert '$env:INTERVIEW_WHISPERCPP_EXE = $whisperCppExe' in script_text
    assert '$env:INTERVIEW_WHISPERCPP_MODEL = $whisperCppModel' in script_text


def test_setup_and_run_adds_cuda_paths_only_with_nvidia_gpu() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    cuda_comment_index = script_text.index("# Expose CUDA runtime DLLs for faster-whisper")
    vbcable_index = script_text.index("# VB-CABLE handling based on detection + user answer")
    cuda_block = script_text[cuda_comment_index:vbcable_index]

    assert "if (Test-NvidiaGPU) {" in cuda_block
    assert "Skipping CUDA PATH setup because no NVIDIA GPU was detected." in cuda_block


def test_setup_and_run_fails_when_pyside_import_fails() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}

    assert "Ensure-SelectedUiModeAvailable" in function_names
    assert 'Run-Proc -File $VenvPy -Args @("-c", "import PySide6")' in script_text
    assert "PySide6 is required; deprecated Tk UI fallback has been removed." in script_text
    assert "falling back to " + "Tk UI" not in script_text
    assert "Ensure-SelectedUiModeAvailable -Cfg $cfg -VenvPy $venvPy" in script_text


def test_setup_and_run_does_not_execute_legacy_vbcable_installer_tail() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    modern_dialog_index = script_text.index("[void]$form.ShowDialog()")
    legacy_tail_index = script_text.index("LPL SETUP AND RUN SCRIPT (FULL INTEGRATION)")

    assert modern_dialog_index < legacy_tail_index
    assert "return" in script_text[modern_dialog_index:legacy_tail_index]


def test_setup_and_run_detects_working_vbcable_audio_device_before_prompt() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")

    detection_index = script_text.index("function Find-VBCableDriverEvidence")
    prompt_index = script_text.index("function Show-VBCablePrompt")
    detection_body = script_text[detection_index:prompt_index]

    assert "Get-CimInstance Win32_SoundDevice" in detection_body
    assert '*CABLE Input*' in detection_body
    assert '*CABLE Output*' in detection_body
    assert '*VB-Audio*' in detection_body
    assert "AudioDevice:" in detection_body


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
    assert '$AllowedLocalDeepSeekModels = @("deepseek-r1:1.5b", "deepseek-r1:8b", "deepseek-r1:14b")' in script_text
    assert 'Join-Path (Join-Path $AppDir "user_artifacts") "interview_app_settings.json"' in script_text
    assert '$AllowedLocalDeepSeekModels -contains $selectedModel' in script_text
    assert '$LocalDeepSeekModel = $env:DEEPSEEK_SUMMARY_MODEL.Trim()' in script_text
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Ollama\\ollama.exe"' in script_text
    assert 'Join-Path $env:LOCALAPPDATA "Ollama\\ollama.exe"' in script_text
    assert 'Microsoft\\WinGet\\Packages' in script_text
    assert '"--id", "Ollama.Ollama"' in script_text
    assert "foreach ($localModel in @($tags.models))" in script_text
    assert "[string]$localModel.name -ieq $Model" in script_text
    assert "[string]$localModel.model -ieq $Model" in script_text
    assert "foreach ($model in @($tags.models))" not in script_text
    assert "Invoke-OllamaModelPull -Model $Model" in script_text
    assert "for ($i = 0; $i -lt 10; $i++)" in script_text
    assert '$env:DEEPSEEK_API_BASE_URL = "$OllamaBaseUrl/v1"' in script_text
    assert '$env:DEEPSEEK_API_KEY = "ollama"' in script_text
    assert "retry local registry checks" in descriptions


def test_setup_and_run_streams_deepseek_pull_progress_to_ui_and_log() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    function_names = {item["name"] for item in contract["functions"]}
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "Invoke-OllamaModelPull" in function_names
    assert '"$OllamaBaseUrl/api/pull"' in script_text
    assert '"stream" = $true' in script_text
    assert '"name" = $Model' in script_text
    assert "ReadLine()" in script_text
    assert '$percent = [Math]::Floor(($completed / $total) * 100)' in script_text
    assert 'Set-Progress 65 "Downloading local DeepSeek model ($Model): $percent%"' in script_text
    assert 'Write-Log "DeepSeek model download progress: $Model $percent% ($completed of $total bytes)"' in script_text
    assert 'Invoke-OllamaModelPull -Model $Model' in script_text
    assert 'Run-Proc -File $OllamaExe -Args @("pull", $Model)' not in script_text
    assert "download percentage" in descriptions


def test_setup_and_run_shows_visible_setup_details() -> None:
    script_text = SETUP_SCRIPT.read_text(encoding="utf-8")
    contract = yaml.safe_load(SETUP_CONTRACT.read_text(encoding="utf-8"))
    descriptions = " ".join(item["description"] for item in contract["functions"])

    assert "$details = New-Object System.Windows.Forms.TextBox" in script_text
    assert "$details.ReadOnly = $true" in script_text
    assert '$details.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text))' in script_text
    assert "Checking local DeepSeek through Ollama" in script_text
    assert "append visible setup details" in descriptions
