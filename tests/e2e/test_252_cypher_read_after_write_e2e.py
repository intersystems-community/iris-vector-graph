"""Spec 252, US1: a Cypher write is visible to every reader of the Adjacency Index, with no sync().

The k-hop fast path, shortestPath, the weighted shortest path and the k-hop counts read ^KG. A Cypher
write is SQL only, so before spec 252 a one-hop MATCH from a bound node answered nothing after CREATE
and the deleted edge after DELETE. Each case also recounts the index from the tables
(tests/e2e/adjacency_recount.py): no missing entry, and no entry with no row behind it.

Run against a scratch namespace: `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .adjacency_recount import differences

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

ONE_HOP = "MATCH (a {node_id: $a})-[:R]->(b) RETURN b.node_id"
COUNT = "MATCH (a {node_id: $a})-[:R]->(b) RETURN count(b) AS n"
SHORTEST = "MATCH p = shortestPath((a {node_id: $a})-[:R*..3]->(b {node_id: $b})) RETURN length(p)"
WEIGHTED = "CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 10, 'out', ['R']) YIELD totalCost RETURN totalCost"


@pytest.fixture(scope="module", autouse=True)
def declared(iris_connection):
    from iris_vector_graph.schema import GraphSchema

    status = GraphSchema.ensure_adjacency_index(iris_connection.cursor())
    iris_connection.commit()
    assert "skipped" not in status, status


@pytest.fixture
def g(iris_connection, iris_master_cleanup):
    e = IRISGraphEngine(iris_connection)
    t = uuid.uuid4().hex[:8]
    a, b = f"raw_{t}_a", f"raw_{t}_b"
    e.execute_cypher(f"CREATE (:RAW {{node_id: '{a}'}}), (:RAW {{node_id: '{b}'}})")
    return e, iris_connection, a, b


def _rows(e, q, **params):
    r = e.execute_cypher(q, params)
    assert getattr(r, "error", None) is None, r.error
    return [list(x) for x in r.rows]


def test_create_is_seen_by_every_reader(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}), (y {node_id: $b}) CREATE (x)-[:R]->(y)", a=a, b=b)
    assert _rows(e, ONE_HOP, a=a) == [[b]]
    assert _rows(e, COUNT, a=a) == [[1]]
    assert _rows(e, SHORTEST, a=a, b=b) == [[1]]
    # the procedure answers [path, totalCost] whatever the YIELD (a projection defect of its own); the cost is last
    assert [r[-1] for r in _rows(e, WEIGHTED, a=a, b=b)] == [1.0]
    assert differences(conn, [a, b]) == []


def test_delete_is_seen_by_every_reader(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}), (y {node_id: $b}) CREATE (x)-[:R]->(y)", a=a, b=b)
    _rows(e, "MATCH (x {node_id: $a})-[r:R]->() DELETE r", a=a)
    assert _rows(e, ONE_HOP, a=a) == []
    assert _rows(e, COUNT, a=a) == [[0]]
    assert _rows(e, SHORTEST, a=a, b=b) == []
    assert differences(conn, [a, b]) == []


def test_detach_delete_removes_the_node_s_edges(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}), (y {node_id: $b}) CREATE (x)-[:R]->(y)", a=a, b=b)
    _rows(e, "MATCH (y {node_id: $b}) DETACH DELETE y", b=b)
    assert _rows(e, ONE_HOP, a=a) == []
    assert differences(conn, [a, b]) == []


def test_merge_creates_an_edge_the_readers_see(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}), (y {node_id: $b}) MERGE (x)-[:R]->(y)", a=a, b=b)
    assert _rows(e, ONE_HOP, a=a) == [[b]]
    assert differences(conn, [a, b]) == []


def test_set_weight_reaches_the_weighted_path(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}), (y {node_id: $b}) CREATE (x)-[:R]->(y)", a=a, b=b)
    _rows(e, "MATCH (x {node_id: $a})-[r:R]->() SET r.weight = 5", a=a)
    assert [r[-1] for r in _rows(e, WEIGHTED, a=a, b=b)] == [5.0]
    assert differences(conn, [a, b]) == []


def test_labels_follow_set_and_remove(g):
    e, conn, a, b = g
    _rows(e, "MATCH (x {node_id: $a}) SET x:EXTRA", a=a)
    assert differences(conn, [a, b]) == []
    _rows(e, "MATCH (x {node_id: $a}) REMOVE x:EXTRA", a=a)
    assert differences(conn, [a, b]) == []


def test_raw_sql_insert_commit_and_rollback(g):
    e, conn, a, b = g
    cur = conn.cursor()
    cur.execute("START TRANSACTION")
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'R', ?)", [a, b])
    cur.execute("ROLLBACK")
    assert _rows(e, ONE_HOP, a=a) == []
    assert differences(conn, [a, b]) == []
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'R', ?)", [a, b])
    conn.commit()
    assert _rows(e, ONE_HOP, a=a) == [[b]]
    assert differences(conn, [a, b]) == []
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", [a, b])
    conn.commit()
    assert _rows(e, ONE_HOP, a=a) == []
    assert differences(conn, [a, b]) == []
