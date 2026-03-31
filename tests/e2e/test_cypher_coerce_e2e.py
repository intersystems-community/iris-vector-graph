"""E2E tests for Cypher CAST coercion functions against live IRIS."""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"CAST_{uuid.uuid4().hex[:6]}"


class TestCastCoercionE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.cursor.execute(f"INSERT INTO Graph_KG.nodes (node_id) VALUES ('{PREFIX}:G1')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('{PREFIX}:G1', 'Gene')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('{PREFIX}:G1', 'chromosome', '7')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('{PREFIX}:G1', 'confidence', '0.92')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('{PREFIX}:G1', 'active', 'True')")
        iris_connection.commit()
        yield
        p = f"{PREFIX}%"
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        iris_connection.commit()

    def _engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        return IRISGraphEngine(self.conn)

    def test_to_integer_filters_correctly(self):
        """T011"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH (n:Gene) WHERE toInteger(n.chromosome) = 7 AND n.id STARTS WITH '{PREFIX}' RETURN n.id"
        )
        assert len(result["rows"]) >= 1

    def test_to_float_comparison(self):
        """T012"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH (n:Gene) WHERE toFloat(n.confidence) > 0.9 AND n.id STARTS WITH '{PREFIX}' RETURN n.id"
        )
        assert len(result["rows"]) >= 1

    def test_to_boolean_case_insensitive(self):
        """T012b"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH (n:Gene) WHERE toBoolean(n.active) = 1 AND n.id STARTS WITH '{PREFIX}' RETURN n.id"
        )
        assert len(result["rows"]) >= 1

    def test_count_distinct(self):
        """T009"""
        self.cursor.execute(f"INSERT INTO Graph_KG.nodes (node_id) VALUES ('{PREFIX}:G2')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('{PREFIX}:G2', 'Gene')")
        self.cursor.execute(f"INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('{PREFIX}:G2', 'chromosome', '7')")
        self.conn.commit()
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH (n:Gene) WHERE n.id STARTS WITH '{PREFIX}' RETURN COUNT(DISTINCT n.chromosome) AS cnt"
        )
        assert len(result["rows"]) == 1
        assert int(result["rows"][0][0]) == 1
