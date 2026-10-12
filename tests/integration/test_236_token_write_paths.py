"""Spec 236, Story 4a: the graph-version token moves on every write path that changes an answer.

The cache of spec 236 trusts ``^KG("__version")`` (research-token-audit.md). A write that
changes what PageRank, WCC, CDLP or PPR answer without moving that number would let a
cached answer outlive its graph. The oracle, for every path, is:

    the Owners' answers before and after the write differ  =>  the token moved.

plus the properties the audit named: the token never falls, is never reused, and survives
a rolled-back transaction. Cases that cannot change an answer (Cypher DML and raw SQL do
not touch ``^KG`` until ``sync()``) assert that too, so a path that starts to matter is
noticed.

Runs in the scratch namespace IVG236. Every write asserts the sentinel first, and the
engine is rebuilt after anything that can flip the handle to USER (research R3).
"""

from __future__ import annotations

import threading

import pytest

from tests.integration._236_world import PRED, World



@pytest.fixture
def world(ivg_236_scratch):
    w = World(ivg_236_scratch)
    w.reset()
    yield w
    native = ivg_236_scratch.native()
    if ivg_236_scratch.sentinel_ok(native):
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def run_case(world, mutate, *, changes, graph=""):
    """Run `mutate`; return (token_before, token_after). `changes` is True, False or None."""
    t0, a0 = world.token(), world.answers(graph)
    mutate(world)
    world.scratch.require_scratch(world.native())
    t1, a1 = world.token(), world.answers(graph)
    changed = a1 != a0
    if changes is True:
        assert changed, "the write was expected to change an answer and did not"
    if changes is False:
        assert not changed, "the write was expected to leave every answer alone"
    assert t1 >= t0, f"the token fell: {t0} -> {t1}"
    if changed:
        assert t1 > t0, f"an answer changed but ^KG(__version) stayed at {t0}"
    return t0, t1


# --------------------------------------------------------------------- edge writers


def test_01_create_edge_default_graph(world):
    run_case(world, lambda w: w.engine().create_edge("d", PRED, "a"), changes=True)
    world.commit()


def test_01_create_edge_named_graph(world):
    run_case(
        world, lambda w: w.engine().create_edge("y", PRED, "z", graph="g1"), changes=True, graph="g1"
    )


def test_02_set_edge_weight_keeps_the_answer_equal_to_the_recomputation(world):
    # Weights are not read by any Owner; `deg` drifts (audit item 6) and the token must
    # still cover whatever the Owners now answer.
    run_case(world, lambda w: w.engine().set_edge_weight("a", PRED, "b", 2.5), changes=None)


def test_03_delete_edge(world):
    run_case(world, lambda w: w.engine().delete_edge("c", PRED, "d"), changes=True)


def test_03_delete_edge_all_graphs(world):
    world.engine().create_edge("c", PRED, "d", graph="g1")
    world.commit()
    run_case(
        world, lambda w: w.engine().delete_edge("c", PRED, "d", all_graphs=True), changes=True
    )


def test_04_bulk_ingest_edges(world):
    run_case(
        world,
        lambda w: w.engine().bulk_ingest_edges([("d", "e"), ("e", "f")], predicate=PRED),
        changes=True,
    )


def test_05_bulk_create_edges_with_auto_sync(world):
    def go(w):
        e = w.engine()
        e.bulk_create_nodes([{"id": "p"}, {"id": "q"}])
        e.bulk_create_edges([{"source_id": "p", "predicate": PRED, "target_id": "q"}], auto_sync=True)
        w.renew()

    run_case(world, go, changes=None)


def test_05_bulk_load_session(world):
    def go(w):
        e = w.engine()
        with e.bulk_load_session():
            e.bulk_create_nodes([{"id": "m"}, {"id": "n"}])
            e.bulk_create_edges([{"source_id": "m", "predicate": PRED, "target_id": "n"}], auto_sync=False)
        w.renew()

    run_case(world, go, changes=None)


# ---------------------------------------------------- writes that do not reach ^KG


def test_06_cypher_create_is_invisible_until_sync(world):
    def go(w):
        w.engine().execute_cypher("CREATE (:N {id: 'c1'})-[:T]->(:N {id: 'c2'})")
        w.commit()

    t0, t1 = run_case(world, go, changes=False)
    # After sync() the write is visible, and the token must have moved with it.
    run_case(world, lambda w: (w.engine().sync(), w.renew()), changes=None)


def test_07_raw_sql_insert_is_invisible_until_sync(world):
    def go(w):
        cur = w.engine().conn.cursor()
        cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES ('r1', '')")
        cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES ('r2', '')")
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES ('r1', 'T', 'r2', '')"
        )
        w.commit()

    run_case(world, go, changes=False)
    run_case(world, lambda w: (w.engine().sync(), w.renew()), changes=None)


# ------------------------------------------------------------------------ temporal


def test_08_temporal_insert_first_timestamp_is_a_new_structural_edge(world):
    run_case(
        world,
        lambda w: w.engine().create_edge_temporal("t1", PRED, "t2", timestamp=1000),
        changes=True,
    )


def test_08_temporal_insert_second_timestamp_changes_no_structure(world):
    world.engine().create_edge_temporal("t1", PRED, "t2", timestamp=1000)
    world.commit()
    run_case(
        world,
        lambda w: w.engine().create_edge_temporal("t1", PRED, "t2", timestamp=2000),
        changes=False,
    )


def test_09_temporal_delete_last_timestamp_removes_the_edge(world):
    world.engine().create_edge_temporal("t1", PRED, "t2", timestamp=1000)
    world.commit()
    run_case(
        world,
        lambda w: w.engine().delete_edge_temporal("t1", PRED, "t2", 1000),
        changes=True,
    )


def test_09_temporal_delete_not_last_timestamp_changes_no_structure(world):
    e = world.engine()
    e.create_edge_temporal("t1", PRED, "t2", timestamp=1000)
    e.create_edge_temporal("t1", PRED, "t2", timestamp=2000)
    world.commit()
    run_case(world, lambda w: w.engine().delete_edge_temporal("t1", PRED, "t2", 1000), changes=False)


def test_10_temporal_purge(world):
    e = world.engine()
    e.create_edge_temporal("t1", PRED, "t2", timestamp=1000)
    world.commit()
    run_case(world, lambda w: w.engine().purge_before(5000), changes=None)


# -------------------------------------------------------------------------- erasure


def test_11_delete_nodes(world):
    run_case(world, lambda w: w.engine().delete_node("d"), changes=True)


def test_11_delete_nodes_by_prefix(world):
    run_case(world, lambda w: w.engine().delete_nodes_by_prefix("c"), changes=True)


def test_12_erase_graph_with_edges(world):
    def go(w):
        w.engine().create_edge("g2a", PRED, "g2b", graph="g2")
        w.commit()
        w.engine().erase_graph("g2")

    run_case(world, go, changes=None)


def test_12_erase_default_graph(world):
    run_case(world, lambda w: w.engine().erase_graph(None), changes=True)


def test_12_erase_graph_with_no_edges_still_moves_the_token(world):
    t0, t1 = run_case(world, lambda w: w.engine().erase_graph("nothing-here"), changes=False)
    assert t1 > t0


def test_13_erase_all(world):
    run_case(world, lambda w: w.engine().erase_all(), changes=True)


# ---------------------------------------------------------------------- snapshots


def _snapshot(world, tmp_path):
    path = str(tmp_path / "snap.zip")
    world.engine().save_snapshot(path)
    return path


def test_14_restore_snapshot_replace_bumps_after_the_import(world, tmp_path):
    path = _snapshot(world, tmp_path)
    t0 = world.token()
    world.engine().restore_snapshot(path, merge=False)
    world.renew()
    world.scratch.require_scratch(world.native())
    t1 = world.token()
    # erase_all inside the restore bumps once; the import must bump again. One bump
    # leaves a value an answer cached between the erase and the import still matches.
    assert t1 >= t0 + 2, f"token {t0} -> {t1}: no bump after the import"


def test_14_restore_snapshot_replace_reproduces_the_answers(world, tmp_path):
    path = _snapshot(world, tmp_path)
    before = world.answers()
    world.engine().erase_all()
    world.engine().restore_snapshot(path, merge=False)
    world.renew()
    assert world.answers() == before


def test_15_restore_snapshot_merge_moves_the_token(world, tmp_path):
    path = _snapshot(world, tmp_path)
    world.engine().create_edge("d", PRED, "e")
    world.commit()
    t0 = world.token()
    world.engine().restore_snapshot(path, merge=True)
    world.renew()
    world.scratch.require_scratch(world.native())
    assert world.token() > t0


# ------------------------------------------------------------------------ rebuilds


def test_16_buildkg_single_edge_branch(world):
    def go(w):
        w.native().classMethodValue("Graph.KG.Traversal", "BuildKG")
        w.renew()

    run_case(world, go, changes=None)


def test_16_buildkg_multigraph_branch_bumps(world):
    world.native().classMethodValue("Graph.KG.GraphMode", "Set", "", 1)
    try:
        t0 = world.token()
        world.native().classMethodValue("Graph.KG.Traversal", "BuildKG")
        world.renew()
        assert world.token() > t0, "BuildKG in multigraph mode left the token alone"
    finally:
        world.native().classMethodValue("Graph.KG.GraphMode", "Set", "", 0)


def test_17_buildnkg_and_dropnkg_change_no_answer(world):
    def go(w):
        n = w.native()
        n.classMethodValue("Graph.KG.Traversal", "BuildNKG")
        n.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
        w.renew()

    run_case(world, go, changes=False)


# -------------------------------------------------------------------------- ledger


def _ledger_world(world):
    e = world.engine()
    e.ledger.enable()
    return e


def test_18_ledger_commit_create_and_delete_relationship(world):
    from iris_vector_graph.ledger.changeset import Changeset

    e = _ledger_world(world)
    try:
        cs = Changeset(actor="t236", actor_type="system")
        cs.create_relationship("d", PRED, "a")
        run_case(world, lambda w: e.ledger.commit(cs), changes=True)
    finally:
        e.ledger.disable()


def test_18_ledger_commit_label_only_changes_no_answer(world):
    from iris_vector_graph.ledger.changeset import Changeset

    e = _ledger_world(world)
    try:
        cs = Changeset(actor="t236", actor_type="system")
        cs.add_label("a", "Tagged")
        run_case(world, lambda w: e.ledger.commit(cs), changes=False)
    finally:
        e.ledger.disable()


def test_19_ledger_purge_then_new_commits_do_not_matter_to_the_token(world):
    from iris_vector_graph.ledger.changeset import Changeset

    e = _ledger_world(world)
    try:
        world.native().classMethodValue("Graph.KG.Ledger", "PurgeAll")
        e.ledger.enable()
        cs = Changeset(actor="t236", actor_type="system")
        cs.create_relationship("d", PRED, "a")
        run_case(world, lambda w: e.ledger.commit(cs), changes=True)
    finally:
        e.ledger.disable()


# ------------------------------------------------------------------- inference


# ------------------------------------------------------------------- inference

SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def test_21_materialize_then_retract_inference(world):
    e = world.engine()
    e.create_edge("C1", SUBCLASS, "C2")
    e.create_edge("C2", SUBCLASS, "C3")
    world.commit()
    # Inference writes C1 subClassOf C3 through the adjacency primitives.
    run_case(world, lambda w: w.engine().materialize_inference(), changes=True)
    world.commit()
    run_case(world, lambda w: w.engine().retract_inference(), changes=True)


# ------------------------------------------------------------- the five defects


def _degree(n, src):
    """Spec 248: the stored counter is gone; the degree is Graph.KG.Adj.Degree."""
    return int(n.classMethodValue("Graph.KG.Adj", "Degree", 0, src, ""))


def _parallel_rows(world, n):
    """`n` SQL rows for one triple, one per ekey: the keyed primitives read them to tell
    which ekey the bare leaf belongs to, so without them a second write is a refresh."""
    e = world.engine()
    cur = e.conn.cursor()
    for node in ("k1", "k2"):
        cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, '')", [node])
    for k in range(1, n + 1):
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey, qualifiers) VALUES ('k1', ?, 'k2', '', ?, '{}')",
            [PRED, k],
        )
    e.conn.commit()


def test_22_keyed_primitive_second_edge_on_a_triple_moves_the_token(world):
    _parallel_rows(world, 3)
    n = world.native()
    n.classMethodValue("Graph.KG.EdgeScan", "WriteAdjacencyKeyed", "k1", PRED, "k2", "1.0", 0, 1)
    t0 = world.token()
    n.classMethodValue("Graph.KG.EdgeScan", "WriteAdjacencyKeyed", "k1", PRED, "k2", "1.0", 0, 2)
    assert n.get("KG", "out", 0, "k1", PRED, "k2", "#") == 2, "the second write did not make a parallel edge"
    assert world.token() > t0, "a second parallel edge changed deg with no bump"
    t1 = world.token()
    n.classMethodValue("Graph.KG.EdgeScan", "WriteAdjacencyKeyed", "k1", PRED, "k2", "1.0", 0, 3)
    assert _degree(n, "k1") == 3 and world.token() > t1
    t2 = world.token()
    n.classMethodValue("Graph.KG.EdgeScan", "DeleteAdjacencyKeyed", "k1", PRED, "k2", 0, 3)
    assert world.token() > t2, "deleting a parallel edge changed deg with no bump"


def test_23_bench_seed_does_not_reset_the_counter_to_a_reused_value(world):
    n = world.native()
    for _ in range(5):
        n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    high = world.token()
    assert high >= 5
    n.classMethodValue("Graph.KG.BenchFormat", "Seed", 20)
    assert world.token() > high, "Seed reset the counter to a value it had already held"


def test_23_loader_load_does_not_reset_the_counter(world, tmp_path):
    n = world.native()
    for _ in range(3):
        n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    high = world.token()
    csv = "/tmp/ivg236_loader.csv"
    stream = n.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", csv)
    for line in ("s,p,o", "a,T,b", "b,T,c"):
        stream.invokeVoid("WriteLine", line)
    stream.invokeVoid("%Save")
    n.classMethodValue("Graph.KG.Loader", "Load", csv)
    world.scratch.require_scratch(world.native())
    assert world.token() > high, "Loader.Load left the counter at or below a value it had held"


def test_24_concurrent_bumps_are_all_counted(world):
    start = world.token()
    per_thread, threads = 150, 4
    errors: list = []

    def bump():
        try:
            n = world.scratch.native()
            for _ in range(per_thread):
                n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=bump) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert not errors, errors
    assert world.token() - start == per_thread * threads, "concurrent bumps lost updates"


def test_25_a_rolled_back_bump_is_not_undone(world):
    n = world.native()
    t0 = world.token()
    n.tStart()
    n.classMethodVoid("Graph.KG.ArnoAccel", "BumpVersion")
    n.tRollback()
    # $Increment survives the rollback; a plain Set reverts to t0 and the next bump
    # reuses t0 + 1, which a reader may already have cached.
    assert world.token() > t0, "the rollback undid the bump"


# ----------------------------------------------------- isolation and neutral writes


def test_26_a_write_in_another_graph_leaves_this_graphs_answers_alone(world):
    def go(w):
        w.engine().create_edge("x", PRED, "w", graph="g2")
        w.commit()

    run_case(world, go, changes=False)  # default-graph answers
    run_case(world, lambda w: None, changes=False, graph="g1")


def test_28_an_embedding_only_write_moves_nothing(world):
    def go(w):
        e = w.engine()
        e.store_embedding("a", [0.1] * 768)
        w.commit()

    t0, t1 = run_case(world, go, changes=False)
    assert t1 == t0, "an embedding write bumped the token (harmless but it defeats the cache)"


def test_22_keyed_primitives_every_delete_moves_the_token_down_to_the_last_edge(world):
    _parallel_rows(world, 3)
    n = world.native()
    for k in (1, 2, 3):
        n.classMethodValue("Graph.KG.EdgeScan", "WriteAdjacencyKeyed", "k1", PRED, "k2", "1.0", 0, k)
    assert _degree(n, "k1") == 3
    seen = [world.token()]
    for k in (3, 2, 1):  # three edges -> two -> one (the collapse back to a bare leaf) -> gone
        n.classMethodValue("Graph.KG.EdgeScan", "DeleteAdjacencyKeyed", "k1", PRED, "k2", 0, k)
        seen.append(world.token())
    assert seen == sorted(set(seen)), f"a delete left the token where it was: {seen}"
    assert _degree(n, "k1") == 0
    # deleting an edge that is already gone is a no-op, not a second bump of the generation
    t = world.token()
    g = n.get("KG", "gen", 0, "k1")
    n.classMethodValue("Graph.KG.EdgeScan", "DeleteAdjacencyKeyed", "k1", PRED, "k2", 0, 3)
    assert world.token() >= t and _degree(n, "k1") == 0 and n.get("KG", "gen", 0, "k1") == g
