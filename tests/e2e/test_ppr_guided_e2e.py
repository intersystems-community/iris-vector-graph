"""E2E tests for kg_PPR_GUIDED_SUBGRAPH against live IRIS."""
import os
import time
import uuid

import pytest

from iris_vector_graph.models import PprGuidedSubgraphData

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS=true",
)

PREFIX = f"PPRG_{uuid.uuid4().hex[:6]}"


def _build_chain(cursor, conn, n=100):
    nodes = [f"{PREFIX}:N{i}" for i in range(n)]
    for nid in nodes:
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    for i in range(n - 1):
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'NEXT', ?)",
            [nodes[i], nodes[i + 1]],
        )
    conn.commit()
    return nodes


def _build_kg(conn):
    from iris_vector_graph.schema import _call_classmethod
    _call_classmethod(conn, "Graph.KG.Traversal", "BuildKG")


def _cleanup(cursor, conn):
    p = f"{PREFIX}:%"
    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
    conn.commit()


class TestPprGuidedSubgraphE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        _cleanup(self.cursor, self.conn)
        self.nodes = _build_chain(self.cursor, self.conn, n=100)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn)

    def _ops(self):
        from iris_vector_graph.operators import IRISGraphOperators
        return IRISGraphOperators(self.conn)

    def test_returns_ppr_guided_subgraph_data(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=20)
        assert isinstance(result, PprGuidedSubgraphData)

    def test_nodes_capped_by_top_k(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=10)
        assert result.nodes_after_pruning <= 10

    def test_seed_in_results(self):
        ops = self._ops()
        seed = self.nodes[0]
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[seed], top_k=20)
        assert seed in result.nodes

    def test_ppr_scores_sorted_descending(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=20)
        scores = [s for _, s in result.ppr_scores]
        assert scores == sorted(scores, reverse=True)

    def test_edges_have_src_dst_type(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=20)
        if result.edges:
            e = result.edges[0]
            assert "src" in e
            assert "dst" in e
            assert "type" in e

    def test_empty_seeds(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[])
        assert result.nodes == []
        assert result.ppr_scores == []

    def test_performance_under_200ms(self):
        ops = self._ops()
        t0 = time.monotonic()
        ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=50, max_hops=5)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2, f"Took {elapsed:.3f}s, expected <200ms"

    def test_nodes_before_ge_nodes_after(self):
        ops = self._ops()
        result = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=[self.nodes[0]], top_k=20)
        assert result.nodes_before_pruning >= result.nodes_after_pruning
