"""Main SuiteCRM agent orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from time import perf_counter

try:  # pragma: no cover
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover
    AzureOpenAI = None  # type: ignore

from .codebase_indexer import IndexedModule, collect_custom_logic, discover_modules
from .config import AgentConfig
from .models import AgentResult, AgentTask, ModuleSummary
from .store import SummaryStore
from .summarizer import AutoSummarizer
from .utils import append_jsonl, chunk_text, console, environment_summary, read_text_safe, utc_now_iso


class SuiteCRMAgent:
    """High-level agent for SuiteCRM-aware code generation and refactoring."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.summarizer = AutoSummarizer(config)
        self.store = SummaryStore(config.project.summary_dir)
        self.suitecrm_root = config.project.suitecrm_root
        self.run_log_path = (Path(__file__).resolve().parent / "runs" / "agent_runs.jsonl")

    def run_task(self, task: AgentTask) -> AgentResult:
        started = perf_counter()
        indexed_modules = list(self._collect_modules(task))
        summary_start = perf_counter()
        summaries = [self._ensure_summary(module) for module in indexed_modules]
        summary_elapsed = perf_counter() - summary_start

        generation_start = perf_counter()
        plan = self._draft_plan(task, summaries)
        code_suggestions = self._generate_code(task, summaries)
        generation_elapsed = perf_counter() - generation_start

        total_elapsed = perf_counter() - started
        evaluation = {
            "suitecrm_root": str(self.suitecrm_root),
            "environment": environment_summary(),
            "metrics": {
                "summaries_used": len(summaries),
                "modules_analyzed": [module.name for module in indexed_modules],
            },
        }

        try:
            append_jsonl(
                self.run_log_path,
                {
                    "timestamp_utc": utc_now_iso(),
                    "task_id": task.task_id,
                    "deployment": self.config.azure.deployment,
                    "suitecrm_root": str(self.suitecrm_root),
                    "modules_analyzed": [module.name for module in indexed_modules],
                    "artifacts": list(task.artifacts or []),
                    "summaries_used": len(summaries),
                    "durations_seconds": {
                        "summarization": round(summary_elapsed, 3),
                        "generation": round(generation_elapsed, 3),
                        "total": round(total_elapsed, 3),
                    },
                },
            )
        except Exception as exc:  # pragma: no cover
            console().print(f"[yellow]Failed to write run stats: {exc}[/yellow]")

        return AgentResult(
            task_id=task.task_id,
            plan=plan,
            code_suggestions=code_suggestions,
            summary_references=[str(self.store.summary_path(summary.name)) for summary in summaries],
            evaluation=evaluation,
        )

    def _collect_modules(self, task: AgentTask) -> Iterable[IndexedModule]:
        available = {module.name: module for module in discover_modules(self.suitecrm_root)}
        selected = task.target_modules or []

        if not selected and task.artifacts:
            # Artifact-driven tasks can skip module summarization entirely.
            return []

        if not selected:
            # default heuristic: pick modules that match objectives keywords
            keywords = {kw.lower() for kw in task.objectives}
            selected = [
                name
                for name in available
                if any(keyword in name.lower() for keyword in keywords)
            ]
            if not selected:
                selected = list(available.keys())[:3]

        for name in selected:
            module = available.get(name)
            if module:
                yield module
            else:
                console().print(f"[yellow]Module '{name}' not found in SuiteCRM/modules.[/yellow]")

    def _ensure_summary(self, module: IndexedModule) -> ModuleSummary:
        file_paths = [artifact.path for artifact in module.artifacts]
        if self.store.needs_refresh(module.name, file_paths):
            console().print(f"[cyan]Summarizing module {module.name}...[/cyan]")
            summary = self.summarizer.summarize_module(module.name, module.artifacts)
            summary.source_hash = summary.source_hash or ""
            summary.source_files = module.artifacts
            self.store.save(summary)
            return summary

        cached = self.store.load(module.name)
        if cached:
            return cached

        summary = self.summarizer.summarize_module(module.name, module.artifacts)
        summary.source_files = module.artifacts
        self.store.save(summary)
        return summary

    def _draft_plan(self, task: AgentTask, summaries: list[ModuleSummary]) -> list[str]:
        plan = [f"Task: {task.description}"] + [f"Objective: {objective}" for objective in task.objectives]
        plan.append("Summaries consulted:")
        if summaries:
            plan.extend(f" - {summary.name}: {summary.purpose[:120]}" for summary in summaries)
        else:
            plan.append(" - (none)")

        if task.artifacts:
            plan.append("Artifacts provided:")
            plan.extend(f" - {artifact}" for artifact in task.artifacts)

        custom_artifacts = collect_custom_logic(self.suitecrm_root)
        if custom_artifacts:
            plan.append(f"Detected {len(custom_artifacts)} custom extension files for additional review.")
        return plan

    def _resolve_task_artifact(self, artifact: str) -> Path | None:
        candidate = Path(artifact)

        if candidate.is_absolute():
            resolved = candidate
        else:
            # Prefer resolving relative to the current working directory (lets tasks reference
            # artifacts outside SuiteCRM, e.g. generated summaries under python/suitecrm_agent/runs/).
            cwd_candidate = (Path.cwd() / candidate).resolve()
            if cwd_candidate.exists() and cwd_candidate.is_file():
                resolved = cwd_candidate
            else:
                resolved = (self.suitecrm_root / candidate).resolve()

        if not resolved.exists() or not resolved.is_file():
            console().print(f"[yellow]Artifact not found or not a file: {artifact}[/yellow]")
            return None

        return resolved

    def _artifact_snippets_for_prompt(self, task: AgentTask) -> str:
        if not task.artifacts:
            return ""

        snippets: list[str] = []
        for artifact in task.artifacts:
            resolved = self._resolve_task_artifact(artifact)
            if resolved is None:
                continue

            text = read_text_safe(resolved)
            chunks = list(
                chunk_text(
                    text,
                    self.config.project.chunk_size,
                    self.config.project.chunk_overlap,
                )
            )
            snippet = chunks[0] if chunks else text
            snippets.append(f"// artifact: {resolved}\n{snippet}")

        if not snippets:
            return ""

        return "\n\nTarget artifacts (excerpted):\n" + "\n\n".join(snippets[:6])

    def _generate_code(self, task: AgentTask, summaries: list[ModuleSummary]):
        if not summaries and not task.artifacts:
            console().print("[yellow]No module summaries or artifacts available; skipping code generation.[/yellow]")
            return []

        azure = self.config.azure
        if not azure.endpoint or not azure.api_key or not azure.deployment or AzureOpenAI is None:
            console().print(
                "[yellow]Skipping Azure OpenAI code generation; credentials missing or SDK unavailable.[/yellow]"
            )
            return []

        client = AzureOpenAI(
            api_key=azure.api_key,
            azure_endpoint=azure.endpoint,
            api_version=azure.api_version,
        )
        summary_text = "\n\n".join(
            f"Module: {summary.name}\nPurpose: {summary.purpose}\nDependencies: {', '.join(summary.dependencies)}\nBusiness rules: {', '.join(summary.business_rules)}"
            for summary in summaries
        )
        if not summary_text:
            summary_text = "(No module summaries provided for this task.)"

        artifact_text = self._artifact_snippets_for_prompt(task)

        prompt = (
            "You are an expert SuiteCRM engineer. Using the summaries and artifacts below, propose code changes to satisfy the task.\n\n"
            "Hard requirements:\n"
            "- Keep behavior identical (no functional changes).\n"
            "- Keep public interfaces stable.\n"
            "- Do NOT change comments/docblocks unless strictly necessary.\n"
            "- Return ONLY JSON (no markdown fences, no extra text).\n\n"
            "JSON schema:\n"
            "{\n"
            "  \"files\": [\"include/CleanCSV.php\"],\n"
            "  \"diffs\": [\"diff --git a/... b/...\\n--- a/...\\n+++ b/...\\n@@ ...\"],\n"
            "  \"testing\": [\"php -l include/CleanCSV.php\", \"php cli/maintenance.php status\"]\n"
            "}\n\n"
            "Rules for diffs:\n"
            "- Each diff MUST be a complete unified diff (git apply compatible).\n"
            "- Use repo-relative paths (e.g., include/CleanCSV.php), never absolute paths.\n"
            "- Keep diffs minimal.\n\n"
            f"Summaries:\n{summary_text}{artifact_text}\n\nTask description:\n{task.description}\nObjectives:\n- "
            + "\n- ".join(task.objectives)
        )

        messages = [
            {"role": "system", "content": "You produce SuiteCRM code patches in JSON format."},
            {"role": "user", "content": prompt},
        ]
        try:
            completion = client.chat.completions.create(
                model=azure.deployment,
                messages=messages,
                temperature=azure.temperature,
                max_completion_tokens=azure.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception:
            completion = client.chat.completions.create(
                model=azure.deployment,
                messages=messages,
                temperature=azure.temperature,
                max_completion_tokens=azure.max_tokens,
            )

        content = completion.choices[0].message.content if completion.choices else ""
        if not (content or "").strip():
            try:
                response = client.responses.create(
                    model=azure.deployment,
                    input=messages,
                    max_output_tokens=azure.max_tokens,
                )
                content = getattr(response, "output_text", "") or ""
            except Exception:
                content = content or ""

        if not (content or "").strip():
            console().print(
                "[red]Azure OpenAI returned empty output text. This deployment may be reasoning-only or not chat-capable for this API. "
                "Switch AZURE_OPENAI_DEPLOYMENT to a chat-capable deployment (for example, gpt-4.1 / gpt-4o) and rerun.[/red]"
            )
            return [
                {
                    "error": "azure_openai_empty_output",
                    "deployment": azure.deployment,
                    "hint": "The model returned no message content. Use a chat-capable Azure OpenAI deployment for chat.completions.",
                }
            ]

        try:
            response = json.loads(content)
        except json.JSONDecodeError:
            console().print("[yellow]LLM returned non-JSON output; capturing raw text.[/yellow]")
            return [{"raw": content}]

        return [response]
