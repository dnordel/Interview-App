@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "RUNNER=%ROOT_DIR%setup_and_run.ps1"

if not exist "%RUNNER%" (
  echo Could not find launcher script:
  echo %RUNNER%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%RUNNER%"
exit /b %errorlevel%
