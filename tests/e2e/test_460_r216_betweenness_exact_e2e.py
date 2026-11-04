"""4.6.0 R216, live: USER_GUIDE's exact-betweenness recipe, `sample_size=engine.node_count()`, is networkx's answer.

USER_GUIDE said "pass the node count minus one for an exact answer". `sample_size` is the number of sources, so N-1
leaves one out and scales the rest by N/(N-1): on the walkthrough's 10-node graph every score came back 10/9 of
networkx's (ann 14.44 for 13.0). What each value means, on each route:

- the `^NKG` route (direction "out"/"in" while only the default graph has edges; `CALL ivg.betweenness` too):
  0 < s < N takes the first s nodes in ID order as sources and scales by N/s; s >= N takes every source (exact);
  0 takes every source when N <= 200 and the first 200 above that (scaled by N/200), an approximation;
- the Python walk (direction "both", or a named graph holds edges): 0 < s < N samples s sources at random and scales
  by N/s; 0 and s >= N take every source.

So s >= N is exact everywhere, and `engine.node_count()` (every graph's nodes, never fewer than the default graph's)
is one. Before R216 direction "both" also gave twice networkx's undirected score (tests/unit/
test_460_r216_betweenness_undirected.py).

Graphs: the walkthrough's (10 nodes, directed) and a directed G(n, p) of 300 nodes (more than 200, so 0 is not exact).
Undirected: the same two with direction="both" against networkx on `nx.Graph(G.to_undirected())`.
Runs in an IVG460FX* scratch namespace (scripts/dev/make_scratch_namespace.py), emptied first.
"""

from __future__ import annotations

import os

import networkx as nx
import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

WALKTHROUGH = [
    ("ann", "ben"), ("ben", "cat"), ("cat", "dan"), ("ann", "cat"), ("dan", "eve"), ("eve", "fay"), ("fay", "ann"),
    ("ben", "dan"), ("ann", "globex"), ("ben", "globex"), ("cat", "initech"), ("dan", "initech"), ("eve", "globex"),
    ("ann", "python"), ("ben", "python"), ("cat", "cypher"), ("fay", "python"), ("fay", "cypher"),
]


def _gnp300() -> list:
    g = nx.gnp_random_graph(300, 0.012, seed=5, directed=True)
    isolated = {n for n in g if g.degree(n) == 0}
    return [(f"d{s:03d}", f"d{t:03d}") for s, t in g.edges() if s not in isolated and t not in isolated]


GRAPHS = {"walkthrough": WALKTHROUGH, "gnp300": _gnp300()}


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


def _scores(engine, **kw) -> dict:
    return {r["id"]: r["score"] for r in engine.betweenness_centrality(top_k=0, **kw)}


def _close(got: dict, expected: dict) -> bool:
    return set(got) <= set(expected) and all(
        got.get(n, 0.0) == pytest.approx(v, abs=1e-6) for n, v in expected.items()
    )


def test_the_recipe_is_networkx_directed(loaded, engine):
    _, g = loaded
    expected = nx.betweenness_centrality(g, normalized=False)
    assert _close(_scores(engine, sample_size=engine.node_count()), expected)


def test_the_recipe_is_networkx_undirected_with_direction_both(loaded, engine):
    _, g = loaded
    expected = nx.betweenness_centrality(nx.Graph(g.to_undirected()), normalized=False)
    assert _close(_scores(engine, sample_size=engine.node_count(), direction="both"), expected)


def test_the_cypher_procedure_with_the_recipe_is_networkx_directed(loaded, engine):
    _, g = loaded
    r = engine.execute_cypher(
        f"CALL ivg.betweenness({{sampleSize: {engine.node_count()}}}) YIELD node, score RETURN node.node_id, score"
    )
    assert r.error is None, r.error
    assert _close({n: float(s) for n, s in r.rows}, nx.betweenness_centrality(g, normalized=False))


def test_the_old_recipe_n_minus_one_is_not_exact(loaded, engine):
    _, g = loaded
    assert not _close(_scores(engine, sample_size=g.number_of_nodes() - 1),
                      nx.betweenness_centrality(g, normalized=False))


def test_the_default_is_exact_up_to_200_nodes_and_sampled_above(loaded, engine):
    name, g = loaded
    exact = _close(_scores(engine), nx.betweenness_centrality(g, normalized=False))
    assert exact == (g.number_of_nodes() <= 200), name
