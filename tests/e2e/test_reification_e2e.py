"""E2E tests for RDF 1.2 reification against live IRIS."""
import os
import time
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"REIF_{uuid.uuid4().hex[:6]}"


class TestReificationE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        self.cursor.execute(f"INSERT INTO Graph_KG.nodes (node_id) VALUES ('{PREFIX}:A')")
        self.cursor.execute(f"INSERT INTO Graph_KG.nodes (node_id) VALUES ('{PREFIX}:B')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('{PREFIX}:A', 'treats', '{PREFIX}:B')")
        iris_connection.commit()
        self.cursor.execute(f"SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = '{PREFIX}:A' AND p = 'treats'")
        self.edge_id = self.cursor.fetchone()[0]
        yield
        p = f"{PREFIX}%"
        self.cursor.execute("DELETE FROM Graph_KG.rdf_reifications WHERE reifier_id LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        iris_connection.commit()

    def test_reify_edge_creates_node_and_junction_under_5ms(self):
        """T021"""
        t0 = time.perf_counter()
        reif_id = self.engine.reify_edge(self.edge_id, props={"confidence": "0.92"})
        elapsed = (time.perf_counter() - t0) * 1000
        assert reif_id is not None
        assert reif_id == f"reif:{self.edge_id}"
        assert elapsed < 5000, f"reify_edge took {elapsed:.0f}ms, expected <5000ms"
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [reif_id])
        assert self.cursor.fetchone()[0] == 1
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ? AND edge_id = ?", [reif_id, self.edge_id])
        assert self.cursor.fetchone()[0] == 1

    def test_multiple_reifications_on_same_edge(self):
        """T022"""
        r1 = self.engine.reify_edge(self.edge_id, reifier_id=f"{PREFIX}:reif1", props={"source": "PMID:1"})
        r2 = self.engine.reify_edge(self.edge_id, reifier_id=f"{PREFIX}:reif2", props={"source": "PMID:2"})
        assert r1 is not None
        assert r2 is not None
        reifs = self.engine.get_reifications(self.edge_id)
        reif_ids = {r["reifier_id"] for r in reifs}
        assert f"{PREFIX}:reif1" in reif_ids
        assert f"{PREFIX}:reif2" in reif_ids

    def test_get_reifications_returns_properties(self):
        """T023"""
        self.engine.reify_edge(self.edge_id, props={"confidence": "0.92", "source": "PMID:12345"})
        reifs = self.engine.get_reifications(self.edge_id)
        assert len(reifs) >= 1
        props = reifs[0]["properties"]
        assert props.get("confidence") == "0.92"
        assert props.get("source") == "PMID:12345"

    def test_delete_reification_preserves_edge(self):
        """T024"""
        reif_id = self.engine.reify_edge(self.edge_id, props={"note": "test"})
        result = self.engine.delete_reification(reif_id)
        assert result is True
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [reif_id])
        assert self.cursor.fetchone()[0] == 0
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE edge_id = ?", [self.edge_id])
        assert self.cursor.fetchone()[0] == 1

    def test_reifier_participates_in_neighbors(self):
        """T025"""
        from iris_vector_graph.operators import IRISGraphOperators
        reif_id = self.engine.reify_edge(self.edge_id, reifier_id=f"{PREFIX}:reif_walk",
                                          props={"accessPolicy": "kg_read"})
        node_data = self.engine.get_node(reif_id)
        assert node_data is not None
        assert "Reification" in node_data.get("labels", [])

    def test_delete_node_cascades_to_reification(self):
        """T017a"""
        reif_id = self.engine.reify_edge(self.edge_id, reifier_id=f"{PREFIX}:reif_cascade",
                                          props={"note": "will cascade"})
        assert reif_id is not None
        self.engine.delete_node(f"{PREFIX}:A")
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?", [reif_id])
        assert self.cursor.fetchone()[0] == 0
        self.cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [reif_id])
        assert self.cursor.fetchone()[0] == 0
