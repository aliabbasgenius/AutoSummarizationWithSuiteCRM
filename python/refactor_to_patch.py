#!/usr/bin/env python3
"""Generate a git-apply patch by asking the LLM for full updated file content.

This avoids relying on the model to format a correct unified diff.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

try:
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover
    AzureOpenAI = None  # type: ignore

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None  # type: ignore

import difflib
import re


class ModelOutputError(RuntimeError):
    pass


def normalize_azure_endpoint(raw: str) -> str:
    if not raw:
        return raw
    raw = raw.strip()
    if not raw:
        return raw

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc and parsed.netloc.endswith(".openai.azure.com"):
        return f"{parsed.scheme}://{parsed.netloc}/"

    if ".openai.azure.com" in raw and "://" not in raw:
        host = raw.split("/", 1)[0]
        return f"https://{host}/"

    return raw


def strip_markdown_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # drop first fence line
        lines = lines[1:]
        # drop last fence line if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask Azure OpenAI for updated file content, then emit a patch.")
    parser.add_argument("--target", required=True, help="Path to the file to refactor.")
    parser.add_argument("--prompt", required=True, help="Path to a prompt text file.")
    parser.add_argument("--output", required=True, help="Where to write the unified diff patch.")
    parser.add_argument("--extra-context", nargs="*", default=[], help="Optional extra context files (e.g. summaries).")

    parser.add_argument(
        "--mode",
        choices=["auto", "full-file", "function"],
        default="auto",
        help="How to ask the model for changes. 'function' is more reliable for large files.",
    )
    parser.add_argument(
        "--function-name",
        default=os.getenv("REFACTOR_FUNCTION_NAME", "getBacktraceString"),
        help="Function to refactor when using --mode=function (or as fallback in auto).",
    )

    parser.add_argument("--deployment", default=os.getenv("AZURE_OPENAI_DEPLOYMENT"), help="Azure OpenAI deployment.")
    parser.add_argument("--endpoint", default=os.getenv("AZURE_OPENAI_ENDPOINT"), help="Azure OpenAI endpoint.")
    parser.add_argument(
        "--api-key",
        default=os.getenv("AZURE_OPENAI_KEY") or os.getenv("AZURE_OPENAI_API_KEY"),
        help="Azure OpenAI API key.",
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
        help="Azure OpenAI API version.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.0")),
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "6000")),
        help="Maximum tokens to request.",
    )
    return parser.parse_args()


def _validate_full_php_output(original_text: str, new_text: str, required_function: str | None) -> None:
    if not new_text.strip():
        raise ModelOutputError("Model returned empty updated file content.")

    if original_text.lstrip().startswith("<?php") and not new_text.lstrip().startswith("<?php"):
        raise ModelOutputError("Model did not return the full PHP file (missing '<?php' header).")

    if required_function and (required_function in original_text) and (required_function not in new_text):
        raise ModelOutputError("Model output appears incomplete (missing target function).")


def _validate_function_output(function_name: str, function_text: str) -> None:
    t = (function_text or "").strip()
    if not t:
        raise ModelOutputError("Model returned empty function content.")
    if "<?php" in t:
        raise ModelOutputError("Model returned a full file; expected function-only output.")
    if re.search(rf"\bfunction\s+{re.escape(function_name)}\b", t) is None:
        raise ModelOutputError(f"Model output does not contain function '{function_name}'.")
    if "{" not in t or "}" not in t:
        raise ModelOutputError("Model function output is missing braces.")


def _find_php_function_span(text: str, function_name: str) -> tuple[int, int]:
    # Find the function keyword + name. Allow modifiers like public/static.
    m = re.search(
        rf"(^|[^\w])(?:public\s+|protected\s+|private\s+)?(?:static\s+)?function\s+{re.escape(function_name)}\s*\(",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        raise ValueError(f"Could not find function '{function_name}' in target file.")

    # Start exactly at the 'function' keyword, not at the preceding delimiter.
    function_kw = text.lower().find("function", m.start(0), m.end(0))
    start = function_kw if function_kw != -1 else m.start(0)

    # Find opening brace for the function body.
    brace_open = text.find("{", m.end(0))
    if brace_open == -1:
        raise ValueError(f"Could not find opening '{{' for function '{function_name}'.")

    i = brace_open
    depth = 0
    in_sq = False
    in_dq = False
    in_line_comment = False
    in_block_comment = False
    escape = False

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_sq:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_dq = False
            i += 1
            continue

        # Start comments
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "#":
            in_line_comment = True
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        # Start strings
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1

        i += 1

    raise ValueError(f"Could not find end of function '{function_name}' (brace mismatch).")


def _replace_php_function(original_text: str, function_name: str, new_function_text: str) -> str:
    start, end = _find_php_function_span(original_text, function_name)
    replacement = new_function_text.strip()
    return original_text[:start] + replacement + original_text[end:]


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()  # pragma: no cover

    args = parse_args()

    if AzureOpenAI is None:
        raise RuntimeError("The 'openai' package is required.")

    if not args.endpoint or not args.api_key or not args.deployment:
        raise ValueError("Azure OpenAI endpoint, key, and deployment must be configured.")

    endpoint = normalize_azure_endpoint(args.endpoint)

    target_path = Path(args.target).resolve()
    if not target_path.exists() or not target_path.is_file():
        raise FileNotFoundError(f"Target file not found: {target_path}")

    suitecrm_root = Path(os.getenv("SUITECRM_ROOT", "../../SuiteCRM")).resolve()
    try:
        rel_path = target_path.relative_to(suitecrm_root).as_posix()
    except Exception:
        rel_path = target_path.name

    original_text = load_text(target_path)
    prompt_text = load_text(Path(args.prompt).resolve())

    extra_blocks: list[str] = []
    for raw in args.extra_context or []:
        p = Path(raw)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Extra context file not found: {raw}")
        extra_blocks.append(f"// extra-context: {p}\n{load_text(p)}")

    extra_context = "\n\n".join(extra_blocks).strip()

    def call_model(system: str, user: str) -> str:
        completion = client.chat.completions.create(
            model=args.deployment,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=args.temperature,
            max_completion_tokens=args.max_tokens,
        )
        text = completion.choices[0].message.content if completion.choices else ""
        return strip_markdown_fences(text)

    client = AzureOpenAI(api_key=args.api_key, azure_endpoint=endpoint, api_version=args.api_version)

    start = perf_counter()

    new_text = ""
    mode_used = args.mode
    function_name = (args.function_name or "").strip()

    def full_file_request() -> str:
        system = (
            "You are an expert SuiteCRM engineer. "
            "Return ONLY the FULL updated file contents for the target file. "
            "Do not omit any lines. "
            "No markdown fences, no explanations."
        )
        user = (
            f"Target path (repo-relative): {rel_path}\n\n"
            f"Instructions:\n{prompt_text.strip()}\n\n"
            + (f"Extra context:\n{extra_context}\n\n" if extra_context else "")
            + "Current file contents:\n"
            + original_text
            + "\n"
        )
        return call_model(system, user)

    def function_only_request() -> str:
        system = (
            "You are an expert SuiteCRM engineer. "
            "Return ONLY the updated PHP function definition requested. "
            "Do not return the full file. "
            "No markdown fences, no explanations."
        )
        user = (
            f"Target path (repo-relative): {rel_path}\n"
            f"Function to refactor: {function_name}\n\n"
            f"Instructions:\n{prompt_text.strip()}\n\n"
            + (f"Extra context:\n{extra_context}\n\n" if extra_context else "")
            + "Current file contents:\n"
            + original_text
            + "\n\n"
            + "Return ONLY the full updated function definition (signature + body)."
        )
        return call_model(system, user)

    if args.mode in {"full-file", "auto"}:
        try:
            new_text = full_file_request()
            _validate_full_php_output(original_text, new_text, f"function {function_name}" if function_name else None)
        except ModelOutputError:
            if args.mode == "full-file":
                raise
            mode_used = "function"

    if mode_used == "function":
        if not function_name:
            raise ValueError("--function-name is required for --mode=function")
        function_text = function_only_request()
        _validate_function_output(function_name, function_text)
        new_text = _replace_php_function(original_text, function_name, function_text)

    elapsed = perf_counter() - start

    original_lines = original_text.splitlines(keepends=False)
    new_lines = new_text.splitlines(keepends=False)

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
    )

    if len(diff_lines) <= 0:
        # No changes
        diff_text = ""
    else:
        diff_text = "diff --git a/{0} b/{0}\n".format(rel_path) + "\n".join(diff_lines) + "\n"

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write patches with LF newlines so `git apply` works reliably regardless
    # of Windows default CRLF translation.
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(diff_text)

    print(f"Patch written to {out_path}")
    print(f"Mode used: {mode_used}")
    print(f"Execution time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
