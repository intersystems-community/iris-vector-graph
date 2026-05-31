"""Spec 186 Phase A — silent error-swallow guard (SC-003).

Scope decision (2026-05-31): an exhaustive scan found ~95 ``except: pass``
sites, the vast majority legitimate (idempotent DDL, cursor cleanup in
``finally``, optional stats). Forcing logs into all of them adds noise and risk
for no benefit. This guard instead enforces logging at the specific
high-value sites the spec-186 audit named — where a swallowed error turns a
real failure into a mysterious empty result — and lets the rest stand as
reviewed-intentional.

Starts xfail; flipped to pass once Phase A logging (A5/A6) lands.
"""
from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OS_SITES = [
    ("iris_src/src/Graph/KG/Traversal.cls", "BuildNKG"),
    ("iris_src/src/Graph/KG/Traversal.cls", "InvalidateAdjCache"),
    ("iris_src/src/Graph/KG/PageRank.cls", None),
]
PY_SITES = [
    "iris_vector_graph/stores/iris_sql_store.py",
    "iris_vector_graph/stores/arno_bridge.py",
]

EMPTY_CATCH = re.compile(r"\}\s*Catch\s*(?:\w+)?\s*\{\s*\}")
LOG_TOKEN = re.compile(r"(logger|logging|warnings\.warn|_LOG)")


def _read(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_objectscript_named_sites_log_on_catch():
    offenders = []
    for rel, marker in OS_SITES:
        text = _read(rel)
        scope = text
        if marker:
            idx = text.find(marker)
            if idx == -1:
                continue
            scope = text[idx : idx + 1500]
        if EMPTY_CATCH.search(scope):
            offenders.append(f"{rel}::{marker or '*'}")
    assert not offenders, "Audit-named ObjectScript sites still swallow silently:\n  " + "\n  ".join(offenders)


def test_python_arno_modules_have_logging():
    offenders = [rel for rel in PY_SITES if not LOG_TOKEN.search(_read(rel))]
    assert not offenders, "Arno detection/store modules have no logging at all:\n  " + "\n  ".join(offenders)
