@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\setup_director_staffing.ps1" -DirectorSchool "Palmdale"

exit /b
