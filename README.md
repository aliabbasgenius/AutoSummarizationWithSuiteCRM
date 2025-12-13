# AutoSummarizationWithSuiteCRM

This workspace combines a .NET 9 console application, Python automation scripts, and SuiteCRM CLI helpers to streamline Azure OpenAI assisted code generation.

## Prerequisites

- .NET SDK 9.0 (Preview) available on the `PATH`
- Python 3.10 or later with `pip`
- PHP CLI for SuiteCRM maintenance commands
- Access to an Azure OpenAI resource with a chat-capable deployment

Set the following environment variables before running any generators:

```powershell
setx AZURE_OPENAI_API_VERSION "2025-01-01-preview"
setx AZURE_OPENAI_TEMPERATURE "0.2"
setx AZURE_OPENAI_MAX_TOKENS "1200"
```

> Tip: restart the terminal after calling `setx` so the values are available.

If you copied a full URL like `.../openai/deployments/<deployment>/chat/completions?...`, set `AZURE_OPENAI_ENDPOINT` to just the base resource URL (`https://<resource>.openai.azure.com/`). The Python tooling also accepts the full URL and will normalize it automatically.

Alternatively, create a `.env` file in the repository root containing values such as:

```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<api-key>

# AutoSummarizationWithSuiteCRM

This folder (`LLMCodeGenerator/`) contains tooling to run two approaches for auto-summarization-driven generation/refactoring against a SuiteCRM codebase:

1) **.NET console tool**: `AzureOpenAICodeGen/`
2) **Python scripts + agent**: `python/` and `python/suitecrm_agent/`

By default, generated artifacts are written into the SuiteCRM working tree under:

- `SuiteCRM/custom/LLMCodeGenerator/`

Note: SuiteCRM is a separate repo in your workspace, so writing there will create untracked/modified files in that repo.

## Configuration

Set environment variables (Windows PowerShell):

```powershell
setx AZURE_OPENAI_ENDPOINT "https://<your-resource>.openai.azure.com/"
setx AZURE_OPENAI_KEY "<api-key>"
setx AZURE_OPENAI_DEPLOYMENT "gpt-4o-mini"
setx AZURE_OPENAI_API_VERSION "2025-01-01-preview"
setx AZURE_OPENAI_TEMPERATURE "0.2"
setx AZURE_OPENAI_MAX_TOKENS "1200"
```

Or create `LLMCodeGenerator/.env` with the same values.

## Approach A: .NET (`AzureOpenAICodeGen`)

From `LLMCodeGenerator/AzureOpenAICodeGen`:

```powershell
dotnet restore
dotnet run -- --prompt ..\prompt.txt --output ..\..\SuiteCRM\custom\LLMCodeGenerator\generated_code.txt
```

Refactor (writes a unified diff patch file; you apply it manually to SuiteCRM):

```powershell
dotnet run -- --refactor ..\..\SuiteCRM\include\CleanCSV.php --output cleancsv_refactor.patch
```

Logs (local, ignored): `AzureOpenAICodeGen/runs/azure_openai_runs.jsonl`

## Approach B: Python scripts (`python/`)

Create venv once:

```powershell
cd LLMCodeGenerator\python
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Generate code from SuiteCRM context (default output goes to `SuiteCRM/custom/LLMCodeGenerator/generated_code_python.txt`):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --run-log .\runs\python_runs.jsonl --print-run-id
```

Generate + validate (same `run_id` logged for both steps):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --run-log .\runs\python_runs.jsonl --validate --suitecrm-root ..\..\SuiteCRM --print-run-id
```

Summarize a module to JSON (default output goes to `SuiteCRM/custom/LLMCodeGenerator/code_summary.json`):

```powershell
python generate_summary.py --sources ..\..\SuiteCRM\modules\Administration --run-log .\runs\python_runs.jsonl --print-run-id
```

Logs (local, ignored): `python/runs/python_runs.jsonl`
```
