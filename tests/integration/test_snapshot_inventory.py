"""The snapshot's plan equals `Graph.KG.GraphStores.Inventory()`, live.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_snapshot_inventory.py

One inventory, two consumers (grilling Q20): the Eraser walks it to remove a
graph's content, and the snapshot walks it to export that content. The Eraser was
made to read it in phase 2; until now the snapshot kept a hand-written list, and
the two disagreed about five temporal subtrees.

A unit test can check the plan against itself. Only this file can check it against
the inventory, which is the thing that actually knows what stores exist — so a store
added to `GraphStores` and forgotten in `snapshot.py` fails here.

Source-level companion: tests/unit/test_snapshot_inventory.py.
"""

from __future__ import annotations

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

TS = 1_700_000_100
GRAPH = "snap_acme"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _inventory(engine) -> list:
    raw = engine._iris_obj().classMethodValue("Graph.KG.GraphStores", "InventoryJSON")
    return json.loads(str(raw))


def _kg(engine, *subs):
    v = engine._iris_obj().classMethodValue(
        "Graph.KG.Meta", "GetKG", *[str(s) for s in subs]
    )
    return "" if v is None else str(v)


# ---------------------------------------------------------------------------
# The plan against the inventory
# ---------------------------------------------------------------------------


def test_the_snapshot_plan_accounts_for_every_store_in_the_inventory(engine):
    """A store the inventory names and the plan does not is a silent data loss."""
    from iris_vector_graph._engine import snapshot as snapshot_mod

    named = {str(entry["name"]) for entry in _inventory(engine)}
    missing = sorted(named - set(snapshot_mod.STORE_PLAN))

    assert not missing, (
        "these stores hold graph content that no snapshot exports and no plan "
        f"entry excuses: {missing}"
    )


def test_the_plan_names_no_store_the_inventory_does_not(engine):
    """Drift in the other direction: a plan entry for a store that no longer exists.

    Harmless at export time, misleading forever after — it reads as coverage.
    """
    from iris_vector_graph._engine import snapshot as snapshot_mod

    named = {str(entry["name"]) for entry in _inventory(engine)}
    stale = sorted(set(snapshot_mod.STORE_PLAN) - named)

    assert not stale, f"the plan names stores the inventory does not: {stale}"


# ---------------------------------------------------------------------------
# What the omission actually cost
# ---------------------------------------------------------------------------


def test_a_temporal_edge_survives_a_round_trip(engine, tmp_path):
    """The defect, end to end: save, erase, restore, and look for the edge.

    `GLOBALS_EXPORT` walked `^KG("out")` and `^KG("in")` only, so this edge's five
    temporal subtrees were never in the archive. The restore then reported success
    with the temporal index empty.
    """
    engine.create_edge_temporal("a", "SAW", "b", timestamp=TS, graph=GRAPH)
    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") != "", "fixture wrote nothing"

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") == ""

    engine.restore_snapshot(path)

    bucket = TS // 300
    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") != "", (
        "the temporal edge did not survive the round trip: ^KG(\"tout\") is not in "
        "the archive"
    )
    assert _kg(engine, "tin", GRAPH, TS, "b", "SAW", "a") != ""
    assert _kg(engine, "bucket", GRAPH, bucket, "a") != ""
    assert _kg(engine, "tagg", GRAPH, bucket, "a", "SAW", "count") != ""


def test_the_restored_temporal_edge_answers_a_window_query(engine, tmp_path):
    """Restoring the globals is only worth something if the reader sees them."""
    engine.create_edge_temporal("a", "SAW", "b", timestamp=TS, graph=GRAPH)

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    engine.restore_snapshot(path)

    edges = engine.get_edges_in_window(start=TS - 60, end=TS + 60, graph=GRAPH)

    assert any(
        e.get("source") == "a" and e.get("target") == "b" for e in edges
    ), f"the window query found no restored edge: {edges}"


def test_a_named_graph_survives_with_its_scope_intact(engine, tmp_path):
    """The graph key is the first subscript, so a restore that loses it is silent.

    A restore that wrote the temporal globals back under the wrong graph key would
    pass every count-based check and still leave the edge invisible to its own
    tenant.
    """
    engine.create_edge_temporal("a", "SAW", "b", timestamp=TS, graph=GRAPH)

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    engine.restore_snapshot(path)

    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") != ""
    assert _kg(engine, "tout", 0, TS, "a", "SAW", "b") == "", (
        "the restore moved a named graph's temporal edge into the default graph"
    )


def test_the_bm25_corpus_survives_a_round_trip(engine, tmp_path):
    """`Graph_KG.docs` was in no plan, so `kg_TXT` came back empty after a restore.

    Nothing caught it because `docs` was in no inventory either: the two lists
    agreed with each other and both left the corpus out. There is no engine write
    path for a document — callers INSERT — so this test writes one the same way.
    """
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)",
            [GRAPH, "snapdoc_1", "the corpus a restore used to drop"],
        )
        engine.conn.commit()
    finally:
        cursor.close()

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    engine.restore_snapshot(path)

    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "SELECT text FROM Graph_KG.docs WHERE graph_id = ? AND id = ?",
            [GRAPH, "snapdoc_1"],
        )
        row = cursor.fetchone()
        # Read the column here: the driver's DataRow is only valid while its cursor
        # is open, and touching row[0] after the close raises COMMUNICATION LINK
        # ERROR rather than reporting the value.
        text = None if row is None else row[0]
    finally:
        cursor.close()

    assert text is not None, "the document did not survive the round trip"
    assert text == "the corpus a restore used to drop"


def test_the_two_hop_counts_survive_a_round_trip(engine, tmp_path):
    """`^KG("deg2p")` is graph-scoped as of spec 230, and exported rather than rebuilt.

    Rebuilding it on restore would need Arno loaded on whichever machine is doing
    the restoring; carrying it keeps the counts consistent with the adjacency in
    the same archive.
    """
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("b", "KNOWS", "c", graph=GRAPH)
    engine.sync()

    before = _kg(engine, "deg2p", GRAPH, "a")
    if before == "":
        pytest.skip("this install does not maintain ^KG(\"deg2p\") on sync")

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    assert _kg(engine, "deg2p", GRAPH, "a") == ""
    engine.restore_snapshot(path)

    assert _kg(engine, "deg2p", GRAPH, "a") == before, (
        "the two-hop count did not survive the round trip"
    )


# ---------------------------------------------------------------------------
# The layout stamp
# ---------------------------------------------------------------------------


def test_the_snapshot_records_the_layout_it_was_taken_from(engine, tmp_path):
    from iris_vector_graph._engine import snapshot as snapshot_mod

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)

    info = engine.snapshot_info(path)

    assert info["layout"] == snapshot_mod.SNAPSHOT_LAYOUT


def test_a_snapshot_from_another_layout_is_refused(engine, tmp_path):
    """A pre-223 archive holds flat temporal globals under a graph-scoped reader.

    Restoring it would put timestamps where graph keys belong. Every window query
    then returns nothing, and the restore reported success.
    """
    import zipfile

    path = str(tmp_path / "flat.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "metadata.json",
            json.dumps({"version": "1.1", "layout": "flat", "layers": [], "tables": {}}),
        )

    with pytest.raises(ValueError, match="flat"):
        engine.restore_snapshot(path)


def test_the_verify_oracle_finds_no_drift_after_a_restore(engine, tmp_path):
    """`verify_graph` is the independent check, as it was for the Eraser.

    A restore that writes SQL rows without the adjacency, or adjacency without the
    rows, is exactly the drift the phase-1 oracle was built to see.
    """
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge_temporal("a", "SAW", "b", timestamp=TS, graph=GRAPH)

    path = str(tmp_path / "snap.zip")
    engine.save_snapshot(path)
    engine.erase_all()
    engine.restore_snapshot(path)

    report = engine.verify_graph(GRAPH)

    assert report["ok"] == 1, f"restore left drift: {json.dumps(report['drift'])}"
