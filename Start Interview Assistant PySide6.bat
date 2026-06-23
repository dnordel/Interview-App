@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\pyside_interview_app.py
) else (
  python src\pyside_interview_app.py
)
