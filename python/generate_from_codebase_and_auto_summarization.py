#!/usr/bin/env python3
"""Generate code using Azure OpenAI with an auto-summarization prompting pipeline.

This script intentionally implements the *second* approach described in the thesis:
- Analyze codebase context
- Summarize per module in a hierarchical JSON format
- Aggregate summaries
- Use aggregated summary (plus small raw snippets) as the primary context for generation

For the *first* approach (full context prompting without summarization), use:
- generate_from_codebase.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

try:  # pragma: no cover
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover
    AzureOpenAI = None  # type: ignore

try:  # pragma: no cover
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None  # type: ignore

try:  # pragma: no cover
    from validate_generated_output import append_validation_run, validate_file
except Exception:  # pragma: no cover
    append_validation_run = None  # type: ignore
    validate_file = None  # type: ignore

# Reuse the non-autosummary core utilities from the raw script.
from generate_from_codebase import (  # noqa: E402
    DEFAULT_API_VERSION,
    DEFAULT_SYSTEM_PROMPT,
    FILE_BUNDLE_INSTRUCTIONS,
    OUTPUT_MODE_MODULE,
    OUTPUT_MODE_TEXT,
    SUPPORTED_EXTENSIONS,
    append_jsonl,
    default_suitecrm_root,
    load_dotenv_fallback,
    load_text,
    normalize_azure_endpoint,
    normalize_unified_diff_hunk_counts,
    parse_file_bundle,
    prefer_deployment_from_dotenv,
    repair_file_bundle_json,
    run_chat_completion,
    strip_markdown_fences,
    suitecrm_modules_root,
    utc_now_iso,
    write_module_files,
)

SUMMARY_SYSTEM_PROMPT = (
    "You are an assistant that summarizes a codebase for downstream code generation. "
    "Produce a semantic, hierarchical summary capturing module purpose, dependencies, key entities, and constraints. "
    "Output JSON only and no markdown fences."
)

SUMMARY_USER_PROMPT = (
    "Summarize the following SuiteCRM code in a semantic, hierarchical way for downstream code generation.\n"
    "Include:\n"
    "- module name and purpose\n"
    "- dependencies between modules/files\n"
    "- function- and class-level descriptions (inputs/outputs/side effects where visible)\n"
    "- key business logic and rules\n"
    "Return JSON only, matching the requested schema."
)


def default_suitecrm_output_path_autosummary() -> str:
    return str((default_suitecrm_root() / "custom" / "LLMCodeGenerator" / "generated_code_autosummary.txt").resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate SuiteCRM code using Azure OpenAI with auto-summarization prompting."
    )
    parser.add_argument(
        "--prompt",
        default=str(Path(__file__).resolve().parent.parent / "prompt.txt"),
        help="Path to the primary prompt file.",
    )
    parser.add_argument("--sources", nargs="*", default=[], help="Files or directories to use as context snippets.")
    parser.add_argument(
        "--extra-context",
        nargs="*",
        default=[],
        help="Optional additional context files to include verbatim.",
    )
    parser.add_argument(
        "--output",
        default=default_suitecrm_output_path_autosummary(),
        help="Destination path for the generated code.",
    )
    parser.add_argument(
        "--output-mode",
        choices=[OUTPUT_MODE_TEXT, OUTPUT_MODE_MODULE],
        default=OUTPUT_MODE_TEXT,
        help=(
            "Where to write output. 'text' writes a single file. "
            "'module' writes generated files into SuiteCRM/modules/<module-name>/..."
        ),
    )
    parser.add_argument(
        "--module-name",
        default="",
        help="(output-mode=module) SuiteCRM module folder name to write into (created if missing).",
    )
    parser.add_argument(
        "--modules-root",
        default="",
        help="(output-mode=module) Override SuiteCRM/modules root (defaults to SuiteCRM/modules).",
    )
    parser.add_argument(
        "--validate-written",
        action="store_true",
        help="(output-mode=module) Run offline validation on each written file (php -l + hallucination scans).",
    )
    parser.add_argument(
        "--run-log",
        default=os.getenv("PYTHON_RUN_LOG", ""),
        help="Optional JSONL file to append run timing + diagnostics.",
    )
    parser.add_argument("--run-id", default="", help="Optional run id (else a new uuid is generated).")
    parser.add_argument("--print-run-id", action="store_true", help="Print the run_id.")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run offline validation after generation and append results to the run log.",
    )
    parser.add_argument(
        "--suitecrm-root",
        default="../../SuiteCRM",
        help="SuiteCRM root for validation path checks (used only with --validate/--validate-written).",
    )
    parser.add_argument(
        "--no-php-lint",
        action="store_true",
        help="Skip php -l check during validation (used only with --validate/--validate-written).",
    )
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        default=40_000,
        help="(generation) Max bytes of raw snippets to include alongside the auto-summary.",
    )
    parser.add_argument(
        "--summary-max-context-bytes",
        type=int,
        default=120_000,
        help="(autosummary) Total bytes from source files to use during summarization (distributed per module).",
    )
    parser.add_argument(
        "--summary-temperature",
        type=float,
        default=0.0,
        help="(autosummary) Temperature for the summarization calls.",
    )
    parser.add_argument(
        "--summary-max-tokens",
        type=int,
        default=900,
        help="(autosummary) Max tokens per module summarization call.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.2")),
        help="(generation) Sampling temperature for the model.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "1200")),
        help="(generation) Maximum number of tokens to request from the model.",
    )
    parser.add_argument(
        "--deployment",
        default=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        help="Azure OpenAI chat deployment name.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AZURE_OPENAI_ENDPOINT"),
        help="Azure OpenAI endpoint (https://<resource>.openai.azure.com).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AZURE_OPENAI_KEY") or os.getenv("AZURE_OPENAI_API_KEY"),
        help="Azure OpenAI API key.",
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
        help="Azure OpenAI API version.",
    )
    parser.add_argument(
        "--system",
        default=os.getenv("AZURE_OPENAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        help="Override the default system prompt.",
    )
    return parser.parse_args()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def iter_source_files(paths: list[str], *, base_root: Path | None = None) -> Iterator[Path]:
    """Yield source files from provided paths.

    If a path does not exist relative to the current working directory, and a
    base_root is provided, we also try resolving it relative to base_root.
    """

    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute() and not candidate.exists() and base_root is not None:
            alt = (base_root / candidate).resolve()
            if alt.exists():
                candidate = alt

        if candidate.is_dir():
            for file_path in sorted(candidate.rglob("*")):
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield file_path.resolve()
        elif candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield candidate.resolve()


def _suitecrm_relative_path(file_path: Path, suitecrm_root: Path) -> Path:
    try:
        return file_path.resolve().relative_to(suitecrm_root.resolve())
    except Exception:
        return file_path


def derive_module_name(file_path: Path, suitecrm_root: Path) -> str:
    rel = _suitecrm_relative_path(file_path, suitecrm_root)
    parts = list(rel.parts)
    if len(parts) >= 2 and parts[0] == "modules":
        return f"modules/{parts[1]}"
    if len(parts) >= 3 and parts[0] == "custom" and parts[1] == "modules":
        return f"custom/modules/{parts[2]}"
    if parts:
        return parts[0].replace("\\", "/")
    return "(root)"


def extract_dependency_hints(source_text: str) -> dict[str, list[str]]:
    includes: list[str] = []
    uses: list[str] = []

    for match in re.finditer(
        r"\b(?:require_once|require|include_once|include)\s*\(?\s*['\"]([^'\"]+)['\"]",
        source_text,
    ):
        dep = match.group(1).strip()
        if dep:
            includes.append(dep)

    for match in re.finditer(r"\buse\s+([A-Za-z0-9_\\\\]+)\s*;", source_text):
        sym = match.group(1).strip()
        if sym:
            uses.append(sym)

    return {
        "includes": sorted(set(includes))[:50],
        "uses": sorted(set(uses))[:50],
    }


def gather_context(paths: list[str], byte_budget: int, *, base_root: Path | None = None) -> str:
    if not paths or byte_budget <= 0:
        return ""

    snippets: list[str] = []
    remaining = byte_budget

    for file_path in iter_source_files(paths, base_root=base_root):
        text = _safe_read_text(file_path)
        encoded = text.encode("utf-8")
        chunk = encoded[:remaining].decode("utf-8", errors="ignore")

        display_path: str
        if base_root is not None:
            try:
                display_path = str(file_path.relative_to(base_root))
            except Exception:
                display_path = str(file_path)
        else:
            display_path = str(file_path)

        snippets.append(
            textwrap.dedent(
                f"""// file: {display_path}
{chunk}
"""
            ).strip()
        )
        remaining -= len(chunk.encode("utf-8"))
        if remaining <= 0:
            break

    return "\n\n".join(snippets)


def summarize_modules_hierarchical(
    *,
    client: Any,
    deployment: str,
    source_files: list[Path],
    suitecrm_root: Path,
    total_context_budget_bytes: int,
    temperature: float,
    max_tokens: int,
) -> tuple[str, Any, int | None, dict[str, int], float]:
    start = perf_counter()

    modules: dict[str, list[Path]] = {}
    for file_path in source_files:
        module_name = derive_module_name(file_path, suitecrm_root)
        modules.setdefault(module_name, []).append(file_path)

    ordered_modules = sorted(modules.items(), key=lambda kv: kv[0])

    max_modules = int(os.getenv("AUTOSUMMARY_MAX_MODULES", "8"))
    max_files_per_module = int(os.getenv("AUTOSUMMARY_MAX_FILES_PER_MODULE", "8"))
    ordered_modules = ordered_modules[:max_modules]

    per_module_budget = max(2_000, int(total_context_budget_bytes / max(1, len(ordered_modules))))

    module_summaries: list[dict[str, Any]] = []
    aggregated_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    accepted_prediction_tokens: int | None = None
    finish_reason: Any = None

    schema_example = {
        "module": {"name": "", "purpose": ""},
        "dependencies": {"includes": [], "uses": [], "other_modules": []},
        "entities": [
            {
                "kind": "class|function|interface|trait|file",
                "name": "",
                "description": "",
                "inputs": [],
                "outputs": [],
                "side_effects": [],
            }
        ],
        "business_logic": [],
        "constraints": [],
    }

    for module_name, files in ordered_modules:
        files = sorted(files, key=lambda p: str(p))[:max_files_per_module]

        dep_hints: dict[str, Any] = {"includes": [], "uses": []}
        rel_files = [str(_suitecrm_relative_path(p, suitecrm_root)).replace("\\", "/") for p in files]

        for p in files:
            hints = extract_dependency_hints(_safe_read_text(p))
            dep_hints["includes"].extend(hints.get("includes", []))
            dep_hints["uses"].extend(hints.get("uses", []))
        dep_hints["includes"] = sorted(set(dep_hints["includes"]))[:80]
        dep_hints["uses"] = sorted(set(dep_hints["uses"]))[:80]

        context_snippets: list[str] = []
        remaining = per_module_budget
        for p in files:
            text = _safe_read_text(p)
            encoded = text.encode("utf-8")
            chunk = encoded[:remaining].decode("utf-8", errors="ignore")
            context_snippets.append(
                textwrap.dedent(
                    f"""// file: {_suitecrm_relative_path(p, suitecrm_root)}
{chunk}
"""
                ).strip()
            )
            remaining -= len(chunk.encode("utf-8"))
            if remaining <= 0:
                break

        module_context = "\n\n".join(context_snippets)

        messages: list[Any] = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{SUMMARY_USER_PROMPT}\n\n"
                    "Output must be a SINGLE JSON object.\n"
                    "Schema example (fill with real content):\n"
                    f"{json.dumps(schema_example, ensure_ascii=False)}\n\n"
                    f"Module name: {module_name}\n"
                    f"Files: {json.dumps(rel_files, ensure_ascii=False)}\n"
                    f"Dependency hints: {json.dumps(dep_hints, ensure_ascii=False)}\n\n"
                    f"Code context:\n{module_context}"
                ),
            },
        ]

        module_text, module_finish, module_accepted, module_usage, _ = run_chat_completion(
            client=client,
            deployment=deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

        finish_reason = module_finish
        accepted_prediction_tokens = module_accepted

        try:
            module_json = json.loads((module_text or "").strip())
        except Exception:
            module_json = {
                "module": {"name": module_name, "purpose": "(unparsed)"},
                "raw_text": (module_text or "").strip(),
            }

        if module_usage is not None:
            aggregated_usage["prompt_tokens"] += int(getattr(module_usage, "prompt_tokens", 0) or 0)
            aggregated_usage["completion_tokens"] += int(getattr(module_usage, "completion_tokens", 0) or 0)
            aggregated_usage["total_tokens"] += int(getattr(module_usage, "total_tokens", 0) or 0)

        module_summaries.append(module_json)

    aggregate = {
        "project": {
            "name": "SuiteCRM",
            "generated_at_utc": utc_now_iso(),
            "source_file_count": len(source_files),
            "summarized_module_count": len(module_summaries),
            "limits": {
                "max_modules": max_modules,
                "max_files_per_module": max_files_per_module,
                "total_context_budget_bytes": int(total_context_budget_bytes),
                "per_module_budget_bytes": int(per_module_budget),
            },
        },
        "modules": module_summaries,
    }

    elapsed = perf_counter() - start
    summary_text = json.dumps(aggregate, ensure_ascii=False, indent=2)
    return summary_text, finish_reason, accepted_prediction_tokens, aggregated_usage, elapsed


def build_messages(system_prompt: str, prompt: str, context: str) -> list[Any]:
    if context:
        user_prompt = f"Context:\n{context}\n\nTask:\n{prompt.strip()}"
    else:
        user_prompt = prompt.strip()

    return [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": user_prompt},
    ]


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()  # pragma: no cover
    else:
        load_dotenv_fallback()

    prefer_deployment_from_dotenv()

    args = parse_args()

    if not args.endpoint or not args.api_key or not args.deployment:
        raise ValueError("Azure OpenAI endpoint, key, and deployment must be configured.")

    if args.output_mode == OUTPUT_MODE_MODULE and not (args.module_name or "").strip():
        raise ValueError("--module-name is required when --output-mode=module")

    args.endpoint = normalize_azure_endpoint(args.endpoint)

    prompt_text = load_text(Path(args.prompt))

    extra_context_text = ""
    extra_chunks: list[str] = []
    for raw in args.extra_context or []:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Extra context file not found: {raw}")
        extra_chunks.append(f"// extra-context: {candidate}\n{load_text(candidate)}")

    if extra_chunks:
        extra_context_text = "\n\n".join(extra_chunks).strip()

    if AzureOpenAI is None:
        raise RuntimeError("The 'openai' package is required to call the Azure OpenAI API.")

    client = AzureOpenAI(
        api_key=args.api_key,
        azure_endpoint=args.endpoint,
        api_version=args.api_version,
    )

    run_id = (args.run_id or "").strip() or str(uuid.uuid4())
    run_started = utc_now_iso()

    suitecrm_root = Path(args.suitecrm_root).expanduser()
    if not suitecrm_root.is_absolute():
        suitecrm_root = (Path.cwd() / suitecrm_root).resolve()
    source_files = list(iter_source_files(args.sources, base_root=suitecrm_root))

    summary_text, summary_finish_reason, summary_accepted, summary_usage, summary_elapsed = summarize_modules_hierarchical(
        client=client,
        deployment=args.deployment,
        source_files=source_files,
        suitecrm_root=suitecrm_root,
        total_context_budget_bytes=int(args.summary_max_context_bytes),
        temperature=float(args.summary_temperature),
        max_tokens=int(args.summary_max_tokens),
    )

    raw_context = gather_context(args.sources, int(args.max_context_bytes), base_root=suitecrm_root)
    context_parts = ["Auto Summary (hierarchical JSON):\n" + (summary_text or "").strip()]
    if raw_context.strip():
        context_parts.append("Raw Context Snippets (reduced):\n" + raw_context)

    context = "\n\n".join(context_parts).strip()
    if extra_context_text:
        context = (extra_context_text + "\n\n" + context).strip()

    effective_prompt = prompt_text
    if args.output_mode == OUTPUT_MODE_MODULE:
        effective_prompt = f"{prompt_text.strip()}\n\n{FILE_BUNDLE_INSTRUCTIONS}"

    messages = build_messages(args.system, effective_prompt, context)
    response_format = {"type": "json_object"} if args.output_mode == OUTPUT_MODE_MODULE else None

    output_text, finish_reason, accepted_prediction_tokens, usage, elapsed = run_chat_completion(
        client=client,
        deployment=args.deployment,
        messages=messages,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        response_format=response_format,
    )

    output_text = strip_markdown_fences(output_text)

    run_finished = utc_now_iso()

    output_path: Path | None = None
    written_files: list[Path] | None = None

    if args.output_mode == OUTPUT_MODE_MODULE:
        try:
            files = parse_file_bundle(output_text)
        except Exception:
            repaired = repair_file_bundle_json(
                client=client,
                deployment=args.deployment,
                bad_output=output_text,
                max_tokens=int(args.max_tokens),
            )
            files = parse_file_bundle(repaired)

        modules_root = suitecrm_modules_root(args)
        module_name = (args.module_name or "").strip() or "LLMCodeGenerator_AutoSummary"
        written_files = write_module_files(modules_root=modules_root, module_name=module_name, files=files)
    else:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_output = normalize_unified_diff_hunk_counts(output_text)
        if (normalized_output or "").strip() and not normalized_output.endswith("\n"):
            normalized_output += "\n"
        output_path.write_text(normalized_output, encoding="utf-8")

    if (args.run_log or "").strip():
        run_log_path = Path(args.run_log).expanduser()
        if not run_log_path.is_absolute():
            run_log_path = (Path.cwd() / run_log_path).resolve()

        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        log_payload: dict[str, Any] = {
            "run_id": run_id,
            "parent_run_id": None,
            "tool": "generate_from_codebase_and_auto_summarization",
            "approach": "autosummary",
            "output_mode": args.output_mode,
            "started_at_utc": run_started,
            "finished_at_utc": run_finished,
            "duration_seconds": round(elapsed, 3),
            "deployment": args.deployment,
            "api_version": args.api_version,
            "output_path": str(output_path) if output_path is not None else "",
            "written_files": [str(p) for p in (written_files or [])],
            "sources": args.sources,
            "max_context_bytes": args.max_context_bytes,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "diagnostics": {
                "finish_reason": finish_reason,
                "accepted_prediction_tokens": accepted_prediction_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "output_length": len((output_text or "").strip()),
            },
            "autosummary": {
                "duration_seconds": round(float(summary_elapsed), 3),
                "summary_length": len((summary_text or "").strip()),
                "finish_reason": summary_finish_reason,
                "accepted_prediction_tokens": summary_accepted,
                "max_context_bytes": int(args.summary_max_context_bytes),
                "temperature": float(args.summary_temperature),
                "max_tokens": int(args.summary_max_tokens),
                "usage": {
                    "prompt_tokens": summary_usage.get("prompt_tokens"),
                    "completion_tokens": summary_usage.get("completion_tokens"),
                    "total_tokens": summary_usage.get("total_tokens"),
                },
            },
        }

        append_jsonl(run_log_path, log_payload)

        if args.validate and output_path is not None:
            if validate_file is None or append_validation_run is None:
                raise RuntimeError("Validation requested but validator helpers could not be imported.")

            suitecrm_root_path = Path(args.suitecrm_root).expanduser()
            if not suitecrm_root_path.is_absolute():
                suitecrm_root_path = (Path.cwd() / suitecrm_root_path).resolve()

            report, findings = validate_file(
                input_path=output_path,
                suitecrm_root=suitecrm_root_path,
                no_php_lint=bool(args.no_php_lint),
            )
            append_validation_run(
                run_log_path=run_log_path,
                run_id=run_id,
                input_path=output_path,
                suitecrm_root=suitecrm_root_path,
                report=report,
                findings=findings,
            )

        if args.output_mode == OUTPUT_MODE_MODULE and args.validate_written and written_files:
            if validate_file is None or append_validation_run is None:
                raise RuntimeError("--validate-written requested but validator helpers could not be imported.")

            suitecrm_root_path = Path(args.suitecrm_root).expanduser()
            if not suitecrm_root_path.is_absolute():
                suitecrm_root_path = (Path.cwd() / suitecrm_root_path).resolve()

            for file_path in written_files:
                report, findings = validate_file(
                    input_path=file_path,
                    suitecrm_root=suitecrm_root_path,
                    no_php_lint=bool(args.no_php_lint),
                )
                append_validation_run(
                    run_log_path=run_log_path,
                    run_id=run_id,
                    input_path=file_path,
                    suitecrm_root=suitecrm_root_path,
                    report=report,
                    findings=findings,
                )

    if args.output_mode == OUTPUT_MODE_MODULE:
        print(f"Generated module files: {len(written_files or [])}")
    else:
        print(f"Generated code saved to {output_path}")
    if args.print_run_id:
        print(f"run_id: {run_id}")
    print(f"Execution time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}")
        raise SystemExit(1)
