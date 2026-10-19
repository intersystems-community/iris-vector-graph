"""Spec 252 / ADR-0009, T030: a temporal edge implies a structural row, marked as temporal.

With the adjacency functional index, ^KG("out"/"in") is derived from rows; a temporal edge's structural
shadow with no row behind it was wiped by every rebuild (sync() measured it present-then-absent). The
temporal writer (Graph.KG.TemporalIndex) now asserts a row with qualifiers {"temporal": true}; deleting
the last timestamp of the triple removes the row only while it is still marked; an install that has
temporal edges with no row gets them backfilled (SELECT Graph_KG.backfill_temporal_rows()).

Run against a scratch namespace: `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .adjacency_recount import differences

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

ONE_HOP = "MATCH (a {node_id: $a})-[:CALLS]->(b) RETURN b.node_id"


@pytest.fixture(scope="module", autouse=True)
def declared(iris_connection):
    from iris_vector_graph.schema import GraphSchema

    status = GraphSchema.ensure_adjacency_index(iris_connection.cursor())
    iris_connection.commit()
    assert "skipped" not in status, status


@pytest.fixture
def t(iris_master_cleanup, iris_connection):
    tag = uuid.uuid4().hex[:8]
    return IRISGraphEngine(iris_connection), iris_connection, f"tr_{tag}_a", f"tr_{tag}_b"


def _rows(conn, a, b):
    cur = conn.cursor()
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", [a, b])
    return [json.loads(q) if q else {} for (q,) in cur.fetchall()]


def _one_hop(engine, a):
    r = engine.execute_cypher(ONE_HOP, {"a": a})
    assert getattr(r, "error", None) is None, r.error
    return [x[0] for x in r.rows]


def test_a_temporal_edge_has_a_marked_row_every_reader_sees(t):
    engine, conn, a, b = t
    assert engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_000)
    rows = _rows(conn, a, b)
    assert len(rows) == 1 and rows[0].get("temporal") is True
    assert _one_hop(engine, a) == [b]
    assert differences(conn, [a, b]) == []
    engine.sync()
    assert _one_hop(engine, a) == [b]
    assert differences(conn, [a, b]) == []


def test_more_timestamps_are_one_row(t):
    engine, conn, a, b = t
    for ts in (1_700_000_000, 1_700_000_100, 1_700_000_200):
        engine.create_edge_temporal(a, "CALLS", b, timestamp=ts)
    assert len(_rows(conn, a, b)) == 1


def test_the_last_timestamp_s_delete_removes_a_marked_row(t):
    engine, conn, a, b = t
    engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_000)
    engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_100)
    assert engine.delete_edge_temporal(a, "CALLS", b, 1_700_000_000)
    assert len(_rows(conn, a, b)) == 1
    assert engine.delete_edge_temporal(a, "CALLS", b, 1_700_000_100)
    assert _rows(conn, a, b) == []
    assert _one_hop(engine, a) == []
    assert differences(conn, [a, b]) == []


def test_an_explicit_edge_outlives_its_temporal_edges(t):
    engine, conn, a, b = t
    engine.create_edge(a, "CALLS", b)
    engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_000)
    assert engine.delete_edge_temporal(a, "CALLS", b, 1_700_000_000)
    assert len(_rows(conn, a, b)) == 1
    assert _one_hop(engine, a) == [b]
    assert differences(conn, [a, b]) == []


def test_bulk_temporal_edges_get_rows(t):
    engine, conn, a, b = t
    c = a[:-1] + "c"
    engine.bulk_create_edges_temporal(
        [{"s": a, "p": "CALLS", "o": b, "ts": 1_700_000_000}, {"s": b, "p": "CALLS", "o": c, "ts": 1_700_000_050}]
    )
    assert len(_rows(conn, a, b)) == 1 and len(_rows(conn, b, c)) == 1
    assert differences(conn, [a, b, c]) == []


def test_a_temporal_edge_with_no_row_is_backfilled(t):
    engine, conn, a, b = t
    engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_000)
    cur = conn.cursor()
    # the state an install had before ADR-0009: temporal globals, no structural row
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", [a, b])
    conn.commit()
    assert _rows(conn, a, b) == []
    cur.execute("SELECT Graph_KG.backfill_temporal_rows()")
    assert int(cur.fetchone()[0]) >= 1
    conn.commit()
    rows = _rows(conn, a, b)
    assert len(rows) == 1 and rows[0].get("temporal") is True
    assert differences(conn, [a, b]) == []


def test_an_explicit_create_after_a_temporal_edge_clears_the_marker(t):
    # ADR-0009: "an explicit create_edge on the same triple clears the marker, after which no temporal
    # delete can remove it"; the row exists already, so create_edge must adopt it rather than fail on it
    engine, conn, a, b = t
    engine.create_edge_temporal(a, "CALLS", b, timestamp=1_700_000_000)
    engine.create_edge(a, "CALLS", b)
    rows = _rows(conn, a, b)
    assert len(rows) == 1 and rows[0].get("temporal") is not True
    assert engine.delete_edge_temporal(a, "CALLS", b, 1_700_000_000)
    assert len(_rows(conn, a, b)) == 1
    assert _one_hop(engine, a) == [b]
    assert differences(conn, [a, b]) == []
