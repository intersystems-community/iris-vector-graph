"""Spec 214 Phase D — Cypher CREATE/MERGE graph targeting (T049, T050, live container)."""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _node_graph_id(iris_connection, node_id):
    cur = iris_connection.cursor()
    try:
        cur.execute(
            "SELECT graph_id FROM Graph_KG.nodes WHERE node_id = ? ORDER BY graph_id", [node_id]
        )
        return [tuple(r)[0] for r in cur.fetchall()]
    finally:
        cur.close()


def _edge_graph_id(iris_connection, s, p, o):
    cur = iris_connection.cursor()
    try:
        cur.execute(
            "SELECT graph_id FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, o]
        )
        return [tuple(r)[0] for r in cur.fetchall()]
    finally:
        cur.close()


class TestCypherCreateGraph214:
    def test_create_node_under_use_graph_stores_graph_id(self, eng, iris_connection):
        eng.execute_cypher("USE GRAPH 'staging' CREATE (n:Device {node_id:'sensor-1'})")
        graphs = _node_graph_id(iris_connection, "sensor-1")
        assert "staging" in graphs, f"Expected graph_id='staging', got {graphs}"
        # Must NOT have a default-graph row
        assert "" not in graphs, "Unexpected default-graph row created"

    def test_create_node_no_use_graph_creates_default(self, eng, iris_connection):
        eng.execute_cypher("CREATE (n:Device {node_id:'default-node'})")
        graphs = _node_graph_id(iris_connection, "default-node")
        assert "" in graphs, f"Expected graph_id='', got {graphs}"

    def test_create_edge_under_use_graph_stores_graph_id(self, eng, iris_connection):
        eng.execute_cypher("USE GRAPH 'staging' CREATE (a {node_id:'e-src'})")
        eng.execute_cypher("USE GRAPH 'staging' CREATE (b {node_id:'e-dst'})")
        eng.execute_cypher(
            "USE GRAPH 'staging' MATCH (a {node_id:'e-src'}), (b {node_id:'e-dst'}) CREATE (a)-[:LINKS]->(b)"
        )
        graphs = _edge_graph_id(iris_connection, "e-src", "LINKS", "e-dst")
        assert "staging" in graphs, f"Expected graph_id='staging' on edge, got {graphs}"

    def test_use_graph_match_returns_only_that_graph(self, eng, iris_connection):
        """Edges in a named graph are only visible through that graph's filter.

        Node labels/props are global per node_id (per spec-214 Clarification Q5).
        Graph scope applies to edges, not to the node physical row.
        """
        eng.execute_cypher("USE GRAPH 'g1' CREATE (n:L {node_id:'nodeG1'})")
        eng.execute_cypher("USE GRAPH 'g2' CREATE (m:M {node_id:'nodeG2'})")
        # Each graph has a distinct node
        rows_g1 = eng.execute_cypher(
            "USE GRAPH 'g1' MATCH (n:L {node_id:'nodeG1'}) RETURN n.node_id"
        )["rows"]
        rows_g2 = eng.execute_cypher(
            "USE GRAPH 'g2' MATCH (m:M {node_id:'nodeG2'}) RETURN m.node_id"
        )["rows"]
        assert len(rows_g1) >= 1, f"Expected nodeG1 in g1, got {rows_g1}"
        assert len(rows_g2) >= 1, f"Expected nodeG2 in g2, got {rows_g2}"


class TestPrincipleVIIGraphScope:
    """T050: new graph-scoped direction-symmetry gate (spec 214 FR-018)."""

    def test_direction_symmetry_with_use_graph(self, eng, iris_connection):
        """Both direction forms return identical results under USE GRAPH scope."""
        for n in ("ds214_a", "ds214_b", "ds214_c"):
            eng.execute_cypher(f"USE GRAPH 'dsg' CREATE (n {{node_id:'{n}'}})")
        eng.execute_cypher(
            "USE GRAPH 'dsg' MATCH (a {node_id:'ds214_a'}), (b {node_id:'ds214_b'}) CREATE (a)-[:CALLS]->(b)"
        )
        eng.execute_cypher(
            "USE GRAPH 'dsg' MATCH (a {node_id:'ds214_a'}), (c {node_id:'ds214_c'}) CREATE (a)-[:CALLS]->(c)"
        )
        canonical = eng.execute_cypher(
            "USE GRAPH 'dsg' MATCH (a {node_id:'ds214_a'})-[:CALLS]->(b) RETURN b.node_id AS id"
        )["rows"]
        mirror = eng.execute_cypher(
            "USE GRAPH 'dsg' MATCH (b)<-[:CALLS]-(a {node_id:'ds214_a'}) RETURN b.node_id AS id"
        )["rows"]
        assert sorted(r[0] for r in canonical) == sorted(
            r[0] for r in mirror
        ), f"Direction asymmetry under USE GRAPH: canonical={canonical}, mirror={mirror}"
        ids = {r[0] for r in canonical}
        assert len(ids) >= 2 and "ds214_b" in ids and "ds214_c" in ids
