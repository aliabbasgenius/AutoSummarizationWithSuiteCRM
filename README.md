# AutoSummarizationWithSuiteCRM

This folder (`LLMCodeGenerator/`) contains tooling to compare two LLM-driven approaches against the SuiteCRM codebase:

1) Full-context prompting (no summarization)
2) Auto-summarization prompting (summarize → aggregate → generate)

Generated artifacts are written into the SuiteCRM working tree by default under:

- `SuiteCRM/custom/LLMCodeGenerator/`

## Prerequisites

- .NET SDK 9.0 (Preview) available on the `PATH`
- Python 3.10+ with `pip`
- (Optional) PHP CLI if you want `php -l` validation
- Access to an Azure OpenAI resource with a chat-capable deployment

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

Note:
- If you copied a full URL like `.../openai/deployments/<deployment>/chat/completions?...`, set `AZURE_OPENAI_ENDPOINT` to the base resource URL (`https://<resource>.openai.azure.com/`).
- The Python scripts normalize full URLs automatically.
- The Python scripts prefer the deployment value from `.env` to avoid stale `setx AZURE_OPENAI_DEPLOYMENT` values.

## Approach A: .NET (`AzureOpenAICodeGen`)

From `LLMCodeGenerator/AzureOpenAICodeGen`:

```powershell
dotnet restore
dotnet run -- --prompt ..\prompt.txt --output ..\..\SuiteCRM\custom\LLMCodeGenerator\generated_code.txt
```

Refactor (writes a unified diff patch file; apply it manually to SuiteCRM):

```powershell
dotnet run -- --refactor ..\..\SuiteCRM\include\CleanCSV.php --output cleancsv_refactor.patch
```

Logs (local, ignored): `AzureOpenAICodeGen/runs/azure_openai_runs.jsonl`

## Approach B1: Python (full-context prompting)

Create venv once:

```powershell
cd LLMCodeGenerator\python
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Generate (writes `SuiteCRM/custom/LLMCodeGenerator/generated_code_raw.txt` by default):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --run-log .\runs\python_runs.jsonl --print-run-id
```

Generate into `SuiteCRM/modules/<ModuleName>/...` (creates module folder if missing):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --output-mode module --module-name LLMCodeGenCompare_Raw --run-log .\runs\python_runs.jsonl
```

## Approach B2: Python (auto-summarization prompting)

This script runs: module grouping → per-module hierarchical JSON summaries → aggregation → generation.

Generate (writes `SuiteCRM/custom/LLMCodeGenerator/generated_code_autosummary.txt` by default):

```powershell
python generate_from_codebase_and_auto_summarization.py --sources ..\..\SuiteCRM\include\CleanCSV.php --run-log .\runs\python_runs.jsonl --print-run-id
```

Generate into `SuiteCRM/modules/<ModuleName>/...`:

```powershell
python generate_from_codebase_and_auto_summarization.py --sources ..\..\SuiteCRM\include\CleanCSV.php --output-mode module --module-name LLMCodeGenCompare_AutoSummary --run-log .\runs\python_runs.jsonl
```

## Validation + summary-only helper

Validate generated outputs (offline validation; PHP lint is optional):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --run-log .\runs\python_runs.jsonl --validate --suitecrm-root ..\..\SuiteCRM --print-run-id
```

Summarize sources to JSON (writes `SuiteCRM/custom/LLMCodeGenerator/code_summary.json` by default):

```powershell
python generate_summary.py --sources ..\..\SuiteCRM\modules\Administration --run-log .\runs\python_runs.jsonl --print-run-id
```

Logs (local, ignored): `python/runs/python_runs.jsonl`
```
