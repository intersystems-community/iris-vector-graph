"""`erase_graph` — one transaction removing a graph's content from every store.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_erase_graph.py

`drop_graph` deleted the SQL rows and said so in its own comment:
`# BYPASS: SQL rows deleted without touching ^KG/^NKG; flag stale.` The adjacency
and the whole temporal index outlived the graph, still answering traversals and
window queries for content that no longer had a row. It then set `_nkg_dirty` and
returned a count that looked like success.

The Eraser removes content from every store `Graph.KG.GraphStores` declares,
inside one transaction it owns in ObjectScript, because the Native API `Kill` and
the SQL `DELETE` ride the same connection and `$TLevel` is invisible from Python
(ADR-0004). Erasure either completes or leaves the graph untouched.

These tests assert through the stores rather than through the returned count: a
count is what `drop_graph` already got right.
"""

from __future__ import annotations

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "erase_acme"
OTHER = "erase_globex"

# A bucket is 300s wide (TemporalIndex.BUCKET) and starts on a multiple of 300.
TS = 1_700_000_100


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _kg(engine, *subs):
    """Read one ^KG node, or "" when undefined."""
    v = engine._iris_obj().classMethodValue(
        "Graph.KG.Meta", "GetKG", *[str(s) for s in subs]
    )
    return "" if v is None else str(v)


def _sql_count(engine, table, graph):
    cursor = engine.conn.cursor()
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
        [graph],
    )
    return int(cursor.fetchone()[0])


# _seed writes two Graph_KG.rdf_edges rows per graph: create_edge writes one, and
# create_edge_temporal writes its own alongside the temporal globals.
SEEDED_EDGE_ROWS = 2


def _seed(engine, graph):
    """One structural edge, one temporal edge at the same coordinates, one node.

    Both are written through the maintained paths, so every store the Eraser has
    to reach is populated the way a real caller would populate it.
    """
    engine.create_node(f"n_{graph}", labels=["Person"], properties={"k": "v"})
    engine.create_edge("a", "KNOWS", "b", graph=graph)
    engine.create_edge_temporal("a", "SAW", "b", timestamp=TS, graph=graph)


# ---------------------------------------------------------------------------
# The stores a graph's content lives in
# ---------------------------------------------------------------------------


def test_erase_removes_the_sql_rows(engine):
    _seed(engine, GRAPH)
    assert _sql_count(engine, "Graph_KG.rdf_edges", GRAPH) == SEEDED_EDGE_ROWS

    engine.erase_graph(GRAPH)

    assert _sql_count(engine, "Graph_KG.rdf_edges", GRAPH) == 0


def test_erase_removes_the_structural_adjacency(engine):
    """The store `drop_graph` left behind. This is the whole point of the Eraser."""
    _seed(engine, GRAPH)
    assert _kg(engine, "out", GRAPH, "a", "KNOWS", "b") != ""

    engine.erase_graph(GRAPH)

    assert _kg(engine, "out", GRAPH, "a", "KNOWS", "b") == "", (
        "^KG(\"out\") still answers traversals for an erased graph"
    )
    assert _kg(engine, "in", GRAPH, "b", "KNOWS", "a") == ""


def test_erase_removes_the_adjacency_counters(engine):
    """A surviving counter claims the node is still in the graph."""
    _seed(engine, GRAPH)
    assert _kg(engine, "deg", GRAPH, "a") != ""

    engine.erase_graph(GRAPH)

    assert _kg(engine, "deg", GRAPH, "a") == ""
    assert _kg(engine, "degp", GRAPH, "a", "KNOWS") == ""


def test_erase_removes_the_temporal_index(engine):
    """All five temporal stores, not just the two `tout`/`tin` trees.

    Partitioning only `tout` and `tin` is the subtler version of the same bug:
    `bucket` still reports the source as active and `tagg` still merges its
    weights into every window query that overlaps the bucket.
    """
    _seed(engine, GRAPH)
    bucket = TS // 300
    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") != ""

    engine.erase_graph(GRAPH)

    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") == ""
    assert _kg(engine, "tin", GRAPH, TS, "b", "SAW", "a") == ""
    assert _kg(engine, "bucket", GRAPH, bucket, "a") == ""
    assert _kg(engine, "tagg", GRAPH, bucket, "a", "SAW", "count") == ""


def test_erase_removes_the_nodes_and_their_labels_and_props(engine):
    """Labels and props are keyed by node id alone, so they follow the node.

    `Graph_KG.rdf_labels` and `rdf_props` have no graph column — the inventory
    records that as a hole in the schema. What the Eraser can do is remove the
    rows belonging to the nodes it is removing, which is what `drop_graph`
    already did and what must not regress.
    """
    _seed(engine, GRAPH)
    node = f"n_{GRAPH}"
    cursor = engine.conn.cursor()
    cursor.execute("UPDATE Graph_KG.nodes SET graph_id = ? WHERE node_id = ?", [GRAPH, node])
    engine.conn.commit()

    engine.erase_graph(GRAPH)

    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ?", [node])
    assert int(cursor.fetchone()[0]) == 0
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ?", [node])
    assert int(cursor.fetchone()[0]) == 0
    assert _sql_count(engine, "Graph_KG.nodes", GRAPH) == 0


# ---------------------------------------------------------------------------
# Isolation — the reason the graph key is the first subscript
# ---------------------------------------------------------------------------


def test_erasing_one_graph_leaves_another_untouched(engine):
    """Identical coordinates in two graphs. Erase one, the other survives whole."""
    _seed(engine, GRAPH)
    _seed(engine, OTHER)

    engine.erase_graph(GRAPH)

    assert _kg(engine, "out", OTHER, "a", "KNOWS", "b") != ""
    assert _kg(engine, "deg", OTHER, "a") != ""
    assert _kg(engine, "tout", OTHER, TS, "a", "SAW", "b") != ""
    assert _kg(engine, "bucket", OTHER, TS // 300, "a") != ""
    assert _sql_count(engine, "Graph_KG.rdf_edges", OTHER) == SEEDED_EDGE_ROWS


def test_erasing_the_default_graph_reaches_both_of_its_spellings(engine):
    """On an upgraded database the default graph has two spellings in one table.

    `create_edge` writes '' explicitly; before `tighten_graph_id_column` ran, any
    INSERT that omitted the column left NULL. A predicate matching one spelling
    erases half the default graph and returns a count that looks like success.

    A database created fresh declares the column `NOT NULL DEFAULT ''`, so the NULL
    spelling cannot be produced there at all and only the '' half is exercised.
    """
    engine.create_edge("d1", "KNOWS", "d2")
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) "
            "VALUES ('d3', 'KNOWS', 'd4', NULL)"
        )
        engine.conn.commit()
    except Exception:
        engine.conn.rollback()
        pytest.skip("graph_id already rejects NULL here; the second spelling cannot exist")
    cursor.execute(
        "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id IS NULL AND s = 'd3'"
    )
    if int(cursor.fetchone()[0]) != 1:
        pytest.skip("graph_id already rejects NULL here; the second spelling cannot exist")

    engine.erase_graph("")

    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s IN ('d1', 'd3')")
    assert int(cursor.fetchone()[0]) == 0, (
        "the NULL-spelled half of the default graph survived the erase"
    )


def test_erasing_the_default_graph_leaves_named_graphs_alone(engine):
    """`ForIndex('')` is the integer 0, which is a key — not a wildcard."""
    engine.create_edge("d1", "KNOWS", "d2")
    _seed(engine, GRAPH)

    engine.erase_graph("")

    assert _kg(engine, "out", GRAPH, "a", "KNOWS", "b") != ""
    assert _sql_count(engine, "Graph_KG.rdf_edges", GRAPH) == SEEDED_EDGE_ROWS


# ---------------------------------------------------------------------------
# The contract at the interface
# ---------------------------------------------------------------------------


def test_erase_leaves_no_drift_for_the_oracle_to_find(engine):
    """`verify_graph` is the independent check: erase is not self-reported.

    Phase 1 built the oracle precisely so this claim could be tested by something
    other than the code making it.
    """
    _seed(engine, GRAPH)

    engine.erase_graph(GRAPH)
    report = engine.verify_graph(GRAPH)

    assert report["ok"] == 1, f"erase left drift: {json.dumps(report['drift'])}"
    assert report["counts"]["outEdges"] == 0
    assert report["counts"]["degNodes"] == 0


def test_erasing_a_graph_with_no_content_returns_zero(engine):
    """A returned 0 means nothing matched, and is not an error (ADR-0004)."""
    assert engine.erase_graph("erase_never_existed") == 0


def test_a_rejected_graph_name_raises_before_anything_is_deleted(engine):
    """`ForIndex` rejects "0" because IRIS canonicalizes it onto the default graph.

    Validation lives in `Graph.KG.GraphKey`, so the rejection has to survive the
    trip through the Eraser rather than being re-implemented at the facade.
    """
    engine.create_edge("d1", "KNOWS", "d2")

    with pytest.raises(Exception):
        engine.erase_graph("0")

    assert _kg(engine, "out", 0, "d1", "KNOWS", "d2") != "", (
        "the default graph was erased by a call that should have been rejected"
    )


def test_drop_graph_is_the_eraser_now(engine):
    """The old name survives until 4.0.0 (grilling Q16) but not the old behaviour.

    Keeping the name is a compatibility decision; keeping the bypass would mean
    two deletion paths again, which is what the Eraser exists to end.
    """
    _seed(engine, GRAPH)

    engine.drop_graph(GRAPH)

    assert _kg(engine, "out", GRAPH, "a", "KNOWS", "b") == ""
    assert _kg(engine, "tout", GRAPH, TS, "a", "SAW", "b") == ""


# ---------------------------------------------------------------------------
# erase_all — the unscoped stores can be emptied even though they cannot be split
# ---------------------------------------------------------------------------


def test_erase_all_empties_every_graph(engine):
    _seed(engine, GRAPH)
    _seed(engine, OTHER)
    engine.create_edge("d1", "KNOWS", "d2")

    engine.erase_all()

    for graph in (GRAPH, OTHER, ""):
        assert _sql_count(engine, "Graph_KG.rdf_edges", graph) == 0
    assert _kg(engine, "out", GRAPH, "a", "KNOWS", "b") == ""
    assert _kg(engine, "out", OTHER, "a", "KNOWS", "b") == ""
    assert _kg(engine, "out", 0, "d1", "KNOWS", "d2") == ""


def test_erase_all_empties_the_stores_that_cannot_be_erased_per_graph(engine):
    """The inventory's `graphScoped: 0` entries.

    `rdf_labels`, `rdf_props` and `rdf_reifications` are keyed by node id alone,
    so no per-graph erase can attribute a row to one graph. "All" has no
    attribution problem, which is why one inventory serves both operations.
    """
    _seed(engine, GRAPH)
    cursor = engine.conn.cursor()

    engine.erase_all()

    for table in ("Graph_KG.rdf_labels", "Graph_KG.rdf_props", "Graph_KG.rdf_reifications"):
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        assert int(cursor.fetchone()[0]) == 0, f"{table} survived erase_all"


def test_erase_all_leaves_the_interning_index_unbuilt_rather_than_stale(engine):
    """`^NKG` ignores `graph_id`, so its ids cannot be attributed to one graph.

    An absent `^NKG` is a state every reader already handles — `WriteAdjacency`
    checks `$Data(^NKG("$meta","nodeCount"))` before interning, and the fast
    paths fall back. A `^NKG` still holding erased edges is not: the BFS fast
    path would traverse them.
    """
    _seed(engine, GRAPH)
    engine.sync()
    iris_obj = engine._iris_obj()
    assert iris_obj.get("^NKG", "$meta", "nodeCount") is not None, "fixture did not build ^NKG"

    engine.erase_all()

    assert iris_obj.get("^NKG", "$meta", "nodeCount") is None, (
        "^NKG survived erase_all still holding the erased graph's interned edges"
    )
