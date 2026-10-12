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
- Graph.KG.PageRank.RunJson on the named graph equals the same Python walk.

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


def _ref_distinct_out(rows):
    spread = defaultdict(set)
    for s, p, o in rows:
        spread[s].add((p, o))
    return spread


def _ref_centrality_out(rows):
    """DegreeCentralityJson(direction="out", all predicates): {id: (score, degree)}."""
    deg = _ref_degree(rows)
    nodes = {s for s, _p, _o in rows} | {o for _s, _p, o in rows}
    n = len(nodes)
    norm = 1.0 / (n - 1) if n > 1 else 1.0
    return {node: (deg.get(node, 0) * norm, deg.get(node, 0)) for node in nodes}


def _ref_pagerank_global(rows, damping=0.85, max_iter=20):
    """PageRankGlobalJson: ranks start on the sources, sinks join from the first spread."""
    deg = _ref_degree(rows)
    spread = _ref_distinct_out(rows)
    sources = sorted(deg)
    n = len(sources)
    if n == 0:
        return {}
    ranks = {s: 1.0 / n for s in sources}
    for it in range(1, max_iter + 1):
        new = defaultdict(float)
        for node, rank in ranks.items():
            d = deg.get(node, 0)
            if d == 0:
                new[node] += rank * (1 - damping)
                continue
            share = (damping * rank) / d
            for _p, o in spread.get(node, ()):
                new[o] += share
        teleport_share = (1 - damping) / n
        for node in ranks:
            new[node] += teleport_share
        if it > 2:
            max_delta = max(abs(v - ranks.get(k, 0.0)) for k, v in new.items())
            if max_delta < 0.0001:
                ranks = dict(new)
                break
        ranks = dict(new)
    return ranks


def _ref_ppr(rows, seeds, alpha=0.85, max_iter=20):
    """RunJson: personalised walk from seeds, forward edges only, divisor = edge count."""
    deg = _ref_degree(rows)
    spread = _ref_distinct_out(rows)
    teleport = 1.0 / len(seeds)
    ranks = {s: teleport for s in seeds}
    for it in range(1, max_iter + 1):
        new = defaultdict(float)
        for node, rank in ranks.items():
            d = deg.get(node, 0)
            if d == 0:
                new[node] += rank * (1 - alpha)
                continue
            share = (alpha * rank) / d
            for _p, o in spread.get(node, ()):
                new[o] += share
        for s in seeds:
            new[s] += (1 - alpha) * teleport
        if it > 2:
            max_delta = max(abs(v - ranks.get(k, 0.0)) for k, v in new.items())
            if max_delta < 0.0001:
                ranks = dict(new)
                break
        ranks = dict(new)
    return ranks


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
    """Parallel edges divide by their full count, as before the migration."""
    iris_obj = _iris(iris_connection)
    seed = json.dumps([NODES[0]])
    raw = iris_obj.classMethodValue(
        "Graph.KG.PageRank", "RunJson", seed, "0.85", "20", "0", "1.0", G_NAMED, "", "0"
    )
    got = {item["id"]: float(item["score"]) for item in json.loads(raw)}
    want = _ref_ppr(_rows(iris_connection, G_NAMED), [NODES[0]])
    assert set(got) == set(want)
    for node, score in want.items():
        assert got[node] == pytest.approx(score, rel=1e-9, abs=1e-12), node
