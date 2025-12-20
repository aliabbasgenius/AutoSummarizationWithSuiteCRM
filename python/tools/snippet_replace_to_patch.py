from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path


def strip_wrapping(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip() + "\n"


def check_no_placeholders(text: str) -> None:
    lowered = text.lower()
    if "existing code" in lowered:
        raise ValueError("Output contains placeholder marker: 'existing code'")

    placeholder_line = re.compile(r"^\s*(?:(?://|/\*+|\*+)\s*)?\.\.\.\s*(?:\*/\s*)?$")
    for line in text.splitlines():
        if placeholder_line.match(line):
            raise ValueError("Output contains placeholder line: '...'")


def this_calls(text: str) -> set[str]:
    return set(re.findall(r"\$this->([A-Za-z_]\w*)\s*\(", text))


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            "Usage: snippet_replace_to_patch.py <phpFile> <relPath> <originalSnippetFile> <generatedSnippetFile> <outPatch>",
            file=sys.stderr,
        )
        return 2

    php_file = Path(argv[1])
    rel_path = argv[2]
    original_snippet_file = Path(argv[3])
    generated_snippet_file = Path(argv[4])
    out_patch = Path(argv[5])

    php_text = php_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    original_snippet = original_snippet_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    generated_snippet = strip_wrapping(generated_snippet_file.read_text(encoding="utf-8", errors="replace"))
    generated_snippet = generated_snippet.replace("\r\n", "\n").replace("\r", "\n")

    check_no_placeholders(generated_snippet)

    expected_start = "        $emailAddressCollection = array(); // used in linking to beans below"
    if not generated_snippet.startswith(expected_start):
        raise ValueError("Generated snippet does not start with the required first line")

    extra_calls = sorted(this_calls(generated_snippet) - this_calls(original_snippet))
    if extra_calls:
        raise ValueError(f"Generated snippet introduces new $this->method() calls: {extra_calls}")

    if original_snippet not in php_text:
        raise ValueError("Original snippet not found verbatim in target PHP file")

    new_php_text = php_text.replace(original_snippet, generated_snippet, 1)

    old_lines = php_text.splitlines(True)
    new_lines = new_php_text.splitlines(True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=3,
    )

    out_patch.parent.mkdir(parents=True, exist_ok=True)
    out_patch.write_text("".join(diff), encoding="utf-8", newline="\n")
    print(f"Wrote {out_patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
