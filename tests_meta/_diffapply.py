"""Minimal pure-Python unified-diff applier for the fast test suite.

The repo's patches (break.patch / golden.patch, agent-generated diffs) are plain
unified diffs produced by `diff -U0` / `diff -U3`. This applier lets the fast
tests build broken/golden trees and round-trip agent patches **without a system
`patch` binary** (adjustment #3). It is deliberately small: it handles the
subset of the format this project emits, not arbitrary patches.
"""

from __future__ import annotations

import pathlib
import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip_p1(path: str) -> str:
    # "a/charts/app/values.yaml" -> "charts/app/values.yaml"; "/dev/null" stays.
    if path == "/dev/null":
        return path
    parts = path.split("/", 1)
    return parts[1] if len(parts) == 2 else path


def parse(patch_text: str) -> list[dict]:
    """Return [{path, hunks:[{old_start, old_lines:[...], new_lines:[...]}]}]."""
    files: list[dict] = []
    cur: dict | None = None
    hunk: dict | None = None
    lines = patch_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- "):
            # header: --- a/x  (next line +++ b/x)
            new_path = ""
            if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                new_path = _strip_p1(lines[i + 1][4:].split("\t", 1)[0].strip())
                i += 1
            cur = {"path": new_path, "hunks": []}
            files.append(cur)
            hunk = None
            i += 1
            continue
        m = _HUNK_RE.match(ln)
        if m and cur is not None:
            old_start = int(m.group(1))
            hunk = {"old_start": old_start, "old_lines": [], "new_lines": []}
            cur["hunks"].append(hunk)
            i += 1
            while i < len(lines):
                b = lines[i]
                if b.startswith("@@ ") or b.startswith("--- "):
                    break
                if b.startswith("+"):
                    hunk["new_lines"].append(b[1:])
                elif b.startswith("-"):
                    hunk["old_lines"].append(b[1:])
                elif b.startswith(" "):
                    hunk["old_lines"].append(b[1:])
                    hunk["new_lines"].append(b[1:])
                elif b == "":
                    hunk["old_lines"].append("")
                    hunk["new_lines"].append("")
                else:
                    break
                i += 1
            continue
        i += 1
    return files


def apply_text(original: str, hunks: list[dict]) -> str:
    """Apply hunks to a file's text. Line-number anchored with a small search
    window; verifies the removed/context block matches before splicing."""
    src = original.splitlines()
    # apply from the bottom so earlier edits don't shift later offsets
    out = list(src)
    for h in sorted(hunks, key=lambda x: x["old_start"], reverse=True):
        old = h["old_lines"]
        new = h["new_lines"]
        start = h["old_start"] - 1  # 1-indexed -> 0-indexed
        if not old:  # pure insertion after `old_start`
            at = h["old_start"]  # @@ -N +M @@ with count 0 => insert after line N
            out[at:at] = new
            continue
        # locate the block: try the given position, then search +/- 40 lines
        cand = [start] + [start + d for d in range(1, 41)] + [start - d for d in range(1, 41)]
        pos = next((c for c in cand if 0 <= c and out[c:c + len(old)] == old), None)
        if pos is None:
            raise AssertionError(
                f"hunk context not found near line {h['old_start']}: {old!r}"
            )
        out[pos:pos + len(old)] = new
    trailing_nl = original.endswith("\n")
    return "\n".join(out) + ("\n" if trailing_nl else "")


def apply(tree: pathlib.Path, patch_path: pathlib.Path) -> list[str]:
    """Apply a `-p1` unified diff rooted at <tree>. Returns the relative paths
    touched."""
    files = parse(pathlib.Path(patch_path).read_text(encoding="utf-8"))
    touched = []
    for f in files:
        rel = f["path"]
        target = tree / rel
        text = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(apply_text(text, f["hunks"]), encoding="utf-8", newline="\n")
        touched.append(rel)
    return touched
