"""E2E tests for CALL { ... } subquery clauses against live IRIS."""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"SQ_{uuid.uuid4().hex[:6]}"


def _setup_data(cursor, conn):
    nodes = {
        "drug1": f"{PREFIX}:Drug1",
        "drug2": f"{PREFIX}:Drug2",
        "prot1": f"{PREFIX}:Prot1",
        "prot2": f"{PREFIX}:Prot2",
        "partner1": f"{PREFIX}:Partner1",
        "partner2": f"{PREFIX}:Partner2",
    }
    for nid in nodes.values():
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])

    cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, 'Drug')", [nodes["drug1"]])
    cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, 'Drug')", [nodes["drug2"]])
    cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, 'Protein')", [nodes["prot1"]])
    cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, 'Protein')", [nodes["prot2"]])

    cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, 'name', 'Aspirin')", [nodes["drug1"]])
    cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, 'name', 'Ibuprofen')", [nodes["drug2"]])

    cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'INTERACTS_WITH', ?)", [nodes["prot1"], nodes["partner1"]])
    cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'INTERACTS_WITH', ?)", [nodes["prot1"], nodes["partner2"]])

    conn.commit()
    return nodes


def _cleanup(cursor, conn):
    p = f"{PREFIX}:%"
    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
    conn.commit()


def _row_dict(result, row_idx):
    cols = result["columns"]
    row = result["rows"][row_idx]
    return {cols[i]: row[i] for i in range(len(cols))}


class TestSubqueryCallE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        _cleanup(self.cursor, self.conn)
        self.nodes = _setup_data(self.cursor, self.conn)
        yield
        _cleanup(self.cursor, self.conn)

    def _engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        return IRISGraphEngine(self.conn)

    def test_independent_subquery_projection(self):
        """T025"""
        engine = self._engine()
        result = engine.execute_cypher(
            "CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name"
        )
        assert len(result["rows"]) == 2
        names = {result["rows"][i][0] for i in range(len(result["rows"]))}
        assert "Aspirin" in names
        assert "Ibuprofen" in names

    def test_independent_subquery_aggregation(self):
        """T026"""
        engine = self._engine()
        result = engine.execute_cypher(
            "CALL { MATCH (n:Drug) RETURN count(n) AS cnt } RETURN cnt"
        )
        assert len(result["rows"]) == 1
        row = _row_dict(result, 0)
        assert int(row["cnt"]) == 2

    def test_correlated_subquery_degree(self):
        """T027"""
        engine = self._engine()
        result = engine.execute_cypher(
            "MATCH (p:Protein) "
            "CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(q) RETURN count(q) AS deg } "
            "RETURN p.id, deg"
        )
        assert len(result["rows"]) >= 2
        degrees = {}
        for i in range(len(result["rows"])):
            row = _row_dict(result, i)
            degrees[row["p_id"]] = int(row["deg"])
        assert degrees[self.nodes["prot1"]] == 2
        assert degrees[self.nodes["prot2"]] == 0

    def test_independent_subquery_no_match_empty(self):
        """T028"""
        engine = self._engine()
        result = engine.execute_cypher(
            "CALL { MATCH (n:NonExistentLabel) RETURN n.name AS name } RETURN name"
        )
        assert len(result["rows"]) == 0

    def test_subquery_missing_return_raises_error(self):
        """T029"""
        engine = self._engine()
        with pytest.raises(Exception):
            engine.execute_cypher("CALL { MATCH (n) } RETURN n")
