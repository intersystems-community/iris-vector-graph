"""4.6.0 R268, live: after a named graph's content goes (`erase_graph`, `delete_node`, `bulk_delete_nodes`), every
`^NKG` algorithm route answers the default graph's nodes, and only them, with no `sync()` in between.

Before: the Eraser dropped `^NKG` only when it could hold one of the erased EDGES. Its dictionary (`^NKG("$NI")`,
`("$ND")`) also holds every node that was ever an edge's end, and keeps a node after its last edge goes, so a named
graph whose edges were deleted before its nodes were erased stayed in `^NKG`: SCC, closeness and their CALLs listed
`x`, `y`, `z` after the graph was gone. And where the Eraser did drop `^NKG`, every `CALL` of an `^NKG` procedure
failed with `^NKG not built` until the next `sync()`.

The graphs, by hand. Default graph: two directed triangles joined by one edge,

    a -> b -> c -> a,   d -> e -> f -> d,   c -> d

and the named graph `r268`: the triangle x -> y -> z -> x (no node shared with the default graph).

    SCC (directed)      {a, b, c}, {d, e, f}; c -> d runs one way only
    k-core              every node 2 (each has two neighbours inside its triangle; a and b only two at all)
    triangles, lcc      one triangle each; lcc = 1 for a, b, e, f; c and d have three neighbours, one linked pair: 1/3
    Leiden              {a, b, c} and {d, e, f}: modularity 2 x (3/7 - (7/14)^2) = 0.357, against 0 for one community
    betweenness         exact (sample 0 is every source below 200 nodes), directed, unnormalized; every shortest path
                        is unique. From a: b on 4 paths (to c..f), c on 3, d on 2, e on 1; from b: c on 4, d on 2, e on
                        1; from c: a on 1 (to b), d on 2, e on 1; from d: e on 1; from e: f on 1; from f: d on 1.
                        a 1, b 4, c 7, d 7, e 4, f 1 (sum 24 = the sum over pairs of the path length less one)
    closeness           harmonic, out, unnormalized: the sum of 1/distance to each node reached
                        a: b1 c2 d3 e4 f5 = 137/60;  b: c1 a2 d2 e3 f4 = 31/12;  c: a1 d1 b2 e2 f3 = 10/3;
                        d: e1 f2 = 3/2;  e: f1 d2 = 3/2;  f: d1 e2 = 3/2

Each route runs against a namespace set up fresh for it (so no earlier route has rebuilt `^NKG` for it): build both
graphs, `sync()`, take the named graph's content away one of six ways, run the route. Runs in an IVG460FX*/IVG460FY*
scratch namespace made by `scripts/dev/make_scratch_namespace.py` (default IVG Schema, width 768).
"""

from __future__ import annotations

import os
from fractions import Fraction

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith(("IVG460FX", "IVG460FY")),
                       reason="IVG_NAMESPACE must name an IVG460FX*/IVG460FY* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

NAMED = "r268"
DEFAULT_EDGES = (("a", "b"), ("b", "c"), ("c", "a"), ("d", "e"), ("e", "f"), ("f", "d"), ("c", "d"))
NAMED_EDGES = (("x", "y"), ("y", "z"), ("z", "x"))
DEFAULT_NODES = {"a", "b", "c", "d", "e", "f"}
NAMED_NODES = ["x", "y", "z"]

SCC = {frozenset("abc"), frozenset("def")}
LEIDEN = {frozenset("abc"), frozenset("def")}
KCORE = {n: 2 for n in DEFAULT_NODES}
THIRD = 1 / 3
TRIANGLES = {"a": (1, 1.0), "b": (1, 1.0), "c": (1, THIRD), "d": (1, THIRD), "e": (1, 1.0), "f": (1, 1.0)}
BETWEENNESS = {"a": 1.0, "b": 4.0, "c": 7.0, "d": 7.0, "e": 4.0, "f": 1.0}
CLOSENESS = {
    "a": float(Fraction(1) + Fraction(1, 2) + Fraction(1, 3) + Fraction(1, 4) + Fraction(1, 5)),
    "b": float(Fraction(1) + Fraction(1, 2) + Fraction(1, 2) + Fraction(1, 3) + Fraction(1, 4)),
    "c": float(Fraction(1) + Fraction(1) + Fraction(1, 2) + Fraction(1, 2) + Fraction(1, 3)),
    "d": 1.5, "e": 1.5, "f": 1.5,
}


def test_the_hand_worked_numbers():
    assert CLOSENESS["a"] == pytest.approx(137 / 60)
    assert CLOSENESS["b"] == pytest.approx(31 / 12)
    assert CLOSENESS["c"] == pytest.approx(10 / 3)
    assert sum(BETWEENNESS.values()) == 24


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    yield c
    iris.createIRIS(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.commit()
    c.close()


@pytest.fixture(scope="module")
def engine(conn):
    from iris_vector_graph import IRISGraphEngine

    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    return eng


def _native(engine):
    import iris

    return iris.createIRIS(engine.conn)


def _delete_named_edges(engine):
    for s, o in NAMED_EDGES:
        engine.delete_edge(s, "R", o, graph=NAMED)
    engine.conn.commit()


def _erase_graph(engine):
    engine.erase_graph(NAMED)


def _delete_node(engine):
    for n in NAMED_NODES:
        assert engine.delete_node(n, graph=NAMED)


def _bulk_delete_nodes(engine):
    result = engine.bulk_delete_nodes(NAMED_NODES, graph=NAMED)
    assert (result.deleted, result.failed) == (3, 0)


#: how the named graph's content goes; the "edges first" forms leave its nodes interned in ^NKG with no edge
REMOVALS = {
    "erase_graph": (False, _erase_graph),
    "erase_graph, edges first": (True, _erase_graph),
    "delete_node": (False, _delete_node),
    "delete_node, edges first": (True, _delete_node),
    "bulk_delete_nodes": (False, _bulk_delete_nodes),
    "bulk_delete_nodes, edges first": (True, _bulk_delete_nodes),
}


@pytest.fixture(params=sorted(REMOVALS))
def removed(request, engine):
    native = _native(engine)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    engine.conn.commit()
    for s, o in DEFAULT_EDGES:
        engine.create_edge(s, "R", o)
    for s, o in NAMED_EDGES:
        engine.create_edge(s, "R", o, graph=NAMED)
    engine.conn.commit()
    engine.sync()
    interned = {k for k, _ in native.iterator("^NKG", "$NI").items()}
    assert interned == DEFAULT_NODES | set(NAMED_NODES), f"setup: sync interned {sorted(interned)}"
    edges_first, remove = REMOVALS[request.param]
    if edges_first:
        _delete_named_edges(engine)
        assert {k for k, _ in native.iterator("^NKG", "$NI").items()} >= set(NAMED_NODES), \
            "setup: deleting the edges took the nodes out of ^NKG's dictionary"
    remove(engine)
    engine.conn.commit()
    cur = engine.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", [NAMED])
    assert cur.fetchone()[0] == 0, "setup: the named graph's nodes are still there"
    cur.close()
    assert engine._named_graph_with_edges() is None
    return engine


def _cypher(engine, query):
    return engine.execute_cypher(query, raise_on_error=True).rows


def _parts(pairs):
    by = {}
    for node, part in pairs:
        by.setdefault(part, set()).add(node)
    return {frozenset(v) for v in by.values()}


# --- each route answers (node -> value) ---------------------------------------------------------------------------


def _leiden_method(e):
    return _parts((r["id"], r["community"]) for r in e.leiden_communities(random_seed=42))


def _leiden_call(e):
    return _parts(_cypher(e, "CALL ivg.leiden({randomSeed: 42}) YIELD node, community "
                             "RETURN node.node_id AS id, community"))


def _scc_method(e):
    return _parts((r["id"], r["component"]) for r in e.strongly_connected_components())


def _scc_call(e):
    return _parts(_cypher(e, "CALL ivg.scc() YIELD node, component RETURN node.node_id AS id, component"))


def _kcore_method(e):
    return {r["id"]: int(r["coreness"]) for r in e.k_core()}


def _kcore_call(e):
    return {i: int(c) for i, c in _cypher(e, "CALL ivg.kcore() YIELD node, coreness "
                                             "RETURN node.node_id AS id, coreness")}


def _rounded(d):
    return {k: tuple(round(float(x), 6) for x in v) if isinstance(v, tuple) else round(float(v), 6)
            for k, v in d.items()}


def _triangles_method(e):
    return _rounded({r["id"]: (int(r["triangles"]), r["lcc"]) for r in e.triangle_count()})


def _triangles_call(e):
    return _rounded({i: (int(t), lcc) for i, t, lcc in _cypher(
        e, "CALL ivg.triangleCount() YIELD node, triangles, lcc RETURN node.node_id AS id, triangles, lcc")})


def _betweenness_method(e):
    return _rounded({r["id"]: r["score"] for r in e.betweenness_centrality()})


def _betweenness_call(e):
    return _rounded({i: s for i, s in _cypher(
        e, "CALL ivg.betweenness() YIELD node, score RETURN node.node_id AS id, score") if s})


def _closeness_method(e):
    return _rounded({r["id"]: r["score"] for r in e.closeness_centrality()})


def _closeness_call(e):
    return _rounded({i: s for i, s in _cypher(
        e, "CALL ivg.closeness() YIELD node, score RETURN node.node_id AS id, score")})


ROUTES = {
    "leiden method": (_leiden_method, LEIDEN),
    "leiden CALL": (_leiden_call, LEIDEN),
    "scc method": (_scc_method, SCC),
    "scc CALL": (_scc_call, SCC),
    "kcore method": (_kcore_method, KCORE),
    "kcore CALL": (_kcore_call, KCORE),
    "triangles method": (_triangles_method, _rounded(TRIANGLES)),
    "triangles CALL": (_triangles_call, _rounded(TRIANGLES)),
    "betweenness method": (_betweenness_method, _rounded(BETWEENNESS)),
    "betweenness CALL": (_betweenness_call, _rounded(BETWEENNESS)),
    "closeness method": (_closeness_method, _rounded(CLOSENESS)),
    "closeness CALL": (_closeness_call, _rounded(CLOSENESS)),
}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_route_answers_the_default_graph_alone(removed, route):
    run, expected = ROUTES[route]
    assert run(removed) == expected


def test_a_dropped_nkg_comes_back_whole_on_its_next_read(removed):
    """The first algorithm read after the drop rebuilds ^NKG from ^KG: the dictionary is the default graph's six
    nodes, the edge rows its seven edges."""
    native = _native(removed)
    _scc_call(removed)
    assert {k for k, _ in native.iterator("^NKG", "$NI").items()} == DEFAULT_NODES
    edges = set()
    for s_idx, _ in native.iterator("^NKG", -1).items():
        s = native.get("^NKG", "$ND", s_idx)
        for neg_p, _ in native.iterator("^NKG", -1, s_idx).items():
            for o_idx, _ in native.iterator("^NKG", -1, s_idx, neg_p).items():
                edges.add((s, native.get("^NKG", "$ND", o_idx)))
    assert edges == set(DEFAULT_EDGES)
