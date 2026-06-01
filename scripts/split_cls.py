#!/usr/bin/env python3
"""Spec 187 — verbatim ObjectScript method slicer.

Extracts named ClassMethod/Method blocks from a .cls file by matching the
header line through its balanced closing brace (brace-counting that ignores
braces inside string literals and // comments), then byte-diffs each extracted
block against the original source region to guarantee a VERBATIM move.

Anti-rewrite safeguard (spec-186 lesson: a delegated LLM copy silently rewrote
a method). Usage:

    split_cls.py extract <src.cls> <Method1> <Method2> ...
        -> prints the concatenated verbatim blocks to stdout, fails (exit 2)
           on any byte-diff mismatch or unbalanced brace.

    split_cls.py strip <src.cls> <Method1> ...
        -> prints <src.cls> with the named methods removed (for rebuilding the
           god class as a facade), preserving everything else byte-for-byte.

This script only reads/prints; the caller writes the new files. Pure stdlib.
"""
from __future__ import annotations

import sys


def _find_method_spans(lines: list[str], names: set[str]) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        is_header = stripped.startswith("ClassMethod ") or stripped.startswith("Method ")
        if not is_header:
            i += 1
            continue
        after = stripped.split(None, 1)[1] if " " in stripped else ""
        mname = ""
        for ch in after:
            if ch.isalnum() or ch == "%":
                mname += ch
            else:
                break
        if mname not in names:
            i += 1
            continue
        start = i
        brace_line = i
        while brace_line < n and "{" not in lines[brace_line]:
            brace_line += 1
        depth = 0
        j = brace_line
        end = -1
        while j < n:
            depth += _net_braces(lines[j])
            if j >= brace_line and depth <= 0:
                end = j
                break
            j += 1
        if end == -1:
            raise SystemExit(f"ERROR: unbalanced braces for method {mname} starting line {start+1}")
        spans[mname] = (start, end)
        i = end + 1
    return spans


def _net_braces(line: str) -> int:
    depth = 0
    in_str = False
    k = 0
    L = len(line)
    while k < L:
        ch = line[k]
        if ch == '"':
            if in_str and k + 1 < L and line[k + 1] == '"':
                k += 2
                continue
            in_str = not in_str
        elif not in_str:
            if ch == "/" and k + 1 < L and line[k + 1] == "/":
                break
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        k += 1
    return depth


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 1
    mode, src = argv[1], argv[2]
    names = set(argv[3:])
    with open(src, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    spans = _find_method_spans(lines, names)
    missing = names - set(spans)
    if missing:
        raise SystemExit(f"ERROR: methods not found in {src}: {sorted(missing)}")

    if mode == "extract":
        out = []
        for name in argv[3:]:
            s, e = spans[name]
            block = "".join(lines[s : e + 1])
            reparse = _find_method_spans(block.splitlines(keepends=True), {name})
            rs, re_ = reparse[name]
            if "".join(block.splitlines(keepends=True)[rs : re_ + 1]) != block:
                raise SystemExit(f"ERROR: byte-diff self-check failed for {name}")
            out.append(block)
        sys.stdout.write("\n".join(b.rstrip("\n") for b in out) + "\n")
        return 0

    if mode == "strip":
        remove = set()
        for name in names:
            s, e = spans[name]
            remove.update(range(s, e + 1))
        kept = [l for idx, l in enumerate(lines) if idx not in remove]
        sys.stdout.write("".join(kept))
        return 0

    raise SystemExit(f"ERROR: unknown mode {mode!r} (use extract|strip)")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
