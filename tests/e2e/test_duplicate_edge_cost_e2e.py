"""A duplicate edge write costs what a probe costs, not a journaled rollback (KNOWN_ISSUES, found 2026-10-23).

An INSERT into rdf_edges that its unique key u_spo_graph refuses (SQLCODE -119) is rolled back through the journal:
31.7 ms against 0.04 ms for a new edge (%NOJOURN takes it to 0.04 ms). create_edge and the no-ObjectScript fallback of
bulk_ingest_edges inserted and treated -119 as "already there", so every duplicate cost ~30 ms. They now insert with
`... WHERE NOT EXISTS` and read the row count. The server-side BulkIngestEdgesSQL already probed first.

Run: IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972.
"""

from __future__ import annotations

import os
import statistics
import time
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

# A duplicate refused by the unique key costs ~30 ms; a guarded insert is a probe. Far from both, so load does not flake it.
DUPLICATE_MS = 8.0


@pytest.fixture
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    tag = uuid.uuid4().hex[:6]
    yield e, tag
    for g in (f"dup{tag}", None):
        cur = iris_connection.cursor()
        cur.execute(
            "DELETE FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
            [f"dup{tag}", g or ""],
        )
        iris_connection.commit()
    e.erase_graph(f"dup{tag}")
    e.delete_nodes_by_prefix(f"dup{tag}")


def _edges(conn, s, graph):
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND COALESCE(graph_id, '') = COALESCE(?, '')", [s, graph or ""]
    )
    return cur.fetchone()[0]


@pytest.mark.parametrize("named", [False, True])
def test_a_duplicate_create_edge_writes_nothing_and_costs_a_probe(engine, named):
    e, tag = engine
    g = f"dup{tag}" if named else None
    s, o = f"dup{tag}_a", f"dup{tag}_b"
    assert e.create_edge(s, "DUP_K", o, graph=g) is True
    times = []
    for _ in range(10):
        t = time.perf_counter()
        assert e.create_edge(s, "DUP_K", o, graph=g) is False
        times.append((time.perf_counter() - t) * 1000)
    assert _edges(e.conn, s, g) == 1
    assert statistics.median(times) < DUPLICATE_MS, times


def test_a_duplicate_create_edge_still_adopts_a_temporal_row(engine):
    """ADR-0009: the duplicate of a temporal edge's marked row adopts it (the row loses its marker), as before."""
    e, tag = engine
    s, o = f"dup{tag}_t", f"dup{tag}_u"
    e.create_edge_temporal(s, "DUP_T", o, timestamp=1_760_000_000)
    assert e.create_edge(s, "DUP_T", o) is True
    cur = e.conn.cursor()
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ? AND COALESCE(graph_id, '') = ''", [s, o])
    rows = cur.fetchall()
    assert len(rows) == 1 and "temporal" not in (rows[0][0] or "")


def test_the_bulk_ingest_fallback_skips_duplicates_cheaply(engine):
    """The SQL fallback (no ObjectScript classes): duplicates are skipped and not counted, at the cost of a probe."""
    e, tag = engine
    e.capabilities.objectscript_deployed = False  # the fallback's trigger, said outright
    edges = [{"s": f"dup{tag}_s{i}", "p": "DUP_B", "o": f"dup{tag}_o{i}"} for i in range(20)]
    assert e.bulk_ingest_edges(edges, auto_sync=False) == 20
    t = time.perf_counter()
    assert e.bulk_ingest_edges(edges, auto_sync=False) == 0
    per_edge_ms = (time.perf_counter() - t) * 1000 / len(edges)
    assert per_edge_ms < DUPLICATE_MS, per_edge_ms
    assert sum(_edges(e.conn, x["s"], None) for x in edges) == 20


def test_a_new_edge_keeps_its_qualifiers_and_weight(engine):
    """The guarded insert binds qualifiers through a SELECT list: what a new edge stores is unchanged."""
    import json

    e, tag = engine
    s, o = f"dup{tag}_q", f"dup{tag}_r"
    assert e.create_edge(s, "DUP_Q", o, qualifiers={"note": "x"}, weight=2.0) is True
    cur = e.conn.cursor()
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", [s, o])
    assert json.loads(cur.fetchone()[0]) == {"note": "x", "weight": 2.0}


def test_a_triple_held_only_by_a_parallel_relationship_still_gets_its_first_row(engine):
    """The unique key is (s, p, o_id, graph_id, ekey): the old insert, at ekey 0, went through where the triple's only
    row has another ekey (a multigraph whose first relationship was deleted). The guard compares ekey 0 as the key did."""
    e, tag = engine
    g = f"dup{tag}"
    s, o = f"dup{tag}_m", f"dup{tag}_n"
    e.set_multigraph(g, True)
    try:
        for _ in range(2):
            r = e.execute_cypher(
                f"USE GRAPH '{g}' MERGE (a {{node_id: $a}}) MERGE (b {{node_id: $b}}) CREATE (a)-[:DUP_M]->(b)", {"a": s, "b": o}
            )
            assert getattr(r, "error", None) is None, r.error
        cur = e.conn.cursor()
        cur.execute("SELECT ekey FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ? AND graph_id = ? ORDER BY ekey", [s, o, g])
        keys = [k for (k,) in cur.fetchall()]
        assert len(keys) == 2 and 0 in keys, keys
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ? AND graph_id = ? AND ekey = 0", [s, o, g])
        e.conn.commit()
        assert e.create_edge(s, "DUP_M", o, graph=g) is True
        cur.execute("SELECT ekey FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ? AND graph_id = ? ORDER BY ekey", [s, o, g])
        assert [k for (k,) in cur.fetchall()] == [0] + [k for k in keys if k != 0]
    finally:
        e.erase_graph(g)  # multigraph mode cannot be turned off over parallel relationships
        e.set_multigraph(g, False)
