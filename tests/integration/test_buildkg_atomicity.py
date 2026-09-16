"""A failed `BuildKG` must leave the adjacency it was rebuilding intact.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_buildkg_atomicity.py

`BuildKG` killed `^KG("out"/"in"/"deg"/"degp"/"label"/"prop")` one line before it
opened `TStart`. Every traversal in the database therefore depended on the
rebuild that followed completing: a single row the rebuild could not process left
the adjacency deleted, the rows untouched, and the `Catch`'s `TRollback` with
nothing to restore. Stale adjacency is recoverable by re-running the rebuild;
destroyed adjacency looks to every reader like a graph with no edges.

The failure is injected with a row whose `graph_id` is "0", because that is a row
a real database can hold and the rebuild genuinely cannot process:
`Graph.KG.GraphKey.ForIndex` rejects "0" — IRIS canonicalizes the subscript "0"
onto the integer 0 that keys the default graph (ADR-0003) — and `graph_id` is a
plain nullable VARCHAR with nothing stopping the INSERT. Phase 4 tightens that
column, which is why the injection is a row and not a mock: it has to keep
working as a demonstration of *some* failing row after that specific row becomes
impossible.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "buildkg_atomic"


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


def _build_kg(engine):
    """Call the rebuild and return its %Status, rather than discarding it.

    `Engine.sync` goes through `classMethodVoid`, so a failed rebuild is
    invisible to it — the point here is the state afterwards, but the status is
    worth asserting on so a rebuild that stops failing does not silently turn
    this into a test of nothing.
    """
    return str(engine._iris_obj().classMethodValue("Graph.KG.Traversal", "BuildKG"))


def _poison(engine):
    """A row the rebuild cannot process, inserted the way a migration would."""
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) "
            "VALUES ('poison_s', 'POISONS', 'poison_o', '0')"
        )
        engine.conn.commit()
    finally:
        cursor.close()


def test_a_rebuild_that_fails_reports_failure(engine):
    """The premise of the test below: this row really does fail the rebuild."""
    engine.create_edge("bkg_s", "KNOWS", "bkg_o")
    _poison(engine)

    assert _build_kg(engine) != "1", (
        "the poison row no longer fails BuildKG, so the atomicity test below "
        "proves nothing — find a row that does"
    )


def test_a_failed_rebuild_leaves_the_default_graph_adjacency_intact(engine):
    engine.create_edge("bkg_s", "KNOWS", "bkg_o")
    assert _kg(engine, "out", 0, "bkg_s", "KNOWS", "bkg_o") != "", (
        "the seed itself never reached ^KG"
    )

    _poison(engine)
    _build_kg(engine)

    assert _kg(engine, "out", 0, "bkg_s", "KNOWS", "bkg_o") != "", (
        "a failed BuildKG destroyed the adjacency it was rebuilding — the rows "
        "are still there and every traversal now sees a graph with no edges"
    )
    assert _kg(engine, "in", 0, "bkg_o", "KNOWS", "bkg_s") != ""
    assert _kg(engine, "deg", 0, "bkg_s") != ""


def test_a_failed_rebuild_leaves_a_named_graph_adjacency_intact(engine):
    """The rebuild is not per-graph, so a failure anywhere reaches every graph."""
    engine.create_edge("bkg_s", "KNOWS", "bkg_o", graph=GRAPH)
    assert _kg(engine, "out", GRAPH, "bkg_s", "KNOWS", "bkg_o") != ""

    _poison(engine)
    _build_kg(engine)

    assert _kg(engine, "out", GRAPH, "bkg_s", "KNOWS", "bkg_o") != "", (
        "a failed BuildKG destroyed a named graph's adjacency"
    )
    assert _kg(engine, "degp", GRAPH, "bkg_s", "KNOWS") != ""


def test_a_successful_rebuild_still_rebuilds(engine):
    """The Kill has to keep happening — inside the transaction, not skipped.

    Without this, moving the Kill could be 'fixed' by dropping it, which would
    turn the rebuild into an append and leave deleted edges in the adjacency
    forever.
    """
    engine.create_edge("bkg_s", "KNOWS", "bkg_o", graph=GRAPH)
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM Graph_KG.rdf_edges WHERE s = 'bkg_s' AND COALESCE(graph_id,'') = ?",
            [GRAPH],
        )
        engine.conn.commit()
    finally:
        cursor.close()

    assert _build_kg(engine) == "1"

    assert _kg(engine, "out", GRAPH, "bkg_s", "KNOWS", "bkg_o") == "", (
        "the rebuild left adjacency for an edge whose row is gone, so it is "
        "appending rather than rebuilding"
    )
