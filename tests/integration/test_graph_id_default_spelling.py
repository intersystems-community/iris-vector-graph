"""Every edge in the default graph spells it `''`, whoever wrote the row.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_graph_id_default_spelling.py

`graph_id` arrived on `rdf_edges` as `VARCHAR(256) NULL` with no default, while a
fresh install creates it `NOT NULL DEFAULT ''`. Upgraded databases therefore hold
both spellings of the default graph at once, and which one a row gets depends on
which writer produced it: `create_edge` passes `''`, and every INSERT that omits
the column writes NULL.

Two spellings mean every reader needs both, forever, and the ones that only had
one were wrong: `list_graphs` returned `''` as a named graph, `delete_edge`
deleted the half that matched, and `materialize_inference` inferred over the NULL
half only — so nothing `create_edge` wrote was ever visible to it.

Source-level companion: tests/unit/test_graph_id_tightening.py.
"""

from __future__ import annotations

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

RDFS_SUBCLASSOF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _tighten(conn) -> dict:
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        return GraphSchema.tighten_graph_id_column(cursor)
    finally:
        cursor.close()


def _graph_id(conn, s: str):
    """The stored graph_id of one edge, distinguishing NULL from ''."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT graph_id, CASE WHEN graph_id IS NULL THEN 1 ELSE 0 END "
            "FROM Graph_KG.rdf_edges WHERE s = ?",
            [s],
        )
        row = cursor.fetchone()
        assert row is not None, f"no edge row was written for {s!r}"
        return None if int(row[1]) == 1 else str(row[0])
    finally:
        cursor.close()


def _seed_null_spelled(conn, s: str, p: str, o: str) -> None:
    """One pre-migration row, or a skip if this database cannot hold one.

    A fresh install declares `graph_id NOT NULL DEFAULT ''`, so the second spelling
    is impossible there and nothing about repairing it can be observed. Skipping says
    that; passing would claim a repair was verified when no NULL row ever existed.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id) VALUES (?, ?, ?, NULL)",
            [s, p, o],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        pytest.skip("graph_id already rejects NULL here; no second spelling to repair")
    finally:
        cursor.close()

    if _graph_id(conn, s) is not None:
        pytest.skip("graph_id coerced NULL to '' here; no second spelling to repair")


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def test_the_migration_repairs_a_null_spelled_row(engine, iris_connection):
    s = f"gid_repair_{_suffix()}"
    _seed_null_spelled(iris_connection, s, "KNOWS", "gid_o")

    result = _tighten(iris_connection)

    assert _graph_id(iris_connection, s) == "", (
        "a NULL-spelled row survived the migration, so the default graph still has "
        "two spellings"
    )
    assert result["rows_repaired"] >= 1


def test_no_null_spelled_edge_survives_the_migration(engine, iris_connection):
    _seed_null_spelled(iris_connection, f"gid_sweep_{_suffix()}", "KNOWS", "gid_o")

    _tighten(iris_connection)

    cursor = iris_connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id IS NULL")
        assert int(cursor.fetchone()[0]) == 0
    finally:
        cursor.close()


def test_running_the_migration_twice_changes_nothing(engine, iris_connection):
    _tighten(iris_connection)
    second = _tighten(iris_connection)

    assert second["rows_repaired"] == 0, (
        "the second run found rows to repair, so something is still writing the "
        "NULL spelling"
    )


# ---------------------------------------------------------------------------
# The writers
# ---------------------------------------------------------------------------


def test_create_edge_writes_the_default_graph_spelling(engine, iris_connection):
    """The spelling every other writer has to agree with."""
    s = f"gid_create_{_suffix()}"
    engine.create_edge(s, "KNOWS", "gid_o")

    assert _graph_id(iris_connection, s) == ""


def test_bulk_create_edges_writes_the_default_graph_spelling(engine, iris_connection):
    s = f"gid_bulk_{_suffix()}"
    engine.bulk_create_edges(
        [{"source_id": s, "predicate": "KNOWS", "target_id": "gid_o"}],
        disable_indexes=False,
        auto_sync=False,
    )

    assert _graph_id(iris_connection, s) == "", (
        "the bulk loader wrote the NULL spelling, so a default-graph reader sees "
        "bulk-loaded edges only if it happens to check both"
    )


def test_bulk_ingest_edges_writes_the_default_graph_spelling(engine, iris_connection):
    s = f"gid_ingest_{_suffix()}"
    engine.bulk_ingest_edges([{"s": s, "p": "KNOWS", "o": "gid_o"}], auto_sync=False)

    assert _graph_id(iris_connection, s) == ""


def test_the_store_writes_the_default_graph_spelling(engine, iris_connection):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    s = f"gid_store_{_suffix()}"
    store = IRISGraphStore(iris_connection)
    store.write_edges([{"source": s, "predicate": "KNOWS", "target": "gid_o"}])

    assert _graph_id(iris_connection, s) == ""


# ---------------------------------------------------------------------------
# The reader that only had one spelling
# ---------------------------------------------------------------------------


def test_default_graph_inference_sees_rows_written_by_create_edge(
    engine, iris_connection
):
    """`materialize_inference` filtered the default graph on `graph_id IS NULL`.

    Every edge `create_edge` has ever written is spelled `''`, so the default-graph
    inference pass read an empty table and reported success having inferred
    nothing.
    """
    a, b, c = (f"inf_{x}_{_suffix()}" for x in "abc")
    engine.create_edge(a, RDFS_SUBCLASSOF, b)
    engine.create_edge(b, RDFS_SUBCLASSOF, c)

    engine.materialize_inference(rules="rdfs")

    cursor = iris_connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?",
            [a, RDFS_SUBCLASSOF, c],
        )
        assert int(cursor.fetchone()[0]) == 1, (
            "the transitive subClassOf edge was not inferred, so the default-graph "
            "filter is not matching the rows create_edge wrote"
        )
    finally:
        cursor.close()
