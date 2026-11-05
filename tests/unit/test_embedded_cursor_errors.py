"""Every embedded SQL cursor in the shipped classes checks for an error after OPEN and after each FETCH.

The loops read ``&sql(FETCH c)`` then ``Quit:SQLCODE'=0``, which takes an error (SQLCODE < 0) for the end of the rows
(SQLCODE 100). On 2026-11-05 (4.6.0 full E2E, IVGFIX) the session's native process stopped compiling cached queries
(-400). ``Graph.KG.Eraser.EraseAll``'s two route lookups, over ``embedding_registry`` and
``INFORMATION_SCHEMA.TABLES``, then found no routed vector table. A routed table with a row (its foreign key names
``nodes``) stayed full, and every later EraseAll raised "erase failed on nodes with SQLCODE -124; nothing was erased":
about 110 setups and teardowns in six files, none of which named the lookup that had failed.

The rule: the first statement after ``&sql(OPEN c)`` and after ``&sql(FETCH c ...)`` tests ``SQLCODE < 0``, and its
block either names the cursor (in the error it raises) or returns an answer (``Return``, the policy of
``GraphEdgesInNKG`` and ``GraphNodesInNKG``: rows it cannot read count as present).
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"

_CURSOR_OP = re.compile(r"&sql\(\s*(OPEN|FETCH)\s+(\w+)", re.IGNORECASE)
_ERROR_TEST = re.compile(r"SQLCODE\s*<\s*0")


def _block(lines: list, start: int) -> str:
    """Line `start` and, when it opens a `{`, every line to the matching `}`."""
    text, depth = "", 0
    for line in lines[start:]:
        text += line + "\n"
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            return text
    return text


def _next_statement(lines: list, after: int) -> int:
    for i in range(after + 1, len(lines)):
        s = lines[i].strip()
        if s and not s.startswith("//") and not s.startswith(";"):
            return i
    return len(lines)


def _sites() -> list:
    sites = []
    for path in sorted(SRC.rglob("*.cls")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _CURSOR_OP.search(line)
            if m:
                sites.append((path, i, m.group(1).upper(), m.group(2), lines))
    return sites


def _unchecked() -> list:
    bad = []
    for path, i, op, cursor, lines in _sites():
        j = _next_statement(lines, i)
        block = _block(lines, j) if j < len(lines) else ""
        checked = bool(_ERROR_TEST.search(lines[j])) if j < len(lines) else False
        answers = cursor in block or re.search(r"\bReturn\b", block)
        if not (checked and answers):
            bad.append(f"{path.relative_to(SRC)}:{i + 1} {op} {cursor}")
    return bad


def test_the_scan_finds_the_cursors():
    """Guard the guard: the Eraser alone opens and fetches nine cursors."""
    eraser = [s for s in _sites() if s[0].name == "Eraser.cls"]
    assert len([s for s in eraser if s[2] == "FETCH"]) == 9
    assert len([s for s in eraser if s[2] == "OPEN"]) == 9


def test_every_open_and_fetch_checks_for_an_error_and_names_its_cursor():
    bad = _unchecked()
    assert not bad, "an embedded cursor takes an error for the end of its rows:\n" + "\n".join(bad)
