"""4.6.3, live: graph algorithms answer node ids holding a quote or a backslash exactly, with networkx's scores.

``Graph.KG.Communities`` and the centrality classes build their answers as JSON in ObjectScript. One that wrote a node
id between quotes by concatenation, unescaped, broke on ``a"b`` (the quote ends the string) and on ``c\\d`` (``\\d`` is
no JSON escape). The graph here is two components, ``a"b -> c\\d -> e -> a"b``, ``e -> x"\\y``, and
``p -> q\\"r``, so every id but two holds a quote, a backslash or both. Each method and Cypher procedure must answer
those ids exactly, with the scores networkx computes on the same graph (CDLP, which networkx does not have in this
form, against tests/oracle/reference.py's rule). Each case runs with the Arno library as found and with
``IVG_DISABLE_ARNO=1`` (the algorithm cache off too).

Runs in the scratch namespace ``IVG_NAMESPACE`` names (IVG463*, IVG460* or IVG262*, IVG installed); erases its graph.

    IVG_NAMESPACE=IVG463B pytest tests/e2e/test_463_json_id_escaping_e2e.py
"""

from __future__ import annotations

import os

import networkx as nx
import pytest
from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

from tests.e2e import ns262
from tests.oracle import reference as ref
from tests.oracle.graphs import Edge, OracleGraph

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith(("IVG463", "IVG460", "IVG262")),
                       reason="IVG_NAMESPACE must name an IVG463*/IVG460*/IVG262* scratch namespace: it erases its graph"),
]

Q, B, QB, QB2 = 'a"b', "c\\d", 'x"\\y', 'q\\"r'
NODES = [Q, B, "e", QB, "p", QB2]
EDGES = [(Q, B), (B, "e"), ("e", Q), ("e", QB), ("p", QB2)]
N = len(NODES)


def _digraph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_nodes_from(NODES)
    G.add_edges_from(EDGES)
    return G


def _oracle() -> OracleGraph:
    return OracleGraph(seed=463, nodes=list(NODES), edges=[Edge(s, "R", o) for s, o in EDGES])


@pytest.fixture(scope="module", params=["arno", "no_arno"])
def engine(request):
    mp = pytest.MonkeyPatch()
    if request.param == "no_arno":
        mp.setenv("IVG_DISABLE_ARNO", "1")
        mp.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    else:
        mp.delenv("IVG_DISABLE_ARNO", raising=False)
        mp.delenv("IVG_DISABLE_ALGO_CACHE", raising=False)
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(ns262.connect(NS))
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    for v in NODES:
        e.create_node(v)
    for s, o in EDGES:
        e.create_edge(s, "R", o)
    e.sync()
    assert sorted(r[0] for r in e.execute_cypher("MATCH (n) RETURN n.node_id").rows) == sorted(NODES)
    yield e
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    e.conn.close()
    mp.undo()


def cy(engine, q):
    r = engine.execute_cypher(q)
    assert not getattr(r, "error", None), r.error
    return {row[0]: row[1] for row in r.rows}


def test_wcc(engine):
    want = {}
    for comp in nx.weakly_connected_components(_digraph()):
        label = min(comp, key=ref.collation_key)
        want.update((v, label) for v in comp)
    assert engine.kg_WCC() == want


def test_cdlp(engine):
    assert engine.kg_CDLP() == ref.cdlp(_oracle())


def test_pagerank(engine):
    want = _pagerank_python(_digraph(), alpha=0.85, tol=1e-12, max_iter=1000)
    got = dict(engine.kg_PAGERANK(max_iterations=200))
    assert set(got) == set(NODES)
    assert got == pytest.approx(want, abs=1e-6)
    stream = cy(engine, "USE GRAPH '' CALL gds.pageRank.stream('g', {dampingFactor: 0.85, maxIterations: 200}) "
                        "YIELD nodeId, score RETURN nodeId, score")
    assert stream == pytest.approx(want, abs=1e-6)


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_degree(engine, direction):
    G = _digraph()
    deg = {"out": G.out_degree, "in": G.in_degree, "both": G.degree}[direction]
    want = {v: d / (N - 1) for v, d in deg()}
    got = {d["id"]: d["score"] for d in engine.degree_centrality(direction=direction, top_k=0)}
    assert got == pytest.approx(want)
    q = f"CALL ivg.degreeCentrality({{direction: '{direction}'}}) YIELD node, score RETURN node.node_id, score"
    assert cy(engine, q) == pytest.approx(want)


def test_closeness(engine):
    # harmonic closeness of the graph read both ways: networkx's harmonic_centrality on the undirected graph
    want = nx.harmonic_centrality(_digraph().to_undirected())
    got = {d["id"]: d["score"] for d in engine.closeness_centrality(formula="harmonic", direction="both", top_k=0)}
    assert got == pytest.approx(want)
    q = "CALL ivg.closeness({formula: 'harmonic', direction: 'both'}) YIELD node, score RETURN node.node_id, score"
    assert cy(engine, q) == pytest.approx(want)


@pytest.mark.parametrize("direction", ["out", "both"])
def test_betweenness(engine, direction):
    G = _digraph() if direction == "out" else _digraph().to_undirected()
    want = nx.betweenness_centrality(G, normalized=False)
    nonzero = {v: s for v, s in want.items() if s}
    assert set(nonzero) & {Q, B, "e"}  # the escaped ids are among the scored ones
    got = {d["id"]: d["score"] for d in engine.betweenness_centrality(sample_size=N, direction=direction)}
    assert {v: s for v, s in got.items() if s} == pytest.approx(nonzero)
    q = (f"CALL ivg.betweenness({{sampleSize: {N}, direction: '{direction}'}}) YIELD node, score "
         "RETURN node.node_id, score")
    assert {v: s for v, s in cy(engine, q).items() if s} == pytest.approx(nonzero)
