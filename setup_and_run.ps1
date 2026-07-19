#requires -version 5.1
param(
  [switch]$DebugMode,
  [switch]$DirectorStaffingMode,
  [string]$DirectorSchool = "",
  [ValidateSet("pyside")]
  [string]$UiMode = ""
)

$ErrorActionPreference = "Stop"

# ===============================================
# Interview Tool Setup (Per-user, Dropbox-safe)
# - Cache-discovered app paths (Python) to avoid re-scanning every run
# - Cache + validate VB-CABLE detection; skip prompt entirely if detected
# - Per-user Python 3.11.x install (no elevation) if missing
# - Venv stored in LOCALAPPDATA (avoids Dropbox/OneDrive locks)
# - Optional VB-CABLE prompt with "Don't ask again" only when NOT detected
# - Upgrades pip tooling before installing requirements
# - Better logging + mutex to prevent double-runs
# ===============================================

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir
$DefaultUiMode = "pyside"
$DefaultInterviewAppFile = "pyside_interview_app.py"
$PySideInterviewAppFile = "pyside_interview_app.py"

# -------------------------
# Logging
# -------------------------
$LogDir = Join-Path $AppDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir "install_run_log.txt"
"===============================================" | Out-File -FilePath $Log -Encoding UTF8
"Started: $(Get-Date)" | Out-File -FilePath $Log -Append -Encoding UTF8
"APP_DIR=$AppDir" | Out-File -FilePath $Log -Append -Encoding UTF8
"User=$env:USERNAME" | Out-File -FilePath $Log -Append -Encoding UTF8
"Computer=$env:COMPUTERNAME" | Out-File -FilePath $Log -Append -Encoding UTF8
"PSVersion=$($PSVersionTable.PSVersion)" | Out-File -FilePath $Log -Append -Encoding UTF8
"DebugMode=$true" | Out-File -FilePath $Log -Append -Encoding UTF8
"===============================================" | Out-File -FilePath $Log -Append -Encoding UTF8

function Write-Log([string]$msg) {
  $msg | Out-File -FilePath $Log -Append -Encoding UTF8
}

function Install-StaffingDesktopShortcut {
  try {
    $launcherPath = Join-Path $AppDir "..START PROGRAM.vbs"
    $iconPath = Join-Path $AppDir "assets\staffing_app.ico"
    if (-not (Test-Path $launcherPath) -or -not (Test-Path $iconPath)) {
      Write-Log "Skipping desktop shortcut; launcher or icon is missing."
      return
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Staffing App.lnk"
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcherPath
    $shortcut.WorkingDirectory = $AppDir
    $shortcut.Description = "Launch Staffing App"
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Save()
    Write-Log "Installed desktop shortcut: $shortcutPath"
  }
  catch {
    Write-Log "Desktop shortcut install skipped: $($_.Exception.Message)"
  }
}

# Force modern TLS for python.org downloads on older boxes
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# Prevent double-run races (venv delete/install collisions)
$mutex = New-Object Threading.Mutex($false, "Global\LPL_InterviewTool_Setup")
if (-not $mutex.WaitOne(0)) {
  throw "Another Interview Tool setup is already running."
}

# -------------------------
# Config (per-user) for caches + "Don't ask again"
# -------------------------
function Get-ConfigBaseDir {
  $base = Join-Path $env:LOCALAPPDATA "LPL_InterviewTool"
  if (-not (Test-Path $base)) { New-Item -ItemType Directory -Path $base | Out-Null }
  return $base
}

function Get-ConfigPath {
  return (Join-Path (Get-ConfigBaseDir) "setup_config.json")
}

function Load-Config {
  $path = Get-ConfigPath
  if (Test-Path $path) {
    try {
      return (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
      Write-Log "Config read failed; using defaults. Error: $($_.Exception.Message)"
    }
  }

  # Defaults
  return [pscustomobject]@{
    VBCable = [pscustomobject]@{
      DontAskAgain = $false
      UserSaysInstalled = $false
      AudioRoutingInstructionsShown = $false
    }
    Tools = [pscustomobject]@{
      Python311Exe = $null
      VBCable = [pscustomobject]@{
        Detected = $null
        Evidence = @()
        LastCheckedUtc = $null
      }
      RequirementsFingerprint = $null
      GpuRequirementsFingerprint = $null
      OpenVinoRequirementsFingerprint = $null
    }
    App = [pscustomobject]@{
      InterviewAppPath = $null
      PreferredUiMode = $DefaultUiMode
      PreferredInterviewAppFile = $DefaultInterviewAppFile
    }
  }
}

function Save-Config($cfg) {
  $path = Get-ConfigPath
  try {
    $cfg | ConvertTo-Json -Depth 10 | Out-File -FilePath $path -Encoding UTF8
    Write-Log "Saved config: $path"
  } catch {
    Write-Log "Failed to save config. Error: $($_.Exception.Message)"
  }
}

function Ensure-ConfigShape($cfg) {
  if (-not $cfg.VBCable) {
    $cfg | Add-Member -NotePropertyName VBCable -NotePropertyValue ([pscustomobject]@{
      DontAskAgain = $false
      UserSaysInstalled = $false
      AudioRoutingInstructionsShown = $false
    }) -Force
  } else {
    if ($cfg.VBCable.PSObject.Properties.Name -notcontains "DontAskAgain") {
      $cfg.VBCable | Add-Member -NotePropertyName DontAskAgain -NotePropertyValue $false -Force
    }
    if ($cfg.VBCable.PSObject.Properties.Name -notcontains "UserSaysInstalled") {
      $cfg.VBCable | Add-Member -NotePropertyName UserSaysInstalled -NotePropertyValue $false -Force
    }
    if ($cfg.VBCable.PSObject.Properties.Name -notcontains "AudioRoutingInstructionsShown") {
      $cfg.VBCable | Add-Member -NotePropertyName AudioRoutingInstructionsShown -NotePropertyValue $false -Force
    }
  }

  if (-not $cfg.Tools) {
    $cfg | Add-Member -NotePropertyName Tools -NotePropertyValue ([pscustomobject]@{
      Python311Exe = $null
      VBCable = [pscustomobject]@{ Detected = $null; Evidence = @(); LastCheckedUtc = $null }
      RequirementsFingerprint = $null
    }) -Force
  }

  if ($cfg.Tools.PSObject.Properties.Name -notcontains "Python311Exe") {
    $cfg.Tools | Add-Member -NotePropertyName Python311Exe -NotePropertyValue $null -Force
  }
  if ($cfg.Tools.PSObject.Properties.Name -notcontains "RequirementsFingerprint") {
    $cfg.Tools | Add-Member -NotePropertyName RequirementsFingerprint -NotePropertyValue $null -Force
  }
  if ($cfg.Tools.PSObject.Properties.Name -notcontains "GpuRequirementsFingerprint") {
    $cfg.Tools | Add-Member -NotePropertyName GpuRequirementsFingerprint -NotePropertyValue $null -Force
  }
  if ($cfg.Tools.PSObject.Properties.Name -notcontains "OpenVinoRequirementsFingerprint") {
    $cfg.Tools | Add-Member -NotePropertyName OpenVinoRequirementsFingerprint -NotePropertyValue $null -Force
  }

  if (-not $cfg.Tools.VBCable) {
    $cfg.Tools | Add-Member -NotePropertyName VBCable -NotePropertyValue ([pscustomobject]@{
      Detected = $null
      Evidence = @()
      LastCheckedUtc = $null
    }) -Force
  }

  if ($cfg.Tools.VBCable.PSObject.Properties.Name -notcontains "Detected") {
    $cfg.Tools.VBCable | Add-Member -NotePropertyName Detected -NotePropertyValue $null -Force
  }
  if ($cfg.Tools.VBCable.PSObject.Properties.Name -notcontains "Evidence") {
    $cfg.Tools.VBCable | Add-Member -NotePropertyName Evidence -NotePropertyValue @() -Force
  }
  if ($cfg.Tools.VBCable.PSObject.Properties.Name -notcontains "LastCheckedUtc") {
    $cfg.Tools.VBCable | Add-Member -NotePropertyName LastCheckedUtc -NotePropertyValue $null -Force
  }

  if (-not $cfg.App) {
    $cfg | Add-Member -NotePropertyName App -NotePropertyValue ([pscustomobject]@{
      InterviewAppPath = $null
      PreferredUiMode = $DefaultUiMode
      PreferredInterviewAppFile = $DefaultInterviewAppFile
    }) -Force
  }
  elseif ($cfg.App.PSObject.Properties.Name -notcontains "InterviewAppPath") {
    $cfg.App | Add-Member -NotePropertyName InterviewAppPath -NotePropertyValue $null -Force
  }
  if ($cfg.App.PSObject.Properties.Name -notcontains "PreferredUiMode") {
    $cfg.App | Add-Member -NotePropertyName PreferredUiMode -NotePropertyValue $DefaultUiMode -Force
  }
  if ($DirectorStaffingMode) {
    $Cfg.App.PreferredUiMode = "pyside"
  }
  elseif ($UiMode) {
    $cfg.App.PreferredUiMode = $UiMode
  }
  elseif ($env:INTERVIEW_APP_UI_MODE -eq "pyside") {
    $cfg.App.PreferredUiMode = $env:INTERVIEW_APP_UI_MODE
  }
  elseif ($cfg.App.PreferredUiMode -ne "pyside") {
    Write-Log "Invalid preferred UI mode '$($cfg.App.PreferredUiMode)'; using '$DefaultUiMode'."
    $cfg.App.PreferredUiMode = $DefaultUiMode
  }

  $selectedAppFile = Resolve-PreferredInterviewAppFile -Cfg $cfg
  if ($cfg.App.PSObject.Properties.Name -notcontains "PreferredInterviewAppFile") {
    $cfg.App | Add-Member -NotePropertyName PreferredInterviewAppFile -NotePropertyValue $selectedAppFile -Force
  }
  if ($cfg.App.PreferredInterviewAppFile -ne $selectedAppFile) {
    Write-Log "Preferred app changed from '$($cfg.App.PreferredInterviewAppFile)' to '$selectedAppFile'; clearing cached app path."
    $cfg.App.PreferredInterviewAppFile = $selectedAppFile
    $cfg.App.InterviewAppPath = $null
  }

  return $cfg
}

function Resolve-PreferredInterviewAppFile {
  param([Parameter(Mandatory=$true)]$Cfg)

  return $PySideInterviewAppFile
}

# -------------------------
# Launch Python GUI safely
# -------------------------
function Start-PythonGuiApp {
  param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$ScriptPath,
    [Parameter(Mandatory=$true)][string]$WorkingDir,
    [string[]]$ScriptArgs = @(),
    [string]$LogFile = $null,
    [switch]$ShowConsole
  )

  if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
  }

  if (-not (Test-Path $ScriptPath)) {
    throw "Python script not found: $ScriptPath"
  }

  $ScriptPath = (Resolve-Path $ScriptPath).Path
  $WorkingDir = (Resolve-Path $WorkingDir).Path

  $pythonw = Join-Path (Split-Path $PythonExe -Parent) "pythonw.exe"
  $launcher = if (-not $ShowConsole -and (Test-Path $pythonw)) { $pythonw } else { $PythonExe }

  Write-Log "Python launcher: $launcher"
  Write-Log "Script: $ScriptPath"
  Write-Log "WorkingDir: $WorkingDir"

  function ConvertTo-ProcessArgumentString {
    param([string[]]$Arguments)

    if (-not $Arguments -or $Arguments.Count -eq 0) {
      return ""
    }

    $escaped = foreach ($arg in $Arguments) {
      if ($null -eq $arg) {
        '""'
      }
      elseif ($arg -match '[\s"]') {
        '"' + ($arg -replace '"', '\\"') + '"'
      }
      else {
        $arg
      }
    }

    return ($escaped -join ' ')
  }

  $argList = @($ScriptPath) + $ScriptArgs
  $argumentString = ConvertTo-ProcessArgumentString -Arguments $argList

  if ($ShowConsole) {
    $p = Start-Process `
      -FilePath $launcher `
      -ArgumentList $argumentString `
      -WorkingDirectory $WorkingDir `
      -PassThru
  }
  else {
    if (-not $LogFile) {
      $LogFile = Join-Path $WorkingDir "python_runtime_log.txt"
    }

    $stdoutFile = Join-Path $WorkingDir "python_stdout.txt"
    $stderrFile = Join-Path $WorkingDir "python_stderr.txt"

    $p = Start-Process `
      -FilePath $launcher `
      -ArgumentList $argumentString `
      -WorkingDirectory $WorkingDir `
      -RedirectStandardOutput $stdoutFile `
      -RedirectStandardError $stderrFile `
      -WindowStyle Hidden `
      -PassThru

    # Merge logs after short delay if process exits immediately
    Start-Sleep -Milliseconds 800

    if ($p.HasExited) {
      if (Test-Path $stdoutFile) {
        Get-Content $stdoutFile | Out-File -FilePath $LogFile -Append -Encoding UTF8
        Remove-Item $stdoutFile -ErrorAction SilentlyContinue
      }
      if (Test-Path $stderrFile) {
        Get-Content $stderrFile | Out-File -FilePath $LogFile -Append -Encoding UTF8
        Remove-Item $stderrFile -ErrorAction SilentlyContinue
      }

      throw "Python app exited immediately (exit code $($p.ExitCode)). See python_runtime_log.txt."
    }
  }

  Start-Sleep -Milliseconds 800

  if ($p.HasExited) {
    throw "Python app exited immediately (exit code $($p.ExitCode))."
  }

  return $p
}

# -------------------------
# Process runner (logs stdout/stderr)
# -------------------------
function Run-Proc {
  param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$false)][string[]]$Args = @(),
    [Parameter(Mandatory=$false)][string]$WorkingDir = $AppDir
  )

  $argLine = ($Args -join " ")
  Write-Log "RUN: $File $argLine"

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $File
  $psi.WorkingDirectory = $WorkingDir
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true

  if ($Args -and $Args.Count -gt 0) {
    $quoted = @()
    foreach ($a in $Args) {
      if ($a -match '\s') { $quoted += '"' + ($a -replace '"','""') + '"' } else { $quoted += $a }
    }
    $psi.Arguments = ($quoted -join " ")
  }

  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi

  [void]$p.Start()
  $stdoutTask = $p.StandardOutput.ReadToEndAsync()
  $stderrTask = $p.StandardError.ReadToEndAsync()

  $p.WaitForExit()

  $stdout = $stdoutTask.Result
  $stderr = $stderrTask.Result

  if ($stdout) { $stdout | Out-File -FilePath $Log -Append -Encoding UTF8 }
  if ($stderr) { $stderr | Out-File -FilePath $Log -Append -Encoding UTF8 }

  return $p.ExitCode
}

# -------------------------
# FFmpeg system install
# -------------------------
function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-ScriptAsAdminIfNeeded {
  if (Test-IsAdministrator) {
    return
  }

  Write-Log "Admin rights required to install FFmpeg to system PATH. Relaunching elevated..."

  $scriptPath = $PSCommandPath
  if (-not $scriptPath) {
    $scriptPath = $MyInvocation.MyCommand.Path
  }

  $argsList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$scriptPath`""
  )

  if ($DebugMode) {
    $argsList += "-DebugMode"
  }

  Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList ($argsList -join " ") `
    -Verb RunAs

  exit
}

function Test-FFmpegAvailable {
  try {
    $cmd = Get-Command "ffmpeg.exe" -ErrorAction Stop
    if ($cmd -and (Test-Path $cmd.Source)) {
      Write-Log "FFmpeg found on PATH: $($cmd.Source)"
      return $true
    }
  } catch {}

  return $false
}

function Add-SystemPathIfMissing {
  param(
    [Parameter(Mandatory=$true)][string]$PathToAdd
  )

  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $parts = @()

  if ($machinePath) {
    $parts = $machinePath -split ";" | Where-Object { $_ -and $_.Trim() }
  }

  $alreadyThere = $false
  foreach ($p in $parts) {
    if ($p.TrimEnd("\") -ieq $PathToAdd.TrimEnd("\")) {
      $alreadyThere = $true
      break
    }
  }

  if (-not $alreadyThere) {
    $newPath = ($parts + $PathToAdd) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "Machine")
    Write-Log "Added FFmpeg to system PATH: $PathToAdd"
  }
  else {
    Write-Log "FFmpeg bin already present in system PATH: $PathToAdd"
  }

  # Make FFmpeg available to this running setup script immediately.
  if (($env:PATH -split ";") -notcontains $PathToAdd) {
    $env:PATH = "$PathToAdd;$env:PATH"
  }

  # Notify Windows that environment variables changed.
  try {
    $signature = @"
using System;
using System.Runtime.InteropServices;

public class NativeMethods {
  [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)]
  public static extern IntPtr SendMessageTimeout(
    IntPtr hWnd,
    UInt32 Msg,
    UIntPtr wParam,
    string lParam,
    UInt32 fuFlags,
    UInt32 uTimeout,
    out UIntPtr lpdwResult
  );
}
"@

    Add-Type $signature -ErrorAction SilentlyContinue

    $HWND_BROADCAST = [IntPtr]0xffff
    $WM_SETTINGCHANGE = 0x001A
    $SMTO_ABORTIFHUNG = 0x0002
    $result = [UIntPtr]::Zero

    [void][NativeMethods]::SendMessageTimeout(
      $HWND_BROADCAST,
      $WM_SETTINGCHANGE,
      [UIntPtr]::Zero,
      "Environment",
      $SMTO_ABORTIFHUNG,
      5000,
      [ref]$result
    )
  } catch {
    Write-Log "Could not broadcast PATH update. Error: $($_.Exception.Message)"
  }
}

function Ensure-FFmpeg {
  if (Test-FFmpegAvailable) {
    return
  }

  Restart-ScriptAsAdminIfNeeded

  $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
  $installRoot = "C:\ProgramData\ffmpeg"
  $binDir = Join-Path $installRoot "bin"

  if ((Test-Path (Join-Path $binDir "ffmpeg.exe")) -and
      (Test-Path (Join-Path $binDir "ffprobe.exe"))) {
    Add-SystemPathIfMissing -PathToAdd $binDir
    if (-not (Test-FFmpegAvailable)) {
      throw "FFmpeg appears installed, but ffmpeg.exe still could not be found on PATH."
    }
    return
  }

  $tempZip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
  $tempExtract = Join-Path $env:TEMP ("ffmpeg_extract_" + [guid]::NewGuid().ToString())

  try {
    Write-Log "Downloading FFmpeg from: $ffmpegUrl"
    Invoke-WebRequest -Uri $ffmpegUrl -OutFile $tempZip -UseBasicParsing

    if (Test-Path $tempExtract) {
      Remove-Item -Recurse -Force $tempExtract
    }

    New-Item -ItemType Directory -Path $tempExtract | Out-Null

    Write-Log "Extracting FFmpeg..."
    Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force

    $extractedBin = Get-ChildItem -Path $tempExtract -Recurse -Directory |
      Where-Object { $_.Name -ieq "bin" -and (Test-Path (Join-Path $_.FullName "ffmpeg.exe")) } |
      Select-Object -First 1

    if (-not $extractedBin) {
      throw "Could not find ffmpeg.exe in the downloaded FFmpeg archive."
    }

    if (Test-Path $installRoot) {
      Remove-Item -Recurse -Force $installRoot
    }

    New-Item -ItemType Directory -Path $installRoot | Out-Null

    Write-Log "Installing FFmpeg to: $installRoot"
    Copy-Item -Path (Join-Path $extractedBin.Parent.FullName "*") -Destination $installRoot -Recurse -Force

    if (-not (Test-Path (Join-Path $binDir "ffmpeg.exe"))) {
      throw "FFmpeg install failed. ffmpeg.exe was not found at $binDir"
    }

    Add-SystemPathIfMissing -PathToAdd $binDir

    if (-not (Test-FFmpegAvailable)) {
      throw "FFmpeg was installed, but ffmpeg.exe could not be found on PATH."
    }

    Write-Log "FFmpeg installed successfully."
  }
  finally {
    Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# -------------------------
# Cached tool path helpers
# -------------------------
function Get-CachedPython311($cfg) {
  try {
    if ($cfg.Tools -and $cfg.Tools.Python311Exe) { return [string]$cfg.Tools.Python311Exe }
  } catch {}
  return $null
}

function Set-CachedPython311($cfg, [string]$path) {
  $cfg.Tools.Python311Exe = $path
  Save-Config $cfg
}

function Test-Python311Exe([string]$pythonExe) {
  if (-not $pythonExe) { return $false }
  if (-not (Test-Path $pythonExe)) { return $false }

  try {
    $ok = & $pythonExe -c "import sys; print(sys.version_info[:2]==(3,11))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ok -match "True") { return $true }
  } catch {}
  return $false
}

# -------------------------
# Python discovery + per-user install (no elevation)
# Accepts any 3.11.x
# -------------------------
function Get-RegistryAppPathsPythonCandidates {
  $cands = New-Object System.Collections.Generic.List[string]

  $keys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.11.exe",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\python.exe",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\python3.exe",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\python3.11.exe",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.11.exe"
  )

  foreach ($k in $keys) {
    try {
      if (Test-Path $k) {
        $p = (Get-ItemProperty $k -ErrorAction Stop).'(default)'
        if ($p) { $cands.Add([string]$p) }
      }
    } catch {}
  }

  return ,($cands.ToArray() | Select-Object -Unique)
}

function Find-Python311 {
  param([Parameter(Mandatory=$true)]$Cfg)

  # 0) Cached path first
  $cached = Get-CachedPython311 $Cfg
  if (Test-Python311Exe $cached) {
    Write-Log "Python 3.11 found from cache: $cached"
    return $cached
  } elseif ($cached) {
    Write-Log "Cached Python path invalid/missing: $cached (will re-scan)"
  }

  # 1) Prefer py launcher (if present)
  try {
    $pyLauncher = (Get-Command py -ErrorAction Stop).Source
    $exe = & $pyLauncher -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe) -and (Test-Python311Exe $exe)) {
      return $exe
    }
  } catch {}

  # 2) Registry App Paths (often present for all-users installs)
  foreach ($p in (Get-RegistryAppPathsPythonCandidates)) {
    if (Test-Python311Exe $p) { return $p }
  }

  # 3) Common install locations (user + all-users)
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311-32\python.exe"),
    "C:\Program Files\Python311\python.exe",
    "C:\Program Files\Python311\pythonw.exe",
    "C:\Program Files (x86)\Python311\python.exe"
  ) | Select-Object -Unique

  foreach ($p in $candidates) {
    if (Test-Python311Exe $p) { return $p }
  }

  # 4) PATH python, but only accept if it is 3.11
  try {
    $python = (Get-Command python -ErrorAction Stop).Source
    if (Test-Python311Exe $python) { return $python }
  } catch {}

  # 5) where.exe python (catch multiple shims)
  try {
    $where = & "$env:WINDIR\System32\where.exe" python 2>$null
    if ($where) {
      foreach ($line in ($where -split "`r?`n")) {
        $p = $line.Trim()
        if ($p -and (Test-Python311Exe $p)) { return $p }
      }
    }
  } catch {}

  return $null
}

function Ensure-Python311 {
  param([Parameter(Mandatory=$true)]$Cfg)

  $py = Find-Python311 -Cfg $Cfg
  if ($py) {
    Write-Log "Python 3.11 found: $py"
    Set-CachedPython311 $Cfg $py
    return $py
  }

  # Per-user install (no admin)
  $url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
  $installer = Join-Path $env:TEMP "python-3.11.9-amd64.exe"

  Write-Log "Downloading Python: $url"
  Invoke-WebRequest -Uri $url -OutFile $installer

  # Explicit per-user target directory
  $targetDir = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311"

  Write-Log "Installing Python per-user to: $targetDir"
  $ec = Run-Proc -File $installer -Args @(
    "/quiet",
    "InstallAllUsers=0",
    "PrependPath=0",
    "Include_test=0",
    "Include_launcher=0",
    "TargetDir=$targetDir"
  )

  if ($ec -ne 0) { throw "Python installer failed with exit code $ec" }

  $py = Find-Python311 -Cfg $Cfg
  if (-not $py) {
    throw "Python installed but could not be located. Re-run setup, or reboot if needed."
  }

  Write-Log "Python 3.11 installed: $py"
  Set-CachedPython311 $Cfg $py
  return $py
}

# -------------------------
# VB-CABLE detection (driver present)
# Cache-first, but always validate cached result quickly each run.
# -------------------------
function Find-VBCableDriverEvidence {
  $evidence = New-Object System.Collections.Generic.List[string]

  # VB-CABLE driver file names observed in the wild
  $driverCandidates = @(
    "C:\Windows\System32\drivers\vbaudio_cable64_win7.sys",
    "C:\Windows\System32\drivers\vbaudio_cable64.sys",
    "C:\Windows\System32\drivers\vbaudio_cable_win7.sys",
    "C:\Windows\System32\drivers\vbaudio_cable.sys"
  )

  foreach ($f in $driverCandidates) {
    if (Test-Path $f) { $evidence.Add("DriverFile:$f") }
  }

  try {
    $devices = @(
      Get-CimInstance Win32_SoundDevice -ErrorAction Stop | Where-Object {
        $_.Name -like '*CABLE Input*' -or
        $_.Name -like '*CABLE Output*' -or
        $_.Name -like '*VB-Audio*'
      }
    )
    foreach ($device in $devices) {
      $name = [string]$device.Name
      if ($name.Trim()) { $evidence.Add("AudioDevice:$name") }
    }
  } catch {
    Write-Log "VB-CABLE audio device check failed: $($_.Exception.Message)"
  }

  return ,$evidence.ToArray()
}

function Set-VBCableCache($cfg, [Nullable[bool]]$detected, [string[]]$evidence) {
  $cfg.Tools.VBCable.Detected = $detected
  $cfg.Tools.VBCable.Evidence = @($evidence)
  $cfg.Tools.VBCable.LastCheckedUtc = ([DateTime]::UtcNow.ToString("o"))
  Save-Config $cfg
}

function Ensure-VBCableStatus {
  param([Parameter(Mandatory=$true)]$Cfg)

  # Quick validation: check driver file presence every run
  $ev = Find-VBCableDriverEvidence
  $detected = $false
  if ($ev -and $ev.Count -gt 0) { $detected = $true }

  Write-Log "VB-CABLE driver check: Detected=$detected; Evidence=$($ev -join '; ')"
  Set-VBCableCache $Cfg $detected $ev

  # If detected, treat as installed and skip prompt entirely
  if ($detected) {
    $Cfg.VBCable.UserSaysInstalled = $true
  }

  return $Cfg
}

# -------------------------
# Venv + deps (stored in LOCALAPPDATA; versioned)
# -------------------------

function Test-NvidiaGPU {
  try {
    $nvidia = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($nvidia) {
      Write-Log "NVIDIA GPU detected via nvidia-smi."
      return $true
    }

    $gpu = Get-WmiObject Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($gpu) {
      Write-Log "NVIDIA GPU detected via WMI: $($gpu.Name)"
      return $true
    }
  } catch {
    Write-Log "GPU detection failed: $($_.Exception.Message)"
  }

  Write-Log "No NVIDIA GPU detected."
  return $false
}

function Get-GpuVendorProfile {
  $profile = [PSCustomObject]@{
    Nvidia = $false
    Amd = $false
    Intel = $false
    Names = @()
  }

  try {
    if (Test-NvidiaGPU) {
      $profile.Nvidia = $true
    }

    $controllers = @(Get-WmiObject Win32_VideoController -ErrorAction Stop)
    foreach ($controller in $controllers) {
      $name = [string]$controller.Name
      if (-not $name) { continue }

      $profile.Names += $name
      if ($name -match "NVIDIA") { $profile.Nvidia = $true }
      if ($name -match "AMD|Radeon|Advanced Micro Devices") { $profile.Amd = $true }
      if ($name -match "Intel|Arc|Iris|UHD Graphics") { $profile.Intel = $true }
    }
  } catch {
    Write-Log "GPU vendor profile detection failed: $($_.Exception.Message)"
  }

  if ($profile.Names.Count -gt 0) {
    Write-Log "Detected GPU profile: Nvidia=$($profile.Nvidia); AMD=$($profile.Amd); Intel=$($profile.Intel); Names=$($profile.Names -join '; ')"
  } else {
    Write-Log "Detected GPU profile: no GPU controllers reported."
  }

  return $profile
}

function Remove-NvidiaGpuPackagesWhenUnsupported {
  param([Parameter(Mandatory=$true)][string]$VenvPy)

  if (Test-NvidiaGPU) {
    Write-Log "NVIDIA GPU present; keeping NVIDIA Python GPU packages if installed."
    return
  }

  Write-Log "No NVIDIA GPU detected. Removing NVIDIA-only Python GPU packages if installed."
  $ec = Run-Proc -File $VenvPy -Args @("-m","pip","uninstall","-y","nvidia-cublas-cu12","nvidia-cudnn-cu12")
  if ($ec -ne 0) {
    Write-Log "NVIDIA GPU package cleanup completed with non-zero exit code $ec; continuing in CPU/non-CUDA mode."
  }
}

function Find-WhisperCppCli {
  try {
    $cmd = Get-Command "whisper-cli.exe" -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) {
      return $cmd.Source
    }
  } catch {}

  $candidates = @(
    (Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\whisper.cpp\whisper-cli.exe"),
    (Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\whisper.cpp\bin\whisper-cli.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\whisper.cpp\whisper-cli.exe")
  )

  foreach ($path in $candidates) {
    if (Test-Path $path) {
      return $path
    }
  }

  return $null
}

function Find-WhisperCppModel {
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\whisper.cpp\models\ggml-small.bin"),
    (Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\whisper.cpp\models\ggml-base.bin"),
    (Join-Path $AppDir "models\ggml-small.bin"),
    (Join-Path $AppDir "models\ggml-base.bin")
  )

  foreach ($path in $candidates) {
    if (Test-Path $path) {
      return $path
    }
  }

  return $null
}

function Set-GpuVendorEnvironment {
  param([Parameter(Mandatory=$true)]$Profile)

  $vendor = "cpu"
  if ($Profile.Nvidia) {
    $vendor = "nvidia"
  } elseif ($Profile.Amd) {
    $vendor = "amd"
  } elseif ($Profile.Intel) {
    $vendor = "intel"
  }

  $env:INTERVIEW_GPU_VENDOR = $vendor
  if ($vendor -eq "amd") {
    $whisperCppExe = Find-WhisperCppCli
    $whisperCppModel = Find-WhisperCppModel
    if ($whisperCppExe -and $whisperCppModel) {
      $env:INTERVIEW_WHISPER_BACKEND = "whisper_cpp"
      $env:INTERVIEW_WHISPERCPP_EXE = $whisperCppExe
      $env:INTERVIEW_WHISPERCPP_MODEL = $whisperCppModel
      Remove-Item Env:\INTERVIEW_OPENVINO_WHISPER_MODEL -ErrorAction SilentlyContinue
      Write-Log "AMD GPU detected. Using configured whisper.cpp backend: $whisperCppExe"
    } else {
      $env:INTERVIEW_WHISPER_BACKEND = "openvino_genai"
      $env:INTERVIEW_OPENVINO_WHISPER_MODEL = "OpenVINO/whisper-small-int8-ov"
      Remove-Item Env:\INTERVIEW_WHISPERCPP_EXE -ErrorAction SilentlyContinue
      Remove-Item Env:\INTERVIEW_WHISPERCPP_MODEL -ErrorAction SilentlyContinue
    }
  } elseif ($vendor -eq "intel") {
    $env:INTERVIEW_WHISPER_BACKEND = "openvino_genai"
    $env:INTERVIEW_OPENVINO_WHISPER_MODEL = "OpenVINO/whisper-small-int8-ov"
    Remove-Item Env:\INTERVIEW_WHISPERCPP_EXE -ErrorAction SilentlyContinue
    Remove-Item Env:\INTERVIEW_WHISPERCPP_MODEL -ErrorAction SilentlyContinue
  } elseif ($vendor -eq "nvidia") {
    $env:INTERVIEW_WHISPER_BACKEND = "openvino_genai"
    $env:INTERVIEW_OPENVINO_WHISPER_MODEL = "OpenVINO/whisper-small-int8-ov"
    Remove-Item Env:\INTERVIEW_WHISPERCPP_EXE -ErrorAction SilentlyContinue
    Remove-Item Env:\INTERVIEW_WHISPERCPP_MODEL -ErrorAction SilentlyContinue
  } else {
    $env:INTERVIEW_WHISPER_BACKEND = "openvino_genai"
    $env:INTERVIEW_OPENVINO_WHISPER_MODEL = "OpenVINO/whisper-small-int8-ov"
    Remove-Item Env:\INTERVIEW_WHISPERCPP_EXE -ErrorAction SilentlyContinue
    Remove-Item Env:\INTERVIEW_WHISPERCPP_MODEL -ErrorAction SilentlyContinue
  }
  Write-Log "App GPU vendor environment set: INTERVIEW_GPU_VENDOR=$vendor"
}

# -------------------------
function Test-VenvUsesSystemSitePackages([string]$VenvDir) {
  if (-not $VenvDir) {
    return $false
  }

  $cfgPath = Join-Path $VenvDir "pyvenv.cfg"
  if (-not (Test-Path $cfgPath)) {
    return $false
  }

  try {
    $cfgText = Get-Content $cfgPath -Raw -Encoding UTF8
    return ($cfgText -match "(?im)^\s*include-system-site-packages\s*=\s*true\s*$")
  } catch {
    Write-Log "Could not read venv config: $cfgPath. Error: $($_.Exception.Message)"
    return $false
  }
}

function Get-RequirementsFingerprint {
  param([Parameter(Mandatory=$true)][string]$RequirementsPath)

  if (-not (Test-Path $RequirementsPath)) {
    throw "requirements.txt not found in app folder: $RequirementsPath"
  }

  $stream = [System.IO.File]::OpenRead($RequirementsPath)
  try {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hashBytes = $sha256.ComputeHash($stream)
      return ([System.BitConverter]::ToString($hashBytes)).Replace("-", "")
    }
    finally {
      $sha256.Dispose()
    }
  }
  finally {
    $stream.Dispose()
  }
}

function Ensure-VenvAndDeps([string]$PyExe) {
  $VenvBase = Join-Path (Get-ConfigBaseDir) "py311"
  $VenvDir  = Join-Path $VenvBase ".venv"
  $VenvPy   = Join-Path $VenvDir "Scripts\python.exe"

  if (-not (Test-Path $VenvBase)) { New-Item -ItemType Directory -Path $VenvBase | Out-Null }

  $needRecreate = $false
  if (Test-Path $VenvPy) {
    try {
      $out = & $VenvPy -c "import sys, pip; print('pip ok')" 2>$null
      if ($LASTEXITCODE -ne 0 -or $out -notmatch "pip ok") { $needRecreate = $true }
    } catch { $needRecreate = $true }

    if (-not $needRecreate -and -not (Test-VenvUsesSystemSitePackages -VenvDir $VenvDir)) {
      Write-Log "Existing venv is isolated; recreating to reuse system site packages."
      $needRecreate = $true
    }
  } else {
    $needRecreate = $true
  }

  if ($needRecreate) {
    Write-Log "Creating/recreating venv at $VenvDir with system site packages enabled"
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
    $ec = Run-Proc -File $PyExe -Args @("-m","venv","--system-site-packages",$VenvDir)
    if ($ec -ne 0) { throw "venv creation failed (exit code $ec)" }
  }

  $req = Join-Path $AppDir "requirements.txt"
  if (-not (Test-Path $req)) {
    throw "requirements.txt not found in app folder: $req"
  }

  $requirementsFingerprint = Get-RequirementsFingerprint -RequirementsPath $req
  $cfg = Ensure-ConfigShape (Load-Config)
  $baseDepsNeedInstall = $needRecreate -or ($cfg.Tools.RequirementsFingerprint -ne $requirementsFingerprint)
  if ($baseDepsNeedInstall) {
    Write-Log "Requirements fingerprint changed; installing dependencies. Old=$($cfg.Tools.RequirementsFingerprint); New=$requirementsFingerprint"
  } else {
    Write-Log "Requirements fingerprint unchanged: $requirementsFingerprint"
  }

  if ($baseDepsNeedInstall) {
    Write-Log "Upgrading pip/setuptools/wheel..."
    $ec = Run-Proc -File $VenvPy -Args @("-m","pip","install","--upgrade","pip","setuptools","wheel")
    if ($ec -ne 0) { throw "pip bootstrap upgrade failed (exit code $ec)" }

    Write-Log "Installing base dependencies from $req..."
    $ec = Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$req)
    if ($ec -ne 0) { throw "pip install failed (exit code $ec)" }
    $cfg.Tools.RequirementsFingerprint = $requirementsFingerprint
    Save-Config $cfg
  } else {
    Write-Log "Requirements fingerprint unchanged and venv healthy; skipping base dependency install."
  }

  $gpuProfile = Get-GpuVendorProfile
  Set-GpuVendorEnvironment -Profile $gpuProfile

  # -------------------------
  # Optional GPU dependencies
  # -------------------------
  $gpuReq = Join-Path $AppDir "requirements-gpu.txt"

  if ((Test-Path $gpuReq) -and $gpuProfile.Nvidia) {
    $gpuRequirementsFingerprint = Get-RequirementsFingerprint -RequirementsPath $gpuReq
    $gpuDepsNeedInstall = $needRecreate -or ($cfg.Tools.GpuRequirementsFingerprint -ne $gpuRequirementsFingerprint)
    if ($gpuDepsNeedInstall) {
      Write-Log "NVIDIA GPU detected. Attempting GPU dependency install..."

      $gpuEc = Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$gpuReq)

      if ($gpuEc -ne 0) {
        Write-Log "GPU dependency install failed. Falling back to CPU mode."
      }
      else {
        Write-Log "GPU dependencies installed successfully."
        $cfg.Tools.GpuRequirementsFingerprint = $gpuRequirementsFingerprint
        Save-Config $cfg
      }
    } else {
      Write-Log "GPU requirements fingerprint unchanged and venv healthy; skipping GPU dependency install."
    }
  } else {
    Write-Log "Skipping GPU dependency install because no NVIDIA GPU was detected."
    Remove-NvidiaGpuPackagesWhenUnsupported -VenvPy $VenvPy
    if ($gpuProfile.Amd) {
      Write-Log "AMD GPU detected. Whisper transcription will use OpenVINO GenAI unless an external Vulkan whisper.cpp backend is configured."
    }
    if ($gpuProfile.Intel) {
      Write-Log "Intel GPU detected. OpenVINO GenAI will be used for Whisper transcription when dependencies install successfully."
    }
  }

  $openVinoReq = Join-Path $AppDir "requirements-openvino.txt"
  if (Test-Path $openVinoReq) {
    $openVinoRequirementsFingerprint = Get-RequirementsFingerprint -RequirementsPath $openVinoReq
    $openVinoDepsNeedInstall = $needRecreate -or ($cfg.Tools.OpenVinoRequirementsFingerprint -ne $openVinoRequirementsFingerprint)
    if ($openVinoDepsNeedInstall) {
      Write-Log "Installing OpenVINO GenAI transcription dependencies..."
      $openVinoEc = Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$openVinoReq)
      if ($openVinoEc -ne 0) {
        Write-Log "OpenVINO dependency install failed. Falling back to faster-whisper CPU mode."
        $env:INTERVIEW_WHISPER_BACKEND = "faster_whisper"
        Remove-Item Env:\INTERVIEW_OPENVINO_WHISPER_MODEL -ErrorAction SilentlyContinue
      } else {
        $cfg.Tools.OpenVinoRequirementsFingerprint = $openVinoRequirementsFingerprint
        Save-Config $cfg
      }
    } else {
      Write-Log "OpenVINO requirements fingerprint unchanged and venv healthy; skipping OpenVINO dependency install."
    }
  }

  Ensure-RequiredDepsInstalled -VenvPy $VenvPy

  return $VenvPy
}

function Ensure-RequiredDepsInstalled {
  param([Parameter(Mandatory=$true)][string]$VenvPy)

  $requiredDeps = @(
    @{ Package = "python-docx"; Module = "docx" },
    @{ Package = "PySide6"; Module = "PySide6" },
    @{ Package = "tkcalendar"; Module = "tkcalendar" }
  )

  foreach ($dep in $requiredDeps) {
    $pkg = $dep.Package
    $module = $dep.Module

    $checkCode = "import importlib.util as u, sys; sys.exit(0 if u.find_spec(r'$module') else 1)"
    $ec = Run-Proc -File $VenvPy -Args @("-c", $checkCode)

    if ($ec -eq 0) {
      Write-Log "Dependency already installed: $pkg"
      continue
    }

    Write-Log "Installing missing dependency: $pkg"
    $installEc = Run-Proc -File $VenvPy -Args @("-m", "pip", "install", $pkg)
    if ($installEc -ne 0) {
      throw "Failed to install required package '$pkg' (exit code $installEc)"
    }
  }
}

function Ensure-SelectedUiModeAvailable {
  param(
    [Parameter(Mandatory=$true)]$Cfg,
    [Parameter(Mandatory=$true)][string]$VenvPy
  )

  $ec = Run-Proc -File $VenvPy -Args @("-c", "import PySide6")
  if ($ec -eq 0) {
    return $Cfg
  }

  Write-Log "PySide UI is unavailable; setup cannot launch the deprecated Tk UI."
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
      "PySide UI is unavailable. Install PySide6 or rerun setup; the deprecated Tk UI has been removed.",
      "PySide UI unavailable",
      [System.Windows.Forms.MessageBoxButtons]::OK,
      [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
  } catch {
    Write-Log "Could not display PySide error message: $($_.Exception.Message)"
  }
  throw "PySide6 is required; deprecated Tk UI fallback has been removed."
}

# -------------------------
# App locator
# -------------------------
function Find-AppFile {
  param([Parameter(Mandatory=$true)]$Cfg)

  $preferredAppFile = Resolve-PreferredInterviewAppFile -Cfg $Cfg
  $Cfg.App.PreferredInterviewAppFile = $preferredAppFile

  # 1) Try cached path first, but invalidate stale or legacy paths after entrypoint changes.
  if ($Cfg.App.InterviewAppPath -and (Test-Path $Cfg.App.InterviewAppPath)) {
    $cachedLeaf = Split-Path $Cfg.App.InterviewAppPath -Leaf
    if ($cachedLeaf -ieq $preferredAppFile) {
      Write-Log "Using cached app path: $($Cfg.App.InterviewAppPath)"
      return $Cfg.App.InterviewAppPath
    }
    Write-Log "Cached app path '$cachedLeaf' does not match preferred '$preferredAppFile'; re-detecting app."
    $Cfg.App.InterviewAppPath = $null
  }

  Write-Log "Cached app path missing or invalid."

  # 2) Try auto-detect in src folder.
  $candidates = @("pyside_interview_app.py")

  foreach ($name in $candidates) {
    $p = Join-Path (Join-Path $AppDir "src") $name
    if (Test-Path $p) {
      Write-Log "Auto-detected app: $p"
      $Cfg.App.InterviewAppPath = $p
      Save-Config $Cfg
      return $p
    }
  }

  # 3) Prompt user to locate it.
  Write-Log "App not found. Prompting user."

  $dlg = New-Object System.Windows.Forms.OpenFileDialog
  $dlg.Title = "Locate the Interview App (.py or .pyw)"
  $dlg.Filter = "Python GUI (*.py;*.pyw)|*.py;*.pyw"
  $dlg.InitialDirectory = [Environment]::GetFolderPath("Desktop")

  if ($dlg.ShowDialog() -eq "OK") {
    $Cfg.App.InterviewAppPath = $dlg.FileName
    Save-Config $Cfg
    Write-Log "User selected app path: $($dlg.FileName)"
    return $dlg.FileName
  }

  throw "Interview app was not located."
}

# -------------------------
# VB-CABLE prompt (only when NOT detected)
# -------------------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Show-VBCablePrompt {
  param([Parameter(Mandatory=$true)]$Cfg)

  # Skip prompt entirely if driver is detected
  if ($Cfg.Tools -and $Cfg.Tools.VBCable -and $Cfg.Tools.VBCable.Detected -eq $true) {
    Write-Log "VB-CABLE detected (driver present). Skipping prompt."
    $Cfg.VBCable.UserSaysInstalled = $true
    return $Cfg
  }

  if ($Cfg.VBCable -and $Cfg.VBCable.DontAskAgain -eq $true) {
    Write-Log "VB-CABLE prompt suppressed by config. UserSaysInstalled=$($Cfg.VBCable.UserSaysInstalled)"
    return $Cfg
  }

  $dlg = New-Object System.Windows.Forms.Form
  $dlg.Text = "Audio Driver Check"
  $dlg.Size = New-Object System.Drawing.Size(520, 200)
  $dlg.StartPosition = "CenterScreen"
  $dlg.TopMost = $true
  $dlg.FormBorderStyle = "FixedDialog"
  $dlg.MaximizeBox = $false
  $dlg.MinimizeBox = $false

  $lbl = New-Object System.Windows.Forms.Label
  $lbl.AutoSize = $false
  $lbl.Size = New-Object System.Drawing.Size(480, 70)
  $lbl.Location = New-Object System.Drawing.Point(15, 10)
  $lbl.Text = "Is VB-Audio Virtual Cable (VB-CABLE) already installed on this computer?`r`n`r`nIf you click Yes, the installer will not open the VB-CABLE download page."
  $dlg.Controls.Add($lbl)

  $chk = New-Object System.Windows.Forms.CheckBox
  $chk.Text = "Don't ask me again on this computer"
  $chk.AutoSize = $true
  $chk.Location = New-Object System.Drawing.Point(18, 90)
  $dlg.Controls.Add($chk)

  $btnYes = New-Object System.Windows.Forms.Button
  $btnYes.Text = "Yes"
  $btnYes.Size = New-Object System.Drawing.Size(90, 28)
  $btnYes.Location = New-Object System.Drawing.Point(310, 125)
  $dlg.Controls.Add($btnYes)

  $btnNo = New-Object System.Windows.Forms.Button
  $btnNo.Text = "No"
  $btnNo.Size = New-Object System.Drawing.Size(90, 28)
  $btnNo.Location = New-Object System.Drawing.Point(410, 125)
  $dlg.Controls.Add($btnNo)

  $result = [pscustomobject]@{ Clicked = $null; DontAsk = $false }

  $btnYes.Add_Click({
    $result.Clicked = "Yes"
    $result.DontAsk = $chk.Checked
    $dlg.Close()
  })
  $btnNo.Add_Click({
    $result.Clicked = "No"
    $result.DontAsk = $chk.Checked
    $dlg.Close()
  })

  [void]$dlg.ShowDialog()

  if (-not $Cfg.VBCable) {
    $Cfg | Add-Member -NotePropertyName VBCable -NotePropertyValue ([pscustomobject]@{
      DontAskAgain = $false
      UserSaysInstalled = $false
      AudioRoutingInstructionsShown = $false
    }) -Force
  }

  if ($result.Clicked -eq "Yes") {
    $Cfg.VBCable.UserSaysInstalled = $true
    $Cfg.VBCable.DontAskAgain = [bool]$result.DontAsk
    Write-Log "User indicated VB-CABLE is installed. DontAskAgain=$($Cfg.VBCable.DontAskAgain)"
    Save-Config $Cfg
    return $Cfg
  }

  if ($result.Clicked -eq "No") {
    $Cfg.VBCable.UserSaysInstalled = $false
    $Cfg.VBCable.DontAskAgain = [bool]$result.DontAsk
    Write-Log "User indicated VB-CABLE is NOT installed. DontAskAgain=$($Cfg.VBCable.DontAskAgain)"
    Save-Config $Cfg
    return $Cfg
  }

  Write-Log "VB-CABLE prompt closed without selection."
  return $Cfg
}

function Show-AudioRoutingInstructions {
  param([Parameter(Mandatory=$true)]$Cfg)

  if (-not $Cfg.VBCable) {
    return $Cfg
  }

  if ($Cfg.VBCable.PSObject.Properties.Name -notcontains "AudioRoutingInstructionsShown") {
    $Cfg.VBCable | Add-Member -NotePropertyName AudioRoutingInstructionsShown -NotePropertyValue $false -Force
  }

  if ($Cfg.VBCable.AudioRoutingInstructionsShown -eq $true) {
    Write-Log "Audio routing instructions already shown; skipping."
    return $Cfg
  }

  $isReadyForRouting = $false
  if ($Cfg.Tools -and $Cfg.Tools.VBCable -and $Cfg.Tools.VBCable.Detected -eq $true) {
    $isReadyForRouting = $true
  }
  if ($Cfg.VBCable.UserSaysInstalled -eq $true) {
    $isReadyForRouting = $true
  }

  if (-not $isReadyForRouting) {
    Write-Log "Audio routing instructions skipped because VB-CABLE is not installed yet."
    return $Cfg
  }

  $message = @"
Before recording interviews with system audio:

1. Open Windows sound settings.
2. Open VB-CABLE Input device properties.
3. Enable listening to this device.
4. Set the listen output device, preferably a headset.
5. Select VB-CABLE as Windows sound output before recording.

If VB-CABLE was just installed, reboot first if Windows does not show the device.
"@

  [System.Windows.Forms.MessageBox]::Show(
    $message,
    "First-run audio routing setup",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
  ) | Out-Null

  $Cfg.VBCable.AudioRoutingInstructionsShown = $true
  Save-Config $Cfg
  Write-Log "Audio routing instructions shown."
  return $Cfg
}

# ---------- Main installer UI ----------
$form = New-Object System.Windows.Forms.Form
$form.Text = "Interview Tool Setup"
$form.Size = New-Object System.Drawing.Size(620, 270)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true

$label = New-Object System.Windows.Forms.Label
$label.AutoSize = $false
$label.Size = New-Object System.Drawing.Size(585, 40)
$label.Location = New-Object System.Drawing.Point(15, 10)
$label.Text = "Starting..."
$form.Controls.Add($label)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Minimum = 0
$bar.Maximum = 100
$bar.Value = 0
$bar.Size = New-Object System.Drawing.Size(585, 24)
$bar.Location = New-Object System.Drawing.Point(15, 60)
$form.Controls.Add($bar)

$details = New-Object System.Windows.Forms.TextBox
$details.Multiline = $true
$details.ReadOnly = $true
$details.ScrollBars = "Vertical"
$details.Size = New-Object System.Drawing.Size(585, 95)
$details.Location = New-Object System.Drawing.Point(15, 95)
$details.Text = "Setup details will appear here.`r`n"
$form.Controls.Add($details)

$btn = New-Object System.Windows.Forms.Button
$btn.Text = "Close"
$btn.Enabled = $false
$btn.Size = New-Object System.Drawing.Size(90, 28)
$btn.Location = New-Object System.Drawing.Point(510, 200)
$btn.Add_Click({ $form.Close() })
$form.Controls.Add($btn)

function Set-Progress([int]$pct, [string]$text) {
  $bar.Value = [Math]::Max($bar.Minimum, [Math]::Min($bar.Maximum, $pct))
  $label.Text = $text
  if ($details) {
    $details.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text))
    $details.SelectionStart = $details.TextLength
    $details.ScrollToCaret()
  }
  $form.Refresh()
  Write-Log $text
}

$form.Add_Shown({
  try {
    $cfg = Load-Config
    $cfg = Ensure-ConfigShape $cfg
    Save-Config $cfg

    Set-Progress 5 "Checking VB-CABLE driver status..."
    $cfg = Ensure-VBCableStatus -Cfg $cfg

    # If VB-CABLE detected, prompt is skipped inside Show-VBCablePrompt
    $cfg = Show-VBCablePrompt -Cfg $cfg

    Set-Progress 15 "Checking FFmpeg availability..."
    Ensure-FFmpeg

    Set-Progress 30 "Checking Python 3.11 install..."
    $py = Ensure-Python311 -Cfg $cfg

    Set-Progress 45 "Checking Python venv and app packages..."
    $venvPy = Ensure-VenvAndDeps $py
    $cfg = Ensure-SelectedUiModeAvailable -Cfg $cfg -VenvPy $venvPy
    Set-GpuVendorEnvironment -Profile (Get-GpuVendorProfile)


    # -------------------------
    # Expose CUDA runtime DLLs for faster-whisper
    # -------------------------
    
    if (Test-NvidiaGPU) {
      $venvScripts = Split-Path $venvPy -Parent
      $venvRoot = Split-Path $venvScripts -Parent
      $cudaBase = Join-Path $venvRoot "Lib\site-packages\nvidia"

      $paths = @(
          (Join-Path $cudaBase "cublas\bin"),
          (Join-Path $cudaBase "cudnn\bin"),
          (Join-Path $cudaBase "cuda_runtime\bin")
      )

      foreach ($p in $paths) {
          if (Test-Path $p) {
              $env:PATH = "$p;$env:PATH"
              Write-Log "Added CUDA path: $p"
          }
      }
    } else {
      Write-Log "Skipping CUDA PATH setup because no NVIDIA GPU was detected."
    }

    # VB-CABLE handling based on detection + user answer
    Set-Progress 75 "Handling VB-CABLE..."
    if ($cfg.Tools.VBCable -and $cfg.Tools.VBCable.Detected -eq $true) {
      Set-Progress 78 "VB-CABLE detected (driver present)."
    } elseif ($cfg.VBCable -and $cfg.VBCable.UserSaysInstalled -eq $true) {
      Set-Progress 78 "VB-CABLE marked as installed (user confirmed)."
    } else {
      if ($cfg.VBCable -and $cfg.VBCable.DontAskAgain -eq $true) {
        Set-Progress 78 "VB-CABLE not detected. Prompt suppressed by user preference."
        Write-Log "VB-CABLE not detected; not opening website because DontAskAgain=true."
      } else {
        Set-Progress 78 "VB-CABLE not detected. Opening download page..."
        Start-Process "https://vb-audio.com/Cable/"
      }
    }
    $cfg = Show-AudioRoutingInstructions -Cfg $cfg

    Set-Progress 85 "Locating app..."
    $app = Find-AppFile -Cfg $cfg
    if (-not $app) { throw "Could not find the app .pyw file in: $AppDir" }

    Install-StaffingDesktopShortcut
    Set-Progress 95 "Launching interview tool..."

    # --- DEBUG LAUNCH (shows Python console) ---
    $appFull = (Resolve-Path $app).Path
    $wrapperPath = Join-Path (Join-Path $AppDir "src") "runtime_wrapper.py"
    if (-not (Test-Path $wrapperPath)) {
      throw "Missing runtime wrapper: $wrapperPath"
    }

    $workDir = Split-Path -Parent $appFull
    $debugFlag = if ($DebugMode) { "--debug" } else { "" }
    $wrapperArgs = @("--target", $appFull, "--app-root", $AppDir)
    if ($DirectorStaffingMode) {
      $wrapperArgs += "--director-staffing"
      if ($DirectorSchool.Trim()) {
        $wrapperArgs += @("--director-school", $DirectorSchool.Trim())
      }
    }
    if ($debugFlag) {
      $wrapperArgs += $debugFlag
    }

    $previousDebugFlag = $env:INTERVIEW_APP_DEBUG
    if ($DebugMode) {
      $env:INTERVIEW_APP_DEBUG = "1"
      Write-Log "Debug mode enabled for launched app."
    }

    try {
      $p = Start-PythonGuiApp `
      -PythonExe $venvPy `
      -ScriptPath $wrapperPath `
      -ScriptArgs $wrapperArgs `
      -WorkingDir $workDir `
      -ShowConsole:$DebugMode
    }
    finally {
      $env:INTERVIEW_APP_DEBUG = $previousDebugFlag
    }

    Set-Progress 100 "Launching complete. Closing installer..."
    $form.BeginInvoke([Action]{ $form.Close() }) | Out-Null
  }
  catch {
    try { Write-Log ("EXCEPTION: " + $_.Exception.ToString()) } catch {}

    $msg = $_.Exception.Message
    Set-Progress 100 "ERROR: $msg"
    $btn.Enabled = $true

    [System.Windows.Forms.MessageBox]::Show(
      "Setup failed:`r`n`r`n$msg`r`n`r`nLog file:`r`n$Log",
      "Interview Tool Setup Error",
      [System.Windows.Forms.MessageBoxButtons]::OK,
      [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
  }
  finally {
    try { $mutex.ReleaseMutex() | Out-Null } catch {}
  }
})

[void]$form.ShowDialog()
return

# ============================================
# LPL SETUP AND RUN SCRIPT (FULL INTEGRATION)
# ============================================

# ----------------------------
# GLOBAL PATHS
# ----------------------------
$AppName    = "LPL_App"
$LocalDir   = "$env:LOCALAPPDATA\$AppName"
$ConfigPath = Join-Path $LocalDir "setup_config.json"
$LogDir     = Join-Path $LocalDir "logs"
$LogFile    = Join-Path $LogDir "install_run_log.txt"

# Mutex name (prevents double execution)
$MutexName  = "Global\LPL_Setup_Mutex"

# ----------------------------
# CREATE DIRECTORIES
# ----------------------------
New-Item -ItemType Directory -Path $LocalDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

# ----------------------------
# LOGGING FUNCTION
# ----------------------------
function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = ("[{0}] {1}" -f $timestamp, $Message)

    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

Write-Log "==============================================="
Write-Log "Started: $(Get-Date)"
Write-Log "User=$env:USERNAME"
Write-Log "Computer=$env:COMPUTERNAME"
Write-Log "PSVersion=$($PSVersionTable.PSVersion)"
Write-Log "==============================================="

# ----------------------------
# MUTEX LOCK
# ----------------------------
$mutex = New-Object System.Threading.Mutex($false, $MutexName)

if (-not $mutex.WaitOne(0, $false)) {
    Write-Log "Another instance is already running. Exiting."
    exit
}

try {

# ----------------------------
# CONFIG LOAD / SAVE
# ----------------------------
function Load-Config {
    if (Test-Path $ConfigPath) {
        try {
            return Get-Content $ConfigPath | ConvertFrom-Json
        } catch {
            Write-Log "Config corrupted. Recreating..."
        }
    }

    return [PSCustomObject]@{
        vbCableInstalled = $false
    }
}

function Save-Config($config) {
    $config | ConvertTo-Json -Depth 5 | Set-Content $ConfigPath
}

$config = Load-Config

# ----------------------------
# VB-CABLE DETECTION
# ----------------------------
function Test-VBCableInstalled {
    try {
        $devices = Get-CimInstance Win32_SoundDevice | Where-Object {
            $_.Name -like "*CABLE Input*" -or $_.Name -like "*VB-Audio*"
        }

        return ($devices -ne $null -and $devices.Count -gt 0)
    } catch {
        Write-Log "VB-CABLE detection failed: $_"
        return $false
    }
}

# ----------------------------
# DOWNLOAD HELPER
# ----------------------------
function Download-File {
    param($Url, $OutFile, $Retries = 3)

    for ($i = 1; $i -le $Retries; $i++) {
        try {
            Write-Log "Downloading VB-CABLE (Attempt $i)..."
            Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
            return $true
        } catch {
            Write-Log "Download failed: $_"
            Start-Sleep -Seconds 2
        }
    }

    return $false
}

# ----------------------------
# INSTALL VB-CABLE
# ----------------------------
function Install-VBCable {

    $url     = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
    $tempDir = "$env:TEMP\VBCableInstall"
    $zipPath = Join-Path $tempDir "vbcable.zip"

    Write-Log "VB-CABLE not detected. Installing..."

    # Clean temp
    if (Test-Path $tempDir) {
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $tempDir | Out-Null

    # Download
    if (-not (Download-File $url $zipPath)) {
        throw "Failed to download VB-CABLE."
    }

    # Validate
    if (!(Test-Path $zipPath) -or (Get-Item $zipPath).Length -lt 100000) {
        throw "Invalid VB-CABLE download."
    }

    # Extract
    Write-Log "Extracting VB-CABLE..."
    Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force

    # Find installer
    $installer = Get-ChildItem -Path $tempDir -Recurse -Filter "VBCABLE_Setup_x64.exe" | Select-Object -First 1

    if (-not $installer) {
        throw "Installer not found."
    }

    Write-Log "Launching installer (admin)..."

    Start-Process -FilePath $installer.FullName -Verb RunAs -Wait

    Start-Sleep -Seconds 5

    if (Test-VBCableInstalled) {
        Write-Log "VB-CABLE installed successfully."
    } else {
        Write-Log "WARNING: VB-CABLE not detected yet. Reboot may be required."
    }
}

# ----------------------------
# VB-CABLE EXECUTION LOGIC
# ----------------------------
if ($config.vbCableInstalled) {
    Write-Log "VB-CABLE cached as installed. Verifying..."

    if (Test-VBCableInstalled) {
        Write-Log "VB-CABLE confirmed installed."
    } else {
        Write-Log "Cache mismatch. Reinstalling..."
        Install-VBCable
        $config.vbCableInstalled = $true
        Save-Config $config
    }
}
else {
    if (Test-VBCableInstalled) {
        Write-Log "VB-CABLE detected. Updating config..."
        $config.vbCableInstalled = $true
        Save-Config $config
    } else {
        Install-VBCable
        $config.vbCableInstalled = $true
        Save-Config $config
    }
}

# ----------------------------
# PYTHON / VENV SECTION (HOOK)
# ----------------------------
Write-Log "Ensuring Python + venv..."

# 👉 KEEP YOUR EXISTING PYTHON / VENV LOGIC HERE
# (I left this as a placeholder so we don't overwrite your working setup)

# Example placeholder:
# Ensure-Python
# Ensure-Venv
# Install-Dependencies

Write-Log "Setup complete."

}
finally {
    $mutex.ReleaseMutex()
    Write-Log "Mutex released."
}
