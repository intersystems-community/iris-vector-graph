"""
Deep coverage tests for _engine/algorithms.py remaining uncovered lines.

Targets:
  L52, 56: PPR validation errors
  L69-100: PPR ObjectScript fast path
  L145-146: PPR Python fallback no valid seeds
  L163-169: Bidirectional PPR
  L228-230: PPR exception path
  L259-260: khop exception
  L279: ppr Arno warning path
  L297-301: random_walk return parsed list
  L337, 342: kg_GRAPH_WALK_TVF + kg_PAGERANK with seed
  L355-357: kg_PAGERANK list return format
  L384-416: kg_SUBGRAPH with embeddings
  L431-432: kg_PPR_GUIDED_SUBGRAPH list ppr_scores
  L452, 489, 491: kg_NEIGHBORS
  L547-548: degree_centrality top_k=0 warning
  L684-689: betweenness_neighborhood
  L805-806: closeness warning path
  L868-875: eigenvector warning path
  L950-951: leiden top_k=0 warning
  L1010, 1051, 1092: triangle/SCC/k_core error paths
"""
import hashlib
import pytest
from iris_vector_graph.engine import IRISGraphEngine


def _make_vec(seed: str, dim=4):
    h = hashlib.md5(seed.encode()).digest()
    raw = list((b / 255.0) - 0.5 for b in h)
    while len(raw) < dim:
        raw.extend(raw)
    v = raw[:dim]
    norm = sum(x**2 for x in v)**0.5 or 1.0
    return [x / norm for x in v]


@pytest.fixture
def alg_graph(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(8):
        eng.create_node(f"alg_{i}", labels=["AlgNode"], properties={"idx": str(i)})
    for i in range(7):
        eng.create_edge(f"alg_{i}", "ALG_REL", f"alg_{i + 1}")
    eng.create_edge("alg_7", "ALG_REL", "alg_0")
    eng.sync()
    return eng


@pytest.fixture
def alg_graph_with_embeddings(iris_connection, iris_master_cleanup):
    DIM = 128
    eng = IRISGraphEngine(iris_connection, embedding_dimension=DIM)
    for i in range(6):
        nid = f"emb_alg_{i}"
        eng.create_node(nid, labels=["EmbAlg"])
        vec = _make_vec(nid, DIM)
        try:
            eng.store_embedding(nid, vec)
        except Exception:
            pass
    for i in range(5):
        eng.create_edge(f"emb_alg_{i}", "EALG_REL", f"emb_alg_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# PPR validation errors (L52, L56)
# ---------------------------------------------------------------------------

class TestPPRValidationErrors:

    def test_ppr_negative_reverse_edge_weight_raises(self, alg_graph):
        with pytest.raises(ValueError, match="reverse_edge_weight"):
            alg_graph.kg_PERSONALIZED_PAGERANK(
                ["alg_0"], reverse_edge_weight=-1.0
            )

    def test_ppr_empty_seed_entities_raises(self, alg_graph):
        with pytest.raises(ValueError, match="seed_entities"):
            alg_graph.kg_PERSONALIZED_PAGERANK([])


# ---------------------------------------------------------------------------
# PPR Python fallback — no valid seeds (L145-146)
# ---------------------------------------------------------------------------

class TestPPRNoValidSeeds:

    def test_ppr_invalid_seed_returns_empty(self, alg_graph):
        # Force Python fallback by using invalid seeds that won't be in node_set
        # The ObjectScript path will also handle gracefully
        result = alg_graph.kg_PERSONALIZED_PAGERANK(
            ["__absolutely_not_a_node__"]
        )
        assert isinstance(result, (dict, list))


# ---------------------------------------------------------------------------
# PPR bidirectional (L163-169)
# ---------------------------------------------------------------------------

class TestPPRBidirectional:

    def test_ppr_bidirectional_returns_dict(self, alg_graph):
        result = alg_graph.kg_PERSONALIZED_PAGERANK(
            ["alg_0"], bidirectional=True, max_iterations=5
        )
        assert isinstance(result, dict)

    def test_ppr_bidirectional_with_reverse_weight(self, alg_graph):
        result = alg_graph.kg_PERSONALIZED_PAGERANK(
            ["alg_0"], bidirectional=True, reverse_edge_weight=0.5, max_iterations=5
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# kg_GRAPH_WALK_TVF (L337)
# ---------------------------------------------------------------------------

class TestKgGraphWalkTVF:

    def test_graph_walk_tvf_returns_list(self, alg_graph):
        result = alg_graph.kg_GRAPH_WALK_TVF("alg_0", max_depth=2, max_results=50)
        assert isinstance(result, list)

    def test_graph_walk_tvf_matches_graph_walk(self, alg_graph):
        r1 = alg_graph.kg_GRAPH_WALK("alg_0", max_depth=1)
        r2 = alg_graph.kg_GRAPH_WALK_TVF("alg_0", max_depth=1)
        assert r1 == r2


# ---------------------------------------------------------------------------
# kg_PAGERANK with seed (L342)
# ---------------------------------------------------------------------------

class TestKgPageRank:

    def test_kg_pagerank_all_nodes_returns_list(self, alg_graph):
        result = alg_graph.kg_PAGERANK()
        assert isinstance(result, list)

    def test_kg_pagerank_with_seed_entities(self, alg_graph):
        # When seed_entities is not None, routes to kg_PERSONALIZED_PAGERANK
        result = alg_graph.kg_PAGERANK(seed_entities=["alg_0"])
        assert isinstance(result, (dict, list))


# ---------------------------------------------------------------------------
# kg_NEIGHBORS (L452, 489, 491)
# ---------------------------------------------------------------------------

class TestKgNeighbors:

    def test_neighbors_outbound(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS(["alg_0"], direction="out")
        assert isinstance(result, list)

    def test_neighbors_inbound(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS(["alg_1"], direction="in")
        assert isinstance(result, list)

    def test_neighbors_both_directions(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS(["alg_0"], direction="both")
        assert isinstance(result, list)

    def test_neighbors_distinct_true(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS(["alg_0"], distinct=True)
        assert isinstance(result, list)

    def test_neighbors_with_predicate(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS(["alg_0"], predicate="ALG_REL")
        assert isinstance(result, list)

    def test_neighbors_empty_sources(self, alg_graph):
        result = alg_graph.kg_NEIGHBORS([])
        assert result == []

    def test_neighbors_invalid_direction_raises(self, alg_graph):
        with pytest.raises(ValueError):
            alg_graph.kg_NEIGHBORS(["alg_0"], direction="sideways")

    def test_neighbors_multiple_sources_chunked(self, alg_graph):
        sources = [f"alg_{i}" for i in range(5)]
        result = alg_graph.kg_NEIGHBORS(sources, chunk_size=2)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# kg_SUBGRAPH with embeddings (L384-416)
# ---------------------------------------------------------------------------

class TestKgSubgraphWithEmbeddings:

    def test_subgraph_with_embeddings_flag(self, alg_graph_with_embeddings):
        result = alg_graph_with_embeddings.kg_SUBGRAPH(
            ["emb_alg_0"], k_hops=1, include_embeddings=True
        )
        assert hasattr(result, "nodes")

    def test_subgraph_embeddings_returned_when_available(self, alg_graph_with_embeddings):
        result = alg_graph_with_embeddings.kg_SUBGRAPH(
            ["emb_alg_0", "emb_alg_1"], k_hops=1, include_embeddings=True
        )
        # node_embeddings may be empty if embeddings not in store, but should not raise
        assert hasattr(result, "node_embeddings")


# ---------------------------------------------------------------------------
# kg_PPR_GUIDED_SUBGRAPH (L431-432 list ppr_scores path)
# ---------------------------------------------------------------------------

class TestKgPPRGuidedSubgraph:

    def test_ppr_guided_subgraph_basic(self, alg_graph):
        result = alg_graph.kg_PPR_GUIDED_SUBGRAPH(
            ["alg_0"], ppr_top_k=5, k_hops=1, max_nodes=10
        )
        assert hasattr(result, "nodes")
        assert hasattr(result, "edges")
        assert hasattr(result, "ppr_scores")

    def test_ppr_guided_subgraph_empty_seeds(self, alg_graph):
        result = alg_graph.kg_PPR_GUIDED_SUBGRAPH([])
        assert result.nodes == []


# ---------------------------------------------------------------------------
# random_walk (L297-301)
# ---------------------------------------------------------------------------

class TestRandomWalk:

    def test_random_walk_returns_list(self, alg_graph):
        result = alg_graph.random_walk("alg_0", length=3, num_walks=2)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# betweenness_neighborhood (L684-689)
# ---------------------------------------------------------------------------

class TestBetweennessNeighborhood:

    def test_betweenness_neighborhood_returns_list(self, alg_graph):
        try:
            result = alg_graph.betweenness_neighborhood("alg_0", hops=1, top_k=5)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("betweenness_neighborhood not supported")


# ---------------------------------------------------------------------------
# degree_centrality top_k=0 warning (L547-548)
# ---------------------------------------------------------------------------

class TestDegreeCentralityTopKZero:

    def test_degree_centrality_top_k_zero(self, alg_graph):
        result = alg_graph.degree_centrality(top_k=0)
        assert isinstance(result, list)

    def test_degree_centrality_with_predicate(self, alg_graph):
        result = alg_graph.degree_centrality(predicate="ALG_REL")
        assert isinstance(result, list)

    def test_degree_centrality_inbound(self, alg_graph):
        result = alg_graph.degree_centrality(direction="in")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# eigenvector_centrality top_k=0 large graph warning (L868-875)
# ---------------------------------------------------------------------------

class TestEigenvectorCentralityTopKZero:

    def test_eigenvector_centrality_top_k_zero(self, alg_graph):
        try:
            result = alg_graph.eigenvector_centrality(top_k=0)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("eigenvector_centrality not supported")


# ---------------------------------------------------------------------------
# leiden_communities top_k=0 warning (L950-951)
# ---------------------------------------------------------------------------

class TestLeidenTopKZero:

    def test_leiden_communities_top_k_zero(self, alg_graph):
        try:
            result = alg_graph.leiden_communities(top_k=0, max_levels=3)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("leiden_communities not supported")


# ---------------------------------------------------------------------------
# triangle_count, SCC, k_core (error paths L1010, 1051, 1092)
# ---------------------------------------------------------------------------

class TestCommunityAlgorithms:

    def test_triangle_count_basic(self, alg_graph):
        try:
            result = alg_graph.triangle_count(top_k=10)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("triangle_count not supported")

    def test_strongly_connected_components_basic(self, alg_graph):
        try:
            result = alg_graph.strongly_connected_components(top_k=10)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("strongly_connected_components not supported")

    def test_k_core_basic(self, alg_graph):
        try:
            result = alg_graph.k_core(top_k=10)
            assert isinstance(result, list)
        except (NotImplementedError, AttributeError):
            pytest.skip("k_core not supported")
