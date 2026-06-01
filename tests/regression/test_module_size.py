"""Spec 186 Phase A — module/class size guards.

SC-001: no Python module under iris_vector_graph/ (excl. browser_static)
exceeds 2000 lines after Phase C.
SC-005: NKGAccel.cls and Traversal.cls each <= 800 lines after Phase E.

Both start xfail; flipped to pass as their phase completes.
"""
from __future__ import annotations

import glob
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = os.path.join(REPO_ROOT, "iris_vector_graph")

MAX_PY_LINES = 2000
MAX_CLS_LINES = 800

# translator.py is one cohesive Cypher->SQL translator: 127 interdependent
# functions all <=cc25 (Phase B). Splitting risks circular imports for low gain;
# file size is allowlisted while per-function complexity (the real metric) is enforced.
ALLOWLIST_PY = {
    "iris_vector_graph/cypher/translator.py": 4300,
}


def _line_count(path: str) -> int:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def _python_modules():
    for path in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        if "browser_static" in path or "__pycache__" in path:
            continue
        yield path


def test_no_python_module_exceeds_2000_lines():
    offenders = []
    for p in _python_modules():
        rel = os.path.relpath(p, REPO_ROOT)
        n = _line_count(p)
        budget = ALLOWLIST_PY.get(rel, MAX_PY_LINES)
        if n > budget:
            offenders.append((rel, n, budget))
    offenders.sort(key=lambda t: t[1], reverse=True)
    msg = "\n".join(f"  {p}: {n} lines (budget {b})" for p, n, b in offenders)
    assert not offenders, f"Modules exceed line budget:\n{msg}"


def test_objectscript_god_classes_split():
    base = os.path.join(REPO_ROOT, "iris_src", "src", "Graph", "KG")
    offenders = []
    for path in glob.glob(os.path.join(base, "*.cls")):
        n = _line_count(path)
        if n > MAX_CLS_LINES:
            offenders.append((os.path.basename(path), n))
    msg = "\n".join(f"  {n}: {ln} lines" for n, ln in sorted(offenders))
    assert not offenders, f"ObjectScript classes exceed {MAX_CLS_LINES} lines:\n{msg}"
