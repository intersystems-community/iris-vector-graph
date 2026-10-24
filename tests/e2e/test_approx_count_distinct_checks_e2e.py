"""`approx_count_distinct` answers only what its HLL route can answer; anything else is counted exactly (KNOWN_ISSUES).

The route (`_execute_approx_count_distinct` -> `Graph.KG.NKGAccel.CountDistinctKHop`) reads the source's id, the
relationship types, the maximum hops and the direction, from `^NKG`, which interns nodes with no graph. So it dropped a
far end's labels, properties and WHERE, a minimum above 1, a start's label, and `USE GRAPH`. And where `^NKG` did not
hold the source, it answered 0. Those reads are now answered by `count(DISTINCT ...)`, exactly, with a warning that
says so; the plain shape keeps the HLL route.

Run: IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture(scope="module")
def g(iris_connection):
    """s -> a:B, s -> c, a -> d:B (d has k=1), in the default graph; the same s, a in graph G with s -> a only."""
    e = IRISGraphEngine(iris_connection)
    t = uuid.uuid4().hex[:5]
    B, G = f"Acd{t}B", f"acd{t}_g"
    ids = {k: f"acd{t}_{k}" for k in "sacd"}
    e.create_node(ids["s"], labels=[f"Acd{t}S"])
    e.create_node(ids["a"], labels=[B])
    e.create_node(ids["c"])
    e.create_node(ids["d"], labels=[B], properties={"k": "1"})
    for x, y in (("s", "a"), ("s", "c"), ("a", "d")):
        e.create_edge(ids[x], "ACD", ids[y])
    e.create_node(ids["s"], graph=G)
    e.create_node(ids["a"], labels=[B], graph=G)
    e.create_edge(ids["s"], "ACD", ids["a"], graph=G)
    yield e, ids, B, G, t
    e.erase_graph(G)
    e.delete_nodes_by_prefix(f"acd{t}_")


def _approx(e, q, params):
    r = e.execute_cypher(q, params)
    assert getattr(r, "error", None) is None, r.error
    warnings = list(getattr(getattr(r, "metadata", None), "warnings", None) or [])
    return r.rows[0][0], " ".join(warnings)


@pytest.mark.parametrize(
    "name,match,expect",
    [
        ("a far label", "MATCH (s {node_id: $s})-[:ACD*1..2]->(m:{B})", 2),
        ("a far WHERE", "MATCH (s {node_id: $s})-[:ACD*1..2]->(m) WHERE m.k = '1'", 1),
        ("a minimum of 2", "MATCH (s {node_id: $s})-[:ACD*2..2]->(m)", 1),
        ("a start label it does not have", "MATCH (s:{B} {node_id: $s})-[:ACD*1..2]->(m)", 0),
    ],
)
def test_a_check_the_hll_route_cannot_apply_is_counted_exactly(g, name, match, expect):
    e, ids, B, G, t = g
    q = match.replace("{B}", B) + " RETURN approx_count_distinct(m) AS n"
    n, warnings = _approx(e, q, {"s": ids["s"]})
    assert n == expect
    assert "exactly" in warnings, warnings


def test_use_graph_is_counted_in_its_graph(g):
    e, ids, B, G, t = g
    q = f"USE GRAPH '{G}' MATCH (s {{node_id: $s}})-[:ACD*1..2]->(m) RETURN approx_count_distinct(m) AS n"
    n, warnings = _approx(e, q, {"s": ids["s"]})
    assert n == 1 and "exactly" in warnings, (n, warnings)


def test_the_plain_shape_keeps_the_hll_route(g):
    e, ids, B, G, t = g
    e.sync()  # ^NKG holds the default graph's nodes
    q = "MATCH (s {node_id: $s})-[:ACD*1..2]->(m) RETURN approx_count_distinct(m) AS n"
    n, warnings = _approx(e, q, {"s": ids["s"]})
    assert "HLL" in warnings, warnings
    assert 2 <= n <= 4  # a, c, d: an estimate


def test_no_snapshot_is_counted_not_zeroed(g, iris_connection):
    """With ^NKG absent (an erase drops it for every graph, until the next sync) the HLL route answered 0."""
    import iris

    e, ids, B, G, t = g
    iris.createIRIS(iris_connection).classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    try:
        q = "MATCH (s {node_id: $s})-[:ACD*1..2]->(m) RETURN approx_count_distinct(m) AS n"
        # from a, whose one relationship (to d) is in the default graph only: no count spans graphs
        n, warnings = _approx(e, q, {"s": ids["a"]})
        assert n == 1 and "exactly" in warnings, (n, warnings)
    finally:
        e.sync()


def test_an_unnamed_column_is_checked_and_keeps_its_name(g):
    """Without `AS` the query took the translator's route, which took the label for the source (4.4.0 gate)."""
    e, ids, B, G, t = g
    r = e.execute_cypher(f"MATCH (s {{node_id: $s}})-[:ACD*1..2]->(m:{B}) RETURN approx_count_distinct(m)", {"s": ids["s"]})
    assert getattr(r, "error", None) is None, r.error
    assert r.columns == ["approx_count_distinct(m)"] and r.rows == [[2]], (r.columns, r.rows)
