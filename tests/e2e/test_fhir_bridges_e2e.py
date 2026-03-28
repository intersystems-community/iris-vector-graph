"""E2E tests for FHIR-to-KG bridge against live IRIS."""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"FB_{uuid.uuid4().hex[:6]}"


class TestFhirBridgesE2E:

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
        self._setup_test_data()
        yield
        self._cleanup()

    def _setup_test_data(self):
        nodes = [f"{PREFIX}:MeSH:D011014", f"{PREFIX}:MeSH:D003924"]
        for nid in nodes:
            self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
        bridges = [
            (f"{PREFIX}_J18.9", f"{PREFIX}:MeSH:D011014", "ICD10CM", "icd10_to_mesh", 1.0, "C0032285"),
            (f"{PREFIX}_E11.9", f"{PREFIX}:MeSH:D003924", "ICD10CM", "icd10_to_mesh", 1.0, "C0011849"),
            (f"{PREFIX}_J18.9", f"{PREFIX}:MeSH:ORPHAN", "ICD10CM", "icd10_to_mesh", 1.0, "C9999999"),
        ]
        for b in bridges:
            try:
                self.cursor.execute(
                    "INSERT INTO Graph_KG.fhir_bridges (fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence, source_cui) "
                    "VALUES (?, ?, ?, ?, ?, ?)", list(b)
                )
            except Exception:
                pass
        self.conn.commit()

    def _cleanup(self):
        p = f"{PREFIX}%"
        self.cursor.execute("DELETE FROM Graph_KG.fhir_bridges WHERE fhir_code LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        self.conn.commit()

    def test_bridge_rows_persist_and_queryable(self):
        """T021"""
        self.cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.fhir_bridges WHERE fhir_code LIKE ?", [f"{PREFIX}%"]
        )
        count = self.cursor.fetchone()[0]
        assert count >= 2

    def test_idempotent_reinsert(self):
        """T022"""
        try:
            self.cursor.execute(
                "INSERT INTO Graph_KG.fhir_bridges (fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence, source_cui) "
                "VALUES (?, ?, 'ICD10CM', 'icd10_to_mesh', 1.0, 'C0032285')",
                [f"{PREFIX}_J18.9", f"{PREFIX}:MeSH:D011014"]
            )
            self.conn.commit()
        except Exception:
            self.conn.commit()
        self.cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.fhir_bridges WHERE fhir_code = ? AND kg_node_id = ?",
            [f"{PREFIX}_J18.9", f"{PREFIX}:MeSH:D011014"]
        )
        assert self.cursor.fetchone()[0] == 1

    def test_get_kg_anchors_filters_to_existing_nodes(self):
        """T023"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.conn)
        result = engine.get_kg_anchors(
            icd_codes=[f"{PREFIX}_J18.9", f"{PREFIX}_E11.9"]
        )
        assert f"{PREFIX}:MeSH:D011014" in result
        assert f"{PREFIX}:MeSH:D003924" in result
        assert f"{PREFIX}:MeSH:ORPHAN" not in result

    def test_get_kg_anchors_empty_input(self):
        """T024"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.conn)
        result = engine.get_kg_anchors(icd_codes=[])
        assert result == []

    def test_get_kg_anchors_no_mapping(self):
        """T025"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(self.conn)
        result = engine.get_kg_anchors(icd_codes=["NONEXISTENT_CODE"])
        assert result == []
