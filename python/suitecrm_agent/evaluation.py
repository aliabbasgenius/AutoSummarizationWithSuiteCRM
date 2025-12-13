"""Evaluation utilities for agent outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .models import AgentResult
from .utils import console


@dataclass(slots=True)
class EvaluationMetrics:
    """Quantitative signals gathered after agent execution."""

    task_id: str
    compilation_success: bool
    tests_passed: bool
    hallucination_flags: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "compilation_success": self.compilation_success,
            "tests_passed": self.tests_passed,
            "hallucination_flags": self.hallucination_flags,
            "notes": self.notes,
        }


def compare_results(baseline: AgentResult, summarization: AgentResult) -> dict[str, object]:
    return {
        "task_id": summarization.task_id,
        "baseline_plan": baseline.plan,
        "summarized_plan": summarization.plan,
        "baseline_code_suggestions": baseline.code_suggestions,
        "summarized_code_suggestions": summarization.code_suggestions,
    }


def record_metrics(path: Path, metrics: EvaluationMetrics) -> None:
    path.write_text(json.dumps(metrics.to_dict(), indent=2), encoding="utf-8")
    console().print(f"[green]Evaluation metrics saved to {path}[/green]")
