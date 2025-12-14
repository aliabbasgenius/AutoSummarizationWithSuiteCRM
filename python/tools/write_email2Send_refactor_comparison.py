from __future__ import annotations

from pathlib import Path
import re
import subprocess
from difflib import SequenceMatcher


def file_stats(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}

    text = path.read_text(encoding="utf-8", errors="replace")
    this_calls = set(re.findall(r"\$this->([A-Za-z_][A-Za-z0-9_]*)\s*\(", text))

    has_diff_header = ("diff --git" in text and "---" in text and "+++" in text) or text.lstrip().startswith("@@")
    prose_markers = ["here's", "explanation", "note:", "sure,", "i updated", "below is", "changes:"]
    has_prose = any(marker in text.lower() for marker in prose_markers)

    return {
        "exists": True,
        "bytes": path.stat().st_size,
        "lines": text.count("\n") + 1,
        "has_diff": has_diff_header,
        "mentions_emailphp": ("modules/Emails/Email.php" in text) or ("SuiteCRM/modules/Emails/Email.php" in text),
        "this_call_count": len(this_calls),
        "has_prose_markers": has_prose,
    }


def yn(value: bool) -> str:
    return "Yes" if value else "No"


def main() -> None:
    root = Path(r"D:/Study/Projects/AutoSummarizationProject")

    manual_patch = root / "LLMCodeGenerator/python/runs/email2Send_manual_refactor_v2.patch"
    raw_patch = root / "LLMCodeGenerator/python/runs/email2Send_refactor_raw_suggested.patch"
    autosummary_patch = root / "LLMCodeGenerator/python/runs/email2Send_refactor_autosummary_suggested.patch"

    suitecrm_root = root / "SuiteCRM"
    email_php_path = suitecrm_root / "modules/Emails/Email.php"

    def extract_function_text(file_path: Path, function_name: str) -> str:
        """Extract function text by slicing from signature to next method signature.

        This is intentionally simple and robust enough for metrics (not parsing).
        """
        text = file_path.read_text(encoding="utf-8", errors="replace")
        signature = re.search(
            r"^\s*public\s+function\s+" + re.escape(function_name) + r"\s*\(.*\)\s*$",
            text,
            re.M,
        )
        if not signature:
            return ""

        start = signature.start()
        next_method = re.search(
            r"^\s*(public|protected|private)\s+function\s+[A-Za-z_][A-Za-z0-9_]*\s*\(",
            text[signature.end() :],
            re.M,
        )
        if not next_method:
            return text[start:]
        end = signature.end() + next_method.start()
        return text[start:end]

    original_function = extract_function_text(email_php_path, "email2Send")
    original_this_calls = set(re.findall(r"\$this->([A-Za-z_][A-Za-z0-9_]*)\s*\(", original_function))

    def new_this_calls_in_output(output_path: Path) -> list[str]:
        if not output_path.exists():
            return []
        text = output_path.read_text(encoding="utf-8", errors="replace")
        calls = set(re.findall(r"\$this->([A-Za-z_][A-Za-z0-9_]*)\s*\(", text))
        return sorted(calls - original_this_calls)

    manual_diffstat = ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(suitecrm_root), "diff", "--stat", "--", "modules/Emails/Email.php"],
            capture_output=True,
            text=True,
            check=False,
        )
        manual_diffstat = (proc.stdout or "").strip()
    except Exception as exc:  # pragma: no cover
        manual_diffstat = f"(failed to get diffstat: {exc})"

    out_md = root / "LLMCodeGenerator/python/runs/email2Send_refactor_comparisons.md"

    stats = {
        "manual": file_stats(manual_patch),
        "raw": file_stats(raw_patch),
        "autosummary": file_stats(autosummary_patch),
    }

    rows = [
        ("Manual (applied)", manual_patch, stats["manual"], True, "OK"),
        ("LLM Raw (suggested)", raw_patch, stats["raw"], False, "-"),
        ("LLM Auto-summary (suggested)", autosummary_patch, stats["autosummary"], False, "-"),
    ]

    lines: list[str] = []
    lines.append("# email2Send() refactor comparisons")
    lines.append("")
    lines.append("## Table")
    lines.append("")
    lines.append(
        "| Approach | Artifact | Patch-like output | Mentions Email.php | Size (lines) | Size (bytes) | $this->call tokens | New $this->calls vs current email2Send() | Prose markers | Applied | PHP lint |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|")

    for approach, artifact, s, applied, php_lint in rows:
        new_calls = new_this_calls_in_output(artifact)
        new_calls_cell = ", ".join(new_calls[:8])
        if len(new_calls) > 8:
            new_calls_cell += f" … (+{len(new_calls) - 8} more)"
        if not new_calls_cell:
            new_calls_cell = "-"

        lines.append(
            "| {} | `{}` | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                approach,
                artifact.as_posix().split("AutoSummarizationProject/")[-1],
                yn(bool(s.get("has_diff"))),
                yn(bool(s.get("mentions_emailphp"))),
                s.get("lines", "-"),
                s.get("bytes", "-"),
                s.get("this_call_count", "-"),
                new_calls_cell,
                yn(bool(s.get("has_prose_markers"))),
                "Yes" if applied else "No",
                php_lint,
            )
        )

    # Similarity between suggested outputs
    if raw_patch.exists() and autosummary_patch.exists():
        raw_text = raw_patch.read_text(encoding="utf-8", errors="replace")
        auto_text = autosummary_patch.read_text(encoding="utf-8", errors="replace")
        similarity = SequenceMatcher(None, raw_text, auto_text).ratio()
    else:
        similarity = 0.0

    lines.append("")
    lines.append("## Suggested output similarity")
    lines.append("")
    lines.append(f"Raw vs auto-summary text similarity (SequenceMatcher ratio): {similarity:.3f}")

    lines.append("")
    lines.append("## Manual diffstat (current working tree)")
    lines.append("")
    lines.append("```")
    lines.append(manual_diffstat or "(no diff)")
    lines.append("```")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_md}")


if __name__ == "__main__":
    main()
