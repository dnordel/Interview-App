# Debug wrapper. Launches setup_and_run.ps1 with debug mode enabled.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-CanonicalSetupScript {
  param(
    [Parameter(Mandatory = $true)][string]$StartDir
  )

  $current = Resolve-Path $StartDir

  while ($true) {
    $candidate = Join-Path $current.Path "setup_and_run.ps1"
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }

    $parent = Split-Path $current.Path -Parent
    if ($parent -eq $current.Path -or [string]::IsNullOrWhiteSpace($parent)) {
      break
    }

    $current = Resolve-Path $parent
  }

  throw "Unable to locate setup_and_run.ps1 by walking parent folders from: $StartDir"
}

$scriptPath = Resolve-CanonicalSetupScript -StartDir $PSScriptRoot

function Start-SetupScript {
  param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(Mandatory = $false)][string[]]$ScriptArgs = @()
  )

  $hostExe = if ($PSVersionTable.PSEdition -eq "Core") { "pwsh" } else { "powershell.exe" }
  $invokeArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath, "-DebugMode") + $ScriptArgs

  & $hostExe @invokeArgs
  return $LASTEXITCODE
}

$exitCode = Start-SetupScript -ScriptPath $scriptPath -ScriptArgs $args
exit $exitCode
