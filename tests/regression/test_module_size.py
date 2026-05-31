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


def _line_count(path: str) -> int:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def _python_modules():
    for path in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        if "browser_static" in path or "__pycache__" in path:
            continue
        yield path


@pytest.mark.xfail(reason="Spec 186 Phase C not yet complete — engine.py 8073 lines", strict=False)
def test_no_python_module_exceeds_2000_lines():
    offenders = [
        (os.path.relpath(p, REPO_ROOT), _line_count(p))
        for p in _python_modules()
        if _line_count(p) > MAX_PY_LINES
    ]
    offenders.sort(key=lambda t: t[1], reverse=True)
    msg = "\n".join(f"  {p}: {n} lines" for p, n in offenders)
    assert not offenders, f"Modules exceed {MAX_PY_LINES} lines:\n{msg}"


@pytest.mark.xfail(reason="Spec 186 Phase E not yet complete — NKGAccel/Traversal", strict=False)
def test_objectscript_god_classes_split():
    targets = ["NKGAccel.cls", "Traversal.cls"]
    base = os.path.join(REPO_ROOT, "iris_src", "src", "Graph", "KG")
    offenders = []
    for name in targets:
        path = os.path.join(base, name)
        if os.path.exists(path) and _line_count(path) > MAX_CLS_LINES:
            offenders.append((name, _line_count(path)))
    msg = "\n".join(f"  {n}: {ln} lines" for n, ln in offenders)
    assert not offenders, f"ObjectScript classes exceed {MAX_CLS_LINES} lines:\n{msg}"
