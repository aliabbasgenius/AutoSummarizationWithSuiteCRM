# Demo steps: Raw vs Auto-summary code generation

This is a step-by-step demo script you can follow in front of teachers.

## 0) Preconditions

- You already ran `composer install` inside `SuiteCRM/`.
- Your Azure OpenAI settings are in `LLMCodeGenerator/.env`:
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_API_KEY`
  - `AZURE_OPENAI_DEPLOYMENT`
  - `AZURE_OPENAI_API_VERSION`

## 1) Activate Python environment

```powershell
cd D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python
.\.venv\Scripts\Activate.ps1
```

## 2) Choose a prompt

Use the demo prompt file:
- `LLMCodeGenerator/python/tasks/ACTIVE_PROMPT.txt`

It forces a strict `git apply` compatible unified diff patch.

## 3) Run Approach A: RAW (full-context prompting)

```powershell
$py = "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\.venv\Scripts\python.exe"
$prompt = ".\tasks\ACTIVE_PROMPT.txt"
$src = "..\..\SuiteCRM\include\CleanCSV.php"

& $py .\generate_from_codebase.py `
  --prompt $prompt `
  --sources $src `
  --output .\runs\demo_codebase.patch `
  --run-log .\runs\demo_runs.jsonl `
  --validate --suitecrm-root ..\..\SuiteCRM --no-php-lint `
  --print-run-id
```

Expected artifacts:
- `LLMCodeGenerator/python/runs/demo_codebase.patch`
- `LLMCodeGenerator/python/runs/demo_runs.jsonl` (contains generation record + validation record)

## 4) Run Approach B: AUTOSUMMARY (summarize → aggregate → generate)

```powershell
$py = "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\.venv\Scripts\python.exe"
$prompt = ".\tasks\ACTIVE_PROMPT.txt"
$src = "..\..\SuiteCRM\include\CleanCSV.php"

& $py .\generate_from_codebase_and_auto_summarization.py `
  --prompt $prompt `
  --sources $src `
  --output .\runs\demo_autosummarization.patch `
  --run-log .\runs\demo_runs.jsonl `
  --validate --suitecrm-root ..\..\SuiteCRM --no-php-lint `
  --summary-temperature 0 --summary-max-tokens 700 `
  --print-run-id
```

Expected artifacts:
- `LLMCodeGenerator/python/runs/demo_autosummarization.patch`
- `LLMCodeGenerator/python/runs/demo_runs.jsonl` (contains generation record + validation record)

## 5) Manual validation (presentation-friendly)

### 5.1 Offline validator (already run by --validate)

Re-run explicitly if you want to show it:

```powershell
$py = "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\.venv\Scripts\python.exe"
$suite = "D:\Study\Projects\AutoSummarizationProject\SuiteCRM"
& $py .\validate_generated_output.py --input .\runs\demo_codebase.patch --suitecrm-root $suite --no-php-lint
& $py .\validate_generated_output.py --input .\runs\demo_autosummarization.patch --suitecrm-root $suite --no-php-lint
```

### 5.2 Check patches apply cleanly (no changes made)

```powershell
$repo = "D:\Study\Projects\AutoSummarizationProject\SuiteCRM"
git -C $repo apply --check "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\runs\demo_codebase.patch"
git -C $repo apply --check "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\runs\demo_autosummarization.patch"
```

### 5.3 (Optional) Apply patch + PHP lint + revert

```powershell
$repo = "D:\Study\Projects\AutoSummarizationProject\SuiteCRM"
$patch = "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\runs\demo_codebase.patch"

git -C $repo apply $patch
php -l "$repo\include\CleanCSV.php"

# Revert only the touched file so the repo stays clean
git -C $repo checkout -- include/CleanCSV.php
```

## 6) Summarize the run log (optional)

```powershell
$py = "D:\Study\Projects\AutoSummarizationProject\LLMCodeGenerator\python\.venv\Scripts\python.exe"
& $py .\summarize_run_logs.py --input .\runs\demo_runs.jsonl --last 20
```
