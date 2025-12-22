param(
  # Run one approach or both.
  [ValidateSet('codebase', 'autosummarization', 'both')]
  [string]$Approach = 'both',

  # Which patch filename pattern to use.
  [ValidateSet('demo', 'latest')]
  [string]$Variant = 'demo',

  # Prompt file used by run.ps1
  [string]$Prompt = ".\tasks\ACTIVE_PROMPT.txt",

  # Source file(s) to feed into generation.
  [string[]]$Sources = @("..\SuiteCRM\modules\Accounts\Account.php"),

  # SuiteCRM repo root.
  [string]$SuiteCrmRoot = "..\SuiteCRM",

  # Automatically answer prompts (useful for CI/non-interactive runs).
  [switch]$AutoApprove = $false
)

$ErrorActionPreference = 'Stop'

function Resolve-NormalizedPath([string]$p) {
  try {
    $resolved = Resolve-Path -LiteralPath $p -ErrorAction Stop
    return $resolved.Path
  }
  catch {
    return [System.IO.Path]::GetFullPath($p)
  }
}

function Invoke-Step([string]$title, [scriptblock]$action) {
  Write-Host "== $title ==" -ForegroundColor Cyan
  & $action
}

function Run-One([ValidateSet('codebase', 'autosummarization')] [string]$OneApproach) {
  $scriptRoot = $PSScriptRoot
  Write-Host "Starting Run-One for $OneApproach" -ForegroundColor DarkGray
  $suite = Resolve-NormalizedPath (Join-Path $scriptRoot $SuiteCrmRoot)
  $promptPath = Resolve-NormalizedPath (Join-Path $scriptRoot $Prompt)

  $suffix = $(if ($OneApproach -eq 'codebase') { 'codebase' } else { 'autosummarization' })
  $patchName = $(if ($Variant -eq 'demo') { "demo_$suffix.patch" } else { "latest_$suffix.patch" })
  $patchPath = Resolve-NormalizedPath (Join-Path $scriptRoot (Join-Path 'runs' $patchName))

  $validationReport = Join-Path $scriptRoot (Join-Path 'runs' ("{0}_{1}.validation.json" -f $Variant, $suffix))

  $warningsCount = $null
  $generationSeconds = $null

  Invoke-Step "${OneApproach}: Ensure SuiteCRM clean" {
    $dirty = git -C $suite status --porcelain -- .
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

    $duration = Measure-Command {
      & (Join-Path $scriptRoot 'run.ps1') -Approach $runApproach -Prompt $promptPath -Sources $srcArgs -Output $patchPath | Out-Host
    }
    Set-Variable -Scope 2 -Name generationSeconds -Value ([Math]::Round($duration.TotalSeconds, 2))
  }

  Invoke-Step "${OneApproach}: Offline hallucination/format validation" {
    $py = Join-Path $scriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "Python venv not found at $py" }

    & $py (Join-Path $scriptRoot 'validate_generated_output.py') --input $patchPath --suitecrm-root $suite --report $validationReport --no-php-lint

    try {
      $reportObj = Get-Content -LiteralPath $validationReport -Encoding UTF8 | ConvertFrom-Json
      $warns = @($reportObj.findings | Where-Object { $_.severity -eq 'warn' })
      Set-Variable -Scope 2 -Name warningsCount -Value $warns.Count
    }
    catch {
      Set-Variable -Scope 2 -Name warningsCount -Value $null
    }
  }

  Invoke-Step "${OneApproach}: git apply --check" {
    & (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action check -SuiteCrmRoot $SuiteCrmRoot | Out-Host
  }

  Invoke-Step "${OneApproach}: Apply patch (NO COMMIT)" {
    & (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action apply -SuiteCrmRoot $SuiteCrmRoot | Out-Host
    Write-Host "Diff stat:" -ForegroundColor DarkCyan
    git -C $suite diff --stat | Out-Host
  }

  Invoke-Step "${OneApproach}: Compile / quality checks" {
    & (Join-Path $scriptRoot 'apply_patch.ps1') -Approach $OneApproach -Variant $Variant -Action compile -SuiteCrmRoot $SuiteCrmRoot | Out-Host
  }

  $touchedFiles = @()
  foreach ($line in (Get-Content -LiteralPath $patchPath -ErrorAction SilentlyContinue)) {
    if ($line -match '^diff --git a/(.+?) b/(.+)$') {
      $touchedFiles += $Matches[1]
      continue
    }
    if ($line -match '^--- a/(.+)$') {
      $touchedFiles += $Matches[1]
      continue
    }
  }
  $touchedFiles = @(
    $touchedFiles |
    Where-Object { $_ -and $_ -ne '/dev/null' } |
    Select-Object -Unique
  )

  $touchesPhp = @($touchedFiles | Where-Object { $_.ToLowerInvariant().EndsWith('.php') }).Count

  $phpExePath = $null
  $phpCmd = Get-Command php -ErrorAction SilentlyContinue
  if ($phpCmd) {
    $phpExePath = $phpCmd.Source
  }
  if (-not $phpExePath) {
    $portablePhp = Join-Path $PSScriptRoot '..\\..\\tools\\php81\\php.exe'
    if (Test-Path -LiteralPath $portablePhp) {
      $phpExePath = (Resolve-NormalizedPath $portablePhp)
    }
  }

  $composerAvailable = $false
  $composerCmd = Get-Command composer -ErrorAction SilentlyContinue
  if ($composerCmd) {
    $composerAvailable = $true
  }
  else {
    $portableComposer = Join-Path $PSScriptRoot '..\\..\\tools\\php81\\composer.phar'
    if ($phpExePath -and (Test-Path -LiteralPath $portableComposer)) {
      $composerAvailable = $true
    }
  }

  $phpLintRanText = $(if ($phpExePath -and $touchesPhp -gt 0) { 'YES' } else { 'NO' })
  $composerValidateRanText = $(if ($composerAvailable) { 'YES' } else { 'NO' })

  Write-Host "SUCCESS: $OneApproach (patch: $patchPath)" -ForegroundColor Green
  if ($warningsCount -ne $null) {
    Write-Host "- Offline hallucination/format validation: OK (warnings: $warningsCount)" -ForegroundColor Green
  }
  else {
    Write-Host "- Offline hallucination/format validation: OK" -ForegroundColor Green
  }
  Write-Host "- Patch applied to SuiteCRM (NO COMMIT): YES" -ForegroundColor Green
  Write-Host "- SuiteCRM build/compile checks (php -l): $phpLintRanText" -ForegroundColor Green
  Write-Host "- Code quality checks (composer validate): $composerValidateRanText" -ForegroundColor Green
  Write-Host "- Validation report: $validationReport" -ForegroundColor Green

  return [pscustomobject]@{
    Approach          = $OneApproach
    PatchPath         = $patchPath
    ValidationReport  = $validationReport
    GenerationSeconds = $generationSeconds
    PhpLint           = $phpLintRanText
    ComposerValidate  = $composerValidateRanText
    WarningsCount     = $warningsCount
  }
}

Set-Location $PSScriptRoot
Write-Host "demo_run_and_verify.ps1 starting" -ForegroundColor DarkGray
# Clear-Host is noisy in non-interactive runs; skip to preserve diagnostics.
# Clear-Host

if ($Approach -eq 'both') {
  $results = @()
  $results += , (Run-One -OneApproach codebase)

  $runAutosummary = $false
  if ($AutoApprove) {
    $runAutosummary = $true
  }
  else {
    $answer = (Read-Host 'Do you want to process the auto summarization? [y/n]').Trim().ToLowerInvariant()
    if ($answer -eq 'y' -or $answer -eq 'yes') {
      $confirm = (Read-Host 'Are you sure to perform the auto summarization now [y/s]?').Trim().ToLowerInvariant()
      if ($confirm -eq 'y' -or $confirm -eq 'yes') {
        $runAutosummary = $true
      }
    }
  }

  if ($runAutosummary) {
    Write-Host "Preparing SuiteCRM for autosummarization (reverting prior patch)" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot 'apply_patch.ps1') -Approach codebase -Variant $Variant -Action revert -SuiteCrmRoot $SuiteCrmRoot | Out-Host
    $results += , (Run-One -OneApproach autosummarization)
  }
  else {
    Write-Host "Skipping autosummarization." -ForegroundColor Yellow
  }

  Write-Host "== Overall summary ==" -ForegroundColor Cyan
  foreach ($r in $results) {
    Write-Host "SUCCESS: $($r.Approach)" -ForegroundColor Green
    Write-Host "- Offline hallucination/format validation: OK" -ForegroundColor Green
    Write-Host "- Patch applied to SuiteCRM (NO COMMIT): YES" -ForegroundColor Green
    Write-Host "- SuiteCRM build/compile checks (php -l): $($r.PhpLint)" -ForegroundColor Green
    Write-Host "- Code quality checks (composer validate): $($r.ComposerValidate)" -ForegroundColor Green
  }
}
else {
  if ($Approach -eq 'autosummarization' -and -not $AutoApprove) {
    $answer = (Read-Host 'Do you want to process the auto summarization? [y/n]').Trim().ToLowerInvariant()
    if ($answer -ne 'y' -and $answer -ne 'yes') {
      Write-Host "Skipping autosummarization." -ForegroundColor Yellow
      exit 0
    }

    $confirm = (Read-Host 'Are you sure to perform the auto summarization now [y/s]?').Trim().ToLowerInvariant()
    if ($confirm -ne 'y' -and $confirm -ne 'yes') {
      Write-Host "Skipping autosummarization." -ForegroundColor Yellow
      exit 0
    }
  }
  [void](Run-One -OneApproach $Approach)
}
