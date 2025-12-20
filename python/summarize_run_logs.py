#!/usr/bin/env python3
"""Summarize run logs for both Python agent and C# AzureOpenAICodeGen.

Reads JSONL logs and prints basic stats:
- total runs, avg durations
- (C#) success/failure, retry behavior
- (Python) summaries used, module coverage

This script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
PY_AGENT_LOG = REPO_ROOT / "LLMCodeGenerator" / "python" / "suitecrm_agent" / "runs" / "agent_runs.jsonl"
PY_TOOLS_LOG = REPO_ROOT / "LLMCodeGenerator" / "python" / "runs" / "python_runs.jsonl"
CS_LOG = REPO_ROOT / "LLMCodeGenerator" / "AzureOpenAICodeGen" / "runs" / "azure_openai_runs.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def avg(values: Iterable[float]) -> float:
    values = list(values)
    return (sum(values) / len(values)) if values else 0.0


def print_python_agent(rows: list[dict[str, Any]]) -> None:
    print(f"Python agent log: {PY_AGENT_LOG}")
    print(f"Runs: {len(rows)}")
    if not rows:
        return

    totals = [float(r.get("durations_seconds", {}).get("total", 0.0) or 0.0) for r in rows]
    gens = [float(r.get("durations_seconds", {}).get("generation", 0.0) or 0.0) for r in rows]
    sums = [float(r.get("durations_seconds", {}).get("summarization", 0.0) or 0.0) for r in rows]

    summaries_used = [int(r.get("summaries_used", 0) or 0) for r in rows]

    modules: set[str] = set()
    for r in rows:
        for m in (r.get("modules_analyzed") or []):
            if isinstance(m, str):
                modules.add(m)

    print(f"Avg duration(s): total={avg(totals):.2f} summarization={avg(sums):.2f} generation={avg(gens):.2f}")
    print(f"Avg summaries_used: {avg([float(x) for x in summaries_used]):.2f}")
    print(f"Distinct modules analyzed: {len(modules)}")


def print_csharp(rows: list[dict[str, Any]]) -> None:
    print(f"C# log: {CS_LOG}")
    print(f"Runs: {len(rows)}")
    if not rows:
        return

    durations = [float(r.get("duration_seconds", 0.0) or 0.0) for r in rows]
    successes = sum(1 for r in rows if r.get("success") is True)
    failures = sum(1 for r in rows if r.get("success") is False)

    modes = {}
    for r in rows:
        mode = r.get("mode") or ""
        if isinstance(mode, str):
            modes[mode] = modes.get(mode, 0) + 1

    attempts = [float((r.get("retry") or {}).get("attempts", 0) or 0) for r in rows]
    dropped_max = sum(1 for r in rows if (r.get("retry") or {}).get("dropped_max_tokens") is True)
    dropped_temp = sum(1 for r in rows if (r.get("retry") or {}).get("dropped_temperature") is True)

    print(f"Success: {successes} | Failure: {failures}")
    print(f"Modes: {modes}")
    print(f"Avg duration(s): {avg(durations):.2f}")
    print(f"Avg attempts: {avg(attempts):.2f} | dropped_max_tokens: {dropped_max} | dropped_temperature: {dropped_temp}")


def print_python_tools(rows: list[dict[str, Any]], log_path: Path) -> None:
    print(f"Python tools log: {log_path}")
    print(f"Runs: {len(rows)}")
    if not rows:
        return

    # Group by tool name (generate_summary / generate_from_codebase / etc.)
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        tool = r.get("tool")
        if isinstance(tool, str) and tool:
            by_tool.setdefault(tool, []).append(r)

    for tool_name in sorted(by_tool.keys()):
        tool_rows = by_tool[tool_name]
        durations = [float(r.get("duration_seconds", 0.0) or 0.0) for r in tool_rows]
        output_lengths = [
            float(((r.get("diagnostics") or {}).get("output_length", 0) or 0))
            for r in tool_rows
            if isinstance(r.get("diagnostics"), dict)
        ]
        print(
            f"- {tool_name}: runs={len(tool_rows)} avg_duration(s)={avg(durations):.2f} "
            f"avg_output_len={avg(output_lengths):.0f}"
        )

    # Validation outcomes (if present)
    validations = [r for r in rows if r.get("tool") == "validate_generated_output"]
    if validations:
        passed = sum(1 for r in validations if r.get("passed") is True)
        failed = sum(1 for r in validations if r.get("passed") is False)
        warn_counts = [float((r.get("counts") or {}).get("warn", 0) or 0) for r in validations]
        err_counts = [float((r.get("counts") or {}).get("error", 0) or 0) for r in validations]
        print(
            f"Validation: runs={len(validations)} passed={passed} failed={failed} "
            f"avg_warn={avg(warn_counts):.2f} avg_err={avg(err_counts):.2f}"
        )

    # Correlate generator runs -> validation runs.
    generator_tools = {
        "generate_from_codebase",
        "generate_summary",
    }

    generator_rows = [r for r in rows if r.get("tool") in generator_tools]
    if not generator_rows or not validations:
        return

    def norm_path(p: Any) -> str:
        if not isinstance(p, str) or not p:
            return ""
        return p.replace("/", "\\").strip().lower()

    validations_by_run_id: dict[str, dict[str, Any]] = {}
    validations_by_output_path: dict[str, dict[str, Any]] = {}
    for v in validations:
        rid = v.get("run_id")
        if isinstance(rid, str) and rid:
            validations_by_run_id.setdefault(rid, v)
        out_p = norm_path(v.get("output_path") or v.get("input_path"))
        if out_p:
            validations_by_output_path.setdefault(out_p, v)

    per_tool: dict[str, dict[str, float]] = {}
    for g in generator_rows:
        tool = g.get("tool")
        if not isinstance(tool, str) or not tool:
            continue
        per_tool.setdefault(
            tool,
            {
                "runs": 0.0,
                "validated": 0.0,
                "passed": 0.0,
                "failed": 0.0,
                "warn_sum": 0.0,
                "err_sum": 0.0,
            },
        )
        per_tool[tool]["runs"] += 1.0

        vmatch: dict[str, Any] | None = None
        rid = g.get("run_id")
        if isinstance(rid, str) and rid:
            vmatch = validations_by_run_id.get(rid)
        if vmatch is None:
            out_p = norm_path(g.get("output_path"))
            if out_p:
                vmatch = validations_by_output_path.get(out_p)

        if vmatch is None:
            continue

        per_tool[tool]["validated"] += 1.0
        if vmatch.get("passed") is True:
            per_tool[tool]["passed"] += 1.0
        elif vmatch.get("passed") is False:
            per_tool[tool]["failed"] += 1.0

        counts = vmatch.get("counts") or {}
        if isinstance(counts, dict):
            per_tool[tool]["warn_sum"] += float(counts.get("warn", 0) or 0)
            per_tool[tool]["err_sum"] += float(counts.get("error", 0) or 0)

    if per_tool:
        print("Validated-by-tool (correlated by run_id then output_path):")
        for tool in sorted(per_tool.keys()):
            s = per_tool[tool]
            validated = s["validated"]
            avg_warn = (s["warn_sum"] / validated) if validated else 0.0
            avg_err = (s["err_sum"] / validated) if validated else 0.0
            print(
                f"- {tool}: runs={int(s['runs'])} validated={int(validated)} "
                f"passed={int(s['passed'])} failed={int(s['failed'])} "
                f"avg_warn={avg_warn:.2f} avg_err={avg_err:.2f}"
            )


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize LLMCodeGenerator run logs (JSONL).")
    p.add_argument(
        "--input",
        default="",
        help=(
            "Optional path to a python tools JSONL log to summarize (overrides the default python/runs/python_runs.jsonl). "
            "Use this for custom logs like runs/demo_runs.jsonl."
        ),
    )
    p.add_argument(
        "--last",
        type=int,
        default=0,
        help="If set, only summarize the last N JSONL rows from the chosen python tools log.",
    )
    args = p.parse_args()

    py_rows = read_jsonl(PY_AGENT_LOG)
    cs_rows = read_jsonl(CS_LOG)

    py_tools_log_path = PY_TOOLS_LOG
    if (args.input or "").strip():
        candidate = Path(args.input).expanduser()
        py_tools_log_path = candidate if candidate.is_absolute() else (Path.cwd() / candidate).resolve()

    py_tool_rows = read_jsonl(py_tools_log_path)
    if args.last and args.last > 0:
        py_tool_rows = py_tool_rows[-args.last :]

    print_python_agent(py_rows)
    print()
    print_python_tools(py_tool_rows, py_tools_log_path)
    print()
    print_csharp(cs_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
