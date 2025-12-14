#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

SUITECRM_ROOT = Path(__file__).resolve().parents[3] / "SuiteCRM"

EXCLUDE_DIRS = {
    "vendor",
    "Zend",
    "XTemplate",
    "jssource",
    "cache",
    "upload",
    "lib",
    "tests",
    "test",
    "build",
    ".git",
}

MAX_FILE_BYTES = 2_000_000  # skip very large files
MIN_FUNCTION_LINES = 120
TOP_N = 20

FUNC_RE = re.compile(
    r"(?i)(?:^|[^\w])(?:public\s+|protected\s+|private\s+)?(?:static\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="replace")


def find_function_spans(text: str) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []

    for match in FUNC_RE.finditer(text):
        name = match.group(1)

        start_kw = text.lower().find("function", match.start(0), match.end(0))
        start = start_kw if start_kw != -1 else match.start(0)

        brace_open = text.find("{", match.end(0))
        if brace_open == -1:
            continue

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
                    spans.append((name, start, i + 1))
                    break

            i += 1

    return spans


def line_no(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def main() -> int:
    suitecrm_root = SUITECRM_ROOT.resolve()
    candidates: list[tuple[int, str, str, int, int]] = []

    for path in suitecrm_root.rglob("*.php"):
        parts = set(path.parts)
        if any(d in parts for d in EXCLUDE_DIRS):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue

        text = safe_read_text(path)
        for name, start, end in find_function_spans(text):
            sl = line_no(text, start)
            el = line_no(text, end)
            length = el - sl + 1
            if length >= MIN_FUNCTION_LINES:
                rel = path.relative_to(suitecrm_root).as_posix()
                candidates.append((length, rel, name, sl, el))

    candidates.sort(key=lambda x: x[0], reverse=True)

    print(f"suitecrm_root={suitecrm_root}")
    print(f"large_functions_found={len(candidates)}")
    for length, rel, name, sl, el in candidates[:TOP_N]:
        print(f"{length:4d} lines | {rel}:{sl}-{el} | function {name}()")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
