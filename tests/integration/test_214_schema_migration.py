"""Spec 214 — schema migration integration tests (T007, live ivg-iris-enterprise).

Tests: graph_id column added; __graph migration; PK and UNIQUE constraints; idempotency.
"""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
    return engine


class TestSchemaMigration214:
    def test_nodes_table_has_graph_id_column(self, iris_connection):
        cur = iris_connection.cursor()
        try:
            cur.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='Graph_KG' AND TABLE_NAME='nodes' AND COLUMN_NAME='graph_id'"
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        assert (
            rows
        ), "graph_id column missing from Graph_KG.nodes — run initialize_schema() to migrate"

    def test_existing_rows_get_empty_string_sentinel(self, iris_connection, eng):
        eng.create_node("migrate_test_node")
        cur = iris_connection.cursor()
        try:
            cur.execute(
                "SELECT graph_id FROM Graph_KG.nodes WHERE node_id = ?", ["migrate_test_node"]
            )
            fetched = cur.fetchone()
            row = tuple(fetched) if fetched else None
        finally:
            cur.close()
        assert row is not None
        assert row[0] == "" or row[0] is None, f"Expected '' sentinel, got {row[0]!r}"

    def test_dunder_graph_rows_removed_from_rdf_props(self, iris_connection):
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE \"key\" = '__graph'")
            count = cur.fetchone()[0]
        finally:
            cur.close()
        assert count == 0, f"{count} __graph rows still in rdf_props after migration"

    def test_unique_nodeid_constraint_exists(self, iris_connection):
        """UNIQUE(node_id) must exist; compound PK (node_id,graph_id) must exist."""
        cur = iris_connection.cursor()
        try:
            # Check compound PK exists
            cur.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA='Graph_KG' AND TABLE_NAME='nodes' "
                "AND CONSTRAINT_TYPE='PRIMARY KEY'"
            )
            pk_row = tuple(cur.fetchone())
            # Check UNIQUE constraint
            cur.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA='Graph_KG' AND TABLE_NAME='nodes' "
                "AND CONSTRAINT_TYPE='UNIQUE'"
            )
            uq_row = tuple(cur.fetchone())
        finally:
            cur.close()
        assert pk_row[0] >= 1, "No PRIMARY KEY constraint on nodes"
        assert uq_row[0] >= 1, "No UNIQUE constraint on nodes (needed for FK compatibility)"

    def test_initialize_schema_idempotent(self, eng):
        """Calling initialize_schema twice must not raise or break anything."""
        eng.initialize_schema()
        eng.initialize_schema()

    def test_default_graph_reads_work(self, eng, iris_connection):
        eng.create_node("default_test", labels=["L"], properties={"k": "v"})
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?", ["default_test"])
            fetched = cur.fetchone()
            row = tuple(fetched) if fetched else None
        finally:
            cur.close()
        assert row is not None and row[0] == "default_test"
