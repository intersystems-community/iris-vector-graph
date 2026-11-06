"""4.6.0 R050, live (Python-newcomer walkthrough W1, W2): every centrality answer is ranked, then cut to topK.

Before: `Graph.KG.NKGAccelCentrality.ClosenessGlobal` and `ClosenessGlobalMSBFS` emitted the nodes in ^NKG node order
and stopped at topK, so `closeness_centrality(top_k=3)` and `CALL ivg.closeness({topK: 3})` kept the first three nodes
by id (the walkthrough's: three scoring 0). `EigenvectorGlobal` emitted every node in index order whatever topK said:
the engine method re-sorted and cut in Python, `CALL ivg.eigenvector({topK: 3})` answered all of them unsorted.
Every answer is now highest first, ties by id, then cut.

Expected answers by hand (the unit file tests/unit/test_460_r050_centrality_ranking.py works them out):
  graph C (z->a, z->b, z->d, a->b, b->c, d->c): harmonic out-closeness z 3.5, a 1.5, b 1, d 1, c 0; classical z 0.8,
  the rest 0; out-degree / (n - 1) z 0.75, a b d 0.25, c 0.
  graph E (z->y, y->x, x->z, x->y, a->z, b->z): eigenvector y : x : z = L : 1 : 1/L with L^3 = L + 1, a and b 0;
  betweenness (unnormalised, directed) z 4 (a-y, a-x, b-y, b-x), y 3 (a-x, b-x, z-x), x 1 (y-z).
In both graphs the id order (a, b, ...) is not the score order, so a cut before ranking keeps the wrong nodes.

Runs in an IVG460FX* scratch namespace (scripts/dev/make_scratch_namespace.py), emptied before each graph.
"""

from __future__ import annotations

import math
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

C_EDGES = [("z", "a"), ("z", "b"), ("z", "d"), ("a", "b"), ("b", "c"), ("d", "c")]
E_EDGES = [("z", "y"), ("y", "x"), ("x", "z"), ("x", "y"), ("a", "z"), ("b", "z")]
HARMONIC = [("z", 3.5), ("a", 1.5), ("b", 1.0), ("d", 1.0), ("c", 0.0)]
CLASSICAL = [("z", 0.8), ("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.0)]
LAM = 1.3247179572447460  # the real root of L^3 = L + 1
_X = 1.0 / math.sqrt(LAM**2 + 1 + 1 / LAM**2)
EIGEN = [("y", LAM * _X), ("x", _X), ("z", _X / LAM), ("a", 0.0), ("b", 0.0)]
BETWEENNESS = [("z", 4.0), ("y", 3.0), ("x", 1.0)]


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    conn.close()


_LOADED: dict = {}


def _load(engine, edges):
    """The namespace holding exactly ``edges``; loaded only when another graph is there."""
    import iris

    if _LOADED.get("edges") != edges:
        iris.createIRIS(engine.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
        engine.conn.commit()
        engine.bulk_create_edges([{"source_id": s, "predicate": "R", "target_id": t} for s, t in edges])
        engine.conn.commit()
        engine.sync()
        _LOADED["edges"] = edges
    assert engine.node_count() == len({n for e in edges for n in e})
    return engine


@pytest.fixture
def graph_c(engine):
    return _load(engine, C_EDGES)


@pytest.fixture
def graph_e(engine):
    return _load(engine, E_EDGES)


def _same(got, expected, tol=1e-9):
    assert [n for n, _ in got] == [n for n, _ in expected], got
    assert [float(s) for _, s in got] == pytest.approx([s for _, s in expected], abs=tol), got


def _tier(engine, method, *args):
    import json

    raw = str(engine._iris_obj().classMethodValue("Graph.KG.NKGAccel", method, *args))
    assert raw.startswith("OK:"), raw
    return [(r["id"], r["score"]) for r in json.loads(raw[3:])]


def _cypher(engine, call):
    r = engine.execute_cypher(f"{call} YIELD node, score RETURN node.node_id AS id, score")
    assert r.error is None, r.error
    return [(n, float(s)) for n, s in r.rows]


# --- graph C: closeness and degree ---


@pytest.mark.parametrize("method", ["ClosenessGlobal", "ClosenessGlobalMSBFS"])
@pytest.mark.parametrize("top_k", [0, 3])
def test_each_objectscript_closeness_tier_ranks_then_cuts(graph_c, method, top_k):
    _same(_tier(graph_c, method, "harmonic", "out", 0, top_k), HARMONIC[: top_k or None])
    _same(_tier(graph_c, method, "classical", "out", 0, top_k), CLASSICAL[: top_k or None])


def test_closeness_centrality_is_highest_first(graph_c):
    _same([(r["id"], r["score"]) for r in graph_c.closeness_centrality()], HARMONIC)
    # the walkthrough's: top_k=3 kept a node below one it dropped
    _same([(r["id"], r["score"]) for r in graph_c.closeness_centrality(top_k=3)], HARMONIC[:3])
    _same([(r["id"], r["score"]) for r in graph_c.closeness_centrality(formula="classical", top_k=2)], CLASSICAL[:2])


def test_call_ivg_closeness_top_k_is_the_highest(graph_c):
    _same(_cypher(graph_c, "CALL ivg.closeness({topK: 3})"), HARMONIC[:3])
    _same(_cypher(graph_c, "CALL ivg.closeness()"), HARMONIC)
    _same(_cypher(graph_c, "CALL ivg.closeness({formula: 'classical', topK: 1})"), CLASSICAL[:1])


def test_call_ivg_degree_centrality_top_k_is_the_highest(graph_c):
    _same(_cypher(graph_c, "CALL ivg.degreeCentrality({direction: 'out', topK: 2})"), [("z", 0.75), ("a", 0.25)])


# --- graph E: eigenvector and betweenness ---


@pytest.mark.parametrize("top_k", [0, 3])
def test_the_objectscript_eigenvector_tier_ranks_then_cuts(graph_e, top_k):
    _same(_tier(graph_e, "EigenvectorGlobal", 1000, 1e-12, top_k), EIGEN[: top_k or None], tol=1e-6)


def test_eigenvector_centrality_is_highest_first(graph_e):
    got = graph_e.eigenvector_centrality(max_iter=1000, tol=1e-12, top_k=3)
    _same([(r["id"], r["score"]) for r in got], EIGEN[:3], tol=1e-6)


def test_call_ivg_eigenvector_honours_top_k(graph_e):
    _same(_cypher(graph_e, "CALL ivg.eigenvector({maxIter: 1000, tol: 1e-12, topK: 3})"), EIGEN[:3], tol=1e-6)


def test_call_ivg_betweenness_top_k_is_the_highest(graph_e):
    _same(_cypher(graph_e, "CALL ivg.betweenness({sampleSize: 5, topK: 2})"), BETWEENNESS[:2])



# --- graph N: neighbourhood betweenness ---

#: from s: s->a->b and s->z->{c, d, e}. Interned in this order, so a comes before z in ^NKG index order. Directed, z
#: lies on s-c, s-d, s-e (3) and a on s-b (1); undirected, z still scores highest. A cut in index order kept a.
N_EDGES = [("s", "a"), ("a", "b"), ("s", "z"), ("z", "c"), ("z", "d"), ("z", "e")]


@pytest.fixture
def graph_n(engine):
    return _load(engine, N_EDGES)


def test_betweenness_neighborhood_top_k_is_the_highest(graph_n):
    # outside USER the ObjectScript tier answers (the Arno call is USER-only, R234)
    assert NS != "USER"
    got = graph_n.betweenness_centrality_neighborhood("s", hops=2, sample_size=7, top_k=1)
    assert [r["id"] for r in got] == ["z"], got
