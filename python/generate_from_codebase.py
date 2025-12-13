#!/usr/bin/env python3
"""Generate code using Azure OpenAI with SuiteCRM-aware context."""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import json
from datetime import datetime, timezone
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from urllib.parse import urlparse

try:  # pragma: no cover - optional dependency resolution
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover - helpful message at runtime
    AzureOpenAI = None  # type: ignore

try:  # pragma: no cover - optional import for in-process validation
    from validate_generated_output import append_validation_run, validate_file
except Exception:  # pragma: no cover
    append_validation_run = None  # type: ignore
    validate_file = None  # type: ignore

try:  # pragma: no cover
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None  # type: ignore

DEFAULT_SYSTEM_PROMPT = (
    "You are an assistant that uses SuiteCRM project context to craft precise, production-ready code. "
    "Respond only with code unless you are explicitly asked to explain. "
    "Do not wrap the code in markdown fences."
)
SUPPORTED_EXTENSIONS = (".php", ".js", ".ts", ".tpl")

DEFAULT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


def load_dotenv_fallback() -> None:
    """Best-effort .env loader (no external deps).

    This keeps the scripts usable even when python-dotenv isn't installed.
    Priority:
    1) existing environment variables
    2) LLMCodeGenerator/.env
    3) repo-root .env
    """

    candidates = [
        Path(__file__).resolve().parents[1] / ".env",  # LLMCodeGenerator/.env
        Path(__file__).resolve().parents[2] / ".env",  # repo root .env (if present)
    ]

    for env_path in candidates:
        if not env_path.exists() or not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def normalize_azure_endpoint(raw: str) -> str:
    """Normalize Azure OpenAI endpoint values.

    Users sometimes paste the full Chat Completions URL. The OpenAI SDK expects the
    base resource endpoint: https://<resource>.openai.azure.com/
    """
    if not raw:
        return raw

    raw = raw.strip()
    if not raw:
        return raw

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc and parsed.netloc.endswith(".openai.azure.com"):
        return f"{parsed.scheme}://{parsed.netloc}/"

    # Handle inputs without scheme (rare).
    if ".openai.azure.com" in raw and "://" not in raw:
        host = raw.split("/", 1)[0]
        return f"https://{host}/"

    return raw


def strip_markdown_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SuiteCRM code snippets using Azure OpenAI.")
    parser.add_argument(
        "--prompt",
        default=str(Path(__file__).resolve().parent.parent / "prompt.txt"),
        help="Path to the primary prompt file.",
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        default=[],
        help="Files or directories to use as context snippets.",
    )
    parser.add_argument(
        "--extra-context",
        nargs="*",
        default=[],
        help="Optional additional context files to include verbatim (e.g., generated summaries).",
    )
    parser.add_argument(
        "--output",
        default="generated_code_python.txt",
        help="Destination path for the generated code.",
    )
    parser.add_argument(
        "--run-log",
        default=os.getenv("PYTHON_RUN_LOG", ""),
        help="Optional JSONL file to append run timing + diagnostics.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id to correlate with downstream validation (else a new uuid is generated).",
    )
    parser.add_argument(
        "--print-run-id",
        action="store_true",
        help="Print the run_id so it can be re-used in follow-up commands.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run offline validation after generation and append results to the run log.",
    )
    parser.add_argument(
        "--suitecrm-root",
        default="../../SuiteCRM",
        help="SuiteCRM root for validation path checks (used only with --validate).",
    )
    parser.add_argument(
        "--no-php-lint",
        action="store_true",
        help="Skip php -l check during validation (used only with --validate).",
    )
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        default=60_000,
        help="Maximum number of bytes to include from source files.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.2")),
        help="Sampling temperature for the model.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "1200")),
        help="Maximum number of tokens to request from the model.",
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
        help="Azure OpenAI API version (e.g., 2024-06-01).",
    )
    parser.add_argument(
        "--system",
        default=os.getenv("AZURE_OPENAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        help="Override the default system prompt.",
    )
    return parser.parse_args()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_text(path: Path) -> str:
    if not path.exists():  # pragma: no cover - runtime validation
        raise FileNotFoundError(f"Prompt file not found at {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def iter_source_files(paths: list[str]) -> Iterator[Path]:
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            for file_path in sorted(candidate.rglob("*")):
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield file_path
        elif candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield candidate


def gather_context(paths: list[str], byte_budget: int) -> str:
    if not paths or byte_budget <= 0:
        return ""

    snippets: list[str] = []
    remaining = byte_budget

    for file_path in iter_source_files(paths):
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1", errors="replace")

        encoded = text.encode("utf-8")
        chunk = encoded[:remaining].decode("utf-8", errors="ignore")
        snippets.append(
            textwrap.dedent(
                f"""// file: {file_path}
{chunk}
"""
            ).strip()
        )
        remaining -= len(chunk.encode("utf-8"))
        if remaining <= 0:
            break

    return "\n\n".join(snippets)


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

    args = parse_args()

    if not args.endpoint or not args.api_key or not args.deployment:
        raise ValueError(
            "Azure OpenAI endpoint, key, and deployment must be configured via arguments or environment."
        )

    args.endpoint = normalize_azure_endpoint(args.endpoint)

    prompt_text = load_text(Path(args.prompt))
    context = gather_context(args.sources, args.max_context_bytes)

    extra_chunks: list[str] = []
    for raw in args.extra_context or []:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Extra context file not found: {raw}")
        extra_chunks.append(f"// extra-context: {candidate}\n{load_text(candidate)}")

    if extra_chunks:
        context = ("\n\n".join(extra_chunks) + ("\n\n" + context if context else "")).strip()

    if AzureOpenAI is None:
        raise RuntimeError("The 'openai' package is required to call the Azure OpenAI API.")

    client = AzureOpenAI(
        api_key=args.api_key,
        azure_endpoint=args.endpoint,
        api_version=args.api_version,
    )
    messages = build_messages(args.system, prompt_text, context)

    run_id = (args.run_id or "").strip() or str(uuid.uuid4())
    run_started = utc_now_iso()
    start = perf_counter()
    response = client.chat.completions.create(
        model=args.deployment,
        messages=messages,
        temperature=args.temperature,
        max_completion_tokens=args.max_tokens,
    )
    elapsed = perf_counter() - start
    run_finished = utc_now_iso()

    output_text = response.choices[0].message.content if response.choices else ""
    output_text = strip_markdown_fences(output_text)
    finish_reason = response.choices[0].finish_reason if response.choices else None
    usage = getattr(response, "usage", None)
    try:
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        accepted_prediction_tokens = (
            getattr(completion_details, "accepted_prediction_tokens", None) if completion_details else None
        )
    except Exception:
        accepted_prediction_tokens = None

    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None

    if not (output_text or "").strip() and accepted_prediction_tokens == 0:
        raise RuntimeError(
            "The selected Azure OpenAI deployment returned only reasoning tokens and no output text. "
            "Fix: deploy a chat-capable model (e.g., gpt-4o / gpt-4.1) and set AZURE_OPENAI_DEPLOYMENT to that deployment name."
        )
    output_path = Path(args.output).resolve()
    output_path.write_text(output_text, encoding="utf-8")

    if (args.run_log or "").strip():
        run_log_path = Path(args.run_log).expanduser()
        if not run_log_path.is_absolute():
            run_log_path = (Path.cwd() / run_log_path).resolve()
        append_jsonl(
            run_log_path,
            {
                "run_id": run_id,
                "tool": "generate_from_codebase",
                "started_at_utc": run_started,
                "finished_at_utc": run_finished,
                "duration_seconds": round(elapsed, 3),
                "deployment": args.deployment,
                "api_version": args.api_version,
                "output_path": str(output_path),
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
            },
        )

        if args.validate:
            if validate_file is None or append_validation_run is None:
                raise RuntimeError(
                    "Validation requested but validator helpers could not be imported. "
                    "Ensure validate_generated_output.py is present and importable."
                )

            suitecrm_root = Path(args.suitecrm_root).expanduser()
            if not suitecrm_root.is_absolute():
                suitecrm_root = (Path.cwd() / suitecrm_root).resolve()

            report, findings = validate_file(
                input_path=output_path,
                suitecrm_root=suitecrm_root,
                no_php_lint=bool(args.no_php_lint),
            )
            append_validation_run(
                run_log_path=run_log_path,
                run_id=run_id,
                input_path=output_path,
                suitecrm_root=suitecrm_root,
                report=report,
                findings=findings,
            )

    print(f"Generated code saved to {output_path}")
    if args.print_run_id:
        print(f"run_id: {run_id}")
    print(f"Execution time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - surface error to CLI
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
