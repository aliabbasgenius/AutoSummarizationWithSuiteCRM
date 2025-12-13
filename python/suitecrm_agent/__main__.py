"""Command-line entry point for the SuiteCRM agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None  # type: ignore

from .agent import SuiteCRMAgent
from .config import AgentConfig
from .models import AgentTask
from .utils import console


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SuiteCRM-aware LLM agent tasks.")
    parser.add_argument(
        "task",
        help="Path to a YAML task definition or a JSON payload describing the request.",
    )
    parser.add_argument(
        "--config",
        help="Optional YAML/JSON config file overriding environment-derived defaults.",
    )
    parser.add_argument(
        "--output",
        default="agent_output.json",
        help="File path where the agent response should be persisted.",
    )
    return parser.parse_args()


def load_payload(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Task definition not found at {file_path}.")

    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def load_config(path: str | None) -> AgentConfig:
    if path is None:
        return AgentConfig.from_env()

    payload = load_payload(path)
    return AgentConfig.from_dict(payload)


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()

    args = parse_args()
    config = load_config(args.config)
    payload = load_payload(args.task)

    task = AgentTask.from_payload(payload)
    agent = SuiteCRMAgent(config)

    console().print("[bold green]Starting SuiteCRM agent task...[/bold green]")
    result = agent.run_task(task)

    output_path = Path(args.output).resolve()
    output_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    console().print(f"[bold blue]Agent output written to {output_path}[/bold blue]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
