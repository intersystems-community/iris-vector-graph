"""Spec 186 Phase C — public API-surface guard.

Captures the exact public method set and signatures of IRISGraphEngine. The
engine.py god-class split (mixin decomposition) MUST NOT change the public
surface or the import path. This guard fails if any public method is removed
or has its signature changed — catching accidental API breaks during the
refactor. Additions are deliberately not failures; new methods are how the
library grows.

The baseline is a snapshot, so it has to be re-captured whenever a signature
changes on purpose. It was last captured at spec 186 and went stale across
214, 223, 226 and 227: 44 methods had appeared (silently tolerated) and 29
signatures had gained a `graph` or `model_key` parameter, so the changed-
signature leg had been failing for several releases and guarded nothing. It is
re-captured here for 4.0.0. Every one of those 29 changes is additive — a new
trailing or keyword-only parameter with a default — and nothing was removed;
re-baselining a *removal* would need a deprecation, not a snapshot refresh.
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
