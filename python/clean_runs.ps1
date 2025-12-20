param(
  # If set, do not prompt.
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runDir = Join-Path $scriptRoot 'runs'

if (-not (Test-Path $runDir)) {
  Write-Host "No runs directory found: $runDir" -ForegroundColor Yellow
  exit 0
}

$targets = Get-ChildItem -Path $runDir -File
if ($targets.Count -eq 0) {
  Write-Host "Runs directory already empty: $runDir" -ForegroundColor Green
  exit 0
}

Write-Host "About to delete $($targets.Count) file(s) from: $runDir" -ForegroundColor Yellow
if (-not $Force) {
  $resp = Read-Host "Type 'yes' to confirm"
  if ($resp -ne 'yes') {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 1
  }
}

$targets | Remove-Item -Force
Write-Host "Deleted. (Note: runs/ is ignored by git.)" -ForegroundColor Green
