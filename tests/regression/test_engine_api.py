"""Spec 186 Phase C — public API-surface guard.

Captures the exact public method set and signatures of IRISGraphEngine. The
engine.py god-class split (mixin decomposition) MUST NOT change the public
surface or the import path. This guard fails if any public method is removed,
added, or has its signature changed — catching accidental API breaks during
the refactor.
"""
from __future__ import annotations

import inspect
import json
import os

from iris_vector_graph.engine import IRISGraphEngine

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE = os.path.join(
    REPO_ROOT, "tests", "regression", "engine_api_baseline.json"
)


def _normalize_sig(sig: str) -> str:
    return sig.replace("'", "").replace("Optional[str]", "str | None").replace(
        "Optional[int]", "int | None"
    ).replace("NoneType", "None").replace(" ", "")


def _current_surface():
    surface = {}
    for name in dir(IRISGraphEngine):
        if name.startswith("_"):
            continue
        attr = inspect.getattr_static(IRISGraphEngine, name, None)
        if not callable(attr) and not isinstance(attr, (staticmethod, classmethod)):
            continue
        target = attr.__func__ if isinstance(attr, (staticmethod, classmethod)) else attr
        try:
            sig = str(inspect.signature(target))
        except (TypeError, ValueError):
            sig = "<unknown>"
        surface[name] = sig
    return surface


def test_engine_public_api_unchanged():
    current = _current_surface()
    if not os.path.exists(BASELINE):
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)
        return
    with open(BASELINE, "r", encoding="utf-8") as fh:
        baseline = json.load(fh)
    removed = sorted(set(baseline) - set(current))
    added = sorted(set(current) - set(baseline))
    changed = sorted(
        n
        for n in set(baseline) & set(current)
        if _normalize_sig(baseline[n]) != _normalize_sig(current[n])
    )
    problems = []
    if removed:
        problems.append(f"REMOVED public methods: {removed}")
    if changed:
        problems.append(
            "CHANGED signatures:\n"
            + "\n".join(f"  {n}: {baseline[n]} -> {current[n]}" for n in changed)
        )
    assert not problems, "IRISGraphEngine public API changed:\n" + "\n".join(problems)


def test_engine_import_path_stable():
    from iris_vector_graph import engine as engine_mod
    assert hasattr(engine_mod, "IRISGraphEngine")
    from iris_vector_graph.engine import IRISGraphEngine as Direct
    assert Direct is IRISGraphEngine
