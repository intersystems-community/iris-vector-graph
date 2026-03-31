"""Integration tests for rdf_reifications SQL layer (Principle IV)."""
import os
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


class TestReificationSQLIntegration:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        try:
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('INT:test_reif_node')")
            self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('INT:test_reif_node', 'test_pred', 'INT:test_reif_node')")
            iris_connection.commit()
            self.cursor.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'INT:test_reif_node' AND p = 'test_pred'")
            self.edge_id = self.cursor.fetchone()[0]
        except Exception:
            iris_connection.commit()
            self.cursor.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'INT:test_reif_node' AND p = 'test_pred'")
            row = self.cursor.fetchone()
            self.edge_id = row[0] if row else None
        yield
        self.cursor.execute("DELETE FROM Graph_KG.rdf_reifications WHERE reifier_id LIKE 'INT:%'")
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'INT:%'")
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'INT:%'")
        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'INT:%' OR o_id LIKE 'INT:%'")
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'INT:%'")
        iris_connection.commit()

    def test_insert_and_select_junction_row(self):
        """T019"""
        if self.edge_id is None:
            pytest.skip("Test edge not found")
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('INT:reif_test1')")
        self.cursor.execute("INSERT INTO Graph_KG.rdf_reifications (reifier_id, edge_id) VALUES ('INT:reif_test1', ?)", [self.edge_id])
        self.conn.commit()
        self.cursor.execute("SELECT reifier_id, edge_id FROM Graph_KG.rdf_reifications WHERE reifier_id = 'INT:reif_test1'")
        row = self.cursor.fetchone()
        assert row is not None
        assert row[0] == "INT:reif_test1"
        assert row[1] == self.edge_id

    def test_get_reifications_sql_join(self):
        """T020"""
        if self.edge_id is None:
            pytest.skip("Test edge not found")
        self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('INT:reif_test2')")
        self.cursor.execute("INSERT INTO Graph_KG.rdf_reifications (reifier_id, edge_id) VALUES ('INT:reif_test2', ?)", [self.edge_id])
        self.cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('INT:reif_test2', 'confidence', '0.92')")
        self.conn.commit()
        self.cursor.execute(
            "SELECT r.reifier_id, p.\"key\", p.val FROM Graph_KG.rdf_reifications r "
            "LEFT JOIN Graph_KG.rdf_props p ON p.s = r.reifier_id WHERE r.edge_id = ?",
            [self.edge_id]
        )
        rows = self.cursor.fetchall()
        assert len(rows) >= 1
        assert any(row[1] == "confidence" and row[2] == "0.92" for row in rows)
