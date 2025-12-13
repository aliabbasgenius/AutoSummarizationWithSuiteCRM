#!/usr/bin/env python3
"""Summarize SuiteCRM modules and persist structured JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from urllib.parse import urlparse

try:  # pragma: no cover - optional dependency
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover
    AzureOpenAI = None  # type: ignore

try:  # pragma: no cover
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None  # type: ignore

DEFAULT_PROMPT = (
    "You are preparing a concise architectural summary for SuiteCRM modules. "
    "Describe each module's purpose, dependencies, key classes or functions, business rules, and potential risks."
)
SUPPORTED_EXTENSIONS = (".php", ".js", ".ts", ".tpl")


def load_dotenv_fallback() -> None:
    """Best-effort .env loader (no external deps).

    This keeps the script usable even when python-dotenv isn't installed.
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

def accepted_prediction_tokens(usage: Any) -> int | None:
    try:
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        if not completion_details:
            return None
        return getattr(completion_details, "accepted_prediction_tokens", None)
    except Exception:
        return None


def run_summary_completion(
    client: Any,
    deployment: str,
    messages: list[Any],
    temperature: float,
    max_tokens: int,
) -> tuple[str, Any, int | None, float]:
    start = perf_counter()
    completion = client.chat.completions.create(
        model=deployment,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    elapsed = perf_counter() - start

    summary_text = completion.choices[0].message.content if completion.choices else ""
    finish_reason = completion.choices[0].finish_reason if completion.choices else None
    accepted_tokens = accepted_prediction_tokens(getattr(completion, "usage", None))
    return (summary_text or ""), finish_reason, accepted_tokens, elapsed

DEFAULT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate semantic summaries for SuiteCRM modules.")
    parser.add_argument(
        "--sources",
        nargs="+",
        help="Files or directories to summarize.",
    )
    parser.add_argument(
        "--output",
        default="code_summary.json",
        help="Path to the JSON file where the summary will be stored.",
    )
    parser.add_argument(
        "--run-log",
        default=os.getenv("PYTHON_RUN_LOG", ""),
        help="Optional JSONL file to append run timing + diagnostics.",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional run id to correlate with downstream steps (else a new uuid is generated).",
    )
    parser.add_argument(
        "--print-run-id",
        action="store_true",
        help="Print the run_id so it can be re-used in follow-up commands.",
    )
    parser.add_argument(
        "--chunk-bytes",
        type=int,
        default=80_000,
        help="Maximum number of bytes from the provided sources to feed into the model.",
    )
    parser.add_argument(
        "--deployment",
        default=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        help="Azure OpenAI chat deployment name.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AZURE_OPENAI_ENDPOINT"),
        help="Azure OpenAI endpoint.",
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
        "--temperature",
        type=float,
        default=float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.1")),
        help="Sampling temperature for the summarization model.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "900")),
        help="Maximum number of tokens to request from the model.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Custom natural-language prompt for summarization.",
    )
    return parser.parse_args()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def iter_source_files(paths: list[str]) -> Iterator[Path]:
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            for file_path in sorted(candidate.rglob("*")):
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield file_path
        elif candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield candidate


def gather_context(paths: list[str], budget: int) -> str:
    remaining = budget
    snippets: list[str] = []

    for file_path in iter_source_files(paths):
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1", errors="replace")

        encoded = text.encode("utf-8")
        chunk = encoded[:remaining].decode("utf-8", errors="ignore")
        snippets.append(f"// source: {file_path}\n{chunk}")
        remaining -= len(chunk.encode("utf-8"))
        if remaining <= 0:
            break

    return "\n\n".join(snippets)


def build_messages(prompt: str, context: str) -> list[Any]:
    return [
        {"role": "system", "content": "You summarize SuiteCRM modules in structured JSON."},
        {"role": "user", "content": f"{prompt}\n\nContext:\n{context}"},
    ]


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()  # pragma: no cover
    else:
        load_dotenv_fallback()

    args = parse_args()

    if not args.endpoint or not args.api_key or not args.deployment:
        raise ValueError("Azure OpenAI endpoint, key, and deployment must be configured.")

    args.endpoint = normalize_azure_endpoint(args.endpoint)

    context = gather_context(args.sources, args.chunk_bytes)
    if not context:
        raise ValueError("No readable source content found in the provided paths.")

    if AzureOpenAI is None:
        raise RuntimeError("The 'openai' package is required to call the Azure OpenAI API.")

    client = AzureOpenAI(
        api_key=args.api_key,
        azure_endpoint=args.endpoint,
        api_version=args.api_version,
    )
    messages = build_messages(args.prompt, context)

    run_id = (args.run_id or "").strip() or str(uuid.uuid4())
    run_started = datetime.now(timezone.utc).isoformat()

    summary_text, finish_reason, accepted_tokens, elapsed = run_summary_completion(
        client=client,
        deployment=args.deployment,
        messages=messages,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    run_finished = datetime.now(timezone.utc).isoformat()

    if not (summary_text or "").strip() and accepted_tokens == 0:
        raise RuntimeError(
            "The selected Azure OpenAI deployment returned only reasoning tokens and no output text. "
            "This typically happens with reasoning-only deployments on the Chat Completions API. "
            "Fix: deploy a chat-capable model (e.g., gpt-4o / gpt-4.1) and set AZURE_OPENAI_DEPLOYMENT to that deployment name."
        )

    if not (summary_text or "").strip():
        try:
            start_fallback = perf_counter()
            response = client.responses.create(
                model=args.deployment,
                input=messages,
                max_output_tokens=args.max_tokens,
            )
            elapsed_fallback = perf_counter() - start_fallback
            summary_text = (getattr(response, "output_text", "") or "").strip()
            finish_reason = f"responses_api (elapsed={elapsed_fallback:.2f}s)"
        except Exception:
            # Keep empty summary_text; diagnostics below will still help debugging.
            pass
    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": args.sources,
        "summary": (summary_text or "").strip(),
        "chunk_bytes": args.chunk_bytes,
        "duration_seconds": round(elapsed, 3),
        "diagnostics": {
            "deployment": args.deployment,
            "finish_reason": finish_reason,
            "summary_length": len((summary_text or "").strip()),
            "accepted_prediction_tokens": accepted_tokens,
        },
    }

    output_path = Path(args.output).resolve()
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if (args.run_log or "").strip():
        run_log_path = Path(args.run_log).expanduser()
        if not run_log_path.is_absolute():
            run_log_path = (Path.cwd() / run_log_path).resolve()
        append_jsonl(
            run_log_path,
            {
                "run_id": run_id,
                "tool": "generate_summary",
                "started_at_utc": run_started,
                "finished_at_utc": run_finished,
                "duration_seconds": round(elapsed, 3),
                "deployment": args.deployment,
                "api_version": args.api_version,
                "output_path": str(output_path),
                "sources": args.sources,
                "chunk_bytes": args.chunk_bytes,
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
                "diagnostics": payload.get("diagnostics", {}),
            },
        )

    print(f"Summary saved to {output_path}")
    if args.print_run_id:
        print(f"run_id: {run_id}")
    print(f"Execution time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
