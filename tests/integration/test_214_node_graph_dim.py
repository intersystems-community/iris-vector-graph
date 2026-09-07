"""Spec 214 Phase F — backward compatibility (T060, US5, live container)."""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


class TestBackwardCompatibility:
    def test_create_node_no_graph_has_empty_graph_id(self, eng, iris_connection):
        eng.create_node("n1", labels=["L"], properties={"p": "1"})
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT graph_id FROM Graph_KG.nodes WHERE node_id='n1'")
            row = tuple(cur.fetchone())
        finally:
            cur.close()
        assert row[0] == "" or row[0] is None, f"Expected '' sentinel, got {row[0]!r}"

    def test_create_edge_no_graph_writes_default(self, eng, iris_connection):
        eng.create_node("a")
        eng.create_node("b")
        eng.create_edge("a", "R", "b")
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT s,p,o_id,graph_id FROM Graph_KG.rdf_edges WHERE s='a' AND p='R'")
            rows = cur.fetchall()
        finally:
            cur.close()
        assert len(rows) == 1
        row = tuple(rows[0])
        assert row[0] == "a" and row[1] == "R" and row[2] == "b"

    def test_cypher_match_no_use_graph_returns_default(self, eng):
        eng.create_node("c1")
        eng.create_node("c2")
        eng.create_edge("c1", "R", "c2")
        rows = eng.execute_cypher("MATCH (a {node_id:'c1'})-[:R]->(b) RETURN b.node_id AS id")[
            "rows"
        ]
        assert rows, f"Expected result from merged-view MATCH, got {rows}"
        assert rows[0][0] == "c2"

    def test_delete_edge_no_graph_deletes_default(self, eng, iris_connection):
        eng.create_node("d1")
        eng.create_node("d2")
        eng.create_edge("d1", "Q", "d2")
        eng.delete_edge("d1", "Q", "d2")
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s='d1'")
            count = cur.fetchone()[0]
        finally:
            cur.close()
        assert count == 0

    def test_initialize_schema_idempotent(self, eng):
        eng.initialize_schema()
        eng.initialize_schema()
