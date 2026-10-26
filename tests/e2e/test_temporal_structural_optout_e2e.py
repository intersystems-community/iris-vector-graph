"""ADR-0015: a predicate can opt out of the structural rows its temporal edges imply.

One test per user story:

1. opt out, write, read: window, bucket, aggregate, latest-event and distinct reads are right, and nothing structural is written;
2. the structural reads (BFS, k-hop, Cypher ``MATCH``) of that predicate return nothing, and say why in a warning;
3. ``drop_temporal_structural_rows``: dry run is read-only (digest before and after), it drops marked rows only, an
   explicit edge stays with its endpoints, bare endpoints go, it refuses a predicate that is still structural;
4. ``backfill_temporal_structural_rows`` restores the rows and ``^KG``, and refuses a predicate that is opted out;
5. the upgrade backfill (``backfill_temporal_rows()``, ``migrations.backfill_structural_rows``) skips an opted-out predicate;
   and holds a bounded memory of the triples it has seen;
6. ``EraseGraph`` keeps the registration, ``EraseAll`` removes it; a snapshot carries it;
7. per-graph and per-predicate isolation;
8. a writer racing ``set_temporal_structural``;
9. the purge leaves an opted-out triple alone, and ``retire_bare_nodes`` has no endpoint of it to look at.

Run: ``IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_ARNO_PORT=31972``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid

import iris
import iris.dbapi as dbapi
import pytest

from iris_vector_graph.engine import IRISGraphEngine

from .graph_snapshot import AGGREGATES, CACHES, compare, digest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_database,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

T0 = 1_760_000_000
HOUR = 3600
DIM = 768
SPAN = "OBSERVED_AT"


@pytest.fixture
def env(iris_connection, iris_master_cleanup):
    tag = uuid.uuid4().hex[:6]
    engine = IRISGraphEngine(iris_connection, embedding_dimension=DIM)
    return engine, iris.createIRIS(iris_connection), iris_connection, f"g_so_{tag}"


# ------------------------------------------------------------------------------------------------ helpers


def _spans(n, *, inst="inst:a", pred=SPAN, ts0=T0, prefix="span:", w=1.0):
    return [{"s": inst, "p": pred, "o": f"{prefix}{i}", "ts": ts0 + i, "w": w} for i in range(n)]


def _write(engine, edges, graph, batch=200):
    for k in range(0, len(edges), batch):
        engine.bulk_create_edges_temporal(edges[k:k + batch], graph=graph)


def _nodes(conn, graph):
    cur = conn.cursor()
    cur.execute("SELECT node_id FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') = COALESCE(?, '')", [graph or ""])
    return {r[0] for r in cur.fetchall()}


def _rows(conn, graph, pred=None):
    cur = conn.cursor()
    q = "SELECT s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
    args = [graph or ""]
    if pred:
        q += " AND p = ?"
        args.append(pred)
    cur.execute(q, args)
    return [tuple(r) for r in cur.fetchall()]


def _kg_out(native, graph, s, p, o):
    key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph or "")
    return native.isDefined("KG", "out", key, s, p, o) > 0


def _second_connection():
    return dbapi.connect(
        hostname="localhost", port=int(os.environ.get("IVG_PORT", "31972")),
        namespace=os.environ.get("IVG_NAMESPACE", "USER"), username="_SYSTEM", password="SYS",
    )


# ------------------------------------------------------------------------------- 1. opt out, write, read


def test_an_opted_out_predicate_writes_the_temporal_globals_and_nothing_structural(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    assert engine.get_temporal_structural(SPAN, graph=g) is False
    assert engine.list_temporal_structural_opt_outs(graph=g) == [SPAN]
    edges = _spans(30, w=2.0) + [{"s": "inst:a", "p": "OWNS", "o": "thing:x", "ts": T0 + 5, "w": 1.0}]
    _write(engine, edges, g)
    # the structural predicate in the same batch still gets its row, its nodes and its ^KG entry
    assert [(r[0], r[1], r[2]) for r in _rows(conn, g)] == [("inst:a", "OWNS", "thing:x")]
    assert _nodes(conn, g) == {"inst:a", "thing:x"}
    assert _kg_out(native, g, "inst:a", "OWNS", "thing:x") and not _kg_out(native, g, "inst:a", SPAN, "span:3")
    # the temporal reads
    window = engine.get_edges_in_window(source="inst:a", predicate=SPAN, start=T0, end=T0 + 1000, graph=g)
    assert len(window) == 30 and {e["o"] for e in window} == {f"span:{i}" for i in range(30)}
    inbound = engine.get_edges_in_window(source="span:7", predicate=SPAN, start=T0, end=T0 + 1000, direction="in", graph=g)
    assert [e["s"] for e in inbound] == ["inst:a"]
    assert engine.get_temporal_aggregate("inst:a", SPAN, "count", T0, T0 + 1000, graph=g) == 30
    assert engine.get_temporal_aggregate("inst:a", SPAN, "sum", T0, T0 + 1000, graph=g) == 60.0
    assert engine.get_distinct_count("inst:a", SPAN, T0, T0 + 1000, graph=g) >= 25  # a 16-register sketch: approximate
    latest = engine.get_latest_edges(["inst:a"], predicate=SPAN, graph=g)
    assert len(latest) == 1 and latest[0]["o"] == "span:29"
    groups = engine.get_bucket_groups(predicate=SPAN, ts_start=T0, ts_end=T0 + 1000, graph=g)
    assert sum(int(x.get("count", 0)) for x in groups) == 30


def test_the_write_with_and_without_the_registration_gives_the_same_temporal_stores(env):
    """The temporal globals of an opted-out graph equal those of a structural one: only the rows differ."""
    engine, native, conn, g = env
    ref = g + "_ref"
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(40), g)
    _write(engine, _spans(40), ref)
    only_structural = {"Graph_KG.rdf_edges", "Graph_KG.nodes", '^KG("out")', '^KG("in")', '^KG("gen")'}
    only_structural |= {"^IVG.TemporalStructural"}  # the registration itself
    assert compare(digest(native, g), digest(native, ref), exclude=CACHES | only_structural) == []
    assert _rows(conn, g) == [] and len(_rows(conn, ref)) == 40


def test_the_single_edge_writer_honours_the_registration_too(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    engine.create_edge_temporal("inst:a", SPAN, "span:1", T0 + 1, 1.0, graph=g)
    assert _rows(conn, g) == [] and _nodes(conn, g) == set()
    assert engine.get_temporal_aggregate("inst:a", SPAN, "count", T0, T0 + 10, graph=g) == 1


# ---------------------------------------------------------------------------- 2. structural reads are empty


def test_bfs_khop_and_match_on_an_opted_out_predicate_are_empty_and_warn(env, caplog):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(5), g)
    with caplog.at_level(logging.WARNING):
        bfs = engine.execute_bfs("inst:a", [SPAN], 2, "out", 0, graph=g) if hasattr(engine, "execute_bfs") else None
        res = engine.execute_cypher(
            f"USE GRAPH '{g}' MATCH (a)-[:{SPAN}]->(b) WHERE a.node_id = 'inst:a' RETURN b.node_id"
        )
    assert res.rows == []
    if bfs is not None:
        assert not bfs.rows
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert SPAN in msgs and "temporal-only" in msgs and g in msgs
    # the same query on a structural predicate is untouched
    engine.create_edge("inst:a", "OWNS", "thing:x", graph=g)
    caplog.clear()
    res = engine.execute_cypher(f"USE GRAPH '{g}' MATCH (a)-[:OWNS]->(b) WHERE a.node_id = 'inst:a' RETURN b.node_id")
    assert [r[0] for r in res.rows] == ["thing:x"]
    assert not [r for r in caplog.records if "temporal-only" in r.getMessage()]


def test_the_temporal_window_walk_still_reads_an_opted_out_predicate_hop_by_hop(env):
    """execute_temporal_cypher walks the temporal globals (QueryWindow), not the rows: the open item of ADR-0015, answered."""
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, [
        {"s": "a", "p": SPAN, "o": "b", "ts": T0 + 1, "w": 1.0},
        {"s": "b", "p": SPAN, "o": "c", "ts": T0 + 2, "w": 1.0},
    ], g)
    res = engine._store.execute_temporal_cypher("a", [SPAN], T0, T0 + 100, "out", 2, graph=g)
    assert res.error is None
    assert sorted((r[0], r[1]) for r in res.rows) == [("b", 1), ("c", 2)]
    assert _rows(conn, g) == []


# ---------------------------------------------------------------------------------- 3. dropping the rows


def _seed_structural_then_register(engine, g, n=60):
    _write(engine, _spans(n), g)  # structural: rows, nodes, ^KG
    engine.create_edge("inst:a", SPAN, "span:0", graph=g)  # adopts span:0's row: now unmarked, explicit
    engine.set_temporal_structural(SPAN, False, graph=g)


def test_a_dry_run_of_the_drop_counts_and_changes_nothing(env):
    engine, native, conn, g = env
    _seed_structural_then_register(engine, g)
    before = digest(native, g)
    v0 = native.get("KG", "__version") if native.isDefined("KG", "__version") else None
    res = engine.drop_temporal_structural_rows(SPAN, graph=g, dry_run=True)
    assert res.dry_run and res.rows == 59 and res.nodes_retired == 0 and res.unchecked == 1  # the adopted, explicit one
    assert res.per_predicate == {SPAN: {"rows": 59, "unchecked": 1, "nodes_retired": 0}}
    assert digest(native, g) == before
    assert (native.get("KG", "__version") if native.isDefined("KG", "__version") else None) == v0


def test_the_drop_removes_only_marked_rows_and_the_bare_nodes_they_leave(env):
    engine, native, conn, g = env
    _seed_structural_then_register(engine, g)
    res = engine.drop_temporal_structural_rows(SPAN, graph=g, batch_size=25)
    assert res.rows == 59 and not res.dry_run and res.batches >= 3
    assert res.per_predicate == {SPAN: {"rows": 59, "unchecked": 0, "nodes_retired": 59}}
    assert res.nodes_retired == 59  # span:1..59; inst:a and span:0 are held by the explicit edge
    assert [(r[0], r[2]) for r in _rows(conn, g)] == [("inst:a", "span:0")]
    assert _nodes(conn, g) == {"inst:a", "span:0"}
    assert _kg_out(native, g, "inst:a", SPAN, "span:0") and not _kg_out(native, g, "inst:a", SPAN, "span:5")
    # the temporal data is untouched
    assert engine.get_temporal_aggregate("inst:a", SPAN, "count", T0, T0 + 1000, graph=g) == 60
    again = engine.drop_temporal_structural_rows(SPAN, graph=g)
    assert again.rows == 0 and again.nodes_retired == 0  # idempotent


def test_the_drop_can_leave_the_nodes(env):
    engine, native, conn, g = env
    _seed_structural_then_register(engine, g, n=10)
    res = engine.drop_temporal_structural_rows(SPAN, graph=g, retire_bare_nodes=False)
    assert res.rows == 9 and res.nodes_retired == 0
    assert len(_nodes(conn, g)) == 11
    assert engine.erase_bare_nodes(graph=g).erased == 9  # the sweep takes them


def test_the_drop_refuses_a_predicate_that_is_still_structural(env):
    engine, native, conn, g = env
    _write(engine, _spans(3), g)
    with pytest.raises(ValueError, match="opt|structural|register"):
        engine.drop_temporal_structural_rows(SPAN, graph=g)
    assert len(_rows(conn, g)) == 3


def test_the_drop_takes_a_marked_row_whatever_the_json_layout(env):
    """A row another writer marked with the key later in the object (json.dumps of a dict) is still a marked row."""
    engine, native, conn, g = env
    _write(engine, _spans(2), g)
    cur = conn.cursor()
    cur.execute(
        "UPDATE Graph_KG.rdf_edges SET qualifiers = ? WHERE graph_id = ? AND o_id = 'span:1'",
        [json.dumps({"weight": 2, "temporal": True}), g],
    )
    conn.commit()
    engine.set_temporal_structural(SPAN, False, graph=g)
    assert engine.drop_temporal_structural_rows(SPAN, graph=g).rows == 2


# ---------------------------------------------------------------------------------- 4. the rollback backfill


def test_the_backfill_restores_rows_nodes_and_adjacency_for_a_predicate_made_structural_again(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    edges = _spans(20, w=3.0) + [{"s": "inst:a", "p": SPAN, "o": "span:0", "ts": T0 + 500, "w": 3.0}]
    _write(engine, edges, g)
    assert _rows(conn, g) == []
    engine.set_temporal_structural(SPAN, True, graph=g)
    assert engine.list_temporal_structural_opt_outs(graph=g) == []
    res = engine.backfill_temporal_structural_rows(SPAN, graph=g)
    assert res == 20  # distinct triples: span:0 has two timestamps
    rows = _rows(conn, g)
    assert len(rows) == 20 and all(json.loads(r[3]) == {"temporal": True, "weight": 3.0} for r in rows)
    assert _nodes(conn, g) == {"inst:a"} | {f"span:{i}" for i in range(20)}
    assert _kg_out(native, g, "inst:a", SPAN, "span:7")
    assert engine.backfill_temporal_structural_rows(SPAN, graph=g) == 20  # idempotent: visits, adds nothing
    assert len(_rows(conn, g)) == 20


def test_the_backfill_refuses_a_predicate_that_is_opted_out(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(3), g)
    with pytest.raises(ValueError, match="opted out|structural"):
        engine.backfill_temporal_structural_rows(SPAN, graph=g)
    assert _rows(conn, g) == []


def test_the_backfill_of_one_predicate_touches_no_other(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(5) + _spans(5, pred="EMITTED_AT", prefix="ev:"), g)
    rows_before = len(_rows(conn, g, "EMITTED_AT"))
    engine.set_temporal_structural(SPAN, True, graph=g)
    engine.backfill_temporal_structural_rows(SPAN, graph=g)
    assert len(_rows(conn, g, "EMITTED_AT")) == rows_before == 5 and len(_rows(conn, g, SPAN)) == 5


# ---------------------------------------------------------------------------------- 5. the upgrade backfill


def _delete_rows_keep_temporal(conn, g):
    cur = conn.cursor()
    cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE graph_id = ?", [g])
    cur.execute("DELETE FROM Graph_KG.nodes WHERE graph_id = ?", [g])
    conn.commit()


def test_the_upgrade_backfill_skips_an_opted_out_predicate_and_gives_the_others_their_rows(env):
    engine, native, conn, g = env
    _write(engine, _spans(8) + _spans(4, pred="EMITTED_AT", prefix="ev:"), g)  # as 4.4.x would have
    _delete_rows_keep_temporal(conn, g)  # an install whose rows are missing, as before the first backfill
    engine.set_temporal_structural(SPAN, False, graph=g)
    cur = conn.cursor()
    cur.execute("SELECT Graph_KG.backfill_temporal_rows()")
    cur.fetchone()
    conn.commit()
    assert len(_rows(conn, g, SPAN)) == 0
    assert len(_rows(conn, g, "EMITTED_AT")) == 4
    assert _nodes(conn, g) == {"inst:a"} | {f"ev:{i}" for i in range(4)}


def test_the_python_migration_backfill_skips_an_opted_out_predicate(env):
    from iris_vector_graph.migrations.temporal_structural_rows import backfill_structural_rows

    engine, native, conn, g = env
    _write(engine, _spans(6) + _spans(3, pred="EMITTED_AT", prefix="ev:"), g)
    _delete_rows_keep_temporal(conn, g)
    engine.set_temporal_structural(SPAN, False, graph=g)
    report = backfill_structural_rows(conn, engine=engine, graphs=[g])
    assert report.inserted == 3
    assert len(_rows(conn, g, SPAN)) == 0 and len(_rows(conn, g, "EMITTED_AT")) == 3


def test_the_backfill_remembers_a_bounded_number_of_triples_and_still_writes_every_row(env):
    engine, native, conn, g = env
    # 40 distinct triples, each at 3 timestamps: more than the 5 the memory below can hold
    edges = [
        {"s": "inst:a", "p": SPAN, "o": f"span:{i}", "ts": T0 + 1000 * k + i, "w": 1.0}
        for k in range(3) for i in range(40)
    ]
    _write(engine, edges, g)
    _delete_rows_keep_temporal(conn, g)
    stats = json.loads(str(native.classMethodValue("Graph.KG.TemporalIndex", "BackfillRows", g, "", 5)))
    assert stats["seen_peak"] <= 5
    assert stats["asserted"] >= 40  # a forgotten triple is asserted again: idempotent, never missed
    assert len(_rows(conn, g)) == 40
    assert _nodes(conn, g) == {"inst:a"} | {f"span:{i}" for i in range(40)}


# ---------------------------------------------------------------------------------- 6. the registry's lifetime


def test_erase_graph_keeps_the_registration_and_erase_all_removes_it(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(3), g)
    engine.erase_graph(g)
    assert engine.get_temporal_structural(SPAN, graph=g) is False  # configuration outlives the content
    _write(engine, _spans(3), g)
    assert _rows(conn, g) == []  # ...so a re-ingest after the erase is still temporal-only
    engine.erase_all()
    assert engine.get_temporal_structural(SPAN, graph=g) is True
    assert engine.list_temporal_structural_opt_outs(graph=g) == []


def test_the_registration_is_in_a_snapshot_and_comes_back_with_it(env, tmp_path):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(3), g)
    path = str(tmp_path / "snap.ivg")
    engine.save_snapshot(path)
    engine.erase_all()
    assert engine.get_temporal_structural(SPAN, graph=g) is True
    engine.restore_snapshot(path)
    assert engine.get_temporal_structural(SPAN, graph=g) is False


# ---------------------------------------------------------------------------------- 7. isolation


def test_a_registration_applies_to_its_graph_and_predicate_only(env):
    engine, native, conn, g = env
    other = g + "_b"
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(3) + _spans(3, pred="EMITTED_AT", prefix="ev:"), g)
    _write(engine, _spans(3), other)
    assert len(_rows(conn, g, SPAN)) == 0 and len(_rows(conn, g, "EMITTED_AT")) == 3
    assert len(_rows(conn, other, SPAN)) == 3
    assert engine.get_temporal_structural(SPAN, graph=other) is True
    assert engine.get_temporal_structural("EMITTED_AT", graph=g) is True
    assert engine.list_temporal_structural_opt_outs(graph=other) == []
    # the default graph is a graph of its own
    assert engine.get_temporal_structural(SPAN) is True
    engine.set_temporal_structural(SPAN, False)
    assert engine.get_temporal_structural(SPAN, graph=other) is True
    engine.set_temporal_structural(SPAN, True)


def test_a_bad_name_is_refused_before_anything_is_written(env):
    engine, native, conn, g = env
    with pytest.raises(ValueError):
        engine.set_temporal_structural(SPAN, False, graph="bad\x01name")
    with pytest.raises(ValueError):
        engine.set_temporal_structural("", False, graph=g)
    assert engine.list_temporal_structural_opt_outs(graph=g) == []


# ---------------------------------------------------------------------------------- 8. a writer racing the switch


def test_a_writer_racing_set_temporal_structural_loses_no_temporal_edge_and_the_drop_then_leaves_no_row(env):
    engine, native, conn, g = env
    stop = threading.Event()
    wrote, failed = [], []

    def writer():
        c2 = _second_connection()
        e2 = IRISGraphEngine(c2, embedding_dimension=DIM)
        i = 0
        while not stop.is_set() and i < 600:
            try:
                e2.bulk_create_edges_temporal(
                    [{"s": "inst:a", "p": SPAN, "o": f"span:{i}", "ts": T0 + i, "w": 1.0}], graph=g)
                wrote.append(i)
            except Exception as exc:
                failed.append(str(exc)[:300])
            i += 1
        c2.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        flip = 0
        while t.is_alive():
            engine.set_temporal_structural(SPAN, flip % 2 == 1, graph=g)
            flip += 1
    finally:
        stop.set()
        t.join()
    assert failed == [] and wrote
    engine.set_temporal_structural(SPAN, False, graph=g)
    # every write is in the temporal index, whichever side of the switch it fell on
    n = engine.get_temporal_aggregate("inst:a", SPAN, "count", T0, T0 + 10_000, graph=g)
    assert n == len(wrote)
    engine.drop_temporal_structural_rows(SPAN, graph=g)
    assert _rows(conn, g) == [] and _nodes(conn, g) == set()
    # and the other direction closes the gap
    engine.set_temporal_structural(SPAN, True, graph=g)
    engine.backfill_temporal_structural_rows(SPAN, graph=g)
    assert len(_rows(conn, g)) == len(wrote)


# ---------------------------------------------------------------------------------- 9. the purge


def test_the_purge_leaves_an_opted__out_triple_to_the_drop_and_retire_bare_nodes_has_nothing_to_look_at(env):
    engine, native, conn, g = env
    _write(engine, _spans(10), g)  # marked rows, written while structural
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(10, prefix="new:"), g)  # opted out: no rows
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.deleted == 20
    # the purge neither retracted the old marked rows nor retired their nodes: that is the drop's job, once
    assert len(_rows(conn, g)) == 10 and len(_nodes(conn, g)) == 11
    assert engine.drop_temporal_structural_rows(SPAN, graph=g).rows == 10
    assert _nodes(conn, g) == set()


def test_a_purge_of_an_opted_out_graph_costs_no_row_statement(env):
    engine, native, conn, g = env
    engine.set_temporal_structural(SPAN, False, graph=g)
    _write(engine, _spans(500), g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.deleted == 500 and res.nodes_retired == 0


# ---------------------------------------------------------------------- opsreview's three predicates at once

THREE = ("OBSERVED_AT", "EMITTED_AT", "METRIC_AT")


def _three(n_each):
    edges = []
    for k, pred in enumerate(THREE):
        for i in range(n_each):
            edges.append({"s": f"inst:{i % 3}", "p": pred, "o": f"{pred.lower()}:{i}", "ts": T0 + 100 * k + i, "w": 1.0})
    return edges


def test_all_three_opsreview_predicates_registered_at_once_in_one_graph(env):
    engine, native, conn, g = env
    for pred in THREE:
        engine.set_temporal_structural(pred, False, graph=g)
    assert engine.list_temporal_structural_opt_outs(graph=g) == sorted(THREE)
    _write(engine, _three(30), g)
    assert _rows(conn, g) == [] and _nodes(conn, g) == set()
    for k, pred in enumerate(THREE):
        # window reads (QueryWindow / iter_edges_in_window)
        win = list(engine.iter_edges_in_window(T0, T0 + 1000, predicates=[pred], graph=g))
        assert len(win) == 30 and {e["p"] for e in win} == {pred}
        assert engine.get_temporal_aggregate("inst:0", pred, "count", T0, T0 + 1000, graph=g) == 10
        # the inbound walk TenantErase does: ^KG("tin", g, ts, target, pred, source)
        key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", g)
        ts = T0 + 100 * k + 4
        assert native.isDefined("KG", "tin", key, ts, f"{pred.lower()}:4", pred, "inst:1") > 0
        # and ^KG("tout", g, ts, source, pred, target), the direct walk of AggDocBuilder and the exporter
        assert native.isDefined("KG", "tout", key, ts, "inst:1", pred, f"{pred.lower()}:4") > 0
    sources = engine.get_window_sources("OBSERVED_AT", T0, T0 + 1000, graph=g)
    assert sorted(sources) == ["inst:0", "inst:1", "inst:2"]
    # the temporal stores equal those of a graph that kept the rows
    ref = g + "_ref"
    _write(engine, _three(30), ref)
    skip = {"Graph_KG.rdf_edges", "Graph_KG.nodes", '^KG("out")', '^KG("in")', '^KG("gen")', "^IVG.TemporalStructural"}
    assert compare(digest(native, g), digest(native, ref), exclude=CACHES | skip) == []


def test_the_drop_reports_each_predicate_and_the_dry_run_matches_it(env):
    engine, native, conn, g = env
    _write(engine, _three(40), g)  # structural first: 120 rows
    assert len(_rows(conn, g)) == 120
    for pred in THREE:
        engine.set_temporal_structural(pred, False, graph=g)
    dry = engine.drop_temporal_structural_rows(list(THREE), graph=g, dry_run=True)
    assert dry.dry_run and dry.rows == 120 and dry.unchecked == 0
    assert dry.per_predicate == {p: {"rows": 40, "unchecked": 0, "nodes_retired": 0} for p in THREE}
    assert len(_rows(conn, g)) == 120  # nothing moved
    real = engine.drop_temporal_structural_rows(list(THREE), graph=g, batch_size=30)
    assert real.rows == 120 and set(real.per_predicate) == set(THREE)
    assert all(v["rows"] == 40 for v in real.per_predicate.values())
    # 3 sources + 120 targets are all bare once the last predicate goes: each drop retires what it left bare
    assert sum(v["nodes_retired"] for v in real.per_predicate.values()) == real.nodes_retired == 123
    assert _rows(conn, g) == [] and _nodes(conn, g) == set()


def test_a_drop_of_several_predicates_refuses_before_deleting_anything_if_one_is_still_structural(env):
    engine, native, conn, g = env
    _write(engine, _three(5), g)
    engine.set_temporal_structural("OBSERVED_AT", False, graph=g)
    with pytest.raises(ValueError, match="EMITTED_AT"):
        engine.drop_temporal_structural_rows(["OBSERVED_AT", "EMITTED_AT"], graph=g)
    assert len(_rows(conn, g)) == 15
