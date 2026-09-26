"""E2E: booleans round-trip as 'true' / 'false' text; legacy '1' rows still match.

Requires a live IRIS container (iris_connection fixture). Set SKIP_IRIS_TESTS=true
to skip.
"""

import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBooleanTextStorageE2E:
    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()
        self.label = f"BoolE2E{uuid.uuid4().hex[:8]}"
        yield
        self.engine.execute_cypher(f"MATCH (n:{self.label}) DETACH DELETE n")

    def _cy(self, q, params=None):
        return self.engine.execute_cypher(q, params or {})

    def _rows(self, q, params=None):
        r = self._cy(q, params)
        return [list(row) for row in (r["rows"] if isinstance(r, dict) else r.rows)]

    def _stored(self, node_id, key):
        cur = self.conn.cursor()
        cur.execute(
            'SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ?', [node_id, key]
        )
        row = cur.fetchone()
        return None if row is None else row[0]

    def test_cypher_create_round_trips(self):
        L = self.label
        self._cy(f"CREATE (:{L} {{id: 'cy1', b: true, f: false}})")
        assert self._rows(
            f"MATCH (n:{L} {{id: 'cy1'}}) RETURN n.b, n.f, toString(n.b), toString(n.f)"
        ) == [[True, False, "true", "false"]]

    def test_cypher_set_stores_text(self):
        L = self.label
        self._cy(f"CREATE (:{L} {{id: 'cy2'}})")
        self._cy(f"MATCH (n:{L} {{id: 'cy2'}}) SET n.b = true, n.c = (1 > 2)")
        rows = self._rows(f"MATCH (n:{L} {{id: 'cy2'}}) RETURN n.b, toString(n.c)")
        assert rows == [[True, "false"]]

    def test_python_api_round_trips(self):
        L = self.label
        nid = f"py_{uuid.uuid4().hex[:8]}"
        self.engine.create_node(nid, labels=[L], properties={"b": True, "f": False})
        assert self._stored(nid, "b") == "true"
        node = self.engine.get_node(nid)
        props = node.get("properties", node) if isinstance(node, dict) else node
        assert props["b"] is True and props["f"] is False
        assert self._rows(f"MATCH (n:{L}) WHERE n.id = '{nid}' RETURN n.b") == [[True]]

    def test_legacy_one_still_matches_true(self):
        L = self.label
        nid = f"legacy_{uuid.uuid4().hex[:8]}"
        self.engine.create_node(nid, labels=[L], properties={"name": "legacy"})
        cur = self.conn.cursor()
        cur.execute(
            'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)', [nid, "b", "1"]
        )
        self.conn.commit()
        for q in (
            f"MATCH (n:{L}) WHERE n.b = true RETURN n.name",
            f"MATCH (n:{L}) WHERE n.b RETURN n.name",
            f"MATCH (n:{L} {{b: true}}) RETURN n.name",
            f"MATCH (n:{L}) WHERE n.b IN [true] RETURN n.name",
        ):
            assert self._rows(q) == [["legacy"]], q
        assert self._rows(f"MATCH (n:{L}) WHERE n.b = false RETURN n.name") == []
