"""
Unit tests for _engine/algorithms.py covering:
- kg_PERSONALIZED_PAGERANK: invalid input, store path, ObjectScript path, fallback
- khop / _khop_fallback
- ppr
- degree_centrality: success, error, top_k warning branch
- betweenness_centrality: success, error, meta row
- betweenness_centrality_neighborhood
- closeness_centrality: success, error
- kg_RERANK (delegates to kg_RRF_FUSE)
- kg_PPR_GUIDED_SUBGRAPH: empty seeds, result path

No IRIS connection needed — mocks conn, store, and iris_obj.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# kg_PERSONALIZED_PAGERANK — input validation
# ---------------------------------------------------------------------------

class TestKgPPRValidation:

    def test_empty_seeds_raises(self):
        eng, _, _ = _make_eng()
        with pytest.raises(ValueError, match="seed_entities"):
            eng.kg_PERSONALIZED_PAGERANK([])

    def test_negative_reverse_weight_raises(self):
        eng, _, _ = _make_eng()
        with pytest.raises(ValueError, match="reverse_edge_weight"):
            eng.kg_PERSONALIZED_PAGERANK(["n1"], reverse_edge_weight=-0.1)

    def test_store_success_path(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.capabilities.return_value = {"ppr": True}
        store_mock.execute_ppr.return_value = IVGResult(
            columns=["id", "score"],
            rows=[["n1", 0.9], ["n2", 0.6]],
        )
        eng._store = store_mock
        eng._store_capabilities = {"ppr": True}
        result = eng.kg_PERSONALIZED_PAGERANK(["seed"], return_top_k=2)
        assert isinstance(result, dict)
        assert "n1" in result

    def test_store_error_triggers_fallback(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.capabilities.return_value = {"ppr": True}
        store_mock.execute_ppr.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="ppr failed"
        )
        eng._store = store_mock
        eng._store_capabilities = {"ppr": True}
        # capabilities is a plain dataclass attribute — set it directly
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False, kg_built=False)
        with patch.object(eng, "_kg_PERSONALIZED_PAGERANK_python_fallback",
                          return_value={"n1": 0.5}) as mock_fb:
            result = eng.kg_PERSONALIZED_PAGERANK(["seed"])
        mock_fb.assert_called_once()

    def test_objectscript_path_returns_scores(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.capabilities.return_value = {"ppr": False}
        store_mock.execute_ppr.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="no ppr"
        )
        eng._store = store_mock
        eng._store_capabilities = {"ppr": False}

        scores = [{"id": "a", "score": 0.8}, {"id": "b", "score": 0.3}]
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps(scores)

        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=True, kg_built=True)

        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.kg_PERSONALIZED_PAGERANK(["seed"], return_top_k=2)
        assert isinstance(result, dict)
        assert "a" in result


# ---------------------------------------------------------------------------
# khop / _khop_fallback
# ---------------------------------------------------------------------------

class TestKhop:

    def test_khop_fallback_with_objectscript(self):
        eng, _, _ = _make_eng()
        edges = [{"s": "a", "o": "b"}, {"s": "b", "o": "c"}]
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps(edges)

        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=True, kg_built=True)

        with patch.object(eng, "_detect_arno", return_value=False):
            with patch.object(eng, "_iris_obj", return_value=iris_obj):
                result = eng.khop("seed_node", hops=2, max_nodes=100)
        assert "nodes" in result
        assert "edges" in result

    def test_khop_fallback_objectscript_returns_empty_on_failure(self):
        eng, _, _ = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("class not found")

        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)

        with patch.object(eng, "_detect_arno", return_value=False):
            with patch.object(eng, "_iris_obj", return_value=iris_obj):
                result = eng._khop_fallback("seed", hops=2, max_nodes=100)
        assert result == {"nodes": [], "edges": []}

    def test_khop_fallback_no_objectscript_returns_empty(self):
        eng, _, _ = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False)
        result = eng._khop_fallback("seed", hops=1, max_nodes=10)
        assert result == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# ppr
# ---------------------------------------------------------------------------

class TestPpr:

    def test_ppr_no_arno_delegates_to_pagerank(self):
        eng, _, _ = _make_eng()
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch.object(eng, "kg_PERSONALIZED_PAGERANK",
                              return_value={"n1": 0.9, "n2": 0.3}) as mock_pr:
                result = eng.ppr("seed_node", top_k=5)
        assert "scores" in result
        scores = result["scores"]
        assert scores[0]["id"] == "n1"
        assert scores[0]["score"] == 0.9


# ---------------------------------------------------------------------------
# degree_centrality
# ---------------------------------------------------------------------------

class TestDegreeCentrality:

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_degree_centrality.return_value = IVGResult(
            columns=["id", "score", "degree"],
            rows=[["n1", 0.9, 5], ["n2", 0.5, 3]],
        )
        eng._store = store_mock
        result = eng.degree_centrality(direction="out", top_k=10)
        assert isinstance(result, list)
        assert result[0]["id"] == "n1"
        assert result[0]["score"] == 0.9
        assert result[0]["degree"] == 5

    def test_store_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_degree_centrality.return_value = IVGResult(
            columns=["id", "score", "degree"], rows=[], error="degree failed"
        )
        eng._store = store_mock
        result = eng.degree_centrality()
        assert result == []

    def test_top_k_zero_warns_on_large_graph(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.get_node_count.return_value = IVGResult(
            columns=["count"], rows=[[200_000]]
        )
        store_mock.execute_degree_centrality.return_value = IVGResult(
            columns=["id", "score", "degree"], rows=[]
        )
        eng._store = store_mock
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng.degree_centrality(top_k=0)
        # A RuntimeWarning should have been issued
        assert any(issubclass(warning.category, RuntimeWarning) for warning in w)


# ---------------------------------------------------------------------------
# betweenness_centrality
# ---------------------------------------------------------------------------

class TestBetweennessCentrality:

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_betweenness.return_value = IVGResult(
            columns=["id", "score"],
            rows=[["n1", 0.75], ["n2", 0.50]],
        )
        eng._store = store_mock
        result = eng.betweenness_centrality()
        assert isinstance(result, list)
        assert result[0]["id"] == "n1"

    def test_store_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_betweenness.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="btw failed"
        )
        eng._store = store_mock
        result = eng.betweenness_centrality()
        assert result == []

    def test_meta_row_kept_as_dict(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        meta = {"elapsed_ms": 100, "sample_size": 50}
        store_mock.execute_betweenness.return_value = IVGResult(
            columns=["id", "score"],
            rows=[["_meta", meta], ["n1", 0.8]],
        )
        eng._store = store_mock
        result = eng.betweenness_centrality()
        assert result[0] == meta
        assert result[1]["id"] == "n1"


# ---------------------------------------------------------------------------
# betweenness_centrality_neighborhood
# ---------------------------------------------------------------------------

class TestBetweennessNeighborhood:

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_betweenness_neighborhood.return_value = IVGResult(
            columns=["id", "score"],
            rows=[["n1", 0.6], ["n2", 0.4]],
        )
        eng._store = store_mock
        result = eng.betweenness_centrality_neighborhood("seed", hops=2)
        assert len(result) == 2
        assert result[0]["id"] == "n1"

    def test_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.execute_betweenness_neighborhood.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="fail"
        )
        eng._store = store_mock
        result = eng.betweenness_centrality_neighborhood("seed")
        assert result == []

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.betweenness_centrality_neighborhood("seed")


# ---------------------------------------------------------------------------
# closeness_centrality
# ---------------------------------------------------------------------------

class TestClosenessCentrality:

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.capabilities.return_value = {"closeness": True}
        store_mock.execute_closeness.return_value = IVGResult(
            columns=["id", "score"],
            rows=[["n1", 0.7], ["n2", 0.5]],
        )
        eng._store = store_mock
        result = eng.closeness_centrality()
        assert len(result) == 2
        assert result[0]["score"] == 0.7

    def test_store_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store_mock = MagicMock()
        store_mock.capabilities.return_value = {"closeness": True}
        store_mock.execute_closeness.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="closeness fail"
        )
        eng._store = store_mock
        result = eng.closeness_centrality()
        assert result == []


# ---------------------------------------------------------------------------
# kg_PPR_GUIDED_SUBGRAPH
# ---------------------------------------------------------------------------

class TestKgPPRGuidedSubgraph:

    def test_empty_seeds_returns_empty_data(self):
        eng, _, _ = _make_eng()
        result = eng.kg_PPR_GUIDED_SUBGRAPH([])
        assert result.nodes == []
        assert result.edges == []

    def test_with_seeds_calls_ppr_and_subgraph(self):
        eng, _, _ = _make_eng()
        with patch.object(eng, "kg_PERSONALIZED_PAGERANK",
                          return_value={"n1": 0.9, "n2": 0.5}) as mock_ppr:
            with patch.object(eng, "kg_SUBGRAPH") as mock_sg:
                from iris_vector_graph.models import SubgraphData
                mock_sg.return_value = SubgraphData(
                    seed_ids=["n1"], nodes=["n1", "n2"],
                    edges=[("n1", "TREATS", "n2")],
                    node_properties={}, node_labels={}, node_embeddings={},
                )
                result = eng.kg_PPR_GUIDED_SUBGRAPH(["n1"], ppr_top_k=5, k_hops=1)
        mock_ppr.assert_called_once()
        assert len(result.nodes) == 2


# ---------------------------------------------------------------------------
# kg_RERANK
# ---------------------------------------------------------------------------

class TestKgRerank:

    def test_delegates_to_rrf_fuse(self):
        eng, _, _ = _make_eng()
        expected = [{"id": "n1", "score": 0.9}]
        with patch.object(eng, "kg_RRF_FUSE", return_value=expected) as mock_rrf:
            result = eng.kg_RERANK(top_n=5, query_vector="[1,0,0,0]", query_text="cancer")
        mock_rrf.assert_called_once()
        assert result == expected
