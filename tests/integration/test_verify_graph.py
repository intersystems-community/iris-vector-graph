"""`verify_graph` — the graph-scoped drift oracle, and the Phase 1 gate.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_verify_graph.py

The existing `verify_sync` cannot see the drift this project set out to fix, and
says so in its own docstring: it compares one SQL count against one `^NKG` count,
graph-blind, and only one-directionally, because `^NKG`'s `edgeCount` over-counts
(interning is append-only and ignores `graph_id`). So it flags
`sql_edges > global_edges` and stays silent on the opposite case.

The opposite case is exactly what every deletion path produces. `drop_graph`
(`_engine/nodes_edges.py:888`) carries the comment
`# BYPASS: SQL rows deleted without touching ^KG/^NKG; flag stale.` — it removes
the rows and leaves the adjacency behind. After it runs, `sql_edges` is *below*
`global_edges`, so `verify_sync` reports in-sync while `^KG` still answers
traversals for a graph that no longer exists.

`verify_graph(graph)` is additive alongside `verify_sync` (grilling Q21): scoped
to one graph, bidirectional, and reading `^KG` directly rather than `^NKG`'s
counters.

**This file is the Phase 1 gate.** It must report today's real drift on a seeded
database BEFORE any fix lands. `test_the_phase_1_gate_*` tests are therefore
expected to show drift, not the absence of it; they will keep passing after the
Eraser lands in Phase 3 because that fix removes the adjacency too, and these
tests seed the drift through the bypass path deliberately.
"""

from __future__ import annotations

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "verifygraph_acme"

# The SQL stores that hold graph content but carry no graph column. `rdf_labels`
# and `rdf_props` were here until spec 227 gave each of them a `graph_id`, and
# `kg_EdgeEmbeddings` until spec 230 gave it the same `(graph_id, model_key)`
# routing the node table already had; a hardcoded list is what let this one go
# stale, so `test_the_inventorys_sql_claims_match_the_columns` checks the whole
# inventory against INFORMATION_SCHEMA rather than restating it.
UNSCOPED = ["Graph_KG.rdf_reifications"]


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


@pytest.fixture()
def seeded(engine):
    """Three edges in one named graph, written through the maintained path."""
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("b", "KNOWS", "c", graph=GRAPH)
    engine.create_edge("c", "KNOWS", "a", graph=GRAPH)
    return engine


def _report(engine, graph: str = GRAPH) -> dict:
    report = engine.verify_graph(graph)
    # A dict keeps the report shape declared in exactly one place — the
    # ObjectScript that produces it. A Python dataclass mirroring it would be a
    # second declaration, free to drift, which is the failure this whole phase
    # exists to remove.
    assert isinstance(report, dict), f"verify_graph returned {type(report).__name__}"
    return report


def _stores_with_drift(report: dict) -> set:
    return {d["store"] for d in report.get("drift", [])}


# --- the interface ----------------------------------------------------------


def test_verify_graph_is_on_the_engine_facade(engine):
    assert hasattr(engine, "verify_graph"), (
        "verify_graph is not reachable from the engine facade"
    )


def test_verify_sync_still_exists(engine):
    """Additive, per Q21 — verify_graph does not replace the old oracle."""
    assert hasattr(engine, "verify_sync")


def test_the_report_names_the_graph_and_its_derived_key(seeded):
    report = _report(seeded)
    assert report["graph"] == GRAPH
    assert report["graphKey"] == GRAPH, (
        "graphKey must come from Graph.KG.GraphKey.ForIndex, not be re-derived"
    )


def test_the_default_graph_reports_its_key_as_zero(engine):
    report = _report(engine, "")
    assert str(report["graphKey"]) == "0", (
        "the default graph keys ^KG under the integer 0 (ADR-0001)"
    )


def test_a_graph_name_that_cannot_be_represented_is_rejected(engine):
    """`"0"` collides with the default graph's subscript (ADR-0003)."""
    with pytest.raises(Exception):
        engine.verify_graph("0")


# --- a graph written through the maintained path is clean -------------------


def test_a_freshly_seeded_graph_reports_no_drift(seeded):
    report = _report(seeded)
    assert report["drift"] == [], f"unexpected drift on a clean graph: {report['drift']}"
    assert report["ok"] == 1


def test_the_report_counts_both_sides(seeded):
    report = _report(seeded)
    assert report["counts"]["sqlEdges"] == 3
    assert report["counts"]["outEdges"] == 3
    assert report["counts"]["inEdges"] == 3


# --- adjacency that outlives its rows, reported -----------------------------
#
# These used to seed the drift by calling `drop_graph`, because `drop_graph` was
# the leak: it deleted the rows and left ^KG answering traversals for a graph
# that no longer had one. Phase 3 made `drop_graph` the Eraser, so it no longer
# produces drift and cannot demonstrate detecting any (ADR-0004).
#
# The drift is now seeded by deleting the rows with raw SQL, which is the shape
# the oracle exists to catch and the one that has not gone away: a deletion path
# that reaches the tables without reaching the globals. `map_sql_table`, a
# migration script and any hand-run DELETE all have it.


def _orphan_the_adjacency(engine, graph=GRAPH):
    """Delete a graph's rows without touching its globals.

    Deliberately raw SQL rather than any engine method: the point is to be a
    deletion path the Eraser does not own, so the oracle has something to find.
    """
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"DELETE FROM {engine._t('rdf_edges')} "
            "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            [graph],
        )
        engine.conn.commit()
    finally:
        cursor.close()


def test_adjacency_that_outlives_its_rows_is_reported(seeded):
    assert _report(seeded)["drift"] == []

    _orphan_the_adjacency(seeded)

    report = _report(seeded)
    assert report["ok"] == 0, (
        "^KG is populated for a graph with no rows and verify_graph reports it clean"
    )
    assert report["counts"]["sqlEdges"] == 0
    assert report["counts"]["outEdges"] == 3, (
        "the ^KG('out') entries should still be present — that is the drift"
    )
    orphans = [d for d in report["drift"] if d["kind"] == "orphan"]
    assert orphans, f"no orphan drift reported: {report['drift']}"
    assert sum(d["count"] for d in orphans) >= 3


def test_the_orphan_drift_spans_every_adjacency_store(seeded):
    """`out`, `in` and the degree counters all outlive the rows."""
    _orphan_the_adjacency(seeded)
    stores = _stores_with_drift(_report(seeded))
    for store in ['^KG("out")', '^KG("in")', '^KG("deg")']:
        assert store in stores, f"{store} drift not reported; got {stores}"


def test_verify_sync_cannot_see_orphaned_adjacency(seeded):
    """The reason verify_graph exists, stated as a test.

    verify_sync only flags sql_edges > global_edges. Rows deleted out from under
    ^KG produce the reverse, so the old oracle reports in-sync over a graph whose
    adjacency still answers traversals.
    """
    _orphan_the_adjacency(seeded)
    assert _report(seeded)["ok"] == 0, "verify_graph missed the drift"
    assert seeded.verify_sync().in_sync is True, (
        "verify_sync now detects deletion drift — this test documented that it "
        "could not; if that changed deliberately, update the comparison here"
    )


def test_a_raw_sql_insert_is_reported_as_missing_adjacency(seeded):
    """No functional index maintains ^KG any more, so raw SQL drifts the other way."""
    # The endpoints have to exist in *this* graph before the row can: spec 227
    # put `fk_edges_dest` on `(graph_id, o_id)`, so an edge into an unregistered
    # destination is SQLCODE -121 rather than the drift this test is after.
    seeded.create_node("x", graph=GRAPH)
    seeded.create_node("y", graph=GRAPH)
    cursor = seeded.conn.cursor()
    try:
        cursor.execute(
            f"INSERT INTO {seeded._t('rdf_edges')} (s, p, o_id, qualifiers, graph_id) "
            "VALUES (?, ?, ?, NULL, ?)",
            ["x", "KNOWS", "y", GRAPH],
        )
        seeded.conn.commit()
    finally:
        cursor.close()

    report = _report(seeded)
    assert report["ok"] == 0
    missing = [d for d in report["drift"] if d["kind"] == "missing"]
    assert missing, f"no missing-adjacency drift reported: {report['drift']}"


# --- drift in one graph must not be attributed to another -------------------


def test_a_row_with_a_null_graph_id_counts_as_the_default_graph(engine):
    """The default graph has two spellings in rdf_edges, and both must count.

    `graph_id` is nullable. `create_edge` writes `''` explicitly, but every
    INSERT that omits the column — the bulk loaders, the `map_sql_table` bridge,
    and most raw inserts in this suite — leaves NULL. An oracle matching only
    `''` reports those rows as absent, which is the one thing it must never do.
    """
    # Registered in the default graph first — `fk_edges_dest` covers
    # `(graph_id, o_id)` and IRIS checks it even when the referencing row leaves
    # `graph_id` NULL, so the omitted column this test is about only survives if
    # the destination is already a default-graph node.
    engine.create_node("n1")
    engine.create_node("n2")
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"INSERT INTO {engine._t('rdf_edges')} (s, p, o_id) VALUES ('n1', 'R', 'n2')"
        )
        engine.conn.commit()
    finally:
        cursor.close()

    assert _report(engine, "")["counts"]["sqlEdges"] == 1, (
        "a NULL graph_id row was not attributed to the default graph"
    )


def test_drift_in_one_graph_is_not_reported_against_another(seeded):
    seeded.create_edge("p", "KNOWS", "q", graph="verifygraph_other")
    _orphan_the_adjacency(seeded)

    assert _report(seeded, GRAPH)["ok"] == 0
    assert _report(seeded, "verifygraph_other")["drift"] == [], (
        "the untouched graph was reported as drifted"
    )


def test_the_default_graph_is_not_reported_against_a_named_graph(engine):
    engine.create_edge("d1", "KNOWS", "d2")  # default graph
    assert _report(engine, GRAPH)["counts"]["sqlEdges"] == 0, (
        "default-graph edges counted against a named graph"
    )


# --- the stores verification cannot check, reported rather than skipped -----


@pytest.mark.parametrize("store", UNSCOPED)
def test_the_report_names_the_stores_that_carry_no_graph_column(seeded, store):
    """Silently skipping these would hide the gap the inventory exists to state."""
    unscoped = {u["store"] for u in _report(seeded).get("unscoped", [])}
    assert store in unscoped, f"{store} not reported as unscoped; got {unscoped}"


def test_the_inventorys_sql_claims_match_the_columns(engine):
    """Every `kind == "sql"` entry's scope claim is checked against the column.

    `UNSCOPED` above is a hardcoded list, and a hardcoded list is exactly what
    went stale when spec 227 gave `rdf_labels` and `rdf_props` a `graph_id`: the
    inventory was already right and the test was wrong. This derives the claim
    from INFORMATION_SCHEMA in both directions, so a table that gains or loses
    its graph column fails here rather than passing a list nobody updated.
    """
    import json as _json

    inventory = _json.loads(
        engine._iris_obj().classMethodValue("Graph.KG.GraphStores", "InventoryJSON")
    )
    cursor = engine.conn.cursor()
    try:
        mismatches = []
        for entry in inventory:
            if entry["kind"] != "sql":
                continue
            schema, _, table = entry["name"].partition(".")
            cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND UPPER(COLUMN_NAME) = 'GRAPH_ID'",
                [schema, table],
            )
            has_column = bool(cursor.fetchone()[0])
            if has_column != bool(entry["graphScoped"]):
                mismatches.append(
                    f"{entry['name']}: graphScoped={entry['graphScoped']} "
                    f"but graph_id column present={has_column}"
                )
        assert not mismatches, "the inventory's scope claims disagree with the schema: " + (
            "; ".join(mismatches)
        )
    finally:
        cursor.close()


def test_the_unscoped_constant_still_names_every_unscoped_sql_store(engine):
    """`UNSCOPED` is the parametrize source, so it has to stay in step too."""
    import json as _json

    inventory = _json.loads(
        engine._iris_obj().classMethodValue("Graph.KG.GraphStores", "InventoryJSON")
    )
    declared = {
        e["name"] for e in inventory if e["kind"] == "sql" and not e["graphScoped"]
    }
    assert declared == set(UNSCOPED), (
        f"UNSCOPED is stale: inventory says {sorted(declared)}"
    )


def test_each_unscoped_entry_explains_itself(seeded):
    for entry in _report(seeded)["unscoped"]:
        assert entry.get("reason"), f"unscoped entry {entry['store']} carries no reason"


def test_unscoped_stores_do_not_by_themselves_fail_the_report(seeded):
    """`ok` answers "does this graph's adjacency agree with its rows?".

    The unscoped stores are a standing schema gap, not per-graph drift; folding
    them into `ok` would make every report fail forever and the signal useless.
    """
    report = _report(seeded)
    assert report["unscoped"], "expected the standing unscoped list"
    assert report["ok"] == 1
