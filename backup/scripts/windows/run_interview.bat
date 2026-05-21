@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\..\"
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%REPO_ROOT%setup_and_run.ps1"
exit /b
