@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "VENV_PY=%LOCALAPPDATA%\LPL_InterviewTool\py311\.venv\Scripts\python.exe"
set "RUNNER=%ROOT_DIR%setup_and_run.ps1"

cd /d "%ROOT_DIR%"
if exist "%VENV_PY%" (
  "%VENV_PY%" src\pyside_interview_app.py
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%RUNNER%" -UiMode pyside
)
