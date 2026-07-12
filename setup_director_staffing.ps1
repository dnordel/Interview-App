#requires -version 5.1
param(
  [string]$DirectorSchool = ""
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir

$LogDir = Join-Path $AppDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir "director_staffing_setup_log.txt"
"Started: $(Get-Date)" | Out-File -FilePath $Log -Encoding UTF8

function Write-Log([string]$Message) {
  $Message | Out-File -FilePath $Log -Append -Encoding UTF8
}

function Run-Proc {
  param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$false)][string[]]$Args = @()
  )
  Write-Log ("RUN: {0} {1}" -f $File, ($Args -join " "))
  Push-Location $AppDir
  try {
    & $File @Args
    return $LASTEXITCODE
  }
  finally {
    Pop-Location
  }
}

function Find-Python311 {
  $candidates = @()
  $managedPython = Join-Path $env:LOCALAPPDATA "LPL_InterviewTool\py311\.venv\Scripts\python.exe"
  if (Test-Path $managedPython) {
    $candidates += $managedPython
  }
  $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
  if (Test-Path $localPython) {
    $candidates += $localPython
  }
  $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    try {
      $path = & py.exe -3.11 -c "import sys; print(sys.executable)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $path) { $candidates += [string]$path }
    } catch {}
  }
  $python = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($python) { $candidates += $python.Source }
  foreach ($candidate in $candidates) {
    try {
      $ok = & $candidate -c "import sys; print(sys.version_info[:2] == (3, 11))" 2>$null
      if ($LASTEXITCODE -eq 0 -and $ok -match "True") { return $candidate }
    } catch {}
  }
  throw "Python 3.11 is required for the director staffing dashboard."
}

function Ensure-DirectorVenv {
  param([Parameter(Mandatory=$true)][string]$PythonExe)
  $base = Join-Path $env:LOCALAPPDATA "LPL_InterviewTool"
  $venvDir = Join-Path $base "director_py311"
  $venvPy = Join-Path $venvDir "Scripts\python.exe"
  if (Test-Path $venvPy) {
    return $venvPy
  }
  $sharedVenvPy = Join-Path $base "py311\.venv\Scripts\python.exe"
  if (Test-Path $sharedVenvPy) {
    return $sharedVenvPy
  }
  if (-not (Test-Path $venvPy)) {
    if (-not (Test-Path $base)) { New-Item -ItemType Directory -Path $base | Out-Null }
    $ec = Run-Proc -File $PythonExe -Args @("-m", "venv", $venvDir)
    if ($ec -ne 0) { throw "Failed to create director staffing virtual environment." }
  }
  return $venvPy
}

function Start-DirectorStaffingApp {
  param(
    [Parameter(Mandatory=$true)][string]$VenvPy,
    [Parameter(Mandatory=$true)][string]$AppPath,
    [Parameter(Mandatory=$false)][string[]]$AppArgs = @()
  )
  $launcher = $VenvPy
  $stdout = Join-Path $LogDir "director_staffing_stdout.txt"
  $stderr = Join-Path $LogDir "director_staffing_stderr.txt"
  $args = @($AppPath) + $AppArgs
  $quotedArgs = foreach ($arg in $args) {
    if ($arg -match '[\s"]') {
      '"' + ($arg -replace '"', '\"') + '"'
    }
    else {
      $arg
    }
  }
  $argumentString = $quotedArgs -join " "
  Write-Log ("START: {0} {1}" -f $launcher, $argumentString)
  "" | Out-File -FilePath $stdout -Encoding UTF8
  "" | Out-File -FilePath $stderr -Encoding UTF8
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $launcher
  $startInfo.Arguments = $argumentString
  $startInfo.WorkingDirectory = Join-Path $AppDir "src"
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = [System.Diagnostics.Process]::Start($startInfo)
  Start-Sleep -Milliseconds 1200
  if ($process.HasExited) {
    $outputText = $process.StandardOutput.ReadToEnd()
    $errorText = ""
    $errorText = $process.StandardError.ReadToEnd()
    $outputText | Out-File -FilePath $stdout -Encoding UTF8
    $errorText | Out-File -FilePath $stderr -Encoding UTF8
    throw "Director staffing dashboard exited immediately. $errorText"
  }
}

function Ensure-DirectorDependencies {
  param([Parameter(Mandatory=$true)][string]$VenvPy)
  $requirements = Join-Path $AppDir "requirements-director.txt"
  if (-not (Test-Path $requirements)) {
    throw "requirements-director.txt not found."
  }
  $probe = Run-Proc -File $VenvPy -Args @("-c", "import PySide6")
  if ($probe -eq 0) { return }
  $ec = Run-Proc -File $VenvPy -Args @("-m", "pip", "install", "-r", $requirements)
  if ($ec -ne 0) { throw "Failed to install director staffing dependencies." }
}

$mutex = New-Object Threading.Mutex($false, "Global\LPL_DirectorStaffing_Setup")
if (-not $mutex.WaitOne(0)) {
  throw "Another Director Staffing setup is already running."
}

try {
  $python = Find-Python311
  $venvPy = Ensure-DirectorVenv -PythonExe $python
  Ensure-DirectorDependencies -VenvPy $venvPy

  $app = Join-Path (Join-Path $AppDir "src") "director_staffing_app.py"
  if (-not (Test-Path $app)) {
    throw "Director staffing app not found: $app"
  }
  $args = @()
  if ($DirectorSchool.Trim()) {
    $args += @("--director-school", $DirectorSchool.Trim())
  }
  Start-DirectorStaffingApp -VenvPy $venvPy -AppPath $app -AppArgs $args
}
finally {
  try { $mutex.ReleaseMutex() | Out-Null } catch {}
}
