"""4.6.0 R232, live: `CALL ivg.betweenness` honours `direction` and `maxHops`, and so does `betweenness_centrality`.

Before: the SQL function `kg_Betweenness` took `direction` and `maxHops` and passed neither to
`Graph.KG.NKGAccel.BetweennessGlobal`, which walked out-edges without a depth limit. `direction:'both'` answered the
directed scores and `maxHops:2` the unbounded ones, with no error. `engine.betweenness_centrality(max_hops=2)` took the
same ObjectScript tier for "out" and "in" and dropped `max_hops` the same way. The server also cut `topK` by node id
before ranking, so `topK:3` answered the first three scoring nodes by id, not the three highest.

Expected answers come from networkx: `nx.betweenness_centrality(G, normalized=False)` unbounded, directed on the
`DiGraph` and undirected on `nx.Graph(G.to_undirected())`; with a hop limit k, every ordered pair (unordered when
undirected) whose distance is at most k adds, to each node inside each of its shortest paths
(`nx.all_shortest_paths`), one over their number. A 4-node path a->b->c->d checks it by hand: unbounded b and c are 2
(b on a-c and a-d), with maxHops 2 they are 1 (a-d is 3 hops).

Graphs: R216's walkthrough (10 nodes) and directed G(n, p) of 300 nodes; every call names `sampleSize` = N so the
answer is exact. Runs in an IVG460FX* scratch namespace (scripts/dev/make_scratch_namespace.py), emptied first.
"""

from __future__ import annotations

import itertools
import os

import networkx as nx
import pytest

from tests.e2e import ns460alt as alt
from tests.e2e.test_460_r216_betweenness_exact_e2e import WALKTHROUGH, _gnp300

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

PATH4 = [("a", "b"), ("b", "c"), ("c", "d")]
GRAPHS = {"path4": PATH4, "walkthrough": WALKTHROUGH, "gnp300": _gnp300()}


def bounded(g, k: int) -> dict:
    """networkx's unnormalized betweenness counting only pairs at most k hops apart (k=0: every pair)."""
    if not k:
        return nx.betweenness_centrality(g, normalized=False)
    bc = {v: 0.0 for v in g}
    for s in g:
        near = nx.single_source_shortest_path_length(g, s, cutoff=k)
        for t, d in near.items():
            if t == s or d < 2:
                continue
            if not g.is_directed() and str(t) < str(s):
                continue  # each unordered pair once
            paths = list(nx.all_shortest_paths(g, s, t))
            for p in paths:
                for v in p[1:-1]:
                    bc[v] += 1.0 / len(paths)
    return bc


def test_the_bounded_reference_by_hand():
    g = nx.DiGraph(PATH4)
    assert bounded(g, 0) == {"a": 0.0, "b": 2.0, "c": 2.0, "d": 0.0}
    assert bounded(g, 2) == {"a": 0.0, "b": 1.0, "c": 1.0, "d": 0.0}
    assert bounded(g, 1) == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
    assert bounded(nx.Graph(PATH4), 2) == {"a": 0.0, "b": 1.0, "c": 1.0, "d": 0.0}


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


@pytest.fixture(scope="module", params=sorted(GRAPHS))
def loaded(request, engine):
    import iris

    edges = GRAPHS[request.param]
    iris.createIRIS(engine.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    engine.conn.commit()
    engine.bulk_create_edges([{"source_id": s, "predicate": "R", "target_id": t} for s, t in edges])
    engine.conn.commit()
    engine.sync()
    g = nx.DiGraph(edges)
    assert engine.node_count() == g.number_of_nodes()
    return request.param, g


def _same(got: dict, expected: dict) -> bool:
    """Every node networkx scores above 0 at its score; nothing else above 0 (a 0 node may be left out)."""
    return all(got.get(n, 0.0) == pytest.approx(v, abs=1e-6) for n, v in expected.items()) and all(
        v == pytest.approx(0.0, abs=1e-9) for n, v in got.items() if n not in expected
    )


def _call(engine, opts: str) -> dict:
    r = engine.execute_cypher(
        f"CALL ivg.betweenness({{sampleSize: {engine.node_count()}{opts}}}) YIELD node, score RETURN node.node_id, score"
    )
    assert r.error is None, r.error
    return {n: float(s) for n, s in r.rows}


CASES = [(d, k) for d, k in itertools.product(("out", "in", "both"), (0, 1, 2)) if (d, k) != ("out", 0)]


@pytest.mark.parametrize("direction,hops", CASES, ids=[f"{d}-{k}" for d, k in CASES])
def test_the_procedure_is_networkx(loaded, engine, direction, hops):
    name, g = loaded
    ref = nx.Graph(g.to_undirected()) if direction == "both" else g
    expected = bounded(ref, hops)
    got = _call(engine, f", direction: '{direction}', maxHops: {hops}")
    assert _same(got, expected), (name, direction, hops)


@pytest.mark.parametrize("direction,hops", CASES, ids=[f"{d}-{k}" for d, k in CASES])
def test_the_python_api_is_networkx(loaded, engine, direction, hops):
    name, g = loaded
    ref = nx.Graph(g.to_undirected()) if direction == "both" else g
    expected = bounded(ref, hops)
    got = {r["id"]: r["score"] for r in engine.betweenness_centrality(
        sample_size=engine.node_count(), direction=direction, max_hops=hops, top_k=0)}
    assert _same(got, expected), (name, direction, hops)


def test_both_is_not_the_directed_answer(loaded, engine):
    """The defect's own symptom: on the walkthrough the undirected and directed scores differ."""
    name, g = loaded
    if name != "walkthrough":
        pytest.skip("one graph is enough")
    assert _call(engine, ", direction: 'both'") != _call(engine, ", direction: 'out'")


@pytest.mark.parametrize("k", [1, 3, 5])
def test_top_k_is_the_highest_scores(loaded, engine, k):
    name, g = loaded
    ranked = sorted(nx.betweenness_centrality(g, normalized=False).values(), reverse=True)
    if len(ranked) <= k or ranked[k - 1] == ranked[k] or ranked[k - 1] == 0:
        pytest.skip(f"{name}: a tie or a zero at {k}")
    got = _call(engine, f", topK: {k}")
    assert sorted(got.values(), reverse=True) == pytest.approx(ranked[:k], abs=1e-6), name
    api = engine.betweenness_centrality(sample_size=engine.node_count(), top_k=k)
    assert [r["score"] for r in api] == pytest.approx(ranked[:k], abs=1e-6), name


@pytest.mark.parametrize("opts", [", direction: 'sideways'", ", maxHops: -1"])
def test_a_value_it_cannot_honour_is_refused(engine, opts):
    with pytest.raises(ValueError, match="direction|maxHops"):
        engine.execute_cypher(f"CALL ivg.betweenness({{sampleSize: 4{opts}}}) YIELD node, score RETURN node.node_id")
