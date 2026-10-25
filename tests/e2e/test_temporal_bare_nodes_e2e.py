"""Bare endpoint nodes a temporal purge leaves behind (opsreview, IVG 4.4.1, 2026-10-25).

``TemporalIndex.InsertEdge`` asserts the two endpoint nodes and a marked ``rdf_edges`` row for every new
``(s, p, o)`` (ADR-0009). ``PurgeRawBefore`` retires the marked row when the triple's last timestamp goes and, until
now, left both nodes: "Nodes stay". With one distinct target per span that is one orphan per span, forever.

Two things are tested here:

* ``purge_raw_before(..., retire_bare_nodes=True)`` deletes each endpoint of a retired triple that is left BARE in that
  graph: no edge naming it (as source or target), no label, no property, no embedding, no document.
* ``erase_bare_nodes`` is the maintenance sweep for a backlog that already exists: bounded batches, a dry run that is
  read-only, a minimum age, the same guard.

The guard is a plain ``DELETE FROM Graph_KG.nodes`` with ``NOT EXISTS`` for the two child tables that carry no
foreign key (properties, documents). The foreign keys ``fk_edges_source``, ``fk_edges_dest``, ``fk_labels_node`` and
the embedding tables' own refuse the delete while a dependant exists, including one another connection has written and
not yet committed; ``test_a_concurrent_*`` proves that live.

Run: ``IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 IVG_ARNO_PORT=31972``.
"""

from __future__ import annotations

import json
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


@pytest.fixture
def env(iris_connection, iris_master_cleanup):
    tag = uuid.uuid4().hex[:6]
    engine = IRISGraphEngine(iris_connection, embedding_dimension=DIM)
    return engine, iris.createIRIS(iris_connection), iris_connection, f"g_bn_{tag}"


# ------------------------------------------------------------------------------------------------ helpers


def _nodes(conn, graph, like=None):
    cur = conn.cursor()
    q = "SELECT node_id FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
    args = [graph or ""]
    if like:
        q += " AND node_id LIKE ?"
        args.append(like)
    cur.execute(q, args)
    return {r[0] for r in cur.fetchall()}


def _count(conn, table, graph, col="graph_id"):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{table} WHERE COALESCE({col}, '') = COALESCE(?, '')", [graph or ""])
    return cur.fetchone()[0]


def _temporal(engine, edges, graph, batch=500):
    for k in range(0, len(edges), batch):
        engine.bulk_create_edges_temporal(edges[k:k + batch], graph=graph)


def _span_edges(n, *, inst="inst:a", pred="OBSERVED_AT", ts0=T0, prefix="span:"):
    return [{"s": inst, "p": pred, "o": f"{prefix}{i}", "ts": ts0 + i, "w": 1.0} for i in range(n)]


def _version(native):
    return native.get("KG", "__version") if native.isDefined("KG", "__version") else None


def _bare_insert(conn, ids, graph):
    cur = conn.cursor()
    for i in ids:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [i, graph or ""])
    conn.commit()


# ----------------------------------------------------------------------------------- purge, option off (today)


def test_a_purge_without_the_option_leaves_the_nodes(env):
    engine, native, conn, g = env
    _temporal(engine, _span_edges(6), g)
    assert len(_nodes(conn, g)) == 7
    res = engine.purge_raw_before(T0 + HOUR, graph=g)
    assert res.deleted == 6
    assert getattr(res, "nodes_retired", 0) == 0
    assert len(_nodes(conn, g)) == 7  # "Nodes stay": the documented behaviour, unchanged when the option is off


# ------------------------------------------------------------------------------------------ purge, option on


def test_a_purge_retires_every_endpoint_the_purge_left_bare(env):
    engine, native, conn, g = env
    _temporal(engine, _span_edges(40), g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.deleted == 40
    assert res.nodes_retired == 41  # 40 spans and the instance
    assert _nodes(conn, g) == set()
    assert _count(conn, "rdf_edges", g) == 0


def test_a_purge_keeps_an_endpoint_a_surviving_triple_still_uses(env):
    engine, native, conn, g = env
    old = _span_edges(10, ts0=T0)
    live = [{"s": "inst:a", "p": "OBSERVED_AT", "o": "span:live", "ts": T0 + 5 * HOUR, "w": 1.0}]
    _temporal(engine, old + live, g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.nodes_retired == 10  # the spans; inst:a still has span:live
    assert _nodes(conn, g) == {"inst:a", "span:live"}


def test_a_triple_with_a_later_timestamp_is_not_retired_and_neither_are_its_nodes(env):
    engine, native, conn, g = env
    edges = [
        {"s": "inst:a", "p": "SEEN", "o": "host:1", "ts": T0 + 1, "w": 1.0},
        {"s": "inst:a", "p": "SEEN", "o": "host:1", "ts": T0 + 9 * HOUR, "w": 1.0},
    ]
    _temporal(engine, edges, g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.deleted == 1 and res.nodes_retired == 0
    assert _nodes(conn, g) == {"inst:a", "host:1"}
    assert _count(conn, "rdf_edges", g) == 1


def test_an_explicit_structural_edge_survives_with_its_endpoints(env):
    engine, native, conn, g = env
    engine.create_edge("inst:a", "OWNS", "thing:x", graph=g)
    _temporal(engine, [{"s": "inst:a", "p": "OWNS", "o": "thing:x", "ts": T0 + 1, "w": 1.0}], g)
    _temporal(engine, _span_edges(3), g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.nodes_retired == 3  # spans only: the unmarked row names inst:a and thing:x
    assert _nodes(conn, g) == {"inst:a", "thing:x"}
    assert _count(conn, "rdf_edges", g) == 1


def test_a_label_a_property_an_embedding_and_a_document_each_keep_their_node(env):
    engine, native, conn, g = env
    # one dependant each, by SQL, so that no other row (create_node writes an ``id`` property) keeps the node
    _bare_insert(conn, ["lab:1", "prop:1", "emb:1", "doc:1", "bare:1"], g)
    engine.create_node("lab:x", graph=g)  # unused; proves the helper path below is not what keeps them
    cur = conn.cursor()
    cur.execute("INSERT INTO Graph_KG.rdf_labels (graph_id, s, label) VALUES (?, ?, ?)", [g, "lab:1", "Thing"])
    cur.execute('INSERT INTO Graph_KG.rdf_props (graph_id, s, "key", val) VALUES (?, ?, ?, ?)', [g, "prop:1", "k", "v"])
    cur.execute("INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)", [g, "doc:1", "hello"])
    conn.commit()
    engine.store_embedding("emb:1", [0.01] * DIM, graph=g)
    ts = [
        {"s": "inst:a", "p": "HAS", "o": o, "ts": T0 + i, "w": 1.0}
        for i, o in enumerate(["lab:1", "prop:1", "emb:1", "doc:1", "bare:1"])
    ]
    _temporal(engine, ts, g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.deleted == 5
    assert res.nodes_retired == 2  # inst:a and bare:1
    assert _nodes(conn, g) == {"lab:1", "prop:1", "emb:1", "doc:1", "lab:x"}
    assert _count(conn, "rdf_labels", g) == 1
    assert _count(conn, "docs", g) == 1  # (a named graph's vector lives in a routed table, which the node's FK names)


def test_a_node_create_node_made_keeps_its_id_property_and_so_survives_both_the_purge_and_the_sweep(env):
    """create_node writes an ``id`` property: a node made on purpose is never bare."""
    engine, native, conn, g = env
    engine.create_node("made:1", graph=g)
    _temporal(engine, [{"s": "made:1", "p": "SEEN", "o": "x:1", "ts": T0 + 1, "w": 1.0}], g)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.nodes_retired == 1  # x:1 only
    assert _nodes(conn, g) == {"made:1"}
    assert engine.erase_bare_nodes(graph=g).erased == 0
    assert _nodes(conn, g) == {"made:1"}


def test_only_the_purged_graphs_row_goes_when_the_id_is_used_in_another_graph(env):
    engine, native, conn, g = env
    other = g + "_b"
    _temporal(engine, _span_edges(4), g)
    # same ids, in a graph where they carry an edge and a label
    engine.create_edge("inst:a", "KNOWS", "span:0", graph=other)
    engine.create_node("span:1", labels=["L"], graph=other)
    res = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert res.nodes_retired == 5
    assert _nodes(conn, g) == set()
    assert _nodes(conn, other) == {"inst:a", "span:0", "span:1"}
    assert _count(conn, "rdf_edges", other) == 1


def test_the_default_graph_is_covered(env):
    engine, native, conn, g = env
    sfx = uuid.uuid4().hex[:6]
    edges = [{"s": f"dinst:{sfx}", "p": "OBSERVED_AT", "o": f"dspan:{sfx}:{i}", "ts": T0 + i, "w": 1.0} for i in range(5)]
    _temporal(engine, edges, None)
    res = engine.purge_raw_before(T0 + HOUR, retire_bare_nodes=True)
    assert res.deleted >= 5 and res.nodes_retired >= 6
    assert _nodes(conn, None, like=f"d%{sfx}%") == set()


def test_the_legacy_purge_before_takes_the_option_too(env):
    engine, native, conn, g = env
    sfx = uuid.uuid4().hex[:6]
    edges = [{"s": f"linst:{sfx}", "p": "OBSERVED_AT", "o": f"lspan:{sfx}:{i}", "ts": T0 + i, "w": 1.0} for i in range(3)]
    _temporal(engine, edges, None)
    engine.purge_before(T0 + HOUR, retire_bare_nodes=True)
    assert _nodes(conn, None, like=f"l%{sfx}%") == set()


def test_a_purge_with_the_option_is_idempotent_and_matches_a_graph_that_never_held_the_edges(env):
    engine, native, conn, g = env
    ref = g + "_ref"
    live = [{"s": "inst:a", "p": "OBSERVED_AT", "o": f"span:live{i}", "ts": T0 + 5 * HOUR + i, "w": 1.0} for i in range(5)]
    _temporal(engine, _span_edges(30) + live, g)
    _temporal(engine, live, ref)
    first = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    second = engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert first.nodes_retired == 30 and second.nodes_retired == 0 and second.deleted == 0
    # nodes included this time: with the option on, a purged graph IS the reference graph
    assert compare(digest(native, g), digest(native, ref), exclude=CACHES | AGGREGATES | {'^KG("gen")'}) == []


def test_a_retired_source_leaves_no_generation_or_cache_entry_and_the_caches_move(env):
    engine, native, conn, g = env
    key = native.classMethodValue("Graph.KG.GraphKey", "ForIndex", g)
    _temporal(engine, _span_edges(3), g)
    assert int(native.classMethodValue("Graph.KG.Adj", "Degree", key, "inst:a", "")) == 3  # warms degc
    v0 = _version(native)
    engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert native.isDefined("KG", "gen", key, "inst:a") == 0
    assert native.isDefined("KG", "degc", key, "inst:a") == 0
    assert _version(native) != v0


# ----------------------------------------------------------------------------------------- the maintenance sweep


def test_the_sweep_erases_bare_nodes_in_bounded_batches(env):
    engine, native, conn, g = env
    ids = [f"orph:{i}" for i in range(205)]
    _bare_insert(conn, ids, g)
    engine.create_node("keep:label", labels=["L"], graph=g)
    engine.create_node("keep:prop", properties={"a": "b"}, graph=g)
    engine.create_edge("keep:s", "R", "keep:o", graph=g)
    res = engine.erase_bare_nodes(graph=g, batch_size=50)
    assert res.erased == 205 and res.bare == 205 and res.kept == 0 and res.batches == 5 and not res.dry_run
    assert _nodes(conn, g) == {"keep:label", "keep:prop", "keep:s", "keep:o"}
    again = engine.erase_bare_nodes(graph=g, batch_size=50)
    assert again.erased == 0 and again.bare == 0 and again.batches == 0


def test_the_dry_run_counts_and_changes_nothing(env):
    engine, native, conn, g = env
    _bare_insert(conn, [f"orph:{i}" for i in range(30)], g)
    engine.create_edge("keep:s", "R", "keep:o", graph=g)
    before = digest(native, g)
    v0 = _version(native)
    res = engine.erase_bare_nodes(graph=g, dry_run=True)
    assert res.dry_run and res.bare == 30 and res.erased == 0
    assert digest(native, g) == before  # every graph-scoped store, byte for byte
    assert _version(native) == v0
    assert len(_nodes(conn, g)) == 32
    assert engine.erase_bare_nodes(graph=g).erased == 30


def test_the_sweep_leaves_a_node_younger_than_the_minimum_age(env):
    engine, native, conn, g = env
    _bare_insert(conn, ["young:1", "young:2"], g)
    res = engine.erase_bare_nodes(graph=g, min_age_seconds=3600)
    assert res.erased == 0 and res.bare == 0
    assert len(_nodes(conn, g)) == 2
    assert engine.erase_bare_nodes(graph=g, min_age_seconds=0).erased == 2


def test_the_sweep_is_scoped_to_one_graph(env):
    engine, native, conn, g = env
    other = g + "_b"
    _bare_insert(conn, ["same:1", "same:2"], g)
    _bare_insert(conn, ["same:1", "same:2"], other)
    assert engine.erase_bare_nodes(graph=g).erased == 2
    assert _nodes(conn, other) == {"same:1", "same:2"}


def test_the_sweep_keeps_a_node_an_embedding_in_a_routed_table_holds(env):
    engine, native, conn, g = env
    engine.create_node("emb:1", graph=g)
    engine.store_embedding("emb:1", [0.02] * DIM, graph=g)
    _bare_insert(conn, ["orph:1"], g)
    res = engine.erase_bare_nodes(graph=g)
    assert res.erased == 1
    assert _nodes(conn, g) == {"emb:1"}


def test_the_sweep_erases_the_backlog_a_temporal_workload_made(env):
    engine, native, conn, g = env
    _temporal(engine, _span_edges(120), g)
    engine.purge_raw_before(T0 + HOUR, graph=g)  # today's purge: nodes stay
    assert len(_nodes(conn, g)) == 121
    dry = engine.erase_bare_nodes(graph=g, dry_run=True)
    assert dry.bare == 121
    assert engine.erase_bare_nodes(graph=g).erased == 121
    assert _nodes(conn, g) == set()


# ------------------------------------------------------------------------------------------ concurrency


def _second_connection():
    return dbapi.connect(
        hostname="localhost", port=int(os.environ.get("IVG_PORT", "31972")),
        namespace=os.environ.get("IVG_NAMESPACE", "USER"), username="_SYSTEM", password="SYS",
    )


def test_a_concurrent_edge_written_and_not_yet_committed_is_never_deleted(env):
    engine, native, conn, g = env
    _bare_insert(conn, ["shared:1", "other:1"], g)
    writer = _second_connection()
    try:
        wc = writer.cursor()
        wc.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)", ["other:1", "R", "shared:1", g])
        # the edge is written, not committed: the sweep must neither see shared:1 as bare nor delete it
        res = engine.erase_bare_nodes(graph=g)
        assert res.erased == 0 and res.bare == 0
        writer.commit()
    finally:
        writer.close()
    assert _nodes(conn, g) == {"shared:1", "other:1"}
    assert _count(conn, "rdf_edges", g) == 1


def test_the_plain_delete_the_sweep_relies_on_is_refused_by_the_foreign_key(env):
    """The primitive, shown on its own: the guard in the statement is a speed-up, the constraint is the safety."""
    engine, native, conn, g = env
    _bare_insert(conn, ["shared:1", "other:1"], g)
    writer = _second_connection()
    try:
        wc = writer.cursor()
        wc.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)", ["other:1", "R", "shared:1", g])
        cur = conn.cursor()
        with pytest.raises(Exception) as ei:
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?", ["shared:1", g])
        assert "-124" in str(ei.value)
        writer.commit()
    finally:
        writer.close()


def test_a_writer_racing_the_sweep_loses_no_edge_it_was_told_succeeded(env):
    """create_edge in a loop against repeated sweeps. Every edge that returned True is in the table afterwards;
    a write the sweep beat (a foreign-key error) fails loudly and is retried, never half-applied."""
    engine, native, conn, g = env
    stop = threading.Event()
    done, errors = [], []

    def writer():
        c2 = _second_connection()
        e2 = IRISGraphEngine(c2, embedding_dimension=DIM)
        i = 0
        while not stop.is_set() and i < 150:
            s, o = f"w:s{i}", f"w:o{i}"
            for _attempt in range(5):
                try:
                    if e2.create_edge(s, "R", o, graph=g):
                        done.append((s, o))
                        break
                except Exception as exc:  # a lost race is an error, not a silent drop
                    errors.append(str(exc))
            i += 1
        c2.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        while t.is_alive():
            engine.erase_bare_nodes(graph=g, batch_size=20)
    finally:
        stop.set()
        t.join()
    cur = conn.cursor()
    cur.execute("SELECT s, o_id FROM Graph_KG.rdf_edges WHERE graph_id = ?", [g])
    rows = {(r[0], r[1]) for r in cur.fetchall()}
    assert set(done) <= rows
    nodes = _nodes(conn, g)
    assert all(s in nodes and o in nodes for s, o in rows)  # no edge without its nodes


def test_a_temporal_writer_racing_the_purge_retirement_keeps_every_triple_it_wrote(env):
    """AssertTemporalRow inserts the nodes and then the edge; a retirement between the two must not fail the write.

    The writer lands new timestamps on five targets while the main thread keeps writing an expired timestamp to the same
    five and purging with retirement, so the purge keeps finding the targets bare an instant before the writer needs them.
    """
    engine, native, conn, g = env
    stop = threading.Event()
    wrote, failed = set(), []

    def writer():
        c2 = _second_connection()
        e2 = IRISGraphEngine(c2, embedding_dimension=DIM)
        i = 0
        while not stop.is_set() and i < 400:
            tgt = f"tgt:{i % 5}"
            try:
                e2.bulk_create_edges_temporal(
                    [{"s": "inst:hot", "p": "OBSERVED_AT", "o": tgt, "ts": T0 + 10 * HOUR + i, "w": 1.0}], graph=g)
                wrote.add(tgt)
            except Exception as exc:
                failed.append(str(exc)[:300])
            i += 1
        c2.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        k = 0
        while t.is_alive():
            _temporal(engine, [{"s": "inst:hot", "p": "OBSERVED_AT", "o": f"tgt:{k % 5}", "ts": T0 + k, "w": 1.0}], g)
            engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
            k += 1
    finally:
        stop.set()
        t.join()
    assert failed == []
    # what the writer wrote is there: its timestamps are in the temporal index, and its triples have rows and nodes
    rows = {r[0] for r in _rows_out(conn, g)}
    assert wrote <= rows
    assert wrote | {"inst:hot"} <= _nodes(conn, g)


def _rows_out(conn, g):
    cur = conn.cursor()
    cur.execute("SELECT o_id FROM Graph_KG.rdf_edges WHERE s = ? AND graph_id = ?", ["inst:hot", g])
    return cur.fetchall()


# ------------------------------------------------------------------ the 4.4.1 / 4.4.2 recipe (no new class needed)


def _interim():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sweep_bare_nodes_interim.py"
    spec = importlib.util.spec_from_file_location("sweep_bare_nodes_interim", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_interim_script_sweeps_with_plain_sql_and_keeps_what_has_a_dependant(env):
    engine, native, conn, g = env
    sweep = _interim().sweep
    _bare_insert(conn, [f"orph:{i}" for i in range(25)], g)
    engine.create_edge("keep:s", "R", "keep:o", graph=g)
    assert sweep(conn, g, dry_run=True, min_age_seconds=0) == {"bare": 25, "erased": 0, "kept": 0}
    assert sweep(conn, g, batch=10, min_age_seconds=0) == {"bare": 25, "erased": 25, "kept": 0}
    assert _nodes(conn, g) == {"keep:s", "keep:o"}
    assert sweep(conn, g, min_age_seconds=0)["erased"] == 0


def test_the_interim_script_loses_nothing_to_an_edge_written_but_not_committed(env):
    engine, native, conn, g = env
    sweep = _interim().sweep
    _bare_insert(conn, ["shared:1", "other:1"], g)
    writer = _second_connection()
    try:
        writer.cursor().execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, ?)", ["other:1", "R", "shared:1", g])
        out = sweep(conn, g, min_age_seconds=0)
        writer.commit()
    finally:
        writer.close()
    assert out["erased"] == 0
    assert _nodes(conn, g) == {"shared:1", "other:1"} and _count(conn, "rdf_edges", g) == 1


# ------------------------------------------------------------------ AssertTemporalRow: same rows, fewer statements


def test_the_structural_rows_a_temporal_batch_writes_carry_the_marker_text_the_purge_prefix_matches(env):
    """RetractTemporalRows deletes with a %STARTSWITH on {"temporal":true: the text the writer stores must keep it."""
    engine, native, conn, g = env
    edges = [
        {"s": "inst:a", "p": "P", "o": "x:1", "ts": T0 + 1, "w": 1.0},
        {"s": "inst:a", "p": "P", "o": "x:2", "ts": T0 + 2, "w": 2.5},
        {"s": "inst:b", "p": "P", "o": "x:1", "ts": T0 + 3, "w": 1.0},
    ]
    _temporal(engine, edges, g)  # one batch: inst:a's node row is written once, for two edges
    cur = conn.cursor()
    cur.execute("SELECT s, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? ORDER BY s, o_id", [g])
    assert [(r[0], r[1], r[2]) for r in cur.fetchall()] == [
        ("inst:a", "x:1", '{"temporal":true}'),
        ("inst:a", "x:2", '{"temporal":true,"weight":2.5}'),
        ("inst:b", "x:1", '{"temporal":true}'),
    ]
    assert _nodes(conn, g) == {"inst:a", "inst:b", "x:1", "x:2"}
    # and the purge's fast path takes them all
    res = engine.purge_raw_before(T0 + HOUR, graph=g)
    assert res.deleted == 3 and _count(conn, "rdf_edges", g) == 0


def test_a_batch_whose_node_was_deleted_after_an_earlier_batch_still_writes_every_edge(env):
    engine, native, conn, g = env
    _temporal(engine, [{"s": "inst:a", "p": "P", "o": "x:1", "ts": T0 + 1, "w": 1.0}], g)
    engine.purge_raw_before(T0 + HOUR, graph=g, retire_bare_nodes=True)
    assert _nodes(conn, g) == set()
    _temporal(engine, [{"s": "inst:a", "p": "P", "o": f"x:{i}", "ts": T0 + 5 * HOUR + i, "w": 1.0} for i in range(1, 6)], g)
    assert _nodes(conn, g) == {"inst:a"} | {f"x:{i}" for i in range(1, 6)}
    assert _count(conn, "rdf_edges", g) == 5


def test_erase_node_ids_after_a_bareness_check_deletes_the_edge_written_in_between__so_it_is_the_wrong_sweep_tool(env):
    """Why opsreview's interim sweep must not be 'collect bare ids, then EraseNodeIds': it deletes every row naming the id."""
    engine, native, conn, g = env
    _bare_insert(conn, ["shared:1", "other:1"], g)
    checked_bare = engine.erase_bare_nodes(graph=g, dry_run=True).bare
    assert checked_bare == 2  # shared:1 is bare at the time of the check
    engine.create_edge("other:1", "R", "shared:1", graph=g)  # a writer asserts an edge to it afterwards
    assert _count(conn, "rdf_edges", g) == 1
    engine.delete_node("shared:1", graph=g)  # EraseNodeIds under the hood: unconditional
    assert _count(conn, "rdf_edges", g) == 0  # the edge went with the node, silently
    # the guarded sweep, given the same sequence, keeps it
    engine.create_edge("other:1", "R", "shared:2", graph=g)
    assert engine.erase_bare_nodes(graph=g).erased == 0
    assert _count(conn, "rdf_edges", g) == 1
