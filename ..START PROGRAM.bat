@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\setup_and_run.ps1" -UiMode pyside

exit /b
