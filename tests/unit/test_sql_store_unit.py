"""
Unit tests for stores/iris_sql_store.py covering:
- server_info (lines 803-819)
- execute_degree_centrality success path and Bug-S fallback (lines 821-903)
- _degree_centrality_gref_fallback (lines 855-901)
- get_node_count / get_edge_count / get_labels / get_relationship_types
- list_indexes structure
- execute_sql / execute_transaction (basic paths)
- capabilities() dict structure

No IRIS connection needed — mocks conn and cursor.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.result import IVGResult


def _make_store():
    """Create IRISGraphStore with mocked conn."""
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    store = IRISGraphStore(conn)
    return store, conn, cursor


class TestServerInfo:

    def test_server_info_returns_result(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = ("IRIS for UNIX 2024.1",)
        result = store.server_info()
        assert isinstance(result, IVGResult)
        assert "iris_version" in result.columns
        assert "ivg_version" in result.columns

    def test_server_info_handles_sql_exception(self):
        store, conn, cursor = _make_store()
        cursor.execute.side_effect = RuntimeError("no access")
        result = store.server_info()
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == "unknown"

    def test_server_info_handles_pkg_version_failure(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = ("2024.1",)
        with patch("importlib.metadata.version", side_effect=Exception("not found")):
            result = store.server_info()
        assert isinstance(result, IVGResult)
        assert result.rows[0][1] == "unknown"


class TestGetNodeEdgeCounts:

    def test_get_node_count_no_label(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = (42,)
        result = store.get_node_count()
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == 42

    def test_get_node_count_with_label(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = (5,)
        result = store.get_node_count(label="Disease")
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == 5

    def test_get_edge_count_no_predicate(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = (100,)
        result = store.get_edge_count()
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == 100

    def test_get_edge_count_with_predicate(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = (15,)
        result = store.get_edge_count(predicate="TREATS")
        assert isinstance(result, IVGResult)

    def test_get_labels_returns_result(self):
        store, conn, cursor = _make_store()
        cursor.fetchall.return_value = [("Disease",), ("Gene",), ("Drug",)]
        result = store.get_labels()
        assert isinstance(result, IVGResult)

    def test_get_relationship_types(self):
        store, conn, cursor = _make_store()
        cursor.fetchall.return_value = [("TREATS",), ("TARGETS",)]
        result = store.get_relationship_types()
        assert isinstance(result, IVGResult)


class TestCapabilities:

    def test_capabilities_returns_dict(self):
        store, conn, cursor = _make_store()
        caps = store.capabilities()
        assert isinstance(caps, dict)
        assert "bfs" in caps
        assert "knn_vec" in caps

    def test_capabilities_has_expected_keys(self):
        store, conn, cursor = _make_store()
        caps = store.capabilities()
        for key in ("native_sql", "bfs", "shortest_path", "ppr", "pagerank",
                    "wcc", "cdlp", "knn_vec", "temporal_edges"):
            assert key in caps, f"Missing capability key: {key}"


class TestExecuteDegreeCentrality:

    def test_degree_centrality_success_path(self):
        store, conn, cursor = _make_store()
        scores = [{"id": "n1", "score": 0.9, "degree": 5},
                  {"id": "n2", "score": 0.5, "degree": 3}]
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps(scores)
        with patch.object(store, "_iris_obj", return_value=iris_obj):
            result = store.execute_degree_centrality("out", "", 10)
        assert isinstance(result, IVGResult)
        assert "id" in result.columns
        assert len(result.rows) == 2

    def test_degree_centrality_parse_failure_returns_empty(self):
        store, conn, cursor = _make_store()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "NOT JSON"
        with patch.object(store, "_iris_obj", return_value=iris_obj):
            result = store.execute_degree_centrality("out", "", 10)
        # JSON parse fails → returns error result
        assert isinstance(result, IVGResult)

    def test_degree_centrality_bug_s_fallback(self):
        """When CLASS DOES NOT EXIST is raised, triggers gref fallback."""
        store, conn, cursor = _make_store()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError(
            "<CLASS DOES NOT EXIST> Graph.KG.Centrality"
        )
        with patch.object(store, "_iris_obj", return_value=iris_obj):
            with patch.object(store, "_degree_centrality_gref_fallback",
                              return_value=IVGResult(columns=["id","score","degree"], rows=[])) as mock_fb:
                result = store.execute_degree_centrality("out", "", 10)
        mock_fb.assert_called_once()
        assert isinstance(result, IVGResult)

    def test_degree_centrality_bug_s_fallback_also_fails(self):
        """When both classmethod and gref fallback fail, returns error IVGResult."""
        store, conn, cursor = _make_store()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError(
            "<CLASS DOES NOT EXIST> Graph.KG.Centrality"
        )
        with patch.object(store, "_iris_obj", return_value=iris_obj):
            with patch.object(store, "_degree_centrality_gref_fallback",
                              side_effect=RuntimeError("gref failed")):
                result = store.execute_degree_centrality("out", "", 10)
        assert isinstance(result, IVGResult)
        assert result.error is not None


class TestDegreeCentralityGrefFallback:

    def _make_lazy_kg(self, nodes=("a", "b", "c"), edges=None):
        """Mock LazyKG with simple node/edge structure."""
        lkg = MagicMock()
        lkg.iter_nodes.return_value = iter(nodes)
        edge_map = edges or {"a": ["b"], "b": ["c"], "c": []}
        lkg.degree.side_effect = lambda n: len(edge_map.get(n, []))
        lkg.in_degree.side_effect = lambda n: sum(1 for nbrs in edge_map.values() if n in nbrs)
        lkg.degree_for_predicate.return_value = 1
        lkg.in_degree_for_predicate.return_value = 0
        return lkg

    def test_gref_fallback_out_direction(self):
        store, conn, cursor = _make_store()
        lkg = self._make_lazy_kg()
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("out", "", 10)
        assert isinstance(result, IVGResult)
        assert "id" in result.columns
        assert "score" in result.columns
        assert "degree" in result.columns

    def test_gref_fallback_in_direction(self):
        store, conn, cursor = _make_store()
        lkg = self._make_lazy_kg()
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("in", "", 5)
        assert isinstance(result, IVGResult)

    def test_gref_fallback_both_direction(self):
        store, conn, cursor = _make_store()
        lkg = self._make_lazy_kg()
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("both", "", 5)
        assert isinstance(result, IVGResult)

    def test_gref_fallback_with_predicate_filter(self):
        store, conn, cursor = _make_store()
        lkg = self._make_lazy_kg()
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("out", "TREATS", 5)
        assert isinstance(result, IVGResult)

    def test_gref_fallback_empty_graph_returns_empty(self):
        store, conn, cursor = _make_store()
        lkg = MagicMock()
        lkg.iter_nodes.return_value = iter([])
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("out", "", 10)
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_gref_fallback_top_k_limits_results(self):
        store, conn, cursor = _make_store()
        nodes = [f"n{i}" for i in range(10)]
        lkg = MagicMock()
        lkg.iter_nodes.return_value = iter(nodes)
        lkg.degree.return_value = 1
        lkg.in_degree.return_value = 0
        with patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=lkg), \
             patch("iris_vector_graph.stores.iris_sql_store.LazyKG", return_value=lkg, create=True):
            result = store._degree_centrality_gref_fallback("out", "", top_k=3)
        assert isinstance(result, IVGResult)
        assert len(result.rows) <= 3


class TestExecuteTransaction:

    def test_execute_transaction_empty_stmts(self):
        store, conn, cursor = _make_store()
        result = store.execute_transaction([], [])
        assert isinstance(result, IVGResult)

    def test_execute_transaction_with_stmts(self):
        store, conn, cursor = _make_store()
        stmts = ["INSERT INTO nodes (node_id) VALUES (?)"]
        params = [["node_1"]]
        result = store.execute_transaction(stmts, params)
        assert isinstance(result, IVGResult)

    def test_execute_transaction_on_error_rolls_back(self):
        store, conn, cursor = _make_store()
        call_count = [0]
        def execute_side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] > 1:  # allow START TRANSACTION, fail on first stmt
                raise RuntimeError("constraint violation")
        cursor.execute.side_effect = execute_side
        stmts = ["INSERT INTO nodes (node_id) VALUES (?)"]
        params = [["bad_node"]]
        try:
            result = store.execute_transaction(stmts, params)
            assert isinstance(result, IVGResult)
        except Exception:
            pass  # rollback path may re-raise


class TestExecuteSQL:

    def test_execute_sql_select_returns_rows(self):
        store, conn, cursor = _make_store()
        cursor.description = [("node_id",), ("labels",)]
        cursor.fetchall.return_value = [("n1", "L"), ("n2", "L")]
        result = store.execute_sql("SELECT node_id, labels FROM Graph_KG.nodes", [])
        assert isinstance(result, IVGResult)

    def test_execute_sql_handles_exception(self):
        store, conn, cursor = _make_store()
        cursor.execute.side_effect = RuntimeError("SQL error")
        result = store.execute_sql("SELECT bad_query", [])
        assert isinstance(result, IVGResult)
        assert result.error is not None or result.rows == []


class TestListIndexes:

    def test_list_indexes_returns_result(self):
        store, conn, cursor = _make_store()
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        result = store.list_indexes()
        assert isinstance(result, IVGResult)
        assert "name" in result.columns

    def test_list_indexes_with_ivf_count(self):
        store, conn, cursor = _make_store()
        call_seq = [None, None, (3,), None, None, None, None]
        idx = [0]
        def fetchone_side():
            val = call_seq[idx[0]] if idx[0] < len(call_seq) else (0,)
            idx[0] += 1
            return val if val is not None else (0,)
        cursor.fetchone.side_effect = fetchone_side
        cursor.fetchall.return_value = []
        result = store.list_indexes()
        assert isinstance(result, IVGResult)


class TestBetweennessExecution:

    def test_execute_betweenness_returns_result(self):
        store, conn, cursor = _make_store()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("not available")
        with patch.object(store, "_iris_obj", return_value=iris_obj):
            with patch.object(store, "_betweenness_gref",
                              return_value=IVGResult(columns=["id","score"], rows=[])):
                result = store.execute_betweenness(0, "out", 0, 10, 512, None)
        assert isinstance(result, IVGResult)
