"""Spec 248 against IRIS: the generation-validated degree cache is exact and warm.

`Graph.KG.Adj.Degree` answers from `^KG("degc")` when its generation equals
`^KG("gen", g, src)`; otherwise it probes the writer lock, recounts, and stores only if
no writer was in flight and the generation did not move. `Graph.KG.Adjacency` bumps the
generation under the shared lock `^IVG.AdjW(g)`.

What this proves, live:

- the rollback race over two connections: a writer holds an open transaction containing
  an adjacency write, a reader reads meanwhile and sees the uncommitted edge, the writer
  rolls back, and the reader's next answer equals a recount; the entry was not stored
  from the dirty read;
- a reader inside the writer's own transaction does not store (a lock never conflicts
  with its own process);
- parallel-edge multiplicity (`"#"`), per predicate and in total;
- `Eraser.EraseGraph` and `Eraser.EraseAll` leave no `gen`/`degc`;
- `BuildKG` leaves the cache warm: every source has a current entry before any reader
  has asked, and nothing writes `^KG("deg")`/`("degp")`;
- `Adjacency.Remove` of a named graph's edge keeps the merged `^NKG` entry the default
  graph still holds;
- a randomized sequence of puts, removes and rolled-back puts and removes agrees with a
  recount after every step.

Runs in a scratch namespace only: BuildKG and EraseAll rebuild or empty `^KG`. By default
that is IVGPAROS (override with IVG_PAROS_NAMESPACE); the tests connect to it themselves
(`paros_connection`), whatever IVG_NAMESPACE the run uses, and skip where it does not exist.
"""

from __future__ import annotations

import contextlib
import os
import random

import pytest

pytestmark = [pytest.mark.e2e]

SCRATCH = os.environ.get("IVG_PAROS_NAMESPACE", "IVGPAROS")
G = "par248"  # the graph key every test here writes under
G2 = "par248b"
ALL = "\x01"  # the cache key for "all predicates"
P = "KNOWS"
Q = "LIKES"


# --- connections ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def target(paros_connection):
    assert getattr(paros_connection, "namespace", None) == SCRATCH
    return paros_connection


def _connect(target):
    import iris

    conn = iris.connect(
        hostname=target.hostname, port=target.port, namespace=target.namespace,
        username="_SYSTEM", password="SYS",
    )
    return conn, iris.createIRIS(conn)


@pytest.fixture
def reader(target):
    conn, native = _connect(target)
    yield native
    conn.close()


@pytest.fixture
def writer(target):
    conn, native = _connect(target)
    yield native
    with contextlib.suppress(Exception):
        native.tRollback()
    conn.close()


@pytest.fixture(autouse=True)
def clean(target):
    """Every test starts and ends with nothing under this file's graph keys."""
    conn, native = _connect(target)
    try:
        _wipe(native)
        yield
        _wipe(native)
    finally:
        conn.close()


DEFAULT_GRAPH_PREFIXES = ("w248_", "f248_", "n248_")


def _wipe(native):
    for key in (G, G2):
        for store in ("out", "in", "gen", "degc", "deg", "degp", "deg2p", "deg2p_exact"):
            native.kill("^KG", store, key)
    # The default graph is shared with every other test in the namespace: remove only our nodes.
    ours = set()
    for store in ("out", "in"):
        ours.update(n for n in _subs(native, "^KG", store, 0) if n.startswith(DEFAULT_GRAPH_PREFIXES))
    for node in sorted(ours):
        native.classMethodValue("Graph.KG.Adjacency", "RemoveNode", 0, node)


# --- helpers over the Native API --------------------------------------------------------------


def degree(native, src, pred="", graph=G):
    return int(native.classMethodValue("Graph.KG.Adj", "Degree", graph, src, pred))


def put(native, src, pred, dst, graph=G, w="1.0"):
    return native.classMethodValue("Graph.KG.Adjacency", "Put", graph, src, pred, dst, w)


def remove(native, src, pred, dst, graph=G):
    return native.classMethodValue("Graph.KG.Adjacency", "Remove", graph, src, pred, dst)


def recount(native, src, pred="", graph=G):
    """Count the out entries the way the contract says, from the globals, in Python."""
    total = 0
    preds = [pred] if pred else list(_subs(native, "^KG", "out", graph, src))
    for p in preds:
        for o in _subs(native, "^KG", "out", graph, src, p):
            marker = native.isDefined("^KG", "out", graph, src, p, o, "#")
            total += int(native.get("^KG", "out", graph, src, p, o, "#")) if marker in (1, 11) else 1
    return total


def _subs(native, *path):
    """Immediate subscripts under `path`."""
    sub = ""
    while True:
        sub = native.nextSubscript(False, *path, sub)
        if sub is None:
            return
        yield sub


def entry(native, src, key=ALL, graph=G):
    """The cache entry as (generation, count), or None."""
    import iris

    raw = native.getBytes("^KG", "degc", graph, src, key)
    if raw is None:
        return None
    lst = iris.IRISList(raw)
    return int(lst.get(1)), int(lst.get(2))


def gen(native, src, graph=G):
    value = native.get("^KG", "gen", graph, src)
    return int(value) if value is not None else 0


def exists(native, *path):
    return native.isDefined(*path) != 0


# --- the rollback race ---------------------------------------------------------------------------


def test_a_write_in_flight_is_seen_but_never_cached_and_rollback_is_clean(reader, writer):
    put(reader, "s", P, "a")
    put(reader, "s", P, "b")
    assert degree(reader, "s") == 2
    before = entry(reader, "s")
    assert before is not None and before[1] == 2 and before[0] == gen(reader, "s")

    writer.tStart()
    writer.classMethodValue("Graph.KG.Adjacency", "PutKeyed", G, "s", P, "c", 9, "1.0")
    # The lock is held until the writer ends its transaction, so the probe fails.
    assert degree(reader, "s") == 3, "another connection reads the uncommitted edge"
    after_dirty = entry(reader, "s")
    assert after_dirty != (gen(reader, "s"), 3), "the dirty count must not be stored at the live generation"

    writer.tRollback()
    assert recount(reader, "s") == 2
    assert degree(reader, "s") == 2
    final = entry(reader, "s")
    assert final == (gen(reader, "s"), 2), final


def test_a_removal_in_flight_is_seen_but_never_cached_and_rollback_is_clean(reader, writer):
    for dst in ("a", "b", "c"):
        put(reader, "s", P, dst)
    assert degree(reader, "s") == 3
    writer.tStart()
    remove(writer, "s", P, "c")
    assert degree(reader, "s") == 2
    writer.tRollback()
    assert recount(reader, "s") == 3
    assert degree(reader, "s") == 3


def test_a_commit_makes_the_next_read_recount(reader, writer):
    put(reader, "s", P, "a")
    assert degree(reader, "s") == 1
    writer.tStart()
    put(writer, "s", P, "b")
    writer.tCommit()
    assert degree(reader, "s") == 2
    assert entry(reader, "s") == (gen(reader, "s"), 2)


def test_a_reader_inside_the_writers_own_transaction_is_rolled_back_with_it(writer):
    """A lock never conflicts with its own process, so the probe succeeds and the reader stores
    its own dirty count. The store is a Set inside the same transaction: the rollback takes it back."""
    put(writer, "s", P, "a")
    assert degree(writer, "s") == 1
    writer.tStart()
    put(writer, "s", P, "b")
    assert degree(writer, "s") == 2, "the process sees its own uncommitted edge"
    writer.tRollback()
    assert recount(writer, "s") == 1
    assert degree(writer, "s") == 1
    assert entry(writer, "s") == (gen(writer, "s"), 1)


# --- multiplicity ----------------------------------------------------------------------------------


@pytest.fixture
def multigraph_rows(target, reader):
    """Rows with their own ekey for one triple (create_edge upserts, so a parallel edge is a row)."""
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(target)
    for node in ("m_s", "m_a", "m_b"):
        engine.create_node(node, graph=G)
    cur = target.cursor()
    try:
        for ekey in (1, 2, 3):
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES (?, ?, ?, ?, ?, ?)",
                ["m_s", P, "m_a", G, ekey, '{"weight": 1.0}'],
            )
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES (?, ?, ?, ?, ?, ?)",
            ["m_s", Q, "m_b", G, 1, '{"weight": 1.0}'],
        )
    finally:
        cur.close()
    yield
    cur = target.cursor()
    try:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE graph_id = ?", [G])
        cur.execute("DELETE FROM Graph_KG.nodes WHERE graph_id = ?", [G])
    finally:
        cur.close()


def test_parallel_edges_count_their_multiplicity(reader, multigraph_rows):
    for ekey in (1, 2, 3):
        reader.classMethodValue("Graph.KG.Adjacency", "PutKeyed", G, "m_s", P, "m_a", ekey, "1.0")
    reader.classMethodValue("Graph.KG.Adjacency", "PutKeyed", G, "m_s", Q, "m_b", 1, "1.0")
    assert reader.get("^KG", "out", G, "m_s", P, "m_a", "#") == 3
    assert degree(reader, "m_s") == 4 == recount(reader, "m_s")
    assert degree(reader, "m_s", P) == 3
    assert degree(reader, "m_s", Q) == 1
    reader.classMethodValue("Graph.KG.Adjacency", "RemoveKeyed", G, "m_s", P, "m_a", 2)
    assert degree(reader, "m_s") == 3 == recount(reader, "m_s")
    assert degree(reader, "m_s", P) == 2


# --- nothing writes the stored counters -----------------------------------------------------------


def test_the_stored_counters_are_not_written(reader):
    put(reader, "s", P, "a")
    reader.classMethodValue("Graph.KG.Adjacency", "PutShadow", G, "s", P, "b", "1.0")
    reader.classMethodValue("Graph.KG.Adjacency", "PutKeyed", G, "s", Q, "c", 1, "1.0")
    remove(reader, "s", P, "a")
    degree(reader, "s")
    assert not exists(reader, "^KG", "deg", G)
    assert not exists(reader, "^KG", "degp", G)
    assert exists(reader, "^KG", "gen", G, "s")
    assert gen(reader, "s") >= 4


def test_reweight_does_not_move_the_generation(reader):
    put(reader, "s", P, "a", w="1.0")
    g0 = gen(reader, "s")
    reader.classMethodValue("Graph.KG.Adjacency", "Reweight", G, "s", P, "a", "2.5")
    assert gen(reader, "s") == g0
    assert degree(reader, "s") == 1


# --- erase --------------------------------------------------------------------------------------------


def test_erase_graph_clears_out_in_gen_and_degc(reader):
    from iris_vector_graph import IRISGraphEngine  # noqa: F401  (import proves the package is importable here)

    put(reader, "s", P, "a")
    put(reader, "s", Q, "b")
    degree(reader, "s")
    degree(reader, "s", P)
    assert exists(reader, "^KG", "gen", G) and exists(reader, "^KG", "degc", G)
    reader.classMethodValue("Graph.KG.Eraser", "EraseGraph", G)
    for store in ("out", "in", "gen", "degc"):
        assert not exists(reader, "^KG", store, G), f'^KG("{store}", {G}) survived EraseGraph'
    assert degree(reader, "s") == 0


def test_erase_nodes_clears_the_nodes_generation_and_cache(reader):
    put(reader, "s", P, "a")
    put(reader, "t", P, "s")
    degree(reader, "s")
    degree(reader, "t")
    reader.classMethodValue("Graph.KG.Eraser", "EraseNodeIds", G, '["s"]')
    assert not exists(reader, "^KG", "gen", G, "s") and not exists(reader, "^KG", "degc", G, "s")
    assert degree(reader, "t") == 0, "the inbound edge to an erased node goes with it"
    assert recount(reader, "t") == 0


def test_erase_all_clears_gen_and_degc_in_every_graph(reader):
    put(reader, "s", P, "a", graph=G)
    put(reader, "s", P, "a", graph=G2)
    degree(reader, "s", graph=G)
    degree(reader, "s", graph=G2)
    reader.classMethodValue("Graph.KG.Eraser", "EraseAll")
    for store in ("out", "in", "gen", "degc"):
        assert not exists(reader, "^KG", store), f'^KG("{store}") survived EraseAll'


def test_remove_source_and_remove_node_bump_or_clear_the_generation(reader):
    put(reader, "s", P, "a")
    put(reader, "s", P, "b")
    put(reader, "t", P, "s")
    assert degree(reader, "s") == 2 and degree(reader, "t") == 1
    reader.classMethodValue("Graph.KG.Adjacency", "RemoveSource", G, "s")
    assert not exists(reader, "^KG", "out", G, "s")
    assert not exists(reader, "^KG", "in", G, "a", P, "s"), "the in mirror goes with the out entry"
    assert degree(reader, "s") == 0 == recount(reader, "s")
    assert degree(reader, "t") == 1, "an inbound edge stays with RemoveSource"
    reader.classMethodValue("Graph.KG.Adjacency", "RemoveNode", G, "s")
    assert degree(reader, "t") == 0 == recount(reader, "t")


# --- the reconciler -----------------------------------------------------------------------------------


def _cache_drift(native):
    import json

    report = json.loads(native.classMethodValue("Graph.KG.GraphVerify", "VerifyGraph", G))
    return [d for d in report["drift"] if d["store"] == '^KG("degc")']


def test_verify_graph_flags_a_wrong_current_entry_and_ignores_a_stale_one(reader):
    import iris

    put(reader, "s", P, "a")
    put(reader, "s", P, "b")
    assert degree(reader, "s") == 2
    assert _cache_drift(reader) == []

    wrong = iris.IRISList()
    wrong.add(gen(reader, "s"))
    wrong.add(99)
    reader.set(bytes(wrong.getBuffer()), "^KG", "degc", G, "s", ALL)
    drift = _cache_drift(reader)
    assert drift and drift[0]["kind"] == "count" and drift[0]["count"] == 1

    put(reader, "s", P, "c")  # the generation moves: the same entry is now merely stale
    assert _cache_drift(reader) == []
    assert degree(reader, "s") == 3


# --- BuildKG leaves the cache warm ----------------------------------------------------------------------


def test_buildkg_leaves_the_cache_warm(target, reader):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(target)
    nodes = [f"w248_{i}" for i in range(6)]
    for n in nodes:
        engine.create_node(n, graph=None)
        engine.create_node(n, graph=G2)
    for s, p, o in [(0, P, 1), (0, P, 2), (0, Q, 2), (1, P, 2), (3, P, 4)]:
        engine.create_edge(nodes[s], p, nodes[o], graph=None)
    for s, p, o in [(0, P, 1), (2, Q, 3)]:
        engine.create_edge(nodes[s], p, nodes[o], graph=G2)
    try:
        reader.classMethodVoid("Graph.KG.Traversal", "BuildKG")
        # BuildKG moves a Native API process into USER (spec 236 R3); use a fresh handle.
        conn, native = _connect(target)
        try:
            assert str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) == SCRATCH
            for graph in (0, G2):
                src = ""
                seen = 0
                while True:
                    src = native.nextSubscript(False, "^KG", "out", graph, src)
                    if src is None:
                        break
                    seen += 1
                    want_all = recount(native, src, graph=graph)
                    cached = entry(native, src, ALL, graph)
                    assert cached == (gen(native, src, graph), want_all), (graph, src, cached, want_all)
                    for pred in _subs(native, "^KG", "out", graph, src):
                        assert entry(native, src, pred, graph) == (
                            gen(native, src, graph),
                            recount(native, src, pred, graph),
                        ), (graph, src, pred)
                assert seen > 0
            assert not exists(native, "^KG", "deg") and not exists(native, "^KG", "degp")
            # A warm read is an answer from the entry: it does not need the writer lock or a walk.
            assert int(native.classMethodValue("Graph.KG.Adj", "Degree", 0, nodes[0])) == 3
            assert int(native.classMethodValue("Graph.KG.Adj", "Degree", 0, nodes[0], P)) == 2
            # The rebuild lock was released: from another job, a cold read can probe and store.
            native.kill("^KG", "degc", 0, nodes[0])
            assert int(native.classMethodValue("Graph.KG.Adj", "Degree", 0, nodes[0])) == 3
            assert entry(native, nodes[0], ALL, 0) == (gen(native, nodes[0], 0), 3), "the probe failed: a lock leaked"
        finally:
            conn.close()
    finally:
        cur = target.cursor()
        try:
            cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'w248_%'")
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'w248_%'")
        finally:
            cur.close()


def test_warm_all_fills_every_source_of_a_graph_that_exists_only_as_globals(reader, writer):
    """Adj.WarmAll(graph): one entry per source for the all-predicate key and each predicate."""
    srcs = [f"wa{i}" for i in range(8)]
    for i, s in enumerate(srcs):
        for j in range(i % 4 + 1):
            put(reader, s, P if j % 2 == 0 else Q, f"t{j}")
    for s in srcs:
        assert entry(reader, s) is None, "nothing cached yet"
    filled = int(reader.classMethodValue("Graph.KG.Adj", "WarmAll", G))
    assert filled == len(srcs)
    for s in srcs:
        assert entry(reader, s) == (gen(reader, s), recount(reader, s)), s
        for pred in _subs(reader, "^KG", "out", G, s):
            assert entry(reader, s, pred) == (gen(reader, s), recount(reader, s, pred)), (s, pred)
        assert degree(reader, s) == recount(reader, s)
    # While a writer holds the graph's lock nothing is stored: the lock is per graph, so every
    # source is skipped, none is cached dirty.
    writer.tStart()
    put(writer, "wa0", P, "dirty")
    reader.kill("^KG", "degc", G)
    assert int(reader.classMethodValue("Graph.KG.Adj", "WarmAll", G)) == 0
    assert all(entry(reader, s) is None for s in srcs)
    writer.tRollback()
    assert degree(reader, "wa0") == recount(reader, "wa0")


def test_a_failed_buildkg_leaves_the_adjacency_and_releases_the_rebuild_lock(target, reader, writer):
    """A row BuildKG cannot process (graph_id '0' is not a legal subscript, ADR-0003).

    Spec 252's adjacency index refuses that row at INSERT (-400), so it goes in with
    %NOINDEX, the way a pre-252 load left it; BuildKG's %BuildIndices still meets it.
    """
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(target)
    try:
        engine.create_node("f248_s", graph=None)
        engine.create_node("f248_o", graph=None)
        engine.create_edge("f248_s", P, "f248_o", graph=None)
        cur = target.cursor()
        try:
            for node_id in ("f248_ps", "f248_po"):
                cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, '0')", [node_id])
            cur.execute(
                "INSERT %NOINDEX INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) "
                "VALUES ('f248_ps', 'POISONS', 'f248_po', '0')"
            )
            target.commit()
        finally:
            cur.close()
        before = degree(reader, "f248_s", graph=0)
        assert before == 1
        status = str(reader.classMethodValue("Graph.KG.Traversal", "BuildKG"))
        assert status != "1", "the poison row no longer fails BuildKG; this test proves nothing"
        assert exists(writer, "^KG", "out", 0, "f248_s", P, "f248_o"), "a failed rebuild destroyed the adjacency"
        assert degree(writer, "f248_s", graph=0) == 1
        writer.kill("^KG", "degc", 0, "f248_s")
        assert degree(writer, "f248_s", graph=0) == 1
        assert entry(writer, "f248_s", ALL, 0) is not None, "the rebuild lock leaked: the probe failed"
    finally:
        cur = target.cursor()
        try:
            # Out the way it went in: the delete callback refuses graph '0' too, and the row is in no index
            # but the extent bitmap, which the failed %BuildIndices entered it in; rebuilt once it is gone.
            cur.execute("DELETE %NOINDEX FROM Graph_KG.rdf_edges WHERE %NOINDEX graph_id = '0' AND %NOINDEX s = 'f248_ps'")
            cur.execute('BUILD INDEX FOR TABLE Graph_KG.rdf_edges INDEX "%%DDLBEIndex"')
            cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'f248_%'")
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'f248_%'")
            target.commit()
        finally:
            cur.close()
        reader.kill("^KG", "out", 0, "f248_s")


def test_backfilldegp_warms_the_cache(reader):
    put(reader, "s", P, "a")
    put(reader, "s", Q, "b")
    reader.kill("^KG", "degc", G)
    reader.classMethodValue("Graph.KG.Traversal", "BackfillDegp")
    assert entry(reader, "s") == (gen(reader, "s"), 2)
    assert entry(reader, "s", P) == (gen(reader, "s"), 1)


# --- Remove and the merged ^NKG ----------------------------------------------------------------------------


def test_removing_a_named_graph_edge_keeps_the_merged_nkg_entry_the_default_graph_holds(reader):
    reader.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    reader.classMethodVoid("Graph.KG.TraversalBuild", "InitNKGSkeleton")
    s, p, o = "n248_s", "n248_p", "n248_o"
    try:
        put(reader, s, p, o, graph=0)
        put(reader, s, p, o, graph=G)
        s_idx = reader.classMethodValue("Graph.KG.GraphIndex", "GetNodeIdx", s)
        o_idx = reader.classMethodValue("Graph.KG.GraphIndex", "GetNodeIdx", o)
        p_idx = int(reader.classMethodValue("Graph.KG.GraphIndex", "GetLabelIdx", p))
        nkg_edge = ("^NKG", -1, int(s_idx), -(p_idx + 1), int(o_idx))
        assert exists(reader, *nkg_edge)
        remove(reader, s, p, o, graph=G)
        assert exists(reader, *nkg_edge), "the default graph still holds the triple; ^NKG is merged"
        remove(reader, s, p, o, graph=0)
        assert not exists(reader, *nkg_edge)
    finally:
        reader.kill("^KG", "out", 0, s)
        reader.kill("^KG", "in", 0, o)
        reader.kill("^KG", "gen", 0, s)
        reader.kill("^KG", "degc", 0, s)
        reader.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")


# --- the randomized sequence ----------------------------------------------------------------------------------

NODES = ["r0", "r1", "r2", "r3"]
TARGETS = [f"o{i}" for i in range(6)]
PREDS = ["p0", "p1"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_randomized_sequence_with_rollbacks_agrees_with_a_recount_after_every_step(reader, seed):
    rng = random.Random(seed)
    bad = []
    for step in range(120):
        s, o, p = rng.choice(NODES), rng.choice(TARGETS), rng.choice(PREDS)
        op = rng.randrange(7)
        if op < 2:
            put(reader, s, p, o)
        elif op == 2:
            remove(reader, s, p, o)
        elif op in (3, 4):
            reader.tStart()
            put(reader, s, p, o)
            if op == 4:
                degree(reader, s)  # a read inside the writer's own transaction
            reader.tRollback()
        elif op == 5:
            reader.tStart()
            remove(reader, s, p, o)
            degree(reader, s)
            reader.tRollback()
        else:
            reader.tStart()
            put(reader, s, p, o)
            reader.tCommit()
        for n in NODES:
            if degree(reader, n) != recount(reader, n) or degree(reader, n, "p0") != recount(reader, n, "p0"):
                bad.append((step, op, n))
    assert not bad, f"cache disagreed with a recount at (step, op, node): {bad[:5]}"
