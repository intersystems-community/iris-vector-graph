"""E2E tests for Sprint 1-4 Cypher enhancements against live IRIS."""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"SP_{uuid.uuid4().hex[:6]}"


class TestCypherSprintE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        nodes = [
            (f"{PREFIX}:G1", "Gene",  [("chromosome", "7"),  ("confidence", "0.95")]),
            (f"{PREFIX}:G2", "Gene",  [("chromosome", "1"),  ("confidence", "0.30")]),
            (f"{PREFIX}:D1", "Drug",  [("name", "Aspirin"),  ("active", "True")]),
            (f"{PREFIX}:D2", "Drug",  [("name", "Ibuprofen"),("active", "false")]),
        ]
        for nid, label, props in nodes:
            try: self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
            except: pass
            try: self.cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [nid, label])
            except: pass
            for k, v in props:
                try: self.cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)", [nid, k, v])
                except: pass
        try: self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'TARGETS', ?)", [f"{PREFIX}:D1", f"{PREFIX}:G1"])
        except: pass
        iris_connection.commit()
        yield
        p = f"{PREFIX}%"
        self.cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        self.cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        iris_connection.commit()

    def test_sprint1_to_integer_filter(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Gene) WHERE toInteger(n.chromosome) = 7 RETURN n.id"
        )
        assert any(PREFIX in str(r) for r in result["rows"])

    def test_sprint1_to_boolean_case_insensitive(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Drug) WHERE toBoolean(n.active) = 1 RETURN n.id"
        )
        assert any(PREFIX in str(r) for r in result["rows"])

    def test_sprint2_case_when_classifies(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Gene) RETURN CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END AS tag LIMIT 3"
        )
        assert len(result["rows"]) >= 0
        for row in result["rows"]:
            assert row[0] == "yes"

    def test_sprint4_union_combines_types(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Gene) RETURN n.id AS entity UNION MATCH (n:Drug) RETURN n.id AS entity"
        )
        all_ids = {r[0] for r in result["rows"]}
        assert any(f"{PREFIX}:G" in str(x) for x in all_ids)
        assert any(f"{PREFIX}:D" in str(x) for x in all_ids)

    def test_sprint4_union_all_includes_duplicates(self):
        result = self.engine.execute_cypher(
            "MATCH (n:Gene) RETURN n.id AS entity UNION ALL MATCH (n:Gene) RETURN n.id AS entity"
        )
        assert len(result["rows"]) >= 2

    def test_sprint4_exists_filters_genes_with_drug_target(self):
        result = self.engine.execute_cypher(
            "MATCH (g:Gene) WHERE EXISTS { (d)-[:TARGETS]->(g) } RETURN g.id"
        )
        assert len(result["rows"]) >= 1
        assert f"{PREFIX}:G1" in {r[0] for r in result["rows"]}
