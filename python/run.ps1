param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('raw', 'autosummary')]
  [string]$Approach,

  [Parameter(Mandatory = $true)]
  [string]$Prompt,

  [Parameter(Mandatory = $true)]
  [string[]]$Sources,

  [string[]]$ExtraContext = @(),

  # Optional override. Defaults to runs/latest_<approach>.patch
  [string]$Output = '',

  # These default from LLMCodeGenerator/.env when present.
  [int]$MaxContextBytes = 120000,
  [int]$MaxTokens = 1800,
  [double]$Temperature = 0,

  # Autosummary-only
  [int]$SummaryMaxTokens = 700,
  [double]$SummaryTemperature = 0
)

$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $scriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $pythonExe)) {
  throw "Python venv not found at $pythonExe. Create it with: cd LLMCodeGenerator\python; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

$runDir = Join-Path $scriptRoot 'runs'
if (-not (Test-Path $runDir)) {
  New-Item -ItemType Directory -Path $runDir | Out-Null
}

$suitecrmRoot = Join-Path $scriptRoot '..\SuiteCRM'
try {
  $suitecrmRoot = (Resolve-Path -Path $suitecrmRoot).Path
}
catch {
  # fall back to the joined path when SuiteCRM folder cannot be resolved
}

if ([string]::IsNullOrWhiteSpace($Output)) {
  $suffix = $(if ($Approach -eq 'raw') { 'codebase' } else { 'autosummarization' })
  $Output = Join-Path $runDir ("latest_{0}.patch" -f $suffix)
}

# Always overwrite patch outputs (never append/accumulate across runs).
if (Test-Path $Output) {
  Remove-Item -Force $Output
}

$runLogSuffix = $(if ($Approach -eq 'raw') { 'codebase' } else { 'autosummarization' })
$runLog = Join-Path $runDir ("latest_{0}.jsonl" -f $runLogSuffix)

$cmd = @(
  $pythonExe,
  (Join-Path $scriptRoot ($(if ($Approach -eq 'raw') { 'generate_from_codebase.py' } else { 'generate_from_codebase_and_auto_summarization.py' }))),
  '--prompt', $Prompt,
  '--sources'
)
$cmd += $Sources
$cmd += @(
  '--output', $Output,
  '--max-context-bytes', $MaxContextBytes,
  '--temperature', $Temperature,
  '--max-tokens', $MaxTokens,
  '--suitecrm-root', $suitecrmRoot,
  '--run-log', $runLog
)

if ($ExtraContext.Count -gt 0) {
  $cmd += '--extra-context'
  $cmd += $ExtraContext
}

if ($Approach -eq 'autosummary') {
  $cmd += @(
    '--summary-temperature', $SummaryTemperature,
    '--summary-max-tokens', $SummaryMaxTokens
  )
}

$runningLine = $(if ($Approach -eq 'raw') {
    'Running codebase code generator'
  }
  elseif ($Approach -eq 'autosummary') {
    'Running auto summarization code generator'
  }
  else {
    "Running $Approach"
  })
Write-Host $runningLine -ForegroundColor Cyan
Write-Host ($cmd -join ' ')

& $cmd[0] @($cmd[1..($cmd.Length - 1)])
