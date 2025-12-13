#!/usr/bin/env python3
"""Validate model-generated output for common errors and hallucinations.

This is an *offline* validator:
- Looks for placeholder / TODO-style hallucinations
- Flags markdown fences and other non-code artifacts
- Optionally checks referenced include/require paths exist under a SuiteCRM root
- Optionally runs `php -l` if the output looks like PHP

It does not execute SuiteCRM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Finding:
    severity: str  # error|warn|info
    code: str
    message: str


_PLACEHOLDER_PATTERNS: list[tuple[str, str, str]] = [
    ("error", "placeholder.todo", r"(?mi)^(\s*//\s*)?(TODO|FIXME|TBD)\b"),
    ("error", "placeholder.not_implemented", r"NotImplemented(Exception)?\b"),
    ("warn", "placeholder.replace_me", r"(?i)\bREPLACE_ME\b"),
    ("warn", "placeholder.your_value", r"(?i)<YOUR_[A-Z0-9_]+>"),
    ("warn", "placeholder.example", r"(?i)\b(example\.com|foo\.bar|lorem ipsum)\b"),
    ("warn", "artifact.ellipsis", r"(?m)^\s*(\.\.\.|…)+\s*$"),
]

_INCLUDE_RE = re.compile(r"(?i)\b(require_once|require|include_once|include)\s*\(?\s*['\"]([^'\"]+)['\"]")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate a generated artifact for common hallucinations.")
    p.add_argument("--input", required=True, help="Generated file to validate.")
    p.add_argument(
        "--suitecrm-root",
        default="../../SuiteCRM",
        help="SuiteCRM root to resolve include/require paths against.",
    )
    p.add_argument(
        "--report",
        default="",
        help="Optional path to write a JSON report.",
    )
    p.add_argument(
        "--run-log",
        default=os.getenv("PYTHON_RUN_LOG", ""),
        help="Optional JSONL file to append validation outcomes.",
    )
    p.add_argument(
        "--run-id",
        default="",
        help="Optional run id to correlate with a generator run (else a new uuid is generated).",
    )
    p.add_argument(
        "--no-php-lint",
        action="store_true",
        help="Skip php -l check even if output looks like PHP.",
    )
    return p.parse_args()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def looks_like_php(text: str, input_path: Path) -> bool:
    if input_path.suffix.lower() == ".php":
        return True
    t = (text or "").lstrip()
    if t.startswith("<?php"):
        return True
    # Heuristic: PHP variables + function/class keywords
    if re.search(r"(?m)^\s*\$[a-zA-Z_]", text) and re.search(r"\b(function|class)\b", text):
        return True
    return False


def validate_text(text: str, suitecrm_root: Path) -> list[Finding]:
    findings: list[Finding] = []

    stripped = (text or "")

    if "```" in stripped:
        findings.append(
            Finding(
                severity="warn",
                code="artifact.markdown_fence",
                message="Output contains markdown code fences (```), which often indicates non-code artifacts.",
            )
        )

    for severity, code, pattern in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, stripped):
            findings.append(Finding(severity=severity, code=code, message=f"Matched pattern: {pattern}"))

    # include/require path existence checks
    for _kw, raw_path in _INCLUDE_RE.findall(stripped):
        raw_path = raw_path.strip()
        if not raw_path or raw_path.startswith(("http://", "https://")):
            continue
        # Skip dynamic paths
        if "$" in raw_path or "{" in raw_path or "}" in raw_path:
            continue
        candidate = (suitecrm_root / raw_path).resolve()
        if not candidate.exists():
            findings.append(
                Finding(
                    severity="warn",
                    code="path.missing",
                    message=f"Referenced path not found under SuiteCRM root: {raw_path}",
                )
            )

    return findings


def run_php_lint_if_applicable(text: str, input_path: Path, skip: bool) -> Finding | None:
    if skip:
        return None
    if not looks_like_php(text, input_path):
        return None

    lint_text = text
    if not lint_text.lstrip().startswith("<?php"):
        lint_text = "<?php\n" + lint_text

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".php", delete=False) as handle:
            handle.write(lint_text)
            tmp_path = handle.name

        proc = subprocess.run(
            ["php", "-l", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return Finding(
            severity="warn",
            code="php.missing",
            message="PHP CLI not found on PATH; skipped php -l.",
        )
    except subprocess.TimeoutExpired:
        return Finding(
            severity="warn",
            code="php.lint_timeout",
            message="php -l timed out.",
        )
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return Finding(severity="error", code="php.lint_failed", message=output.strip() or "php -l failed")

    return Finding(severity="info", code="php.lint_ok", message=output.strip() or "php -l OK")


def validate_file(
    input_path: Path,
    suitecrm_root: Path,
    *,
    no_php_lint: bool = False,
) -> tuple[dict[str, Any], list[Finding]]:
    """Run offline validation for an output artifact.

    Returns a JSON-serializable report dict plus the raw Finding list.
    """

    text = read_text(input_path)

    findings = validate_text(text, suitecrm_root)
    lint_finding = run_php_lint_if_applicable(text, input_path, skip=bool(no_php_lint))
    if lint_finding:
        findings.append(lint_finding)

    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    infos = [f for f in findings if f.severity == "info"]

    report: dict[str, Any] = {
        "input": str(input_path),
        "suitecrm_root": str(suitecrm_root),
        "findings": [f.__dict__ for f in findings],
        "counts": {"error": len(errors), "warn": len(warns), "info": len(infos)},
    }

    return report, findings


def append_validation_run(
    *,
    run_log_path: Path,
    run_id: str,
    input_path: Path,
    suitecrm_root: Path,
    report: dict[str, Any],
    findings: list[Finding],
) -> None:
    """Append a compact validation record to a JSONL run log."""

    append_jsonl(
        run_log_path,
        {
            "run_id": run_id,
            "tool": "validate_generated_output",
            "input_path": str(input_path),
            "output_path": str(input_path),
            "suitecrm_root": str(suitecrm_root),
            "passed": int((report.get("counts") or {}).get("error", 0) or 0) == 0,
            "counts": report.get("counts", {}),
            "finding_codes": [f.code for f in findings],
        },
    )


def resolve_path(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def resolve_existing_file(path_str: str) -> Path | None:
    path = resolve_path(path_str)
    if not path.exists() or not path.is_file():
        return None
    return path


def maybe_append_validation_log(
    *,
    run_log: str,
    run_id: str,
    input_path: Path,
    suitecrm_root: Path,
    report: dict[str, Any],
    findings: list[Finding],
) -> None:
    if not (run_log or "").strip():
        return

    run_log_path = resolve_path(run_log)
    append_validation_run(
        run_log_path=run_log_path,
        run_id=run_id,
        input_path=input_path,
        suitecrm_root=suitecrm_root,
        report=report,
        findings=findings,
    )


def maybe_write_report(report_path: str, report: dict[str, Any]) -> None:
    if not (report_path or "").strip():
        return

    path = resolve_path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {path}")


def print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        if finding.severity != "info":
            print(f"[{finding.severity}] {finding.code}: {finding.message}")


def main() -> int:
    args = parse_args()

    input_path = resolve_existing_file(args.input)
    if input_path is None:
        print(f"Error: input not found: {resolve_path(args.input)}", file=sys.stderr)
        return 2

    suitecrm_root = resolve_path(args.suitecrm_root)

    report, findings = validate_file(
        input_path=input_path,
        suitecrm_root=suitecrm_root,
        no_php_lint=bool(args.no_php_lint),
    )

    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]

    run_id = (args.run_id or "").strip() or str(uuid.uuid4())
    maybe_append_validation_log(
        run_log=args.run_log,
        run_id=run_id,
        input_path=input_path,
        suitecrm_root=suitecrm_root,
        report=report,
        findings=findings,
    )

    maybe_write_report(args.report, report)

    print_findings(findings)

    if errors:
        print(f"Validation failed: {len(errors)} error(s), {len(warns)} warning(s)")
        return 2

    print(f"Validation OK: {len(warns)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
