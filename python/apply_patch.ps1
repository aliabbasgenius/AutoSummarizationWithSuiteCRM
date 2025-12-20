param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('codebase','autosummarization')]
  [string]$Approach,

  # Which patch filename pattern to use.
  # - demo: runs/demo_<approach>.patch
  # - latest: runs/latest_<approach>.patch
  [ValidateSet('demo','latest')]
  [string]$Variant = 'demo',

  # What to do.
  [ValidateSet('check','apply','revert','status')]
  [string]$Action = 'apply',

  # SuiteCRM repo root.
  [string]$SuiteCrmRoot = "..\..\SuiteCRM",

  # Optional explicit patch path override.
  [string]$PatchPath = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-NormalizedPath([string]$p) {
  $resolved = Resolve-Path -LiteralPath $p
  return $resolved.Path
}

function Get-PatchedFilesFromPatch([string]$patchFile) {
  $files = New-Object System.Collections.Generic.List[string]
  foreach ($line in Get-Content -LiteralPath $patchFile -Encoding UTF8) {
    if ($line -like 'diff --git a/* b/*') {
      $parts = $line.Split(' ')
      if ($parts.Length -ge 4) {
        $aPath = $parts[2]
        if ($aPath.StartsWith('a/')) {
          $files.Add($aPath.Substring(2))
        }
      }
    }
    elseif ($line -like '--- a/*') {
      # Some tools output a minimal unified diff without the leading `diff --git` line.
      $aPath = $line.Substring(4).Trim()
      if ($aPath.StartsWith('a/')) {
        $files.Add($aPath.Substring(2))
      }
    }
  }
  return $files | Select-Object -Unique
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$suite = Resolve-NormalizedPath (Join-Path $scriptRoot $SuiteCrmRoot)

$patch = $PatchPath
if ([string]::IsNullOrWhiteSpace($patch)) {
  $suffix = $(if ($Approach -eq 'codebase') { 'codebase' } else { 'autosummarization' })
  $name = $(if ($Variant -eq 'demo') { "demo_$suffix.patch" } else { "latest_$suffix.patch" })
  $patch = Join-Path $scriptRoot (Join-Path 'runs' $name)
}
$patch = Resolve-NormalizedPath $patch

Write-Host "SuiteCRM: $suite" -ForegroundColor Cyan
Write-Host "Patch:   $patch" -ForegroundColor Cyan
Write-Host "Action:  $Action" -ForegroundColor Cyan

if ($Action -eq 'status') {
  git -C $suite status --porcelain
  exit 0
}

if ($Action -eq 'check') {
  git -C $suite apply --check $patch
  Write-Host 'OK: git apply --check passed' -ForegroundColor Green
  exit 0
}

if ($Action -eq 'apply') {
  git -C $suite apply --check $patch
  git -C $suite apply $patch
  git -C $suite status --porcelain
  exit 0
}

if ($Action -eq 'revert') {
  $files = Get-PatchedFilesFromPatch $patch
  if (-not $files -or $files.Count -eq 0) {
    throw "Could not find any file paths in patch: $patch"
  }

  foreach ($f in $files) {
    git -C $suite checkout -- $f
  }
  git -C $suite status --porcelain
  exit 0
}
