"""Every ObjectScript class/method this package dispatches to must exist.

A dispatch target is a pair of string literals — a class name and a method name —
handed to ``_call_classmethod``, ``_arno_call``, ``classMethodValue`` or
``classMethodVoid``. IRIS resolves that pair at call time, so a typo or a
class-name drift is invisible until the call runs, and every algorithm entry point
in ``iris_sql_store.py`` wraps its call in ``except Exception`` and answers with an
empty result. The combination reports "no subgraph", "no PPR scores", "no temporal
edges" for a method that was never there.

That is how ``execute_subgraph`` came to call ``Graph.KG.PageRank.SubgraphJson``
(``SubgraphJson`` lives on ``Graph.KG.Subgraph``) and answer every caller with an
empty subgraph, and how ``execute_temporal_cypher`` came to call a
``QueryWindowBFS`` that exists in no class at all.

This test parses the dispatch sites out of the Python sources and resolves each
pair against `iris_src/src`, following ``Extends``. It needs no IRIS.
"""

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC_DIRS = [REPO / "iris_vector_graph"]
CLS_ROOT = REPO / "iris_src" / "src"

DISPATCH_FUNCS = {"_call_classmethod", "_arno_call", "classMethodValue", "classMethodVoid"}


def _load_classes():
    """Return (methods_by_class, supers_by_class) parsed from the .cls tree."""
    methods, supers = {}, {}
    for path in CLS_ROOT.rglob("*.cls"):
        text = path.read_text(errors="ignore")
        m = re.search(r"^Class\s+([\w.]+)\s+Extends\s+([^\n{[]+)", text, re.M)
        if m:
            cls = m.group(1)
            raw = m.group(2).strip().strip("()")
            supers[cls] = [s.strip() for s in raw.split(",") if s.strip()]
        else:
            m2 = re.search(r"^Class\s+([\w.]+)", text, re.M)
            if not m2:
                continue
            cls = m2.group(1)
            supers[cls] = []
        methods[cls] = set(re.findall(r"^ClassMethod\s+(\w+)", text, re.M))
    return methods, supers


def _resolves(cls, meth, methods, supers, seen=()):
    if cls in seen:
        return False
    if meth in methods.get(cls, ()):
        return True
    return any(_resolves(s, meth, methods, supers, seen + (cls,)) for s in supers.get(cls, ()))


def _dispatch_sites():
    """Yield (file, line, class_name, method_name) for every literal dispatch."""
    sites = []
    for root in SRC_DIRS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name not in DISPATCH_FUNCS:
                    continue
                literals = [
                    a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ]
                # `_call_classmethod` exists both as a method (cls, meth, ...) and as a
                # module function taking the connection first; either way the class is
                # the first string literal that looks like an ObjectScript class name.
                pair = [s for s in literals if "." in s or s[:1].isupper()]
                cand = next((s for s in pair if "." in s), None)
                if cand is None:
                    continue
                idx = literals.index(cand)
                if idx + 1 >= len(literals):
                    continue
                sites.append((path.relative_to(REPO), node.lineno, cand, literals[idx + 1]))
    return sites


def test_dispatch_sites_are_found():
    """Guard the parser itself: a silent zero would make this file vacuous."""
    assert len(_dispatch_sites()) > 50


def test_every_dispatch_target_exists_in_iris_src():
    methods, supers = _load_classes()
    assert "Graph.KG.Subgraph" in methods, "class tree failed to parse"

    unresolved = []
    for rel, line, cls, meth in _dispatch_sites():
        if not cls.startswith(("Graph.KG.", "iris.vector.graph.", "PageRank")):
            continue
        # `%`-prefixed methods (`%Exists`, `%New`, …) come from %Library and are
        # never declared in this tree, so they are resolvable without being here.
        if meth.startswith("%"):
            continue
        if not _resolves(cls, meth, methods, supers):
            owners = sorted(c for c, ms in methods.items() if meth in ms)
            unresolved.append(f"{rel}:{line} {cls}.{meth} — defined in {owners or 'nothing'}")

    assert not unresolved, "dispatch targets that do not exist:\n" + "\n".join(unresolved)
