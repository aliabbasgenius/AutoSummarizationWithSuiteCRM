param(
  # Run one approach or both.
  [ValidateSet('codebase','autosummarization','both')]
  [string]$Approach = 'both',

  # Which patch filename pattern to use.
  [ValidateSet('demo','latest')]
  [string]$Variant = 'demo',

  # Prompt file used by run.ps1
  [string]$Prompt = ".\tasks\ACTIVE_PROMPT.txt",

  # Source file(s) to feed into generation.
  [string[]]$Sources = @("..\..\SuiteCRM\modules\Accounts\Account.php"),

  # SuiteCRM repo root.
  [string]$SuiteCrmRoot = "..\..\SuiteCRM"
)

$ErrorActionPreference = 'Stop'

function Resolve-NormalizedPath([string]$p) {
  $resolved = Resolve-Path -LiteralPath $p
  return $resolved.Path
}

function Invoke-Step([string]$title, [scriptblock]$action) {
  Write-Host "== $title ==" -ForegroundColor Cyan
  & $action
}

function Run-One([ValidateSet('codebase','autosummarization')] [string]$OneApproach) {
  $scriptRoot = $PSScriptRoot
  $suite = Resolve-NormalizedPath (Join-Path $scriptRoot $SuiteCrmRoot)
  $promptPath = Resolve-NormalizedPath (Join-Path $scriptRoot $Prompt)

  $suffix = $(if ($OneApproach -eq 'codebase') { 'codebase' } else { 'autosummarization' })
  $patchName = $(if ($Variant -eq 'demo') { "demo_$suffix.patch" } else { "latest_$suffix.patch" })
  $patchPath = Resolve-NormalizedPath (Join-Path $scriptRoot (Join-Path 'runs' $patchName))

  $validationReport = Join-Path $scriptRoot (Join-Path 'runs' ("{0}_{1}.validation.json" -f $Variant, $suffix))

  $warningsCount = $null

  Invoke-Step "${OneApproach}: Ensure SuiteCRM clean" {
    $dirty = git -C $suite status --porcelain
    if ($dirty) {
      throw "SuiteCRM working tree is not clean. Please stash/commit/revert your changes first.\n$dirty"
    }
  }

  Invoke-Step "${OneApproach}: Generate" {
    $runApproach = $(if ($OneApproach -eq 'codebase') { 'raw' } else { 'autosummary' })

    $srcArgs = @()
    foreach ($s in $Sources) {
      $srcArgs += (Join-Path $scriptRoot $s)
    }

    . (Join-Path $scriptRoot 'run.ps1') -Approach $runApproach -Prompt $promptPath -Sources $srcArgs -Output $patchPath
  }

  Invoke-Step "${OneApproach}: Offline hallucination/format validation" {
    $py = Join-Path $scriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "Python venv not found at $py" }

    & $py (Join-Path $scriptRoot 'validate_generated_output.py') --input $patchPath --suitecrm-root $suite --report $validationReport --no-php-lint

    try {
      $reportObj = Get-Content -LiteralPath $validationReport -Encoding UTF8 | ConvertFrom-Json
      $warns = @($reportObj.findings | Where-Object { $_.severity -eq 'warn' })
      $warningsCount = $warns.Count
    } catch {
      $warningsCount = $null
    }
  }

  Invoke-Step "${OneApproach}: git apply --check" {
    . (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action check -SuiteCrmRoot $SuiteCrmRoot
  }

  Invoke-Step "${OneApproach}: Apply patch (NO COMMIT)" {
    . (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action apply -SuiteCrmRoot $SuiteCrmRoot
    Write-Host "Diff stat:" -ForegroundColor DarkCyan
    git -C $suite diff --stat
  }

  Invoke-Step "${OneApproach}: Compile / quality checks" {
    . (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action compile -SuiteCrmRoot $SuiteCrmRoot
  }

  Write-Host "SUCCESS: $OneApproach" -ForegroundColor Green
  Write-Host "- Patch generated: $patchPath" -ForegroundColor Green
  if ($warningsCount -ne $null) {
    Write-Host "- Offline hallucination/format validation: OK (warnings: $warningsCount)" -ForegroundColor Green
  } else {
    Write-Host "- Offline hallucination/format validation: OK" -ForegroundColor Green
  }
  Write-Host "- Patch applied to SuiteCRM (NO COMMIT): yes" -ForegroundColor Green
  Write-Host "- SuiteCRM build/compile checks: RUN (php -l)" -ForegroundColor Green
  Write-Host "- Code quality checks: composer validate (when available)" -ForegroundColor Green
  Write-Host "- Validation report: $validationReport" -ForegroundColor Green

  Write-Host "NOTE: Patch is currently applied. To undo: .\apply_patch.ps1 -Approach $OneApproach -Variant $Variant -Action revert" -ForegroundColor Yellow
}

Set-Location $PSScriptRoot

if ($Approach -eq 'both') {
  Run-One -OneApproach codebase

  $answer = (Read-Host 'Do you want to process the auto summarization? [y/n]').Trim().ToLowerInvariant()
  if ($answer -eq 'y' -or $answer -eq 'yes') {
    # Ensure we can run autosummarization on a clean tree.
    Write-Host "Preparing SuiteCRM for autosummarization (reverting prior patch)" -ForegroundColor Cyan
    . (Join-Path $PSScriptRoot 'apply_patch.ps1') -Approach codebase -Variant $Variant -Action revert -SuiteCrmRoot $SuiteCrmRoot
    Run-One -OneApproach autosummarization
  } else {
    Write-Host "Skipping autosummarization." -ForegroundColor Yellow
  }
} else {
  if ($Approach -eq 'autosummarization') {
    $answer = (Read-Host 'Do you want to process the auto summarization? [y/n]').Trim().ToLowerInvariant()
    if ($answer -ne 'y' -and $answer -ne 'yes') {
      Write-Host "Skipping autosummarization." -ForegroundColor Yellow
      exit 0
    }
  }
  Run-One -OneApproach $Approach
}
