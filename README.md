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

PowerShell helpers (run from the SuiteCRM root directory):
php cli/maintenance.php clear-cache
php cli/maintenance.php quick-repair
# Forward upgrade arguments to silentUpgrade.php
php cli/maintenance.php silent-upgrade <zip-file> <admin-user> <admin-pass> <log-dir> <patch-dir>
```

`clear-cache` purges caches and metadata. `quick-repair` performs a Quick Repair & Rebuild with database synchronization. `silent-upgrade` passes all arguments to `modules/UpgradeWizard/silentUpgrade.php` for unattended upgrades.

## Evaluating Errors and Hallucinations

1. **Compile the .NET project**: `dotnet build` verifies dependencies and API usage.
2. **Run the Python scripts**:
   - `python generate_from_codebase.py --sources <paths>`
   - `python generate_summary.py --sources <paths>`
   Inspect timing output and review generated artifacts for unexpected content.
3. **Validate SuiteCRM after automation**:
   - **Offline (no SuiteCRM install/DB required):**
     - `php -l <file.php>` for syntax checks on touched files
     - `php vendor/bin/phpstan --version` (or `php vendor/bin/phpstan analyse ...`) for static analysis
     - `git apply --check <patch>` before applying model-generated patches
   - **Installed/configured instance (requires `SuiteCRM/config.php` + DB):**
     - `php cli/maintenance.php status`
     - `php cli/maintenance.php quick-repair` and examine `suitecrm.log`
     - Execute relevant SuiteCRM smoke tests.
4. **Regression safeguards**:
   - Review diffs before committing generated code
   - Lower temperature (e.g., `--temperature 0.0`) to reduce variance for large prompts
   - If the .NET generator emits compilation errors, refine `prompt.txt` and rerun `dotnet run`

## Prompt Management

`prompt.txt` stores the request shared across the .NET application and Python utilities. Keep it up to date with the artifact description you need.
