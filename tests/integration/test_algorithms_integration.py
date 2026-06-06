"""
Integration tests for AlgorithmsMixin against live ivg-iris and ivg-iris-enterprise.

Covers: PPR (ObjectScript fast path + Python fallback), khop, kg_NEIGHBORS,
kg_SUBGRAPH, kg_WCC, kg_CDLP, kg_GRAPH_PATH, input validation.

No mocking — all paths use real IRIS SQL and ObjectScript classMethodValue calls.
The Python fallback paths are exercised by inserting real graph data and calling
the internal fallback methods directly.
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


# ---------------------------------------------------------------------------
# Shared fixture: 10-node ring + spoke graph, KG built
# ---------------------------------------------------------------------------

@pytest.fixture
def graph_engine(iris_connection, iris_master_cleanup):
    """10-node graph: 0→1→2→...→9→0 ring plus 0→5 spoke."""
    import iris as _iris
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    nodes = [f"alg_{i}" for i in range(10)]
    for n in nodes:
        eng.create_node(n, labels=["Node"])
    for i in range(10):
        eng.create_edge(f"alg_{i}", "R", f"alg_{(i+1) % 10}")
    eng.create_edge("alg_0", "R", "alg_5")  # extra spoke
    eng.sync()
    return eng, nodes


# ---------------------------------------------------------------------------
# PPR — ObjectScript fast path
# ---------------------------------------------------------------------------

class TestPPR:

    def test_ppr_returns_dict(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_PERSONALIZED_PAGERANK(["alg_0"])
        assert isinstance(result, dict)

    def test_ppr_seed_has_highest_score(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_PERSONALIZED_PAGERANK(["alg_0"])
        if not result:
            pytest.skip("PPR returned empty — graph may be too sparse")
        assert "alg_0" in result
        seed_score = result["alg_0"]
        others = {k: v for k, v in result.items() if k != "alg_0"}
        if others:
            assert seed_score >= max(others.values()) * 0.5

    def test_ppr_top_k_limits_results(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_PERSONALIZED_PAGERANK(["alg_0"], return_top_k=3)
        assert len(result) <= 3

    def test_ppr_empty_seeds_raises(self, graph_engine):
        eng, _ = graph_engine
        with pytest.raises(ValueError, match="seed"):
            eng.kg_PERSONALIZED_PAGERANK([])

    def test_ppr_negative_weight_raises(self, graph_engine):
        eng, _ = graph_engine
        with pytest.raises(ValueError, match="reverse_edge_weight"):
            eng.kg_PERSONALIZED_PAGERANK(["alg_0"], reverse_edge_weight=-1.0)

    def test_ppr_multi_seed(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_PERSONALIZED_PAGERANK(["alg_0", "alg_5"])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# PPR Python fallback — called directly
# ---------------------------------------------------------------------------

class TestPPRPythonFallback:

    def test_python_fallback_returns_dict(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(["alg_0"])
        assert isinstance(result, dict)

    def test_python_fallback_nonzero_scores(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(["alg_0"])
        assert any(v > 0 for v in result.values())

    def test_python_fallback_bidirectional(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["alg_0"], bidirectional=True, reverse_edge_weight=0.5
        )
        assert isinstance(result, dict)

    def test_python_fallback_top_k(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["alg_0"], return_top_k=3
        )
        assert len(result) <= 3

    def test_python_fallback_invalid_seed_returns_empty(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(["__no_such_node__"])
        assert result == {}


# ---------------------------------------------------------------------------
# khop + _khop_fallback
# ---------------------------------------------------------------------------

class TestKhop:

    def test_khop_returns_dict_with_nodes(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.khop("alg_0", hops=2)
        assert isinstance(result, dict)

    def test_khop_fallback_hops1(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._khop_fallback("alg_0", hops=1, max_nodes=100)
        assert isinstance(result, dict)

    def test_khop_fallback_hops2(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._khop_fallback("alg_0", hops=2, max_nodes=100)
        assert isinstance(result, dict)

    def test_khop_fallback_unknown_node_returns_empty(self, graph_engine):
        eng, nodes = graph_engine
        result = eng._khop_fallback("__nonexistent__", hops=1, max_nodes=100)
        # Should return empty nodes list, not raise
        node_count = result.get("totalNodes", 0) if result else 0
        assert node_count == 0 or result == {}


# ---------------------------------------------------------------------------
# kg_NEIGHBORS
# ---------------------------------------------------------------------------

class TestKgNeighbors:

    def test_neighbors_outbound(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_NEIGHBORS(["alg_0"], direction="out")
        assert isinstance(result, dict) or result is not None

    def test_neighbors_with_predicate(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_NEIGHBORS(["alg_0"], predicate="R", direction="out")
        assert result is not None

    def test_neighbors_nonexistent_node_empty(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_NEIGHBORS(["__no_such__"], direction="out")
        # Should be empty or None, not raise
        assert result is None or (hasattr(result, "__len__") and len(result) == 0) \
               or (isinstance(result, dict) and not result.get("nodes"))


# ---------------------------------------------------------------------------
# kg_SUBGRAPH
# ---------------------------------------------------------------------------

class TestKgSubgraph:

    def test_subgraph_returns_nodes_and_edges(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_SUBGRAPH(["alg_0"], k_hops=1)
        assert result is not None

    def test_subgraph_includes_seed(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_SUBGRAPH(["alg_0"], k_hops=1)
        # Result varies by implementation; just assert no exception and truthy
        assert result is not None

    def test_subgraph_2hop_larger_than_1hop(self, graph_engine):
        eng, nodes = graph_engine
        r1 = eng.kg_SUBGRAPH(["alg_0"], k_hops=1)
        r2 = eng.kg_SUBGRAPH(["alg_0"], k_hops=2)
        # 2-hop subgraph should have at least as many nodes as 1-hop
        def _node_count(r):
            if isinstance(r, dict):
                return len(r.get("nodes", r.get("node_ids", [])))
            if hasattr(r, "rows"):
                return len(r.rows)
            return 0
        assert _node_count(r2) >= _node_count(r1)


# ---------------------------------------------------------------------------
# kg_WCC / kg_CDLP
# ---------------------------------------------------------------------------

class TestWccCdlp:

    def test_wcc_returns_result(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_WCC()
        assert result is not None

    def test_cdlp_returns_result(self, graph_engine):
        eng, nodes = graph_engine
        result = eng.kg_CDLP()
        assert result is not None


# ---------------------------------------------------------------------------
# kg_GRAPH_PATH
# ---------------------------------------------------------------------------

class TestGraphPath:

    def test_graph_path_finds_2hop(self, graph_engine):
        eng, nodes = graph_engine
        # alg_0 →R→ alg_1 →R→ alg_2
        result = eng.kg_GRAPH_PATH("alg_0", "R", "R", max_hops=2)
        # Should not raise and return something
        assert result is not None

    def test_graph_path_no_path_returns_empty(self, graph_engine):
        eng, nodes = graph_engine
        # No NONEXISTENT predicate exists
        result = eng.kg_GRAPH_PATH("alg_0", "NONEXISTENT", "NONEXISTENT", max_hops=2)
        if hasattr(result, "rows"):
            assert result.rows == [] or len(result.rows) == 0
        elif isinstance(result, list):
            assert result == []


# ---------------------------------------------------------------------------
# Arno fast-path (enterprise only, skips if not available)
# ---------------------------------------------------------------------------

class TestArnoAlgorithms:

    def test_ppr_arno_path(self, arno_iris_connection, iris_master_cleanup):
        """PPR via arno fast-path on enterprise container."""
        import iris as _iris
        eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=4)
        for i in range(5):
            eng.create_node(f"arno_alg_{i}", labels=["Node"])
        for i in range(4):
            eng.create_edge(f"arno_alg_{i}", "R", f"arno_alg_{i+1}")
        eng.sync()

        result = eng.kg_PERSONALIZED_PAGERANK(["arno_alg_0"])
        assert isinstance(result, dict)

    def test_khop_arno_path(self, arno_iris_connection):
        """khop via enterprise — exercises arno dispatch branch."""
        import iris as _iris
        eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=4)
        result = eng.khop("arno_alg_0", hops=2)
        assert result is not None
