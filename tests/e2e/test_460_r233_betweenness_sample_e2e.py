"""4.6.0 R233, live: a sampled betweenness takes the documented pseudo-random sources on the server and on the walk.

Before, `BetweennessGlobalParallel` took the first k node IDs in `^NKG("$NI")` order as sources (k = sample_size, or
200 for sample_size=0 above 200 nodes). The rule now (tests/unit/test_460_r233_betweenness_sample.py says it in full):
the k IDs first by SHA-1 of their UTF-8 bytes, scaled by N/k; sample_size >= N exact. Expected answers are
networkx's `betweenness_centrality_subset` from those sources, times N/k, on a 300-node graph.

The server answers while only the default graph has edges; the Python walk answers once a named graph has one
(R184). Both must give the same answer for the same call. Runs in an IVG460FX* scratch namespace, emptied first.
"""

from __future__ import annotations

import os

import networkx as nx
import pytest

from tests.e2e import ns460alt as alt
from tests.unit.test_460_r233_betweenness_sample import expected, graph300, sample

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = graph300()


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    eng.bulk_create_edges([{"source_id": s, "predicate": "R", "target_id": t} for s, t in G.edges()])
    conn.commit()
    eng.sync()
    assert eng.node_count() == 300
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    conn.close()


def _api(engine, **kw) -> dict:
    return {r["id"]: r["score"] for r in engine.betweenness_centrality(top_k=0, **kw)}


def _same(got: dict, want: dict) -> bool:
    return all(got.get(n, 0.0) == pytest.approx(v, rel=1e-9, abs=1e-6) for n, v in want.items())


SERVER = {}


@pytest.mark.parametrize("sample_size,k", [(0, 200), (50, 50)])
def test_the_server_samples_by_the_rule(engine, sample_size, k):
    got = _api(engine, sample_size=sample_size)
    assert _same(got, expected(G, k))
    SERVER[("out", sample_size)] = got


def test_the_server_was_not_given_the_first_ids(engine):
    first = sorted(G)[:200]
    sub = nx.betweenness_centrality_subset(G, sources=first, targets=list(G), normalized=False)
    assert first != sample(list(G), 200)
    assert not _same(_api(engine), {n: v * 300 / 200 for n, v in sub.items()})


def test_the_server_undirected_samples_by_the_rule(engine):
    got = _api(engine, direction="both")
    assert _same(got, expected(nx.Graph(G.to_undirected()), 200))
    SERVER[("both", 0)] = got


def test_the_procedure_samples_by_the_rule(engine):
    r = engine.execute_cypher("CALL ivg.betweenness({}) YIELD node, score RETURN node.node_id, score")
    assert r.error is None, r.error
    assert _same({n: float(s) for n, s in r.rows}, expected(G, 200))


def test_the_recipe_stays_exact(engine):
    assert _same(_api(engine, sample_size=engine.node_count()), nx.betweenness_centrality(G, normalized=False))


def test_the_python_walk_answers_as_the_server(engine):
    """A named graph's edge sends the call to the walk over the default graph's ^KG (R184): the same answer."""
    assert set(SERVER) == {("out", 0), ("out", 50), ("both", 0)}, "run the whole file"
    engine.create_edge("zz_a", "R", "zz_b", graph="r233_other")
    engine.conn.commit()
    try:
        assert engine._named_graph_with_edges() == "r233_other"
        for (direction, sample_size), server in SERVER.items():
            walk = _api(engine, sample_size=sample_size, direction=direction)
            assert walk == pytest.approx(server, rel=1e-9, abs=1e-6), (direction, sample_size)
    finally:
        engine.erase_graph("r233_other")
        engine.conn.commit()


def test_an_id_outside_ascii_is_sampled_by_its_utf8_bytes_on_the_server(engine):
    """Last: it replaces the 300-node graph. The server hashes $ZConvert(id, "O", "UTF8"), as Python hashes
    id.encode("utf-8")."""
    import iris

    g = nx.DiGraph([("é1", "b"), ("b", "ü2"), ("ü2", "é1"), ("b", "c"), ("c", "日本"), ("日本", "b")])
    iris.createIRIS(engine.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    engine.conn.commit()
    engine.bulk_create_edges([{"source_id": s, "predicate": "R", "target_id": t} for s, t in g.edges()])
    engine.conn.commit()
    engine.sync()
    for k in (1, 2, 3):
        assert _same(_api(engine, sample_size=k), expected(g, k)), k
