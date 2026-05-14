"""Unit tests for operators.py — thin shim over IRISGraphEngine."""
from unittest.mock import MagicMock, patch


def _make_operators():
    from iris_vector_graph.operators import IRISGraphOperators
    conn = MagicMock()
    with patch("iris_vector_graph.engine.IRISGraphEngine") as MockEngine:
        mock_engine = MagicMock()
        MockEngine.return_value = mock_engine
        ops = IRISGraphOperators(conn)
    ops._engine = MagicMock()
    return ops, ops._engine


class TestIRISGraphOperatorsShim:

    def setup_method(self):
        self.ops, self.eng = _make_operators()

    def test_kg_knn_vec_delegates(self):
        self.eng.kg_KNN_VEC.return_value = [("n1", 0.9)]
        r = self.ops.kg_KNN_VEC([0.1, 0.2], k=5, label_filter="Gene")
        self.eng.kg_KNN_VEC.assert_called_once_with([0.1, 0.2], k=5, label_filter="Gene")
        assert r == [("n1", 0.9)]

    def test_kg_txt_delegates(self):
        self.eng.kg_TXT.return_value = []
        self.ops.kg_TXT("insulin", k=10, min_confidence=0.5)
        self.eng.kg_TXT.assert_called_once_with("insulin", k=10, min_confidence=0.5)

    def test_kg_rrf_fuse_delegates(self):
        self.eng.kg_RRF_FUSE.return_value = []
        self.ops.kg_RRF_FUSE(k=10, query_text="test", query_vector="[0.1]")
        self.eng.kg_RRF_FUSE.assert_called_once()

    def test_kg_graph_path_delegates(self):
        self.eng.kg_GRAPH_PATH.return_value = []
        self.ops.kg_GRAPH_PATH("n1", "P1", "P2", max_hops=3)
        self.eng.kg_GRAPH_PATH.assert_called_once_with("n1", "P1", "P2", max_hops=3)

    def test_kg_graph_walk_delegates(self):
        self.eng.kg_GRAPH_WALK.return_value = []
        self.ops.kg_GRAPH_WALK("n1", max_depth=2, edge_types=["E"], max_results=50)
        self.eng.kg_GRAPH_WALK.assert_called_once_with("n1", max_depth=2, edge_types=["E"], max_results=50)

    def test_kg_graph_walk_tvf_delegates(self):
        self.eng.kg_GRAPH_WALK_TVF.return_value = []
        self.ops.kg_GRAPH_WALK_TVF("n1")
        self.eng.kg_GRAPH_WALK_TVF.assert_called_once()

    def test_kg_neighborhood_expansion_delegates(self):
        self.eng.kg_NEIGHBORHOOD_EXPANSION.return_value = {}
        self.ops.kg_NEIGHBORHOOD_EXPANSION("n1", depth=2)
        self.eng.kg_NEIGHBORHOOD_EXPANSION.assert_called_once_with("n1", depth=2)

    def test_kg_vector_graph_search_uses_k_final(self):
        self.eng.kg_VECTOR_GRAPH_SEARCH.return_value = []
        self.ops.kg_VECTOR_GRAPH_SEARCH([0.1], k=10, k_final=20)
        call_kwargs = self.eng.kg_VECTOR_GRAPH_SEARCH.call_args.kwargs
        assert call_kwargs["k"] == 20

    def test_kg_vector_graph_search_uses_k_vector_fallback(self):
        self.eng.kg_VECTOR_GRAPH_SEARCH.return_value = []
        self.ops.kg_VECTOR_GRAPH_SEARCH([0.1], k=10, k_vector=15)
        call_kwargs = self.eng.kg_VECTOR_GRAPH_SEARCH.call_args.kwargs
        assert call_kwargs["k"] == 15

    def test_kg_vector_graph_search_uses_k_default(self):
        self.eng.kg_VECTOR_GRAPH_SEARCH.return_value = []
        self.ops.kg_VECTOR_GRAPH_SEARCH([0.1], k=10)
        call_kwargs = self.eng.kg_VECTOR_GRAPH_SEARCH.call_args.kwargs
        assert call_kwargs["k"] == 10

    def test_kg_pagerank_delegates(self):
        self.eng.kg_PAGERANK.return_value = []
        self.ops.kg_PAGERANK(seed_entities=["n1"], damping=0.85)
        self.eng.kg_PAGERANK.assert_called_once()

    def test_kg_wcc_delegates(self):
        self.eng.kg_WCC.return_value = []
        self.ops.kg_WCC(max_iterations=50)
        self.eng.kg_WCC.assert_called_once_with(max_iterations=50)

    def test_kg_cdlp_delegates(self):
        self.eng.kg_CDLP.return_value = []
        self.ops.kg_CDLP(max_iterations=5)
        self.eng.kg_CDLP.assert_called_once_with(max_iterations=5)

    def test_kg_subgraph_delegates(self):
        self.eng.kg_SUBGRAPH.return_value = {}
        self.ops.kg_SUBGRAPH(["n1"], k_hops=2)
        self.eng.kg_SUBGRAPH.assert_called_once()

    def test_kg_ppr_guided_subgraph_uses_top_k(self):
        self.eng.kg_PPR_GUIDED_SUBGRAPH.return_value = {}
        self.ops.kg_PPR_GUIDED_SUBGRAPH(["n1"], top_k=30)
        call_kwargs = self.eng.kg_PPR_GUIDED_SUBGRAPH.call_args.kwargs
        assert call_kwargs["ppr_top_k"] == 30

    def test_kg_ppr_guided_subgraph_uses_max_hops(self):
        self.eng.kg_PPR_GUIDED_SUBGRAPH.return_value = {}
        self.ops.kg_PPR_GUIDED_SUBGRAPH(["n1"], max_hops=3)
        call_kwargs = self.eng.kg_PPR_GUIDED_SUBGRAPH.call_args.kwargs
        assert call_kwargs["k_hops"] == 3

    def test_kg_neighbors_delegates(self):
        self.eng.kg_NEIGHBORS.return_value = []
        self.ops.kg_NEIGHBORS(["n1"], predicate="P", direction="in")
        self.eng.kg_NEIGHBORS.assert_called_once()

    def test_kg_mentions_delegates(self):
        self.eng.kg_MENTIONS.return_value = []
        self.ops.kg_MENTIONS(["n1"])
        self.eng.kg_MENTIONS.assert_called_once()

    def test_kg_ppr_delegates(self):
        self.eng.kg_PPR.return_value = []
        self.ops.kg_PPR(["n1"], damping=0.9)
        self.eng.kg_PPR.assert_called_once()

    def test_kg_rerank_delegates(self):
        self.eng.kg_RERANK.return_value = []
        self.ops.kg_RERANK(10, [0.1], "query")
        self.eng.kg_RERANK.assert_called_once_with(10, [0.1], "query")
