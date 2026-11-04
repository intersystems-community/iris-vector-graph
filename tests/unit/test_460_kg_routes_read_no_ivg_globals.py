"""4.6.0 R147: no `/kg` route reads a global `^IVG` or `^IVG.*`, so the route role needs no read on where they live.

`Graph.KG.Service.EnsureWebAppRole` grants read on the database each of `^KG`, `^NKG` and `^IVG` is mapped to, through
`%SYS.Namespace.GetGlobalDest(ns, name)`. For "IVG" that resolves `^IVG` alone: `^IVG.Ledger` or `^IVG.AlgoCache`
mapped to a database of their own would get no read. That is harmless only while no route reads them, which this
file checks: it follows every call the three routes make, from `Graph.KG.Service.VectorSearch`, `HybridSearch` and
`MetaPath`, through `##class(Graph.KG.X).M(`, `..M(` and embedded Python's `iris.cls("Graph.KG.X").M(`, and asserts
no method reached names `^IVG`. The SQL procedures the routes call (`kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE`) are DDL in
`iris_vector_graph/schema.py` and read tables only.

A route that starts reading one fails here, naming the method: then `EnsureWebAppRole` must resolve that global too.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
ROUTES = [("Graph.KG.Service", m) for m in ("VectorSearch", "HybridSearch", "MetaPath")]

_METHOD = re.compile(r"^(?:Class)?Method (\w+)\(", re.M)
_CLASS_CALL = re.compile(r"##class\((Graph\.KG\.[\w.]+)\)\.(\w+)\(")
_PY_CALL = re.compile(r"""iris\.cls\(\s*["'](Graph\.KG\.[\w.]+)["']\s*\)\.(\w+)\(""")
_SELF_CALL = re.compile(r"(?<![\w#])\.\.(\w+)\(")


def _methods(cls: str) -> dict:
    """{method name: body} for one class source: the text from its `{` line to the `}` line that closes it."""
    text = (SRC / (cls.replace(".", "/") + ".cls")).read_text()
    out = {}
    for match in _METHOD.finditer(text):
        start = text.index("\n{", match.end()) + 2
        end = text.index("\n}", start)
        out[match.group(1)] = text[start:end]
    return out


def _reached() -> dict:
    """{(class, method): body} for every IVG method the routes reach."""
    sources: dict = {}
    seen: dict = {}
    todo = list(ROUTES)
    while todo:
        cls, name = todo.pop()
        if (cls, name) in seen:
            continue
        methods = sources.setdefault(cls, _methods(cls))
        assert name in methods, f"{cls}.{name} is called by a route and not found in {cls}.cls"
        body = seen[(cls, name)] = methods[name]
        todo += [(c, m) for c, m in _CLASS_CALL.findall(body)]
        todo += [(c, m) for c, m in _PY_CALL.findall(body)]
        todo += [(cls, m) for m in _SELF_CALL.findall(body) if m in methods]
    return seen


def test_the_walk_reaches_the_code_each_route_runs():
    """The walk itself is checked: each route's own reads are in what it found."""
    reached = set(_reached())
    for expected in (("Graph.KG.PyOps", "VectorSearch"), ("Graph.KG.PyOps", "HybridSearch"),
                     ("Graph.KG.PyOps", "getExpectedDimension"), ("Graph.KG.PyOps", "getVectorTable"),
                     ("Graph.KG.TraversalBFS", "BFS"), ("Graph.KG.TraversalBFS", "TraverseWithPredicate"),
                     ("Graph.KG.TraversalBFS", "TraverseAllPredicates"), ("Graph.KG.GraphKey", "ForIndex")):
        assert expected in reached, (expected, sorted(reached))


def test_the_walk_sees_a_global_reference_where_there_is_one():
    """The pattern below finds the ^KG reads /metaPath makes, so its silence on ^IVG means something."""
    bodies = _reached()
    assert re.search(r"\^KG\(", bodies[("Graph.KG.TraversalBFS", "TraverseWithPredicate")])


def test_no_kg_route_reads_an_ivg_global():
    readers = sorted(f"{c}.{m}" for (c, m), body in _reached().items() if re.search(r"\^IVG\b", body))
    assert readers == [], f"a /kg route reads ^IVG or ^IVG.*: {readers}; EnsureWebAppRole must resolve that global"
