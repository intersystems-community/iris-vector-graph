"""Spec 214 Phase B — graph-aware adjacency index tests (T017, T045, live container)."""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
    return engine


def _kg(eng, *subs):
    v = eng._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", *[str(s) for s in subs])
    return "" if v is None else str(v)


def _count_edges_in_graph(iris_connection, graph_id):
    cur = iris_connection.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ?", [graph_id])
        return cur.fetchone()[0]
    finally:
        cur.close()


class TestPhaseBAdjacencyLayout:
    """T017: WriteAdjacency now uses graph subscript."""

    def test_create_edge_writes_to_graph_subscript(self, eng):
        eng.create_node("adj_a")
        eng.create_node("adj_b")
        eng.create_edge("adj_a", "R", "adj_b", graph="umls")
        # ^KG("out", "umls", 0, "adj_a", "R", "adj_b") must exist
        val = _kg(eng, "out", "umls", "adj_a", "R", "adj_b")
        assert val != "", "Named-graph edge not written to ^KG(out, umls, 0, ...)"

    def test_default_graph_edge_writes_to_empty_sentinel(self, eng):
        eng.create_node("def_a")
        eng.create_node("def_b")
        eng.create_edge("def_a", "R", "def_b")
        val = _kg(eng, "out", "0", "def_a", "R", "def_b")
        assert val != "", "Default-graph edge not written to ^KG(out, '', 0, ...)"

    def test_delete_edge_removes_graph_subscript(self, eng):
        eng.create_node("del_a")
        eng.create_node("del_b")
        eng.create_edge("del_a", "R", "del_b", graph="go")
        assert _kg(eng, "out", "go", "del_a", "R", "del_b") != ""
        eng.delete_edge("del_a", "R", "del_b", graph="go")
        assert _kg(eng, "out", "go", "del_a", "R", "del_b") == ""


class TestPhaseBGraphAwareAlgorithms:
    """T045: degree centrality and BFS scoped by graph."""

    def _build_graph(self, eng):
        for n in ("A", "B", "C"):
            eng.create_node(n)
        eng.create_edge("A", "R", "B", graph="umls")
        eng.create_edge("A", "R", "C", graph="umls")
        eng.create_edge("A", "R", "B", graph="go")
        eng.sync()  # BuildKG + BuildNKG

    def test_sync_writes_graph_subscripts(self, eng):
        self._build_graph(eng)
        umls_ab = _kg(eng, "out", "umls", "A", "R", "B")
        go_ab = _kg(eng, "out", "go", "A", "R", "B")
        assert umls_ab != "" and go_ab != "", "sync() did not populate graph subscripts"

    def test_buildkg_reads_graph_id_from_rdf_edges(self, eng, iris_connection):
        self._build_graph(eng)
        # Verify rdf_edges has graph_id populated
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = 'umls'")
            umls_count = cur.fetchone()[0]
        finally:
            cur.close()
        assert umls_count == 2, f"Expected 2 umls edges, got {umls_count}"

    def test_merged_view_traversal_returns_all_graphs(self, eng):
        self._build_graph(eng)
        rows = eng.execute_cypher("MATCH (a {node_id:'A'})-[:R]->(b) RETURN b.node_id AS id")[
            "rows"
        ]
        targets = {r[0] for r in rows}
        assert "B" in targets and "C" in targets, f"Merged-view BFS missing targets: {targets}"

    def test_drop_graph_then_sync_removes_from_index(self, eng):
        self._build_graph(eng)
        assert _kg(eng, "out", "umls", "A", "R", "B") != ""
        eng.drop_graph("umls")
        eng.sync()
        assert _kg(eng, "out", "umls", "A", "R", "B") == "", "Dropped graph still in ^KG after sync"
        # go graph unaffected
        assert _kg(eng, "out", "go", "A", "R", "B") != "", "drop_graph('umls') removed go entries"
