"""E2E tests for CALL { ... } subquery clauses against live IRIS."""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"SQ_{uuid.uuid4().hex[:6]}"


def _setup_data(engine, conn):
    nodes = {
        "drug1": f"{PREFIX}:Drug1",
        "drug2": f"{PREFIX}:Drug2",
        "prot1": f"{PREFIX}:Prot1",
        "prot2": f"{PREFIX}:Prot2",
        "partner1": f"{PREFIX}:Partner1",
        "partner2": f"{PREFIX}:Partner2",
    }
    
    engine.create_node(nodes["drug1"], labels=["Drug"], properties={"name": "Aspirin"})
    engine.create_node(nodes["drug2"], labels=["Drug"], properties={"name": "Ibuprofen"})
    engine.create_node(nodes["prot1"], labels=["Protein"])
    engine.create_node(nodes["prot2"], labels=["Protein"])
    engine.create_node(nodes["partner1"])
    engine.create_node(nodes["partner2"])

    engine.create_edge(nodes["prot1"], "INTERACTS_WITH", nodes["partner1"])
    engine.create_edge(nodes["prot1"], "INTERACTS_WITH", nodes["partner2"])

    return nodes


def _cleanup(engine, conn):
    p = f"{PREFIX}%"
    cursor = conn.cursor()
    cursor.execute("SELECT node_id FROM nodes WHERE node_id LIKE ?", [p])
    node_ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    if node_ids:
        engine.bulk_delete_nodes(node_ids)


def _row_dict(result, row_idx):
    cols = result["columns"]
    row = result["rows"][row_idx]
    return {cols[i]: row[i] for i in range(len(cols))}


class TestSubqueryCallE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)
        self.conn = iris_connection
        _cleanup(self.engine, self.conn)
        self.nodes = _setup_data(self.engine, self.conn)
        yield
        _cleanup(self.engine, self.conn)

    def _engine(self):
        return self.engine

    def test_independent_subquery_projection(self):
        """T025"""
        engine = self._engine()
        result = engine.execute_cypher(
            "CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name"
        )
        assert len(result["rows"]) >= 2
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
        assert int(row["cnt"]) >= 2

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
