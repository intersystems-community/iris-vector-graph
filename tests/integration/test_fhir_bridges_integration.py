"""Integration tests for fhir_bridges SQL layer (Principle IV)."""
import os
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


class TestFhirBridgesSQLIntegration:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        try:
            self.cursor.execute("DROP TABLE IF EXISTS Graph_KG.fhir_bridges")
            self.conn.commit()
        except Exception:
            pass
        try:
            self.cursor.execute("""
                CREATE TABLE Graph_KG.fhir_bridges (
                    fhir_code VARCHAR(64) %EXACT NOT NULL,
                    kg_node_id VARCHAR(256) %EXACT NOT NULL,
                    fhir_code_system VARCHAR(128) NOT NULL DEFAULT 'ICD10CM',
                    bridge_type VARCHAR(64) NOT NULL DEFAULT 'icd10_to_mesh',
                    confidence FLOAT DEFAULT 1.0,
                    source_cui VARCHAR(16),
                    CONSTRAINT pk_bridge PRIMARY KEY (fhir_code, kg_node_id)
                )
            """)
            self.conn.commit()
        except Exception:
            pass
        yield
        self.cursor.execute("DELETE FROM Graph_KG.fhir_bridges WHERE fhir_code LIKE 'TEST_%'")
        self.conn.commit()

    def test_insert_and_select_roundtrip(self):
        """T019"""
        self.cursor.execute(
            "INSERT INTO Graph_KG.fhir_bridges (fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence, source_cui) "
            "VALUES ('TEST_J18.9', 'MeSH:D011014', 'ICD10CM', 'icd10_to_mesh', 1.0, 'C0032285')"
        )
        self.conn.commit()
        self.cursor.execute("SELECT fhir_code, kg_node_id, bridge_type, source_cui FROM Graph_KG.fhir_bridges WHERE fhir_code = 'TEST_J18.9'")
        row = self.cursor.fetchone()
        assert row is not None
        assert row[0] == "TEST_J18.9"
        assert row[1] == "MeSH:D011014"
        assert row[2] == "icd10_to_mesh"
        assert row[3] == "C0032285"

    def test_get_kg_anchors_sql_join(self):
        """T020"""
        try:
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('MeSH:TESTNODE1')")
        except Exception:
            pass
        self.conn.commit()
        try:
            self.cursor.execute(
                "INSERT INTO Graph_KG.fhir_bridges (fhir_code, kg_node_id, bridge_type) "
                "VALUES ('TEST_X99.0', 'MeSH:TESTNODE1', 'icd10_to_mesh')"
            )
        except Exception:
            pass
        try:
            self.cursor.execute(
                "INSERT INTO Graph_KG.fhir_bridges (fhir_code, kg_node_id, bridge_type) "
                "VALUES ('TEST_X99.0', 'MeSH:NONEXISTENT', 'icd10_to_mesh')"
            )
        except Exception:
            pass
        self.conn.commit()

        self.cursor.execute(
            "SELECT DISTINCT b.kg_node_id FROM Graph_KG.fhir_bridges b "
            "JOIN Graph_KG.nodes n ON n.node_id = b.kg_node_id "
            "WHERE b.fhir_code = 'TEST_X99.0' AND b.bridge_type = 'icd10_to_mesh'"
        )
        results = [r[0] for r in self.cursor.fetchall()]
        assert "MeSH:TESTNODE1" in results
        assert "MeSH:NONEXISTENT" not in results

        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = 'MeSH:TESTNODE1'")
        self.conn.commit()
