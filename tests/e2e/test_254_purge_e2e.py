"""Spec 254 US1 / FR-001 / FR-002: retention removes everything an expired temporal edge left.

With one node per event (opsreview: ``(ts, instance, ACTION_AT, action:<event_id>)``) every event asserts a structural
row and a ``^KG("out")``/``("in")`` entry (ADR-0009). ``PurgeRawBefore`` used to remove only ``tout``, ``tin`` and
``edgeprop``, so adjacency grew without bound under retention. Now a purge also retires the marked rows of every
triple no surviving timestamp holds; a row a non-temporal write asserted (unmarked) stays; nodes stay.

The story test compares every store of a purged graph with a reference graph that only ever held the surviving
hours. The path tests below are the cross product TESTING_POLICY asks for: one case per write and delete path that can
put a row or a timestamp under a triple.

Run: ``IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972``.
"""

from __future__ import annotations

import json
import os
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .adjacency_recount import differences
from .graph_snapshot import AGGREGATES, CACHES, compare, digest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_database,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

T0 = 1_760_000_000
HOUR = 3600
SPAN = 72 * HOUR
CUTOFF = T0 + 24 * HOUR  # keep the last 48 of 72 hours (opsreview's metric-ingest raw retention)
EVENTS = 2400


@pytest.fixture
def env(iris_connection, iris_master_cleanup):
    tag = uuid.uuid4().hex[:6]
    engine = IRISGraphEngine(iris_connection)
    return engine, iris.createIRIS(iris_connection), iris_connection, f"g254p_{tag}"


def _rows(conn, s, o, graph):
    cur = conn.cursor()
    cur.execute(
        "SELECT qualifiers FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
        [s, o, graph or ""],
    )
    return [json.loads(q) if q else {} for (q,) in cur.fetchall()]


def _node_exists(conn, node, graph):
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
        [node, graph or ""],
    )
    return cur.fetchone()[0] == 1


def _out(native, graph, s, p, o):
    key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph or "")
    return native.isDefined("KG", "out", key, s, p, o) > 0


def _degree(native, graph, node):
    key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph or "")
    return int(native.classMethodValue("Graph.KG.Adj", "Degree", key, node, ""))


def _event_load():
    """72 hours of one-node-per-event edges, a long-lived triple across the cutoff, and a source that is all expired."""
    edges = []
    for i in range(EVENTS):
        edges.append({"s": f"inst:{i % 8}", "p": "ACTION_AT", "o": f"action:{i}", "ts": T0 + (i * SPAN) // EVENTS,
                      "w": 1.0, "attrs": {"kind": "cfg", "n": i}})
    for h in range(72):
        edges.append({"s": "inst:0", "p": "HAS_CFG", "o": "cfg:shared", "ts": T0 + h * HOUR + 17, "w": 1.0})
    for k in range(5):
        edges.append({"s": "inst:old", "p": "ACTION_AT", "o": f"action:old{k}", "ts": T0 + k * HOUR + 3, "w": 1.0})
    # one triple, several timestamps, all expired
    for k in range(4):
        edges.append({"s": "inst:2", "p": "PINGED", "o": "host:old", "ts": T0 + k * 60, "w": 1.0})
    return edges


def _write(engine, edges, graph, batch=500):
    for k in range(0, len(edges), batch):
        engine.bulk_create_edges_temporal(edges[k:k + batch], graph=graph)


# --------------------------------------------------------------------------------------------- the story


def test_a_48_hour_purge_equals_a_graph_that_only_held_48_hours(env):
    engine, native, conn, g = env
    ref = g + "_ref"
    load = _event_load()
    _write(engine, load, g)
    _write(engine, [e for e in load if e["ts"] >= CUTOFF], ref)
    # a structural edge asserted by create_edge, whose timestamps are all expired (FR-002)
    for graph in (g, ref):
        engine.create_edge("inst:1", "OWNS", "thing:x", graph=graph)
    _write(engine, [{"s": "inst:1", "p": "OWNS", "o": "thing:x", "ts": T0 + 5, "w": 1.0}], g)
    assert _degree(native, g, "inst:old") == 5  # warm the degree cache before the purge

    expired = [e for e in load if e["ts"] < CUTOFF] + [{"ts": T0 + 5}]
    res = engine.purge_raw_before(CUTOFF, graph=g)
    assert res.deleted == len(expired) and res.skipped == 0

    # every store but the aggregates (kept by design), nodes (scenario 3) and gen (a history counter)
    excluded = CACHES | AGGREGATES | {"Graph_KG.nodes", '^KG("gen")'}
    assert compare(digest(native, g), digest(native, ref), exclude=excluded) == []

    # SC-001: nothing from the purged 24 hours in rows or adjacency
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ?", [g])
    assert cur.fetchone()[0] == len({(e["s"], e["p"], e["o"]) for e in load if e["ts"] >= CUTOFF}) + 1
    for e in load:
        if e["ts"] < CUTOFF and e["o"] != "cfg:shared":
            assert not _out(native, g, e["s"], e["p"], e["o"]), e
    # scenario 1 and FR-002: the create_edge row stays, unmarked
    assert _rows(conn, "inst:1", "thing:x", g) == [{}]
    # scenario 2: timestamps on both sides of the cutoff
    assert _rows(conn, "inst:0", "cfg:shared", g) == [{"temporal": True}]
    # scenario 3: nodes stay, the degree cache answers 0
    assert _node_exists(conn, "action:0", g) and _node_exists(conn, "inst:old", g)
    assert _degree(native, g, "inst:old") == 0
    assert differences(conn, ["inst:old", "inst:0", "inst:1", "inst:2", "action:0", "host:old"], graphs=(0, g)) == []


# --------------------------------------------------------------------------------------------- write paths


def _purge_all(engine, graph):
    return engine.purge_raw_before(T0 + SPAN, graph=graph)


def test_create_edge_before_the_temporal_edge_survives(env):
    engine, native, conn, g = env
    engine.create_edge("a", "P", "b", graph=g)
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    _purge_all(engine, g)
    assert _rows(conn, "a", "b", g) == [{}] and _out(native, g, "a", "P", "b")


def test_create_edge_after_the_temporal_edge_survives(env):
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, weight=3.0, graph=g)
    engine.create_edge("a", "P", "b", graph=g)  # adopts the marked row (ADR-0009)
    _purge_all(engine, g)
    assert len(_rows(conn, "a", "b", g)) == 1 and _out(native, g, "a", "P", "b")


def test_bulk_create_edges_after_the_temporal_edge_survives(env):
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    engine.create_edge_temporal("a", "P", "c", timestamp=T0, graph=None)
    engine.bulk_create_edges([{"source_id": "a", "predicate": "P", "target_id": "b"}], graph=g)
    engine.bulk_create_edges([{"source_id": "a", "predicate": "P", "target_id": "c"}])
    assert _rows(conn, "a", "b", g) == [{}] and _rows(conn, "a", "c", None) == [{}]
    _purge_all(engine, g)
    _purge_all(engine, None)
    assert _rows(conn, "a", "b", g) == [{}] and _out(native, g, "a", "P", "b")
    assert _rows(conn, "a", "c", None) == [{}] and _out(native, None, "a", "P", "c")


@pytest.mark.parametrize("clause", ["CREATE", "MERGE"])
def test_a_cypher_write_after_the_temporal_edge_survives(env, clause):
    engine, native, conn, _ = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0)
    r = engine.execute_cypher(f"MATCH (x {{node_id: 'a'}}), (y {{node_id: 'b'}}) {clause} (x)-[:P]->(y)")
    assert getattr(r, "error", None) is None
    _purge_all(engine, None)
    assert _out(native, None, "a", "P", "b")


def test_a_staged_cypher_create_with_properties_adopts_the_row(env):
    """The WITH-staged CREATE (a CTE ahead of the INSERT) adopts the row and its literal properties replace the marker."""
    engine, native, conn, _ = env
    engine.create_edge("a", "Q", "b")
    engine.create_edge_temporal("a", "P", "b", timestamp=T0)
    r = engine.execute_cypher("MATCH (x)-[:Q]->(y) WHERE x.node_id = 'a' WITH x, y CREATE (x)-[:P {w: 1}]->(y)")
    assert getattr(r, "error", None) is None
    assert sorted(_rows(conn, "a", "b", None), key=len) == [{}, {"w": "1"}]  # Q's row, then P's adopted one
    _purge_all(engine, None)
    assert _out(native, None, "a", "P", "b")


def test_a_merge_on_match_set_of_a_marked_row_keeps_what_it_set(env):
    """The adopt runs ahead of the MERGE's ON MATCH SET, so it cannot overwrite the property the SET wrote."""
    engine, native, conn, _ = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0)
    r = engine.execute_cypher("MATCH (x {node_id: 'a'}), (y {node_id: 'b'}) MERGE (x)-[r:P]->(y) ON MATCH SET r.seen = 1")
    assert getattr(r, "error", None) is None
    rows = _rows(conn, "a", "b", None)
    assert len(rows) == 1 and "temporal" not in rows[0] and str(rows[0].get("seen")) == "1"
    _purge_all(engine, None)
    assert _out(native, None, "a", "P", "b")


def test_a_cypher_create_leaves_an_unmarked_row_alone(env):
    engine, native, conn, _ = env
    engine.create_edge("a", "P", "b", qualifiers={"w": 7})
    r = engine.execute_cypher("MATCH (x {node_id: 'a'}), (y {node_id: 'b'}) CREATE (x)-[:P {w: 1}]->(y)")
    assert getattr(r, "error", None) is None
    assert _rows(conn, "a", "b", None) == [{"w": 7}]


def test_a_cypher_create_before_the_temporal_edge_survives(env):
    engine, native, conn, _ = env
    engine.execute_cypher("CREATE (x {node_id: 'a'})-[:P]->(y {node_id: 'b'})")
    engine.create_edge_temporal("a", "P", "b", timestamp=T0)
    _purge_all(engine, None)
    assert _out(native, None, "a", "P", "b") and len(_rows(conn, "a", "b", None)) == 1


@pytest.mark.parametrize("marker", ['{"temporal": true, "weight": 2.0}', '{"weight":2,"temporal":true}'])
def test_every_spelling_of_the_marker_is_retired(env, marker):
    """The upgrade backfill writes the marker through create_edge (json.dumps spacing); any key order is a marker."""
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    cur = conn.cursor()
    cur.execute("UPDATE Graph_KG.rdf_edges SET qualifiers = ? WHERE s = 'a' AND o_id = 'b' AND graph_id = ?", [marker, g])
    conn.commit()
    assert _rows(conn, "a", "b", g)[0].get("temporal") is True
    _purge_all(engine, g)
    assert _rows(conn, "a", "b", g) == [] and not _out(native, g, "a", "P", "b")


def test_a_suppressed_reverse_index_edge_goes_with_its_row(env):
    engine, native, conn, g = env
    engine.bulk_create_edges_temporal([{"s": "a", "p": "P", "o": "b", "ts": T0}], graph=g, suppress_reverse_index=True)
    assert _rows(conn, "a", "b", g) == [{"temporal": True}]
    assert _purge_all(engine, g).deleted == 1
    assert _rows(conn, "a", "b", g) == [] and not _out(native, g, "a", "P", "b")


def test_millisecond_precision(env):
    engine, native, conn, graph = env
    batch = json.dumps([{"s": "a", "p": "P", "o": "b", "ts": T0 * 1000 + 1}, {"s": "a", "p": "P", "o": "c", "ts": (T0 + 2 * HOUR) * 1000}])
    native.classMethodValue("Graph.KG.TemporalIndexMS", "BulkInsert", graph, batch, 0, "")
    res = str(native.classMethodValue("Graph.KG.TemporalIndexMS", "PurgeRawBefore", graph, (T0 + HOUR) * 1000, 0))
    assert res == "1:0"
    assert _rows(conn, "a", "b", graph) == [] and not _out(native, graph, "a", "P", "b")
    assert _rows(conn, "a", "c", graph) == [{"temporal": True}]


def test_ts_start_keeps_the_rows_of_timestamps_below_it(env):
    engine, native, conn, g = env
    engine.bulk_create_edges_temporal([{"s": "a", "p": "P", "o": "b", "ts": T0},
                                       {"s": "a", "p": "P", "o": "b", "ts": T0 + 2 * HOUR},
                                       {"s": "a", "p": "P", "o": "c", "ts": T0 + 2 * HOUR}], graph=g)
    res = engine.purge_raw_before(T0 + 3 * HOUR, ts_start=T0 + HOUR, graph=g)
    assert (res.deleted, res.skipped) == (2, 1)
    assert _rows(conn, "a", "b", g) == [{"temporal": True}]  # T0 survives below ts_start
    assert _rows(conn, "a", "c", g) == []


def test_only_the_purged_graph_loses_its_row(env):
    engine, native, conn, g = env
    other = g + "_o"
    for graph in (g, other, None):
        engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=graph)
    _purge_all(engine, g)
    assert _rows(conn, "a", "b", g) == []
    assert _rows(conn, "a", "b", other) == [{"temporal": True}] and _out(native, other, "a", "P", "b")
    assert _rows(conn, "a", "b", None) == [{"temporal": True}] and _out(native, None, "a", "P", "b")


def test_the_legacy_purge_before_retires_rows_too(env):
    engine, native, conn, _ = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0)
    engine.create_edge_temporal("a", "P", "c", timestamp=T0 + 2 * HOUR)
    engine.create_edge("a", "P", "d")
    engine.create_edge_temporal("a", "P", "d", timestamp=T0)
    engine.purge_before(T0 + HOUR)
    assert _rows(conn, "a", "b", None) == [] and not _out(native, None, "a", "P", "b")
    assert _rows(conn, "a", "c", None) == [{"temporal": True}]
    assert _rows(conn, "a", "d", None) == [{}]


# --------------------------------------------------------------------------------------------- delete paths


def test_delete_temporal_edge_is_unchanged_and_a_purge_after_it_is_clean(env):
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    engine.create_edge_temporal("a", "P", "b", timestamp=T0 + 2 * HOUR, graph=g)
    assert engine.delete_edge_temporal("a", "P", "b", T0 + 2 * HOUR, graph=g)
    assert _rows(conn, "a", "b", g) == [{"temporal": True}]
    assert _purge_all(engine, g).deleted == 1
    assert _rows(conn, "a", "b", g) == [] and differences(conn, ["a", "b"], graphs=(0, g)) == []


def test_erase_nodes_then_purge(env):
    engine, native, conn, g = env
    engine.bulk_create_edges_temporal([{"s": "inst:1", "p": "P", "o": "action:1", "ts": T0},
                                       {"s": "inst:1", "p": "P", "o": "keep:1", "ts": T0}], graph=g)
    engine.delete_nodes_by_prefix("action:", graph=g)
    assert _rows(conn, "inst:1", "action:1", g) == []
    assert _purge_all(engine, g).deleted == 1
    assert _rows(conn, "inst:1", "keep:1", g) == [] and differences(conn, ["inst:1", "keep:1"], graphs=(0, g)) == []


def test_erase_graph_then_purge(env):
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    engine.erase_graph(g)
    assert _purge_all(engine, g).deleted == 0
    assert _rows(conn, "a", "b", g) == [] and not _out(native, g, "a", "P", "b")


def test_delete_edge_then_purge(env):
    engine, native, conn, g = env
    engine.create_edge_temporal("a", "P", "b", timestamp=T0, graph=g)
    engine.delete_edge("a", "P", "b", graph=g)
    assert _purge_all(engine, g).deleted == 1
    assert _rows(conn, "a", "b", g) == [] and differences(conn, ["a", "b"], graphs=(0, g)) == []


def test_a_purge_of_an_empty_range_changes_no_store(env):
    engine, native, conn, g = env
    _write(engine, _event_load()[:300], g)
    before = digest(native, g)
    res = engine.purge_raw_before(T0 - 1, graph=g)
    assert (res.deleted, res.skipped) == (0, 0)
    assert compare(before, digest(native, g)) == []
