"""
Extended unit tests for _engine/algorithms.py covering remaining uncovered branches:
- kg_PERSONALIZED_PAGERANK python fallback (bidirectional, convergence warning)
- random_walk (no arno path returns [])
- kg_GRAPH_PATH, kg_GRAPH_WALK, kg_GRAPH_WALK_TVF
- kg_PAGERANK (global path)
- kg_WCC, kg_CDLP (fallback paths)
- kg_SUBGRAPH (ObjectScript fallback, include_embeddings)
- kg_NEIGHBORS (direction=both, predicate)
- kg_PPR (wrapper)
- eigenvector_centrality (success, error, top_k=0 warning)
- leiden_communities (success, error, meta row)
- triangle_count (success, error)
- strongly_connected_components (success, error)
- k_core (success, error)
- closeness_centrality top_k=0 warning

No IRIS connection needed — mocks conn, cursor, and store.
"""
import json
import warnings
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.capabilities import IRISCapabilities


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


def _make_store(**caps):
    store = MagicMock()
    store.capabilities.return_value = caps
    return store


# ---------------------------------------------------------------------------
# PPR python fallback — bidirectional + convergence logging
# ---------------------------------------------------------------------------

class TestPPRPythonFallback:

    def test_bidirectional_path_runs(self):
        """Cover lines 163-169: bidirectional reverse edges."""
        eng, conn, cursor = _make_eng()
        # fetchall returns (src, dst) 2-tuples for edges
        call_seq = iter([
            [("n1",), ("n2",), ("n3",)],   # SELECT node_id
            [("n1", "n2"), ("n2", "n3")],   # SELECT s, o_id (forward)
            [("n2", "n1"), ("n3", "n2")],   # SELECT o_id, s (reverse, bidirectional)
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["n1"], damping_factor=0.85, max_iterations=3,
            reverse_edge_weight=0.5, bidirectional=True,
        )
        assert isinstance(result, dict)

    def test_fallback_returns_top_k(self):
        """Cover line 222-224: return_top_k slicing."""
        eng, conn, cursor = _make_eng()
        call_seq = iter([
            [("n1",), ("n2",), ("n3",)],
            [("n1", "n2"), ("n2", "n3")],
            [],
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["n1"], return_top_k=1
        )
        assert len(result) <= 1

    def test_fallback_convergence(self):
        """Cover lines 213-217: convergence logging."""
        eng, conn, cursor = _make_eng()
        call_seq = iter([
            [("n1",)],
            [("n1", "n1")],  # self-loop
            [],
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        # Large tolerance triggers convergence path immediately
        result = eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["n1"], max_iterations=5, tolerance=1.0
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# random_walk (no arno)
# ---------------------------------------------------------------------------

class TestRandomWalk:

    def test_no_arno_returns_empty(self):
        """Cover line 304: fallback returns []."""
        eng, _, _ = _make_eng()
        with patch.object(eng, "_detect_arno", return_value=False):
            result = eng.random_walk("seed", length=5, num_walks=2)
        assert result == []

    def test_arno_error_returns_empty(self):
        """Cover lines 302-303: arno call raises."""
        eng, _, _ = _make_eng()
        with patch.object(eng, "_detect_arno", return_value=True):
            eng._arno_capabilities = {"algorithms": ["random_walk"]}
            with patch.object(eng, "_arno_call", side_effect=RuntimeError("timeout")):
                result = eng.random_walk("seed")
        assert result == []


# ---------------------------------------------------------------------------
# kg_GRAPH_PATH
# ---------------------------------------------------------------------------

class TestKgGraphPath:

    def test_basic_path_execution(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [
            (1, 1, "n1", "TREATS", "n2"),
            (1, 2, "n2", "HAS", "n3"),
        ]
        result = eng.kg_GRAPH_PATH("n1", "TREATS", "HAS", max_hops=2)
        assert isinstance(result, list)

    def test_empty_result(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        result = eng.kg_GRAPH_PATH("n1", "X", "Y")
        assert result == []


# ---------------------------------------------------------------------------
# kg_PAGERANK (global path)
# ---------------------------------------------------------------------------

class TestKgPagerank:

    def test_global_pagerank_with_no_seeds(self):
        """Cover lines 345-349: global path using _call_classmethod."""
        eng, _, _ = _make_eng()
        scores = [{"id": "n1", "score": 0.8}, {"id": "n2", "score": 0.4}]
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=json.dumps(scores)):
            result = eng.kg_PAGERANK(seed_entities=None, damping=0.85, max_iterations=5)
        assert isinstance(result, list)
        assert result[0][0] == "n1"

    def test_global_pagerank_delegates_to_personalized_with_seeds(self):
        """Cover lines 341-343: seeds → personalized PPR."""
        eng, _, _ = _make_eng()
        with patch.object(eng, "kg_PERSONALIZED_PAGERANK", return_value={"n1": 0.9}) as mock_pr:
            result = eng.kg_PAGERANK(seed_entities=["n1"])
        mock_pr.assert_called_once()


# ---------------------------------------------------------------------------
# kg_WCC / kg_CDLP fallback paths
# ---------------------------------------------------------------------------

class TestKgWccCdlp:

    def test_wcc_store_success(self):
        eng, _, _ = _make_eng()
        store = _make_store(wcc=True)
        store.execute_wcc.return_value = IVGResult(
            columns=["id", "component"], rows=[["n1", 0], ["n2", 0]]
        )
        eng._store = store
        eng._store_capabilities = {"wcc": True}
        result = eng.kg_WCC()
        assert isinstance(result, dict)
        assert "n1" in result

    def test_wcc_fallback_to_classmethod(self):
        """Cover lines 354-357: ObjectScript fallback."""
        eng, _, _ = _make_eng()
        eng._store_capabilities = {"wcc": False}
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=json.dumps({"n1": 0, "n2": 1})):
            result = eng.kg_WCC()
        assert result == {"n1": 0, "n2": 1}

    def test_cdlp_store_success(self):
        eng, _, _ = _make_eng()
        store = _make_store(cdlp=True)
        store.execute_cdlp.return_value = IVGResult(
            columns=["id", "label"], rows=[["n1", 42], ["n2", 43]]
        )
        eng._store = store
        eng._store_capabilities = {"cdlp": True}
        result = eng.kg_CDLP()
        assert isinstance(result, dict)

    def test_cdlp_fallback_to_classmethod(self):
        """Cover lines 363-365: ObjectScript fallback."""
        eng, _, _ = _make_eng()
        eng._store_capabilities = {"cdlp": False}
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=json.dumps({"n1": 0})):
            result = eng.kg_CDLP()
        assert result == {"n1": 0}


# ---------------------------------------------------------------------------
# kg_SUBGRAPH (ObjectScript fallback)
# ---------------------------------------------------------------------------

class TestKgSubgraph:

    def test_objectscript_fallback_path(self):
        """Cover lines 384-416."""
        eng, conn, cursor = _make_eng()
        eng._store_capabilities = {"subgraph": False}
        parsed = {
            "nodes": ["n1", "n2"],
            "edges": [{"s": "n1", "p": "TREATS", "o": "n2"}],
            "properties": {"n1": {"name": "BRCA1"}},
            "labels": {"n1": ["Gene"]},
        }
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=json.dumps(parsed)):
            result = eng.kg_SUBGRAPH(["n1"], k_hops=1)
        assert "n1" in result.nodes
        assert len(result.edges) == 1

    def test_objectscript_fallback_with_embeddings(self):
        """Cover lines 396-410: include_embeddings path."""
        eng, conn, cursor = _make_eng()
        eng._store_capabilities = {"subgraph": False}
        parsed = {
            "nodes": ["n1"],
            "edges": [],
            "properties": {},
            "labels": {},
        }
        cursor.fetchall.return_value = [("n1", "0.1,0.2,0.3,0.4")]
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=json.dumps(parsed)):
            result = eng.kg_SUBGRAPH(["n1"], k_hops=1, include_embeddings=True)
        assert "n1" in result.nodes

    def test_empty_objectscript_result_returns_empty(self):
        """Cover line 416: raw empty → empty SubgraphData."""
        eng, _, _ = _make_eng()
        eng._store_capabilities = {"subgraph": False}
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value=""):
            result = eng.kg_SUBGRAPH(["n1"])
        assert result.nodes == []


# ---------------------------------------------------------------------------
# kg_NEIGHBORS
# ---------------------------------------------------------------------------

class TestKgNeighbors:

    def test_empty_sources_returns_empty(self):
        eng, _, _ = _make_eng()
        result = eng.kg_NEIGHBORS([])
        assert result == []

    def test_invalid_direction_raises(self):
        eng, _, _ = _make_eng()
        with pytest.raises(ValueError, match="direction"):
            eng.kg_NEIGHBORS(["n1"], direction="sideways")

    def test_both_direction(self):
        """Cover lines 458-476: direction='both' queries both out and in."""
        eng, _, _ = _make_eng()
        outrows = [["n2"], ["n3"]]
        inrows = [["n0"]]

        call_seq = iter([outrows, inrows])

        def fake_execute_cypher(q, params):
            try:
                rows = next(call_seq)
            except StopIteration:
                rows = []
            return {"rows": rows}

        with patch.object(eng, "execute_cypher", side_effect=fake_execute_cypher):
            result = eng.kg_NEIGHBORS(["n1"], direction="both")
        assert "n2" in result or "n0" in result

    def test_with_predicate(self):
        eng, _, _ = _make_eng()
        def fake_execute_cypher(q, params):
            return {"rows": [["n2"]]}
        with patch.object(eng, "execute_cypher", side_effect=fake_execute_cypher):
            result = eng.kg_NEIGHBORS(["n1"], predicate="TREATS")
        assert "n2" in result


# ---------------------------------------------------------------------------
# kg_PPR wrapper
# ---------------------------------------------------------------------------

class TestKgPPRWrapper:

    def test_empty_seeds_returns_empty(self):
        eng, _, _ = _make_eng()
        result = eng.kg_PPR([])
        assert result == []

    def test_with_seeds_delegates(self):
        eng, _, _ = _make_eng()
        with patch.object(eng, "kg_PERSONALIZED_PAGERANK", return_value={"n1": 0.9, "n2": 0.3}):
            result = eng.kg_PPR(["seed"])
        assert isinstance(result, list)
        assert result[0][0] == "n1"  # sorted descending


# ---------------------------------------------------------------------------
# eigenvector_centrality
# ---------------------------------------------------------------------------

class TestEigenvectorCentrality:

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.eigenvector_centrality()

    def test_store_no_capability_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        store = _make_store(eigenvector=False)
        eng._store = store
        with pytest.raises(NotImplementedError):
            eng.eigenvector_centrality()

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store = _make_store(eigenvector=True)
        store.execute_eigenvector.return_value = IVGResult(
            columns=["id", "score"], rows=[["n1", 0.9], ["n2", 0.5]]
        )
        eng._store = store
        result = eng.eigenvector_centrality()
        assert result[0]["id"] == "n1"
        assert result[0]["score"] == 0.9

    def test_store_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store = _make_store(eigenvector=True)
        store.execute_eigenvector.return_value = IVGResult(
            columns=["id", "score"], rows=[], error="eigenvector failed"
        )
        eng._store = store
        result = eng.eigenvector_centrality()
        assert result == []

    def test_top_k_zero_warns_on_large_graph(self):
        """Cover lines 868-875."""
        eng, _, _ = _make_eng()
        store = _make_store(eigenvector=True)
        store.get_node_count.return_value = IVGResult(columns=["count"], rows=[[200_000]])
        store.execute_eigenvector.return_value = IVGResult(
            columns=["id", "score"], rows=[]
        )
        eng._store = store
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng.eigenvector_centrality(top_k=0)
        assert any(issubclass(warning.category, RuntimeWarning) for warning in w)


# ---------------------------------------------------------------------------
# leiden_communities
# ---------------------------------------------------------------------------

class TestLeidenCommunities:

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.leiden_communities()

    def test_store_no_capability_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        store = _make_store(leiden=False)
        eng._store = store
        with pytest.raises(NotImplementedError):
            eng.leiden_communities()

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store = _make_store(leiden=True)
        store.execute_leiden.return_value = IVGResult(
            columns=["id", "community", "size"],
            rows=[["n1", 0, 5], ["n2", 1, 3]]
        )
        eng._store = store
        result = eng.leiden_communities()
        assert result[0]["id"] == "n1"
        assert result[0]["community"] == 0

    def test_store_error_returns_empty(self):
        eng, _, _ = _make_eng()
        store = _make_store(leiden=True)
        store.execute_leiden.return_value = IVGResult(
            columns=["id", "community", "size"], rows=[], error="leiden failed"
        )
        eng._store = store
        result = eng.leiden_communities()
        assert result == []

    def test_meta_row_included(self):
        """Cover lines 963-966."""
        eng, _, _ = _make_eng()
        store = _make_store(leiden=True)
        meta = {"elapsed_ms": 200}
        store.execute_leiden.return_value = IVGResult(
            columns=["id", "community", "size"],
            rows=[["_meta", json.dumps(meta)], ["n1", 0, 5]]
        )
        eng._store = store
        result = eng.leiden_communities()
        assert result[0] == meta
        assert result[1]["id"] == "n1"

    def test_top_k_zero_warns_on_large_graph(self):
        """Cover lines 940-951."""
        eng, _, _ = _make_eng()
        store = _make_store(leiden=True)
        store.get_node_count.return_value = IVGResult(columns=["count"], rows=[[300_000]])
        store.execute_leiden.return_value = IVGResult(
            columns=["id", "community", "size"], rows=[]
        )
        eng._store = store
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng.leiden_communities(top_k=0)
        assert any(issubclass(warning.category, RuntimeWarning) for warning in w)


# ---------------------------------------------------------------------------
# triangle_count
# ---------------------------------------------------------------------------

class TestTriangleCount:

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.triangle_count()

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store = _make_store(triangle_count=True)
        store.execute_triangle_count.return_value = IVGResult(
            columns=["id", "triangles", "lcc"],
            rows=[["n1", 5, 0.8], ["n2", 2, 0.5]]
        )
        eng._store = store
        result = eng.triangle_count()
        assert result[0]["id"] == "n1"
        assert result[0]["triangles"] == 5

    def test_store_error_returns_empty(self):
        """Cover line 1010."""
        eng, _, _ = _make_eng()
        store = _make_store(triangle_count=True)
        store.execute_triangle_count.return_value = IVGResult(
            columns=["id", "triangles", "lcc"], rows=[], error="failed"
        )
        eng._store = store
        result = eng.triangle_count()
        assert result == []


# ---------------------------------------------------------------------------
# strongly_connected_components
# ---------------------------------------------------------------------------

class TestSCC:

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.strongly_connected_components()

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store = _make_store(scc=True)
        store.execute_scc.return_value = IVGResult(
            columns=["id", "component", "size"],
            rows=[["n1", 0, 3], ["n2", 0, 3]]
        )
        eng._store = store
        result = eng.strongly_connected_components()
        assert result[0]["component"] == 0

    def test_store_error_returns_empty(self):
        """Cover line 1051."""
        eng, _, _ = _make_eng()
        store = _make_store(scc=True)
        store.execute_scc.return_value = IVGResult(
            columns=["id", "component", "size"], rows=[], error="scc failed"
        )
        eng._store = store
        result = eng.strongly_connected_components()
        assert result == []


# ---------------------------------------------------------------------------
# k_core
# ---------------------------------------------------------------------------

class TestKCore:

    def test_no_store_raises_not_implemented(self):
        eng, _, _ = _make_eng()
        if hasattr(eng, "_store"):
            del eng._store
        with pytest.raises(NotImplementedError):
            eng.k_core()

    def test_success_returns_list(self):
        eng, _, _ = _make_eng()
        store = _make_store(k_core=True)
        store.execute_k_core.return_value = IVGResult(
            columns=["id", "coreness"],
            rows=[["n1", 5], ["n2", 3]]
        )
        eng._store = store
        result = eng.k_core()
        assert result[0]["coreness"] == 5

    def test_store_error_returns_empty(self):
        """Cover line 1092."""
        eng, _, _ = _make_eng()
        store = _make_store(k_core=True)
        store.execute_k_core.return_value = IVGResult(
            columns=["id", "coreness"], rows=[], error="k_core failed"
        )
        eng._store = store
        result = eng.k_core()
        assert result == []


# ---------------------------------------------------------------------------
# closeness_centrality top_k=0 warning
# ---------------------------------------------------------------------------

class TestClosenessTopKWarning:

    def test_top_k_zero_warns_on_large_graph(self):
        """Cover lines 794-806."""
        eng, _, _ = _make_eng()
        store = _make_store(closeness=True)
        store.get_node_count.return_value = IVGResult(columns=["count"], rows=[[500_000]])
        store.execute_closeness.return_value = IVGResult(
            columns=["id", "score"], rows=[]
        )
        eng._store = store
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng.closeness_centrality(top_k=0)
        assert any(issubclass(warning.category, RuntimeWarning) for warning in w)
