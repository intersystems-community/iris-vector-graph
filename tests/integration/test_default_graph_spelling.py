"""The default graph must have one spelling, and deletion must find it.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_default_graph_spelling.py

`Graph_KG.rdf_edges.graph_id` is nullable, and the write paths disagree about the
default graph:

  * `create_edge` writes the empty string explicitly,
  * every INSERT that omits the column — the bulk loaders, the `map_sql_table`
    bridge, most raw inserts in this suite — leaves NULL.

So `WHERE graph_id = ''` finds one half of the default graph and silently misses
the other. `delete_edge` and `drop_graph` both use exactly that predicate, which
means deletion reports success while leaving rows behind. It is the same defect
that made `verify_graph` report `sqlEdges: 0` on a populated table.

Two rules, one per side:

  writers  always spell the default graph `''`, never NULL
  readers  always `COALESCE(graph_id, '')` so they see both spellings

The reader rule is not redundant once the writers are fixed: existing databases
already hold NULL rows, and this suite's own fixtures still create them.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "spelling_acme"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _insert_with_null_graph(engine, s, p, o):
    """A row written the way every loader that omits graph_id writes it."""
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"INSERT INTO {engine._t('rdf_edges')} (s, p, o_id) VALUES (?, ?, ?)",
            [s, p, o],
        )
        engine.conn.commit()
    finally:
        cursor.close()


def _count_rows(engine, s, p, o):
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"SELECT COUNT(*) FROM {engine._t('rdf_edges')} "
            "WHERE s = ? AND p = ? AND o_id = ?",
            [s, p, o],
        )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()


def _null_graph_rows(engine):
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"SELECT COUNT(*) FROM {engine._t('rdf_edges')} WHERE graph_id IS NULL"
        )
        return int(cursor.fetchone()[0])
    finally:
        cursor.close()


# --- readers must see both spellings ----------------------------------------


def test_delete_edge_removes_a_default_graph_row_written_as_null(engine):
    _insert_with_null_graph(engine, "n1", "R", "n2")
    assert _count_rows(engine, "n1", "R", "n2") == 1

    engine.delete_edge("n1", "R", "n2")

    assert _count_rows(engine, "n1", "R", "n2") == 0, (
        "delete_edge used `graph_id = ''` and missed the NULL-graph_id row, "
        "reporting success while the edge survived"
    )


def test_drop_graph_removes_default_graph_rows_written_as_null(engine):
    _insert_with_null_graph(engine, "n3", "R", "n4")

    engine.drop_graph("")

    assert _count_rows(engine, "n3", "R", "n4") == 0, (
        "drop_graph('') left the NULL-graph_id rows behind"
    )


def test_verify_graph_agrees_that_the_default_graph_is_empty_afterwards(engine):
    """The oracle and the eraser must agree on which rows are the default graph."""
    _insert_with_null_graph(engine, "n5", "R", "n6")
    assert engine.verify_graph("")["counts"]["sqlEdges"] == 1

    engine.drop_graph("")

    assert engine.verify_graph("")["counts"]["sqlEdges"] == 0, (
        "drop_graph and verify_graph disagree about what the default graph holds"
    )


# --- all_graphs deletion must reach the adjacency ----------------------------


def test_delete_edge_all_graphs_removes_adjacency_in_every_graph(engine):
    """`all_graphs=True` never deleted any adjacency at all.

    `graph_id` was only assigned in the `else` branch, so the `all_graphs` path
    raised `UnboundLocalError` when building the `DeleteAdjacency` argument. The
    call sat inside a `try` that logs and swallows, so every row disappeared from
    SQL and every `^KG` entry stayed — the exact drift this project is about, in
    the one path that opts into touching every graph.
    """
    engine.create_edge("a", "KNOWS", "b")  # default graph
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)

    assert engine.delete_edge("a", "KNOWS", "b", all_graphs=True) is True

    for graph in ("", GRAPH):
        report = engine.verify_graph(graph)
        assert report["counts"]["sqlEdges"] == 0, f"rows left in graph {graph!r}"
        assert report["counts"]["outEdges"] == 0, (
            f"all_graphs=True left ^KG adjacency behind in graph {graph!r}"
        )
        assert report["ok"] == 1, f"drift in graph {graph!r}: {report['drift']}"


# --- writers must use one spelling ------------------------------------------


def test_create_edge_never_writes_a_null_graph_id(engine):
    engine.create_edge("w1", "R", "w2")
    assert _null_graph_rows(engine) == 0


def test_the_bulk_ingest_path_writes_the_same_default_spelling_as_create_edge(engine):
    """Two writers, two spellings, is the defect — not either spelling itself."""
    engine._iris_obj().classMethodValue(
        "Graph.KG.EdgeScan",
        "BulkIngestEdgesSQL",
        '[{"s":"b1","p":"KNOWS","o":"b2"}]',
    )

    assert _null_graph_rows(engine) == 0, (
        "Graph.KG.EdgeScan.BulkIngestEdgesSQL omits graph_id from its INSERT, so "
        "it writes NULL where create_edge writes '' — the default graph ends up "
        "with two spellings in one table"
    )
    assert engine.verify_graph("")["counts"]["sqlEdges"] == 1, (
        "the bulk-ingested edge is not attributed to the default graph"
    )
