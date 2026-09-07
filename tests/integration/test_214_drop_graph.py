"""Spec 214 — drop_graph extended to delete nodes integration tests (T008, live container)."""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


class TestDropGraph214:
    def test_drop_graph_removes_nodes_in_named_graph(self, eng, iris_connection):
        eng.create_node("dg_a", graph="g_drop")
        eng.create_node("dg_b", graph="g_drop")
        count = eng.drop_graph("g_drop")
        assert count >= 2, f"Expected >=2 deleted, got {count}"
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = 'g_drop'")
            remaining = cur.fetchone()[0]
        finally:
            cur.close()
        assert remaining == 0, f"{remaining} g_drop nodes still in nodes table"

    def test_drop_graph_removes_edges_in_named_graph(self, eng, iris_connection):
        eng.create_node("src", graph="g_drop2")
        eng.create_node("dst", graph="g_drop2")
        eng.create_edge("src", "R", "dst", graph="g_drop2")
        eng.drop_graph("g_drop2")
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = 'g_drop2'")
            edge_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = 'g_drop2'")
            node_count = cur.fetchone()[0]
        finally:
            cur.close()
        assert edge_count == 0
        assert node_count == 0

    def test_drop_graph_leaves_default_graph_untouched(self, eng, iris_connection):
        eng.create_node("default_safe")
        eng.create_node("named_drop", graph="g_drop3")
        eng.drop_graph("g_drop3")
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = 'default_safe'")
            count = cur.fetchone()[0]
        finally:
            cur.close()
        assert count == 1, "Default-graph node deleted by drop_graph"

    def test_drop_graph_nonexistent_returns_zero(self, eng):
        result = eng.drop_graph("nonexistent_graph_xyz")
        assert result == 0
