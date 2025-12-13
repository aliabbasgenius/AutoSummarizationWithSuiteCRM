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
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_TEMPERATURE=0.2
AZURE_OPENAI_MAX_TOKENS=1200
```

The .NET console app and both Python scripts automatically load `.env` files. If only `AZURE_OPENAI_API_KEY` is provided, it is mapped to the expected `AZURE_OPENAI_KEY` variable at runtime.

## .NET Azure OpenAI Code Generator

Project location: `AzureOpenAICodeGen`

```powershell
cd d:/Study/Projects/AutoSummarizationProject/LLMCodeGenerator/AzureOpenAICodeGen
# Restore dependencies
 dotnet restore
# Generate code using the default prompt
 dotnet run -- --prompt ../prompt.txt --output ./generated_code.txt
```

Arguments (all optional):

- `--prompt=<path>` overrides the prompt file
- `--output=<path>` sets the generated file destination
- `--refactor=<path>` generates a unified diff patch to refactor a specific file
- `--deployment=<name>` selects an Azure OpenAI deployment
- `--system=<text>` replaces the system prompt
- `--temperature=<value>` and `--maxTokens=<int>` tune sampling
- `--stats=1` prints a summary of the JSONL run log
- `--statsPath=<path>` reads a specific JSONL log file
Note: if `--output` is a relative path in refactor mode, the patch is written under `AzureOpenAICodeGen/runs/patches/` to keep artifacts organized.

Apply a generated patch (review first):

```powershell
cd d:/Study/Projects/AutoSummarizationProject/SuiteCRM
# Optional sanity check (no changes applied)
git apply --check ..\LLMCodeGenerator\AzureOpenAICodeGen\runs\patches\cleancsv_refactor_v4.patch
# Apply it
git apply ..\LLMCodeGenerator\AzureOpenAICodeGen\runs\patches\cleancsv_refactor_v4.patch
```

## Python Utilities

Create a virtual environment and install dependencies once:

```powershell
cd d:/Study/Projects/AutoSummarizationProject/LLMCodeGenerator/python
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 1. Direct Code Generation

```powershell
cd d:/Study/Projects/AutoSummarizationProject/LLMCodeGenerator/python
python generate_from_codebase.py --sources ..\..\SuiteCRM\include --output generated_code_python.txt --run-log .\runs\python_runs.jsonl
```

Validate the generated output for common hallucinations / placeholders (offline):

```powershell
python validate_generated_output.py --input generated_code_python.txt --suitecrm-root ..\..\SuiteCRM --report .\runs\generated_code_python.validation.json
```

Key switches:

- `--prompt <path>`: custom prompt text
- `--sources <paths>`: files or directories used as context
- `--max-context-bytes <int>`: byte budget for contextual snippets

Execution time is printed at completion.

### 2. Summarization to JSON

```

The resulting JSON captures metadata, timing, and the model summary payload.

### 3. SuiteCRM Agent Workflow

An extensible agent lives under `python/suitecrm_agent`. It combines automated SuiteCRM module summarization with code generation prompts.

```powershell
cd d:/Study/Projects/AutoSummarizationProject/LLMCodeGenerator/python
python -m suitecrm_agent tasks/example_task.yaml --output agent_run.json
```

- Ensure the `SUITECRM_ROOT` environment variable points to your SuiteCRM clone (defaults to `../../SuiteCRM`).
- Provide a YAML or JSON task file describing `description`, optional `objectives`, and `target_modules`.
- The agent refreshes cached summaries under `python/suitecrm_agent/summaries/`, then optionally calls Azure OpenAI to suggest SuiteCRM patches.
- Results are stored in the specified `--output` location as structured JSON plans and diffs.

## SuiteCRM CLI Integration

# AutoSummarizationWithSuiteCRM

This repo focuses on two approaches for auto-summarization-driven code generation/refactoring against a SuiteCRM codebase:

1) **.NET console tool**: `AzureOpenAICodeGen/`
2) **Python scripts + agent**: `python/` and `python/suitecrm_agent/`

Generated outputs and local logs are intentionally kept out of Git (see `.gitignore`).

## Prerequisites

- .NET SDK 9.0
- Python 3.10+
- PHP CLI (for offline `php -l` syntax checks)
- Azure OpenAI resource + a chat-capable deployment (example: `gpt-4o-mini`)

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

Or create `LLMCodeGenerator/.env`:

```
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview
AZURE_OPENAI_TEMPERATURE=0.2
AZURE_OPENAI_MAX_TOKENS=1200
```

## Approach A: .NET (`AzureOpenAICodeGen`)

From `LLMCodeGenerator/AzureOpenAICodeGen`:

```powershell
dotnet restore
dotnet run -- --prompt ..\prompt.txt --output .\generated_code.txt
```

Refactor (writes a unified diff patch):

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

Generate code from SuiteCRM context:

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --output generated_code_python.txt --run-log .\runs\python_runs.jsonl --print-run-id
```

Generate + validate (same `run_id` logged for both steps):

```powershell
python generate_from_codebase.py --sources ..\..\SuiteCRM\include\CleanCSV.php --output generated_code_python.txt --run-log .\runs\python_runs.jsonl --validate --suitecrm-root ..\..\SuiteCRM --print-run-id
```

Summarize a module to JSON:

```powershell
python generate_summary.py --sources ..\..\SuiteCRM\modules\Administration --output code_summary.json --run-log .\runs\python_runs.jsonl --print-run-id
```

Validate a generated artifact (offline checks + optional `php -l`):

```powershell
python validate_generated_output.py --input generated_code_python.txt --suitecrm-root ..\..\SuiteCRM --report .\runs\generated_code_python.validation.json
```

Logs (local, ignored): `python/runs/python_runs.jsonl`

## Python Agent (`python/suitecrm_agent`)

```powershell
cd LLMCodeGenerator\python
python -m suitecrm_agent tasks\example_task.yaml --output agent_run.json
```

Agent logs (local, ignored): `python/suitecrm_agent/runs/agent_runs.jsonl`

## Compare runs

```powershell
cd LLMCodeGenerator\python
python summarize_run_logs.py
```
