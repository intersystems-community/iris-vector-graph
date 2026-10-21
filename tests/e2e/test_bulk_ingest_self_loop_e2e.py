"""A self-loop is an edge on the bulk path too.

`Graph.KG.EdgeScan.BulkIngestEdgesSQL` skipped every edge whose source is its target, without counting it or saying so:
the full DRKG load kept 5,870,759 of its 5,874,258 distinct triples, and the 3,499 missing were its self-loops (gene
self-interactions). `create_edge` and the SQL fallback store them, and Cypher matches them.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]


def n(k):
    return f"sl{T}_{k}"


@pytest.fixture
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    yield e
    for k in "abc":
        try:
            e.delete_node(n(k))
        except Exception:
            pass


def _edges(conn, s):
    cur = conn.cursor()
    cur.execute("SELECT p, o_id FROM Graph_KG.rdf_edges WHERE s = ? ORDER BY p, o_id", [s])
    return [tuple(r) for r in cur.fetchall()]


def test_bulk_ingest_keeps_a_self_loop(engine, iris_connection):
    landed = engine.bulk_ingest_edges([{"s": n("a"), "p": "SL_R", "o": n("a")}, {"s": n("a"), "p": "SL_R", "o": n("b")}])
    assert landed == 2
    assert _edges(iris_connection, n("a")) == [("SL_R", n("a")), ("SL_R", n("b"))]
    r = engine.execute_cypher("MATCH (x {node_id: $x})-[:SL_R]->(x) RETURN count(*) AS c", {"x": n("a")})
    assert r.rows == [[1]]


def test_a_bulk_load_session_keeps_a_self_loop(engine, iris_connection):
    with engine.bulk_load_session() as s:
        s.add_edges([{"s": n("c"), "p": "SL_R", "o": n("c")}])
    assert s.stats["edges"] == 1
    assert _edges(iris_connection, n("c")) == [("SL_R", n("c"))]
