"""E2E tests for named path bindings against live IRIS."""
import json
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"NP_{uuid.uuid4().hex[:6]}"


def _setup_chain(cursor, conn, n=4):
    nodes = [f"{PREFIX}:N{i}" for i in range(n)]
    preds = ["KNOWS", "LIKES", "WORKS_AT"]
    for nid in nodes:
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    for i in range(n - 1):
        p = preds[i % len(preds)]
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
            [nodes[i], p, nodes[i + 1]],
        )
    cursor.execute(
        "INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, 'name', 'Alice')",
        [nodes[0]],
    )
    conn.commit()
    return nodes, preds


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


class TestNamedPathsE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        _cleanup(self.cursor, self.conn)
        self.nodes, self.preds = _setup_chain(self.cursor, self.conn, n=4)
        yield
        _cleanup(self.cursor, self.conn)

    def _engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        return IRISGraphEngine(self.conn)

    def test_return_p_1hop(self):
        """T023"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r]->(b) WHERE a.id = '{self.nodes[0]}' RETURN p"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        p_val = row["p"]
        if isinstance(p_val, str):
            p_val = json.loads(p_val)
        assert "nodes" in p_val
        assert "rels" in p_val
        assert len(p_val["nodes"]) == 2
        assert len(p_val["rels"]) == 1

    def test_return_p_2hop(self):
        """T024"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r1]->(b)-[r2]->(c) WHERE a.id = '{self.nodes[0]}' RETURN p"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        p_val = row["p"]
        if isinstance(p_val, str):
            p_val = json.loads(p_val)
        assert len(p_val["nodes"]) == 3
        assert len(p_val["rels"]) == 2

    def test_return_p_3hop(self):
        """T024a"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r1]->(b)-[r2]->(c)-[r3]->(d) WHERE a.id = '{self.nodes[0]}' RETURN p"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        p_val = row["p"]
        if isinstance(p_val, str):
            p_val = json.loads(p_val)
        assert len(p_val["nodes"]) == 4
        assert len(p_val["rels"]) == 3

    def test_length_p(self):
        """T025"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r]->(b) WHERE a.id = '{self.nodes[0]}' RETURN length(p) AS hops"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        assert str(row["hops"]) == "1"

    def test_nodes_p(self):
        """T026"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r]->(b) WHERE a.id = '{self.nodes[0]}' RETURN nodes(p) AS ns"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        ns = row["ns"]
        if isinstance(ns, str):
            ns = json.loads(ns)
        assert isinstance(ns, list)
        assert self.nodes[0] in ns

    def test_relationships_p(self):
        """T027"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r]->(b) WHERE a.id = '{self.nodes[0]}' RETURN relationships(p) AS rs"
        )
        assert len(result["rows"]) >= 1
        row = _row_dict(result, 0)
        rs = row["rs"]
        if isinstance(rs, str):
            rs = json.loads(rs)
        assert isinstance(rs, list)
        assert len(rs) == 1

    def test_named_path_with_where_filter(self):
        """T028"""
        engine = self._engine()
        result = engine.execute_cypher(
            f"MATCH p = (a)-[r]->(b) WHERE a.name = 'Alice' RETURN nodes(p) AS ns"
        )
        assert len(result["rows"]) >= 1
        for i in range(len(result["rows"])):
            row = _row_dict(result, i)
            ns = row["ns"]
            if isinstance(ns, str):
                ns = json.loads(ns)
            assert self.nodes[0] in ns

    def test_no_match_returns_empty(self):
        """T029"""
        engine = self._engine()
        result = engine.execute_cypher(
            "MATCH p = (a:NonExistentLabel)-[r]->(b) RETURN p"
        )
        assert len(result["rows"]) == 0
