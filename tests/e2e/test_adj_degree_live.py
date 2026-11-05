"""ADR-0010 against IRIS: Graph.KG.Adj counts equal the SQL rows, and the analytics
readers that moved onto it return what the SQL rows say they should.

Builds a small graph with parallel edges in a named multigraph, plus a default
graph, and a rolled-back SQL insert. Then:

- Graph.KG.Adj.Degree (per node, and per node and predicate) equals the count of
  live Graph_KG.rdf_edges rows, and the rolled-back row is not counted;
- NextSource enumerates exactly the sources, and HasEdges agrees with Degree;
- Graph.KG.Centrality.DegreeCentralityJson (out) and
  Graph.KG.PageRank.PageRankGlobalJson equal values computed in Python from the
  same rows, with the same algorithm, including the parallel-edge divisor;
- Graph.KG.PageRank.RunJson on the named graph equals networkx's personalized PageRank (4.6.0 R270).

Runs against ivg-iris-enterprise only (the lead runs it; it needs a live IRIS).
The ObjectScript for Adj.cls and the four migrated classes is uncompiled until
this runs.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections import Counter, defaultdict

import pytest

pytestmark = [pytest.mark.e2e]

G_NAMED = "ivg242:adj"
PRED_A = "IVG242_KNOWS"
PRED_B = "IVG242_LIKES"
NODES = [f"ivg242:n{i}" for i in range(6)]
GHOST = "ivg242:ghost"  # exists only as a rolled-back row


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail("spec 242 asserts ^KG on the server; SKIP_IRIS_TESTS=true is not acceptable")
    if iris_connection is None:
        pytest.fail("no live IRIS connection")


def _iris(conn):
    import iris as _iris_mod

    return _iris_mod.createIRIS(conn)


def _key(iris_obj, graph):
    return iris_obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph)


def _rows(conn, graph):
    """Live edge rows of one graph, read from SQL: (s, p, o) per row, with multiplicity."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT s, p, o_id FROM Graph_KG.rdf_edges WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            [graph or ""],
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        cur.close()


# --- Python reference: the same algorithms the ObjectScript runs ----------------------


def _ref_degree(rows):
    return Counter(s for s, _p, _o in rows)


def _ref_centrality_out(rows):
    """DegreeCentralityJson(direction="out", all predicates): {id: (score, degree)}."""
    deg = _ref_degree(rows)
    nodes = {s for s, _p, _o in rows} | {o for _s, _p, o in rows}
    n = len(nodes)
    norm = 1.0 / (n - 1) if n > 1 else 1.0
    return {node: (deg.get(node, 0) * norm, deg.get(node, 0)) for node in nodes}


def _ref_pagerank_global(rows, damping=0.85, max_iter=20):
    """PageRankGlobalJson, textbook since 4.5.2: every source and target is a node, a row passes its share (so a parallel
    edge counts once per row), a node with no out-rows spreads its rank over all nodes, and the walk stops early only
    at a fixed point."""
    deg = _ref_degree(rows)
    nodes = sorted({s for s, _p, _o in rows} | {o for _s, _p, o in rows})
    n = len(nodes)
    if n == 0:
        return {}
    ranks = {v: 1.0 / n for v in nodes}
    for _ in range(max_iter):
        dangling = sum(r for v, r in ranks.items() if v not in deg)
        new = {v: (1 - damping) / n + damping * dangling / n for v in nodes}
        for s, _p, o in rows:
            new[o] += damping * ranks[s] / deg[s]
        done = max(abs(new[v] - ranks[v]) for v in nodes) < 1e-15
        ranks = new
        if done:
            break
    return ranks


def _ref_ppr(rows, seeds, alpha=0.85, tol=1e-12):
    """RunJson since 4.6.0 R270: networkx's personalized PageRank on the rows as a multigraph, so a parallel row is
    one more edge (n0 -> n1 three times weighs 3 of n0's 4), and a dangling node's score goes back to the seeds."""
    import networkx as nx
    from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

    g = nx.MultiDiGraph()
    for s, _p, o in rows:
        g.add_edge(s, o)
    return _pagerank_python(g, alpha=alpha, personalization={x: 1 for x in seeds}, tol=tol, max_iter=10000)


# --- fixtures ------------------------------------------------------------------------


@pytest.fixture
def adj_graph(iris_connection, iris_master_cleanup):
    _require_iris(iris_connection)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    for node in NODES:
        engine.create_node(node, graph=None)
        engine.create_node(node, graph=G_NAMED)
    # Default graph: distinct edges only.
    for s, p, o in [
        (NODES[0], PRED_A, NODES[1]),
        (NODES[0], PRED_A, NODES[2]),
        (NODES[0], PRED_B, NODES[2]),
        (NODES[1], PRED_A, NODES[2]),
        (NODES[3], PRED_A, NODES[4]),
    ]:
        engine.create_edge(s, p, o, graph=None)
    # Named multigraph: parallel edges on the same triple. create_edge upserts, so a parallel edge is a
    # row with its own ekey plus the keyed adjacency write (the way test_234_multigraph_storage_e2e does it).
    engine.set_multigraph(G_NAMED, True)
    iris_obj = _iris(iris_connection)
    key = _key(iris_obj, G_NAMED)
    seen = defaultdict(int)
    for s, p, o in [
        (NODES[0], PRED_A, NODES[1]),
        (NODES[0], PRED_A, NODES[1]),
        (NODES[0], PRED_A, NODES[1]),
        (NODES[0], PRED_B, NODES[2]),
        (NODES[2], PRED_A, NODES[3]),
        (NODES[2], PRED_A, NODES[3]),
        (NODES[4], PRED_A, NODES[5]),
    ]:
        ekey = seen[(s, p, o)]
        seen[(s, p, o)] += 1
        cur = iris_connection.cursor()
        try:
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES (?, ?, ?, ?, ?, ?)",
                [s, p, o, G_NAMED, ekey, '{"weight": 1.0}'],
            )
        finally:
            cur.close()
        iris_obj.classMethodValue("Graph.KG.EdgeScan", "WriteAdjacencyKeyed", s, p, o, "1.0", key, ekey)
    # No SQL-only row here: the connection autocommits, so a rolled-back INSERT is committed and SQL then
    # holds a source the adjacency never saw. The rollback case is the oracle's (test_derived_oracle_property).
    yield engine
    with contextlib.suppress(Exception):
        engine.set_multigraph(G_NAMED, False)


# --- tests ---------------------------------------------------------------------------


@pytest.mark.parametrize("graph", [None, G_NAMED])
def test_degree_equals_sql_row_count(adj_graph, iris_connection, graph):
    iris_obj = _iris(iris_connection)
    k = _key(iris_obj, graph)
    rows = _rows(iris_connection, graph)
    deg = _ref_degree(rows)
    for node in NODES + [GHOST]:
        got = iris_obj.classMethodValue("Graph.KG.Adj", "Degree", k, node)
        assert int(got) == deg.get(node, 0), (graph, node)
        for pred in (PRED_A, PRED_B):
            want = sum(1 for s, p, _o in rows if s == node and p == pred)
            got_p = iris_obj.classMethodValue("Graph.KG.Adj", "Degree", k, node, pred)
            assert int(got_p) == want, (graph, node, pred)


def test_parallel_edges_count_each_edge(adj_graph, iris_connection):
    iris_obj = _iris(iris_connection)
    k = _key(iris_obj, G_NAMED)
    # NODES[0] -> NODES[1] KNOWS has three live edges, plus one LIKES edge.
    assert int(iris_obj.classMethodValue("Graph.KG.Adj", "Degree", k, NODES[0])) == 4
    assert int(iris_obj.classMethodValue("Graph.KG.Adj", "Degree", k, NODES[0], PRED_A)) == 3


def test_rolled_back_row_is_not_counted(adj_graph, iris_connection):
    iris_obj = _iris(iris_connection)
    k = _key(iris_obj, G_NAMED)
    assert int(iris_obj.classMethodValue("Graph.KG.Adj", "Degree", k, NODES[5])) == 0
    assert int(iris_obj.classMethodValue("Graph.KG.Adj", "HasEdges", k, NODES[5])) == 0


@pytest.mark.parametrize("graph", [None, G_NAMED])
def test_next_source_lists_exactly_the_sources(adj_graph, iris_connection, graph):
    iris_obj = _iris(iris_connection)
    k = _key(iris_obj, graph)
    want = sorted({s for s, _p, _o in _rows(iris_connection, graph)})
    got = []
    node = iris_obj.classMethodValue("Graph.KG.Adj", "NextSource", k, "")
    while node not in (None, ""):  # the Native API hands back None for ""
        got.append(node)
        node = iris_obj.classMethodValue("Graph.KG.Adj", "NextSource", k, node)
    assert got == want


@pytest.mark.parametrize("graph", [None, G_NAMED])
def test_has_edges_agrees_with_rows(adj_graph, iris_connection, graph):
    iris_obj = _iris(iris_connection)
    k = _key(iris_obj, graph)
    rows = _rows(iris_connection, graph)
    sources = {s for s, _p, _o in rows}
    for node in NODES:
        got = int(iris_obj.classMethodValue("Graph.KG.Adj", "HasEdges", k, node))
        assert got == (1 if node in sources else 0), (graph, node)
        got_p = int(iris_obj.classMethodValue("Graph.KG.Adj", "HasEdges", k, node, PRED_B))
        assert got_p == (1 if any(s == node and p == PRED_B for s, p, _o in rows) else 0)


def test_degree_centrality_out_equals_rows(adj_graph, iris_connection):
    iris_obj = _iris(iris_connection)
    raw = iris_obj.classMethodValue("Graph.KG.Centrality", "DegreeCentralityJson", "out", "", 0)
    got = {item["id"]: (float(item["score"]), int(item["degree"])) for item in json.loads(raw)}
    want = _ref_centrality_out(_rows(iris_connection, None))
    assert set(got) == set(want)
    for node, (score, degree) in want.items():
        assert got[node][1] == degree, node
        assert got[node][0] == pytest.approx(score, rel=1e-9, abs=1e-12), node


def test_pagerank_global_equals_rows(adj_graph, iris_connection):
    iris_obj = _iris(iris_connection)
    raw = iris_obj.classMethodValue("Graph.KG.PageRank", "PageRankGlobalJson", "0.85", "20")
    got = {item["id"]: float(item["score"]) for item in json.loads(raw)}
    want = _ref_pagerank_global(_rows(iris_connection, None))
    assert set(got) == set(want)
    for node, score in want.items():
        assert got[node] == pytest.approx(score, rel=1e-9, abs=1e-12), node


def test_pagerank_run_named_graph_equals_rows(adj_graph, iris_connection):
    """Parallel edges divide by their full count, as before the migration. The reference is networkx (R270), run to a
    tolerance of 1e-12 as RunJson is here; a node the seed never reaches is absent from RunJson and 0 in networkx."""
    iris_obj = _iris(iris_connection)
    seed = json.dumps([NODES[0]])
    raw = iris_obj.classMethodValue(
        "Graph.KG.PageRank", "RunJson", seed, "0.85", "10000", "0", "1.0", G_NAMED, "", "0", "1E-12"
    )
    got = {item["id"]: float(item["score"]) for item in json.loads(raw)}
    want = _ref_ppr(_rows(iris_connection, G_NAMED), [NODES[0]])
    assert set(got) == {node for node, score in want.items() if score > 0}
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-9)
    for node, score in want.items():
        assert got.get(node, 0.0) == pytest.approx(score, abs=1e-9), node
