"""Spec 214 Phase C — graph-scoped delete_edge tests (T040, live container)."""

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


def _edge_count(iris_connection, s, p, o, graph_id):
    cur = iris_connection.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=? AND graph_id=?",
            [s, p, o, graph_id],
        )
        return cur.fetchone()[0]
    finally:
        cur.close()


class TestDeleteEdgeScoped214:
    def test_delete_edge_with_graph_removes_only_that_graph(self, eng, iris_connection):
        eng.create_node("A")
        eng.create_node("B")
        eng.create_edge("A", "R", "B", graph="umls")
        eng.create_edge("A", "R", "B", graph="go")
        eng.delete_edge("A", "R", "B", graph="umls")
        assert _edge_count(iris_connection, "A", "R", "B", "umls") == 0
        assert _edge_count(iris_connection, "A", "R", "B", "go") == 1

    def test_delete_edge_no_graph_deletes_default_only(self, eng, iris_connection):
        eng.create_node("C")
        eng.create_node("D")
        eng.create_edge("C", "R", "D")  # default graph (graph_id='')
        eng.create_edge("C", "R", "D", graph="g1")
        eng.delete_edge("C", "R", "D")  # should delete only default
        assert _edge_count(iris_connection, "C", "R", "D", "") == 0
        assert _edge_count(iris_connection, "C", "R", "D", "g1") == 1

    def test_delete_edge_all_graphs_true_removes_all(self, eng, iris_connection):
        eng.create_node("E")
        eng.create_node("F")
        eng.create_edge("E", "R", "F", graph="umls")
        eng.create_edge("E", "R", "F", graph="go")
        eng.delete_edge("E", "R", "F", all_graphs=True)
        assert _edge_count(iris_connection, "E", "R", "F", "umls") == 0
        assert _edge_count(iris_connection, "E", "R", "F", "go") == 0

    def test_delete_edge_removes_adjacency_from_correct_graph(self, eng):
        eng.create_node("G")
        eng.create_node("H")
        eng.create_edge("G", "R", "H", graph="umls")
        eng.create_edge("G", "R", "H", graph="go")
        eng.delete_edge("G", "R", "H", graph="umls")
        io = eng._iris_obj()
        umls_gone = io.get("^KG", "out", "umls", "G", "R", "H")
        go_present = io.get("^KG", "out", "go", "G", "R", "H")
        assert umls_gone is None or str(umls_gone) == "", "umls adjacency not removed"
        assert go_present is not None and str(go_present) != "", "go adjacency incorrectly removed"
