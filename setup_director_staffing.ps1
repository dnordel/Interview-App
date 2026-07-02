#requires -version 5.1
param(
  [switch]$DebugMode
)

$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir
$LogDir = Join-Path $AppDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Log = Join-Path $LogDir "director_staffing_install_run_log.txt"
"===============================================" | Out-File -FilePath $Log -Encoding UTF8
"Started: $(Get-Date)" | Out-File -FilePath $Log -Append -Encoding UTF8
"APP_DIR=$AppDir" | Out-File -FilePath $Log -Append -Encoding UTF8
"User=$env:USERNAME" | Out-File -FilePath $Log -Append -Encoding UTF8
"Computer=$env:COMPUTERNAME" | Out-File -FilePath $Log -Append -Encoding UTF8
"PSVersion=$($PSVersionTable.PSVersion)" | Out-File -FilePath $Log -Append -Encoding UTF8
"DebugMode=$DebugMode" | Out-File -FilePath $Log -Append -Encoding UTF8
"===============================================" | Out-File -FilePath $Log -Append -Encoding UTF8

function Write-Log([string]$msg) {
  $msg | Out-File -FilePath $Log -Append -Encoding UTF8
}

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$mutex = New-Object Threading.Mutex($false, "Global\LPL_DirectorStaffing_Setup")
if (-not $mutex.WaitOne(0)) {
  throw "Another Director Staffing setup is already running."
}

function Get-ConfigBaseDir {
  $base = Join-Path $env:LOCALAPPDATA "LPL_DirectorStaffing"
  if (-not (Test-Path $base)) { New-Item -ItemType Directory -Path $base | Out-Null }
  return $base
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

function Find-Python311 {
  $candidates = New-Object System.Collections.Generic.List[string]
  try {
    $pyPath = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyPath) { $candidates.Add([string]$pyPath) }
  } catch {}

  $known = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
    (Join-Path $env:ProgramFiles "Python311\python.exe")
  )
  foreach ($path in $known) {
    if ($path) { $candidates.Add($path) }
  }

  try {
    $cmd = Get-Command "python.exe" -ErrorAction Stop
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
  } catch {}

  foreach ($candidate in $candidates) {
    if (Test-Python311Exe $candidate) {
      Write-Log "Python 3.11 found: $candidate"
      return $candidate
    }
  }
  return $null
}

function Ensure-Python311 {
  $found = Find-Python311
  if ($found) { return $found }

  $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
  $installerPath = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
  Write-Log "Downloading Python 3.11 from: $installerUrl"
  Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
  $targetDir = Join-Path (Get-ConfigBaseDir) "Python311"
  $args = @(
    "/quiet",
    "InstallAllUsers=0",
    "PrependPath=0",
    "Include_launcher=1",
    "Include_test=0",
    "SimpleInstall=1",
    "TargetDir=$targetDir"
  )
  $p = Start-Process -FilePath $installerPath -ArgumentList ($args -join " ") -Wait -PassThru
  if ($p.ExitCode -ne 0) {
    throw "Python install failed (exit code $($p.ExitCode))."
  }
  $pythonExe = Join-Path $targetDir "python.exe"
  if (-not (Test-Python311Exe $pythonExe)) {
    throw "Python 3.11 install completed but python.exe was not usable."
  }
  return $pythonExe
}

function Run-Proc {
  param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$false)][string[]]$Args = @(),
    [Parameter(Mandatory=$false)][string]$WorkingDir = $AppDir
  )

  Write-Log "RUN: $File $($Args -join ' ')"
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
  $stdout = $p.StandardOutput.ReadToEnd()
  $stderr = $p.StandardError.ReadToEnd()
  $p.WaitForExit()
  if ($stdout) { $stdout | Out-File -FilePath $Log -Append -Encoding UTF8 }
  if ($stderr) { $stderr | Out-File -FilePath $Log -Append -Encoding UTF8 }
  return $p.ExitCode
}

function Get-VenvPython {
  $venvDir = Join-Path (Get-ConfigBaseDir) "py311\.venv"
  return (Join-Path $venvDir "Scripts\python.exe")
}

function Ensure-DirectorVenv([string]$PyExe) {
  $venvDir = Join-Path (Get-ConfigBaseDir) "py311\.venv"
  $venvPy = Get-VenvPython
  if (-not (Test-Path $venvPy)) {
    if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir }
    $ec = Run-Proc -File $PyExe -Args @("-m","venv","--system-site-packages",$venvDir)
    if ($ec -ne 0) { throw "Director venv creation failed (exit code $ec)." }
  }
  return $venvPy
}

function Ensure-DirectorDeps([string]$VenvPy) {
  $req = Join-Path $AppDir "requirements-director.txt"
  if (-not (Test-Path $req)) { throw "Missing director requirements: $req" }
  $pipEc = Run-Proc -File $VenvPy -Args @("-m","pip","install","--upgrade","pip")
  if ($pipEc -ne 0) { throw "pip upgrade failed (exit code $pipEc)." }
  $installEc = Run-Proc -File $VenvPy -Args @("-m","pip","install","-r",$req)
  if ($installEc -ne 0) { throw "Director package install failed (exit code $installEc)." }
  $probeEc = Run-Proc -File $VenvPy -Args @("-c","import PySide6, staffing_store, staffing_service")
  if ($probeEc -ne 0) { throw "Director staffing package check failed (exit code $probeEc)." }
}

function Find-StaffingAppFile {
  $app = Join-Path (Join-Path $AppDir "src") "staffing_dashboard_app.py"
  if (-not (Test-Path $app)) { throw "Staffing dashboard app was not found: $app" }
  return (Resolve-Path $app).Path
}

function Start-PythonGuiApp {
  param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$ScriptPath,
    [Parameter(Mandatory=$true)][string]$WorkingDir,
    [string]$LogFile = $null,
    [switch]$ShowConsole
  )

  if (-not (Test-Path $PythonExe)) { throw "Python executable not found: $PythonExe" }
  if (-not (Test-Path $ScriptPath)) { throw "Python script not found: $ScriptPath" }
  $pythonw = Join-Path (Split-Path $PythonExe -Parent) "pythonw.exe"
  $launcher = if (-not $ShowConsole -and (Test-Path $pythonw)) { $pythonw } else { $PythonExe }
  if (-not $LogFile) { $LogFile = Join-Path $LogDir "director_staffing_runtime_log.txt" }
  Write-Log "Python launcher: $launcher"
  Write-Log "Script: $ScriptPath"
  $p = Start-Process `
    -FilePath $launcher `
    -ArgumentList ('"' + $ScriptPath + '"') `
    -WorkingDirectory $WorkingDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir "director_staffing_stdout.txt") `
    -RedirectStandardError (Join-Path $LogDir "director_staffing_stderr.txt") `
    -PassThru
  Start-Sleep -Milliseconds 800
  if ($p.HasExited) {
    throw "Staffing dashboard exited immediately (exit code $($p.ExitCode)). See $LogFile."
  }
  return $p
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "Director Staffing Dashboard Setup"
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
  $details.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text))
  $details.SelectionStart = $details.TextLength
  $details.ScrollToCaret()
  $form.Refresh()
  Write-Log $text
}

$form.Add_Shown({
  try {
    Set-Progress 20 "Checking Python 3.11 install..."
    $py = Ensure-Python311
    Set-Progress 45 "Checking director staffing packages..."
    $venvPy = Ensure-DirectorVenv $py
    Ensure-DirectorDeps $venvPy
    Set-Progress 75 "Locating staffing dashboard..."
    $app = Find-StaffingAppFile
    Set-Progress 90 "Launching staffing dashboard..."
    $workDir = Split-Path -Parent $app
    [void](Start-PythonGuiApp -PythonExe $venvPy -ScriptPath $app -WorkingDir $workDir -ShowConsole:$DebugMode)
    Set-Progress 100 "Launching complete. Closing setup..."
    $form.BeginInvoke([Action]{ $form.Close() }) | Out-Null
  }
  catch {
    try { Write-Log ("EXCEPTION: " + $_.Exception.ToString()) } catch {}
    $msg = $_.Exception.Message
    Set-Progress 100 "ERROR: $msg"
    $btn.Enabled = $true
    [System.Windows.Forms.MessageBox]::Show(
      "Setup failed:`r`n`r`n$msg`r`n`r`nLog file:`r`n$Log",
      "Director Staffing Dashboard Setup Error",
      [System.Windows.Forms.MessageBoxButtons]::OK,
      [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
  }
  finally {
    try { $mutex.ReleaseMutex() | Out-Null } catch {}
  }
})

[void]$form.ShowDialog()
