@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM Launch Pad Learning - Structured Interview Tool Installer
REM Installs: Python 3.11.x (3.11.9), creates venv, installs deps.
REM Requires: Internet + Admin (system-wide install)
REM ============================================================

REM ---- Admin check ----
net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo.
  echo ERROR: This installer must be run as Administrator.
  echo Right-click this .bat file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo 1) Checking for Python 3.11.x
echo ============================================================

set "PYEXE="

REM Prefer existing python on PATH if it's 3.11.x
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PYEXE (
    for /f "tokens=1,2" %%A in ('"%%P -c "import sys; print(sys.version_info.major,sys.version_info.minor)" 2^>nul"') do (
      if "%%A"=="3" if "%%B"=="11" set "PYEXE=%%P"
    )
  )
)

REM If not found, look in the default Program Files path (Python 3.11.9 64-bit)
if not defined PYEXE (
  if exist "C:\Program Files\Python311\python.exe" (
    set "PYEXE=C:\Program Files\Python311\python.exe"
  )
)

if defined PYEXE (
  echo Found Python: "%PYEXE%"
  "%PYEXE%" -c "import sys; print('Python version:', sys.version)"
) else (
  echo Python 3.11.x not found. Installing Python 3.11.9 (64-bit) system-wide...
  echo (Python 3.11.9 is the last 3.11 bugfix release with Windows installers.)
  echo.

  set "PY_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
  set "PY_EXE=%TEMP%\python-3.11.9-amd64.exe"

  echo Downloading Python installer...
  powershell -NoProfile -ExecutionPolicy Bypass ^
    -Command "Try { Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_EXE%' -UseBasicParsing } Catch { Write-Host $_; exit 1 }"
  if not "%errorlevel%"=="0" (
    echo.
    echo ERROR: Failed to download Python from python.org
    echo URL: %PY_URL%
    pause
    exit /b 1
  )

  echo Running Python installer (silent)...
  "%PY_EXE%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
  if not "%errorlevel%"=="0" (
    echo.
    echo ERROR: Python installer failed.
    pause
    exit /b 1
  )

  REM Refresh python detection
  if exist "C:\Program Files\Python311\python.exe" (
    set "PYEXE=C:\Program Files\Python311\python.exe"
  ) else (
    REM Fall back to PATH after install
    for /f "delims=" %%P in ('where python 2^>nul') do (
      if not defined PYEXE set "PYEXE=%%P"
    )
  )

  if not defined PYEXE (
    echo.
    echo ERROR: Python installed but could not be located. Try restarting your PC and re-run this installer.
    pause
    exit /b 1
  )

  echo Python installed: "%PYEXE%"
  "%PYEXE%" -c "import sys; print('Python version:', sys.version)"
)

echo.
echo ============================================================
echo 2) Creating virtual environment + installing dependencies
echo ============================================================

REM App source stays in repository; venv lives in LOCALAPPDATA to avoid synced-folder locks.
set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%..\..\"
cd /d "%APP_DIR%"

set "VENV_DIR=%LOCALAPPDATA%\LPL_InterviewTool\py311\.venv"
set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating venv at: "%VENV_DIR%"
  "%PYEXE%" -m venv --system-site-packages "%VENV_DIR%"
  if not "%errorlevel%"=="0" (
    echo.
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
  )
)

echo Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip

REM Write requirements.txt (overwrites each run)
set "REQ=%APP_DIR%requirements.txt"
(
  echo python-docx==1.2.0
  echo soundfile==0.13.1
  echo faster-whisper==1.2.1
) > "%REQ%"

echo Installing packages...
"%PIP_EXE%" install -r "%REQ%"
if not "%errorlevel%"=="0" (
  echo.
  echo ERROR: Package install failed.
  echo If this machine is behind a proxy/firewall, you may need IT to allow PyPI.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo 3) OPTIONAL (Recommended): Install VB-Audio Virtual Cable
echo ============================================================

echo This tool can record the candidate's audio from Zoom/Teams more reliably
echo if you install VB-CABLE and set Zoom output to it.
echo Official site: https://vb-audio.com/Cable/
echo.
choice /c YN /m "Open the VB-CABLE download page now"
if "%errorlevel%"=="1" (
  start "" "https://vb-audio.com/Cable/"
)

echo.
echo ---------------- VB-CABLE setup (Zoom) ----------------
echo 1) Install VB-CABLE (run its Setup as Admin, then reboot).
echo 2) In Zoom: Settings ^> Audio
echo    - Speaker (Output): "CABLE Input (VB-Audio Virtual Cable)"
echo    - Microphone (Input): your normal microphone device
echo 3) In Windows Sound settings:
echo    - Recording tab: ensure "CABLE Output" exists
echo 4) In the Interview Tool:
echo    - Select the system/candidate device as "CABLE Output"
echo -------------------------------------------------------
echo.

echo ============================================================
echo DONE
echo ============================================================
echo Next steps:
echo - Keep this folder intact (it contains app source and requirements.txt).
echo - Venv location:
echo     "%VENV_DIR%"
echo - Run the app using the venv Python:
echo     "%VENV_PY%" "src\Initial Teacher Interview Guide.pyw"
echo.
pause
exit /b 0
