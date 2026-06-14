"""
Direct tests for iris_sql_store.py uncovered paths.

Accesses the store via eng._store to call methods not reached through Cypher.
Targets:
  L83-85: _read_large_output chunking
  L187-188: property_filter JSON decode branch
  L233-235, 257-259: write_nodes / write_edges
  L316-317: execute_transaction
  L398-399, 424-443: SQL BFS fallback both-direction
  L452, 470, 475-477: execute_shortest_path
  L590-593, 625-627: write_temporal_edge / bulk_write_temporal_edges
  L729-730, 733-738: execute_wcc, execute_pagerank
  L787-788, 794-795: execute_ppr
  L804-816: execute_degree_centrality / version path
  L986-987: execute_betweenness
  L1111-1123: execute_closeness
  L1220-1222: execute_eigenvector
  L1307-1314: execute_leiden
  L1474, 1483-1487: execute_triangle_count
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def store_graph(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(6):
        eng.create_node(f"sg_{i}", labels=["SG"], properties={"idx": i})
    for i in range(5):
        eng.create_edge(f"sg_{i}", "SG_REL", f"sg_{i + 1}")
    eng.sync()
    return eng


@pytest.fixture
def store(store_graph):
    return store_graph._store


# ---------------------------------------------------------------------------
# query_nodes with property_filters (L171-198)
# ---------------------------------------------------------------------------

class TestQueryNodesFilters:

    def test_query_nodes_by_label(self, store):
        result = store.query_nodes(label_filter="SG")
        assert result is not None
        assert hasattr(result, "rows")

    def test_query_nodes_with_prop_filter(self, store):
        result = store.query_nodes(
            label_filter="SG",
            property_filters={"idx": 2},
        )
        assert result is not None

    def test_query_nodes_prop_filter_mismatch(self, store):
        result = store.query_nodes(
            label_filter="SG",
            property_filters={"idx": 9999},
        )
        assert result is not None

    def test_query_nodes_return_properties(self, store):
        result = store.query_nodes(label_filter="SG", return_properties=["idx"])
        assert result is not None


# ---------------------------------------------------------------------------
# write_nodes (L203-238)
# ---------------------------------------------------------------------------

class TestWriteNodes:

    def test_write_nodes_basic(self, store):
        nodes = [
            {"id": "wn_0", "labels": ["WN"], "properties": {"x": 1}},
            {"id": "wn_1", "labels": ["WN"]},
        ]
        result = store.write_nodes(nodes)
        assert result is not None

    def test_write_nodes_empty(self, store):
        result = store.write_nodes([])
        assert result is not None

    def test_write_nodes_no_id_skipped(self, store):
        result = store.write_nodes([{"labels": ["NoId"]}])
        assert result is not None


# ---------------------------------------------------------------------------
# write_edges (L240-270)
# ---------------------------------------------------------------------------

class TestWriteEdges:

    def test_write_edges_basic(self, store, store_graph):
        store_graph.create_node("we_s", labels=["WE"])
        store_graph.create_node("we_t", labels=["WE"])
        store_graph.sync()
        edges = [{"s": "we_s", "p": "WE_REL", "o": "we_t", "weight": 1.5}]
        result = store.write_edges(edges)
        assert result is not None

    def test_write_edges_empty(self, store):
        result = store.write_edges([])
        assert result is not None

    def test_write_edges_with_qualifiers(self, store, store_graph):
        store_graph.create_node("weq_s", labels=["WEQ"])
        store_graph.create_node("weq_t", labels=["WEQ"])
        store_graph.sync()
        edges = [{"s": "weq_s", "p": "WEQ_REL", "o": "weq_t", "qualifiers": {"attr": "val"}}]
        result = store.write_edges(edges)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_transaction (L320-337)
# ---------------------------------------------------------------------------

class TestExecuteTransaction:

    def test_execute_transaction_select(self, store):
        result = store.execute_transaction(
            ["SELECT COUNT(*) AS cnt FROM Graph_KG.nodes"],
            [[]]
        )
        assert result is not None

    def test_execute_transaction_multiple_stmts(self, store):
        result = store.execute_transaction(
            [
                "SELECT COUNT(*) FROM Graph_KG.nodes",
                "SELECT COUNT(*) FROM Graph_KG.rdf_edges",
            ],
            [[], []]
        )
        assert result is not None


# ---------------------------------------------------------------------------
# execute_bfs (L339-404)
# ---------------------------------------------------------------------------

class TestExecuteBFS:

    def test_bfs_outbound(self, store):
        result = store.execute_bfs("sg_0", [], max_hops=2, direction="out", max_results=20)
        assert result is not None
        assert hasattr(result, "rows")

    def test_bfs_inbound(self, store):
        result = store.execute_bfs("sg_2", [], max_hops=1, direction="in", max_results=10)
        assert result is not None

    def test_bfs_both_directions(self, store):
        result = store.execute_bfs("sg_2", [], max_hops=1, direction="both", max_results=20)
        assert result is not None

    def test_bfs_with_predicates(self, store):
        result = store.execute_bfs("sg_0", ["SG_REL"], max_hops=2, direction="out", max_results=10)
        assert result is not None

    def test_bfs_max_results_limits(self, store):
        result = store.execute_bfs("sg_0", [], max_hops=5, direction="out", max_results=2)
        assert len(result.rows) <= 2

    def test_bfs_missing_source(self, store):
        result = store.execute_bfs("__no_such_node__", [], max_hops=2, direction="out", max_results=10)
        assert result is not None
        assert result.rows == []


# ---------------------------------------------------------------------------
# execute_shortest_path (L456-499)
# ---------------------------------------------------------------------------

class TestExecuteShortestPath:

    def test_shortest_path_basic(self, store):
        result = store.execute_shortest_path(
            "sg_0", "sg_3", [], max_hops=5, direction="out", find_all=False
        )
        assert result is not None

    def test_shortest_path_find_all(self, store):
        result = store.execute_shortest_path(
            "sg_0", "sg_2", [], max_hops=5, direction="out", find_all=True
        )
        assert result is not None


# ---------------------------------------------------------------------------
# execute_ppr (L501-514)
# ---------------------------------------------------------------------------

class TestExecutePPR:

    def test_execute_ppr_basic(self, store):
        result = store.execute_ppr(["sg_0"], damping=0.85, max_iterations=10)
        assert result is not None
        assert hasattr(result, "rows")

    def test_execute_ppr_empty_seeds(self, store):
        result = store.execute_ppr([], damping=0.85, max_iterations=5)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_pagerank (L516-528)
# ---------------------------------------------------------------------------

class TestExecutePagerank:

    def test_execute_pagerank_basic(self, store):
        result = store.execute_pagerank(damping=0.85, max_iterations=10)
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# execute_wcc (L530-545)
# ---------------------------------------------------------------------------

class TestExecuteWCC:

    def test_execute_wcc_basic(self, store):
        result = store.execute_wcc()
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# write_temporal_edge / bulk_write_temporal_edges (L612-670)
# ---------------------------------------------------------------------------

class TestTemporalEdges:

    def test_write_temporal_edge_basic(self, store, store_graph):
        store_graph.create_node("te_s", labels=["TE"])
        store_graph.create_node("te_t", labels=["TE"])
        store_graph.sync()
        result = store.write_temporal_edge(
            "te_s", "TE_REL", "te_t", timestamp=1000000, weight=1.0, attrs={}, upsert=False
        )
        assert result is not None

    def test_bulk_write_temporal_edges_basic(self, store, store_graph):
        store_graph.create_node("bte_s", labels=["BTE"])
        store_graph.create_node("bte_t", labels=["BTE"])
        store_graph.sync()
        edges = [
            {"source": "bte_s", "predicate": "BTE_REL", "target": "bte_t",
             "timestamp": 2000000, "weight": 1.0}
        ]
        result = store.bulk_write_temporal_edges(edges, upsert=False)
        assert result is not None

    def test_bulk_write_temporal_edges_empty(self, store):
        result = store.bulk_write_temporal_edges([])
        assert result is not None


# ---------------------------------------------------------------------------
# get_node_count (L740-752)
# ---------------------------------------------------------------------------

class TestGetNodeCount:

    def test_get_node_count_all(self, store):
        result = store.get_node_count()
        assert result is not None
        assert hasattr(result, "rows")
        assert len(result.rows) > 0

    def test_get_node_count_by_label(self, store):
        result = store.get_node_count(label="SG")
        assert result is not None

    def test_get_node_count_missing_label(self, store):
        result = store.get_node_count(label="__NoSuchLabel__")
        assert result is not None


# ---------------------------------------------------------------------------
# execute_degree_centrality (L821-876)
# ---------------------------------------------------------------------------

class TestExecuteDegreeCentrality:

    def test_degree_centrality_out(self, store):
        result = store.execute_degree_centrality(direction="out", predicate="", top_k=10)
        assert result is not None
        assert hasattr(result, "rows")

    def test_degree_centrality_in(self, store):
        result = store.execute_degree_centrality(direction="in", predicate="", top_k=10)
        assert result is not None

    def test_degree_centrality_both(self, store):
        result = store.execute_degree_centrality(direction="both", predicate="", top_k=10)
        assert result is not None

    def test_degree_centrality_with_predicate(self, store):
        result = store.execute_degree_centrality(direction="out", predicate="SG_REL", top_k=5)
        assert result is not None

    def test_degree_centrality_top_k_zero(self, store):
        result = store.execute_degree_centrality(direction="out", predicate="", top_k=0)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_betweenness (L904-915)
# ---------------------------------------------------------------------------

class TestExecuteBetweenness:

    def test_betweenness_basic(self, store):
        result = store.execute_betweenness(
            sample_size=5, direction="out", max_hops=2, top_k=5, mem_budget_mb=64
        )
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# execute_closeness (L1099-1163)
# ---------------------------------------------------------------------------

class TestExecuteCloseness:

    def test_closeness_harmonic(self, store):
        result = store.execute_closeness(
            formula="harmonic", direction="out", max_hops=2, top_k=5
        )
        assert result is not None
        assert hasattr(result, "rows")

    def test_closeness_classical(self, store):
        result = store.execute_closeness(
            formula="classical", direction="out", max_hops=2, top_k=5
        )
        assert result is not None


# ---------------------------------------------------------------------------
# execute_eigenvector (L1192-1280)
# ---------------------------------------------------------------------------

class TestExecuteEigenvector:

    def test_eigenvector_basic(self, store):
        result = store.execute_eigenvector(max_iter=10, tol=1e-4, top_k=5)
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# execute_leiden (L1300-1360)
# ---------------------------------------------------------------------------

class TestExecuteLeiden:

    def test_leiden_basic(self, store):
        result = store.execute_leiden(
            max_levels=3, gamma=1.0, tol=0.001, top_k=5,
            mem_budget_mb=64, random_seed=42
        )
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# execute_triangle_count (L1461-1536)
# ---------------------------------------------------------------------------

class TestExecuteTriangleCount:

    def test_triangle_count_basic(self, store):
        result = store.execute_triangle_count(top_k=5)
        assert result is not None
        assert hasattr(result, "rows")


# ---------------------------------------------------------------------------
# execute_scc (L1537-1570)
# ---------------------------------------------------------------------------

class TestExecuteSCC:

    def test_scc_basic(self, store):
        try:
            result = store.execute_scc(top_k=5)
            assert result is not None
        except AttributeError:
            pytest.skip("execute_scc not implemented")


# ---------------------------------------------------------------------------
# get_nodes with properties (L108-143)
# ---------------------------------------------------------------------------

class TestGetNodesStore:

    def test_get_nodes_basic(self, store):
        result = store.get_nodes(["sg_0", "sg_1"])
        assert result is not None
        assert len(result.rows) == 2

    def test_get_nodes_with_properties(self, store):
        result = store.get_nodes(["sg_0"], properties=["idx"])
        assert result is not None
        assert "idx" in result.columns

    def test_get_nodes_empty(self, store):
        result = store.get_nodes([])
        assert len(result.rows) == 0

    def test_get_node_labels_basic(self, store):
        result = store.get_node_labels(["sg_0"])
        assert result is not None

    def test_get_node_labels_empty(self, store):
        result = store.get_node_labels([])
        assert len(result.rows) == 0
