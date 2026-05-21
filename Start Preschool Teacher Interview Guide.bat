@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "RUNNER=%ROOT_DIR%scripts\windows\run_interview.bat"

if not exist "%RUNNER%" (
  echo Could not find launcher script:
  echo %RUNNER%
  pause
  exit /b 1
)

call "%RUNNER%"
exit /b %errorlevel%
