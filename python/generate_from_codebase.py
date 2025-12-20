#!/usr/bin/env python3
"""Generate code using Azure OpenAI with SuiteCRM-aware context."""

from __future__ import annotations

import argparse
import base64
import os
import re
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

FILE_BUNDLE_INSTRUCTIONS = (
    "Return a SINGLE JSON object (no markdown fences).\n"
    "Schema:\n"
    "{\n"
    "  \"files\": [\n"
    "    {\n"
    "      \"path\": \"<relative path under the module>\",\n"
    "      \"content_lines\": [\"line 1\", \"line 2\", \"...\"]\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- paths must be relative and must NOT start with '/' or contain '..'\n"
    "- content_lines must be an array of strings; do not embed unescaped newlines in a JSON string\n"
    "- include ALL necessary files for the change (e.g., PHP, metadata, language strings)\n"
    "- output JSON only; no commentary."
)

APPROACH_RAW = "raw"
OUTPUT_MODE_TEXT = "text"
OUTPUT_MODE_MODULE = "module"
SUPPORTED_EXTENSIONS = (".php", ".js", ".ts", ".tpl")

DEFAULT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


def _dotenv_candidates() -> list[Path]:
    return [
        Path(__file__).resolve().parents[1] / ".env",  # LLMCodeGenerator/.env
        Path(__file__).resolve().parents[2] / ".env",  # repo root .env (if present)
    ]


def _read_dotenv_value(key: str) -> str | None:
    for env_path in _dotenv_candidates():
        if not env_path.exists() or not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k != key:
                continue
            value = v.strip().strip('"').strip("'")
            return value if value else None
    return None


def prefer_deployment_from_dotenv() -> None:
    """Prefer Azure OpenAI settings from repo-local .env over machine/user env vars.

    This avoids stale `setx ...` values overriding the intended repo-local
    configuration.

    Note: CLI args still take precedence over environment variables.
    """

    # Keep AZURE_OPENAI_KEY and AZURE_OPENAI_API_KEY in sync.
    api_key = _read_dotenv_value("AZURE_OPENAI_API_KEY") or _read_dotenv_value("AZURE_OPENAI_KEY")
    if api_key:
        os.environ["AZURE_OPENAI_API_KEY"] = api_key
        os.environ["AZURE_OPENAI_KEY"] = api_key

    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION"):
        value = _read_dotenv_value(key)
        if value:
            os.environ[key] = value


def default_suitecrm_root() -> Path:
    return Path(__file__).resolve().parents[2] / "SuiteCRM"


def default_suitecrm_output_path() -> str:
    # Write generated artifacts into the SuiteCRM working tree by default.
    # This makes it obvious that the tool is producing SuiteCRM-relevant output.
    return str((default_suitecrm_root() / "custom" / "LLMCodeGenerator" / "generated_code_raw.txt").resolve())


def load_dotenv_fallback() -> None:
    """Best-effort .env loader (no external deps).

    This keeps the scripts usable even when python-dotenv isn't installed.
    Priority:
    1) existing environment variables
    2) LLMCodeGenerator/.env
    3) repo-root .env
    """

    candidates = _dotenv_candidates()

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


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def normalize_unified_diff_hunk_counts(text: str) -> str:
    """Fix incorrect hunk line-counts in unified diffs.

    Some models emit valid-looking diffs but with incorrect hunk counts, which
    causes `git apply` to fail with 'corrupt patch'. We recompute counts from
    the hunk body and rewrite the @@ header counts.
    """

    if not text or "@@" not in text:
        return text

    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HUNK_HEADER_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue

        old_start = m.group(1)
        new_start = m.group(3)

        # Count hunk body lines until next header or file boundary.
        old_count = 0
        new_count = 0
        j = i + 1
        while j < len(lines):
            body = lines[j]
            if body.startswith("@@") or body.startswith("diff --git ") or body.startswith("--- ") or body.startswith("+++ "):
                break
            if body.startswith("\\"):
                j += 1
                continue
            if body.startswith(" "):
                old_count += 1
                new_count += 1
            elif body.startswith("-"):
                old_count += 1
            elif body.startswith("+"):
                new_count += 1
            else:
                # Not a valid hunk line; leave as-is.
                break
            j += 1

        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")
        i += 1
        while i < j:
            out.append(lines[i])
            i += 1

    return "\n".join(out)


def accepted_prediction_tokens_from_usage(usage: Any) -> int | None:
    try:
        completion_details = getattr(usage, "completion_tokens_details", None) if usage else None
        if not completion_details:
            return None
        return getattr(completion_details, "accepted_prediction_tokens", None)
    except Exception:
        return None


def run_chat_completion(
    client: Any,
    deployment: str,
    messages: list[Any],
    temperature: float,
    max_tokens: int,
    response_format: Any | None = None,
) -> tuple[str, Any, int | None, Any, float]:
    start = perf_counter()
    extra: dict[str, Any] = {}
    if response_format is not None:
        extra["response_format"] = response_format
    response = client.chat.completions.create(
        model=deployment,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        **extra,
    )
    elapsed = perf_counter() - start

    output_text = response.choices[0].message.content if response.choices else ""
    output_text = strip_markdown_fences(output_text)
    finish_reason = response.choices[0].finish_reason if response.choices else None
    usage = getattr(response, "usage", None)
    accepted_prediction_tokens = accepted_prediction_tokens_from_usage(usage)
    return output_text, finish_reason, accepted_prediction_tokens, usage, elapsed


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
        default=default_suitecrm_output_path(),
        help="Destination path for the generated code.",
    )
    parser.add_argument(
        "--output-mode",
        choices=[OUTPUT_MODE_TEXT, OUTPUT_MODE_MODULE],
        default=OUTPUT_MODE_TEXT,
        help=(
            "Where to write output. 'text' writes a single file (existing behavior). "
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


def default_module_name_for_approach(base: str, approach: str, run_both: bool) -> str:
    name = (base or "").strip()
    if not name:
        name = "LLMCodeGenerator"
    return name


def suitecrm_modules_root(args: argparse.Namespace) -> Path:
    if (args.modules_root or "").strip():
        root = Path(args.modules_root).expanduser()
        return root.resolve() if root.is_absolute() else (Path.cwd() / root).resolve()
    return (default_suitecrm_root() / "modules").resolve()


def _is_safe_relative_path(rel_path: str) -> bool:
    p = rel_path.replace("\\", "/").strip()
    if not p or p.startswith("/"):
        return False
    if ":" in p:
        return False
    parts = [seg for seg in p.split("/") if seg]
    if any(seg == ".." for seg in parts):
        return False
    return True


def parse_file_bundle(output_text: str) -> list[dict[str, str]]:
    try:
        payload = json.loads((output_text or "").strip())
    except Exception as exc:
        raise ValueError(f"Model did not return valid JSON file bundle: {exc}")

    if isinstance(payload, list):
        files = payload
    elif isinstance(payload, dict):
        files = payload.get("files")
    else:
        files = None

    if not isinstance(files, list) or not files:
        raise ValueError("File bundle JSON must contain a non-empty 'files' list.")

    normalized: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        content_lines = item.get("content_lines")
        content_b64 = item.get("content_b64")
        content = str(item.get("content") or "")
        if not _is_safe_relative_path(path):
            raise ValueError(f"Unsafe or invalid file path in bundle: {path!r}")

        decoded: str | None = None
        if isinstance(content_lines, list) and content_lines:
            try:
                decoded = "\n".join(str(line) for line in content_lines)
            except Exception:
                decoded = None
        if isinstance(content_b64, str) and content_b64.strip():
            raw_b64 = re.sub(r"\s+", "", content_b64.strip())
            if re.fullmatch(r"[A-Za-z0-9+/=_-]+", raw_b64) is None:
                if content.strip():
                    decoded = content
                else:
                    # Best-effort fallback: treat provided value as literal content.
                    decoded = content_b64
            else:
                padded = raw_b64 + ("=" * ((4 - (len(raw_b64) % 4)) % 4))
                try:
                    decoded_bytes = base64.b64decode(padded, validate=False)
                    try:
                        decoded = decoded_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        decoded = decoded_bytes.decode("latin-1")
                except Exception:
                    try:
                        decoded_bytes = base64.urlsafe_b64decode(padded)
                        try:
                            decoded = decoded_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            decoded = decoded_bytes.decode("latin-1")
                    except Exception as exc:
                        if content.strip():
                            decoded = content
                        else:
                            # Best-effort fallback: treat provided value as literal content.
                            decoded = content_b64
        elif content.strip():
            # Back-compat if a model returns plain content.
            decoded = content

        if not (decoded or "").strip():
            raise ValueError(f"Empty content for file path in bundle: {path!r}")

        normalized.append({"path": path.replace("\\", "/"), "content": decoded or ""})

    if not normalized:
        raise ValueError("File bundle contained no usable files.")
    return normalized


def write_module_files(
    *,
    modules_root: Path,
    module_name: str,
    files: list[dict[str, str]],
) -> list[Path]:
    module_dir = (modules_root / module_name).resolve()
    module_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for item in files:
        rel_path = item["path"]
        content = item["content"]
        target = (module_dir / rel_path).resolve()
        if module_dir not in target.parents and target != module_dir:
            raise ValueError(f"Refusing to write outside module dir: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)

    return written


def repair_file_bundle_json(
    *,
    client: Any,
    deployment: str,
    bad_output: str,
    max_tokens: int,
) -> str:
    messages: list[Any] = [
        {
            "role": "system",
            "content": "You are a strict JSON formatter. Output only valid JSON.",
        },
        {
            "role": "user",
            "content": (
                "Fix and normalize the following model output into a valid JSON file bundle.\n\n"
                f"{FILE_BUNDLE_INSTRUCTIONS}\n\n"
                "Input to fix (may be invalid JSON):\n"
                f"{bad_output}"
            ),
        },
    ]

    fixed_text, _, _, _, _ = run_chat_completion(
        client=client,
        deployment=deployment,
        messages=messages,
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return fixed_text


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


def gather_context(paths: list[str], byte_budget: int, *, base_root: Path | None = None) -> str:
    if not paths or byte_budget <= 0:
        return ""

    snippets: list[str] = []
    remaining = byte_budget

    for file_path in iter_source_files(paths, base_root=base_root):
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1", errors="replace")

        display_path: str
        if base_root is not None:
            try:
                display_path = str(file_path.relative_to(base_root))
            except Exception:
                display_path = str(file_path)
        else:
            display_path = str(file_path)

        encoded = text.encode("utf-8")
        chunk = encoded[:remaining].decode("utf-8", errors="ignore")
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
        raise ValueError(
            "Azure OpenAI endpoint, key, and deployment must be configured via arguments or environment."
        )

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

    def run_one() -> tuple[str, Path | None, list[Path] | None, float]:
        approach = APPROACH_RAW
        run_id = (args.run_id or "").strip() or str(uuid.uuid4())

        run_started = utc_now_iso()

        # Prefer SuiteCRM-root relative paths in context so generated patches
        # target repo paths like include/Foo.php instead of filesystem paths.
        base_root: Path | None = None
        try:
            base_root = Path(args.suitecrm_root).expanduser()
            if not base_root.is_absolute():
                base_root = (Path.cwd() / base_root).resolve()
        except Exception:
            base_root = None

        context = gather_context(args.sources, int(args.max_context_bytes), base_root=base_root)

        if extra_context_text:
            context = (extra_context_text + "\n\n" + (context or "").strip()).strip()

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

        run_finished = utc_now_iso()

        if not (output_text or "").strip() and accepted_prediction_tokens == 0:
            raise RuntimeError(
                "The selected Azure OpenAI deployment returned only reasoning tokens and no output text. "
                "Fix: deploy a chat-capable model (e.g., gpt-4o / gpt-4.1) and set AZURE_OPENAI_DEPLOYMENT to that deployment name."
            )

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
            module_name = default_module_name_for_approach(args.module_name, approach, False)
            written_files = write_module_files(modules_root=modules_root, module_name=module_name, files=files)
        else:
            output_path = Path(args.output).resolve()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            normalized_output = output_text
            normalized_output = normalize_unified_diff_hunk_counts(normalized_output)
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
                "tool": "generate_from_codebase",
                "approach": approach,
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
            }

            append_jsonl(run_log_path, log_payload)

            # Text-mode validation: validate the single output file.
            if args.validate and output_path is not None:
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

            # Module-mode validation: validate each written file.
            if args.output_mode == OUTPUT_MODE_MODULE and args.validate_written and written_files:
                if validate_file is None or append_validation_run is None:
                    raise RuntimeError(
                        "--validate-written requested but validator helpers could not be imported. "
                        "Ensure validate_generated_output.py is present and importable."
                    )

                suitecrm_root = Path(args.suitecrm_root).expanduser()
                if not suitecrm_root.is_absolute():
                    suitecrm_root = (Path.cwd() / suitecrm_root).resolve()

                for file_path in written_files:
                    report, findings = validate_file(
                        input_path=file_path,
                        suitecrm_root=suitecrm_root,
                        no_php_lint=bool(args.no_php_lint),
                    )
                    append_validation_run(
                        run_log_path=run_log_path,
                        run_id=run_id,
                        input_path=file_path,
                        suitecrm_root=suitecrm_root,
                        report=report,
                        findings=findings,
                    )

        return run_id, output_path, written_files, elapsed

    try:
        run_id, output_path, written_files, elapsed = run_one()
    except Exception as exc:  # pragma: no cover - CLI diagnostics
        message = str(exc)
        if "DeploymentNotFound" in message or "deployment for this resource does not exist" in message:
            raise RuntimeError(
                "Azure OpenAI deployment not found for the configured resource.\n"
                f"endpoint={args.endpoint}\n"
                f"deployment={args.deployment}\n"
                f"api_version={args.api_version}\n\n"
                "Fix: In Azure Portal for this *same* Azure OpenAI resource, open Deployments and copy the exact *deployment name* "
                "(not the model name), then set AZURE_OPENAI_DEPLOYMENT to that value."
            ) from exc
        raise
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
    except Exception as exc:  # pragma: no cover - surface error to CLI
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
