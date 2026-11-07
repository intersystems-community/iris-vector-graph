"""A traversal answers the same edges whichever BFS route runs (4.6.0, the second Arno difference).

The Arno library's native BFS (``kg_bfs_global``, behind ``Graph.KG.NKGAccel.BFSJson``) reports each node once, at
the first edge that reaches it; ``Graph.KG.Traversal.BFSFastJsonSorted`` reports every edge it walks. On a graph with
two edges into one node (and two parallel edges) the routes' raw hits differ, so every user-facing answer built on
them is asked here in both modes, the library loaded and ``IVG_DISABLE_ARNO=1``, and held to openCypher's answer
worked by hand (relationship-unique paths, one row per path).

The graph, in the default graph (the only one the native route walks):

    s -T-> a  {w: 'sa1', k: 'x'}     s -U-> a  {w: 'sa2'}     s -T-> b  {w: 'sb'}
    a -T-> c  {w: 'ac',  k: 'x'}     b -T-> d  {w: 'bd', k: 'x'}
    s -T-> d  {w: 'sd',  k: 'x'}     c -T-> d  {w: 'cd'}         c -T-> 10 {w: 'c10'}

Paths from s of length 1: s-T->a, s-U->a, s-T->b, s-T->d (ends a, a, b, d); of length 2: s-T->a-T->c, s-U->a-T->c,
s-T->b-T->d (ends c, c, d). Over the edges with k = 'x' only: s->a, s->a->c, s->d (ends a, c, d).

Runs in the scratch namespace ``IVG_NAMESPACE`` names (an IVG460* one; the R309 gate makes one), which it erases.
"""

from __future__ import annotations

import os
from collections import Counter

import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith("IVG460"), reason="IVG_NAMESPACE must name an IVG460* scratch namespace "
                                                           "(the module erases its graph)"),
]

EDGES = [
    ("s", "T", "a", {"w": "sa1", "k": "x"}), ("s", "U", "a", {"w": "sa2"}), ("s", "T", "b", {"w": "sb"}),
    ("a", "T", "c", {"w": "ac", "k": "x"}), ("b", "T", "d", {"w": "bd", "k": "x"}),
    ("s", "T", "d", {"w": "sd", "k": "x"}), ("c", "T", "d", {"w": "cd"}),
    # three hops out, an id IRIS collates as a number: no Cypher case above walks this far
    ("c", "T", "10", {"w": "c10"}),
]

#: query, expected rows (a multiset), worked by hand from the docstring's paths
CASES = {
    "ends *1..2": ("MATCH (x {node_id: 's'})-[r*1..2]->(y) RETURN y.node_id",
                   Counter({"a": 2, "b": 1, "d": 2, "c": 2})),
    "distinct ends *1..2": ("MATCH (x {node_id: 's'})-[r*1..2]->(y) RETURN DISTINCT y.node_id",
                            Counter("abcd")),
    "count *1..2": ("MATCH (x {node_id: 's'})-[r*1..2]->(y) RETURN count(*) AS n", Counter({7: 1})),
    "count distinct *1..2": ("MATCH (x {node_id: 's'})-[r*1..2]->(y) RETURN count(DISTINCT y) AS n",
                             Counter({4: 1})),
    "ends *2..2": ("MATCH (x {node_id: 's'})-[r*2..2]->(y) RETURN y.node_id", Counter({"c": 2, "d": 1})),
    "ends *1..2 over k = x": ("MATCH (x {node_id: 's'})-[r*1..2 {k: 'x'}]->(y) RETURN y.node_id",
                              Counter("acd")),
    "count *1..2 over k = x": ("MATCH (x {node_id: 's'})-[r*1..2 {k: 'x'}]->(y) RETURN count(*) AS n",
                               Counter({3: 1})),
    "ends *1..1 typed T": ("MATCH (x {node_id: 's'})-[r:T*1..1]->(y) RETURN y.node_id", Counter("abd")),
    # the shapes the engine's far-end routes answer (Graph.KG.VarLength), not the SQL expansion: d is reached at
    # length 1 and 2, so a filter on the first depth BFS reaches a node would drop it from *2..2
    "distinct ends *2..2": ("MATCH (x {node_id: 's'})-[*2..2]->(y) RETURN DISTINCT y.node_id", Counter("cd")),
    "count distinct *2..2": ("MATCH (x {node_id: 's'})-[*2..2]->(y) RETURN count(DISTINCT y) AS n",
                             Counter({2: 1})),
    "distinct ends over k = x": ("MATCH (x {node_id: 's'})-[*1..2 {k: 'x'}]->(y) RETURN DISTINCT y.node_id",
                                 Counter("acd")),
    "distinct ends *2..2 over k = x": ("MATCH (x {node_id: 's'})-[*2..2 {k: 'x'}]->(y) RETURN DISTINCT y.node_id",
                                       Counter("c")),
    "distinct ends typed U": ("MATCH (x {node_id: 's'})-[:U*1..2]->(y) RETURN DISTINCT y.node_id", Counter("a")),
    "distinct ends inbound to d": ("MATCH (x {node_id: 'd'})<-[*1..2]-(y) RETURN DISTINCT y.node_id",
                                   Counter("abcs")),
    "types of *1..2": ("MATCH (x {node_id: 's'})-[r*1..2]->(y) RETURN y.node_id, [e IN r | type(e)] AS t",
                       Counter({("a", ("T",)): 1, ("a", ("U",)): 1, ("b", ("T",)): 1, ("d", ("T",)): 1,
                                ("c", ("T", "T")): 1, ("c", ("U", "T")): 1, ("d", ("T", "T")): 1})),
}


@pytest.fixture(scope="module", params=["arno", "no_arno"])
def mode(request):
    return request.param


_ENGINES: dict = {}


@pytest.fixture
def engine(mode, monkeypatch):
    from iris_vector_graph import IRISGraphEngine

    if mode == "no_arno":
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    else:
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    if mode not in _ENGINES:
        conn = ns262.connect(NS)
        eng = IRISGraphEngine(conn)
        if not _ENGINES:
            native = ns262.native(conn)
            native.classMethodValue("Graph.KG.Eraser", "EraseAll")
            for v in ("s", "a", "b", "c", "d", "10"):
                eng.create_node(v)
            for s, p, o, q in EDGES:
                eng.create_edge(s, p, o, qualifiers=q)
            eng.sync()
        _ENGINES[mode] = eng
    eng = _ENGINES[mode]
    if mode == "arno" and not eng._store._detect_arno():
        pytest.skip("the Arno library is not loaded on this container: there is no second route to compare")
    return eng


def teardown_module(module):
    for e in _ENGINES.values():
        try:
            ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
            e.conn.close()
        except Exception:  # noqa: BLE001 - best effort; the namespace is scratch
            pass
    _ENGINES.clear()


def _row(r):
    vals = [tuple(v) if isinstance(v, list) else v for v in r]
    return vals[0] if len(vals) == 1 else tuple(vals)


@pytest.mark.parametrize("case", sorted(CASES))
def test_cypher_answers_open_cypher_s_rows_on_each_route(engine, mode, case):
    query, want = CASES[case]
    got = Counter(_row(r) for r in engine.execute_cypher(query, raise_on_error=True).rows)
    assert got == want, f"{mode}: {query}\n  IVG      {sorted(got.items(), key=str)}\n  expected {sorted(want.items(), key=str)}"


def _bfs_reference(source, preds, max_hops, direction):
    """``execute_bfs``'s documented rows, from EDGES: each node other than the source once, at its BFS depth, with the
    first (in collation) of the types of the edges that reach it from a node one hop nearer, walked in ``direction``;
    ordered by depth, then id."""
    from tests.oracle.reference import collation_key

    steps = []  # (from, type, to) as walked
    for s, p, o, _q in EDGES:
        if preds and p not in preds:
            continue
        if direction in ("out", "both"):
            steps.append((s, p, o))
        if direction in ("in", "both"):
            steps.append((o, p, s))
    depth = {source: 0}
    for h in range(1, max_hops + 1):
        for u, _p, v in steps:
            if depth.get(u) == h - 1 and v not in depth:
                depth[v] = h
    rows = []
    for v, h in depth.items():
        if v == source:
            continue
        types = sorted({p for u, p, w in steps if w == v and depth.get(u) == h - 1}, key=collation_key)
        rows.append([v, h, types[0]])
    return sorted(rows, key=lambda r: (r[1], collation_key(r[0])))


@pytest.mark.parametrize("hops", [2, 3])
@pytest.mark.parametrize("source", ["s", "d"])
@pytest.mark.parametrize("direction", ["out", "in", "both"])
@pytest.mark.parametrize("preds", [[], ["T"], ["U", "T"]], ids=["every-type", "T", "U-T"])
def test_the_store_s_bfs_rows_are_the_same_on_each_route(engine, mode, source, direction, preds, hops):
    """``IRISGraphStore.execute_bfs``: ``(id, hops, pred)``, one row per node at its BFS depth with the first type (in
    collation) of an edge reaching it from the depth before, by depth then id. The ObjectScript route reported a node
    at every depth it was reached at, with the type staged last, and the library each node once, at the edge it took:
    on this graph the two answered d at depth 1 and 2 against d at 1, and s -> a as U against T."""
    got = engine._store.execute_bfs(source, preds, hops, direction, 0).rows
    assert [list(r) for r in got] == _bfs_reference(source, preds, hops, direction), mode


@pytest.mark.parametrize("max_results", [1, 2, 3])
def test_a_capped_bfs_keeps_the_nearest_rows_on_each_route(engine, mode, max_results):
    got = engine._store.execute_bfs("s", [], 2, "both", max_results).rows
    assert [list(r) for r in got] == _bfs_reference("s", [], 2, "both")[:max_results], mode


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_the_rows_type_read_answers_the_same_without_reach_types(engine, mode, direction, monkeypatch):
    """Where ``Graph.KG.TraversalBFS.ReachTypes`` cannot be called, the types are read from ``rdf_edges``: the same
    rows."""
    store = engine._store
    real = store._call_classmethod

    def refuse(cls, method, *args):
        if method == "ReachTypes":
            raise RuntimeError("<METHOD DOES NOT EXIST> forced")
        return real(cls, method, *args)

    monkeypatch.setattr(store, "_call_classmethod", refuse)
    got = store.execute_bfs("s", [], 3, direction, 0).rows
    assert [list(r) for r in got] == _bfs_reference("s", [], 3, direction), mode
