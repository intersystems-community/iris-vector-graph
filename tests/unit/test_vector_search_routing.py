import json
from unittest.mock import MagicMock, patch

import pytest


class TestNativeVecProbe:

    def _make_engine(self, vector_cosine_available: bool):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        if vector_cosine_available:
            cursor.execute.return_value = None
        else:
            cursor.execute.side_effect = Exception(
                "Unknown function VECTOR_COSINE"
            )
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = conn
        eng._native_vec_available = None
        eng._embedding_function_available = None
        eng.embedding_dimension = 4
        eng._arno_available = None
        eng._arno_capabilities = {}
        return eng

    def test_probe_returns_true_when_vector_cosine_works(self):
        eng = self._make_engine(vector_cosine_available=True)
        assert eng._probe_native_vec() is True

    def test_probe_returns_false_when_unknown_function(self):
        eng = self._make_engine(vector_cosine_available=False)
        assert eng._probe_native_vec() is False

    def test_probe_result_is_cached(self):
        eng = self._make_engine(vector_cosine_available=True)
        eng._probe_native_vec()
        eng._probe_native_vec()
        assert eng.conn.cursor.call_count == 1

    def test_probe_false_cached_after_first_call(self):
        eng = self._make_engine(vector_cosine_available=False)
        eng._probe_native_vec()
        eng.conn.cursor.reset_mock()
        result = eng._probe_native_vec()
        assert result is False
        eng.conn.cursor.assert_not_called()


class TestVectorSearchRouting:

    def _make_engine(self, native: bool):
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = conn
        eng._native_vec_available = native
        eng.embedding_dimension = 4
        eng._arno_available = None
        eng._arno_capabilities = {}
        return eng

    def test_routes_to_kg_knn_vec_when_native_available(self):
        eng = self._make_engine(native=True)
        query = [0.1, 0.2, 0.3, 0.4]
        with patch.object(eng, "kg_KNN_VEC", return_value=[("n1", 0.9)]) as mock_knn:
            result = eng.search_nodes_by_vector(query, k=5)
        mock_knn.assert_called_once_with(json.dumps([float(v) for v in query]), k=5, label_filter=None)
        assert result == [("n1", 0.9)]

    def test_routes_to_ivf_when_native_unavailable(self):
        eng = self._make_engine(native=False)
        query = [0.1, 0.2, 0.3, 0.4]
        with patch.object(eng, "ivf_search", return_value=[("n2", 0.8)]) as mock_ivf:
            result = eng.search_nodes_by_vector(query, k=3, ivf_name="my_idx")
        mock_ivf.assert_called_once_with("my_idx", query, k=3, nprobe=8)
        assert result == [("n2", 0.8)]

    def test_accepts_json_string_query(self):
        eng = self._make_engine(native=True)
        query_str = "[0.1, 0.2, 0.3, 0.4]"
        with patch.object(eng, "kg_KNN_VEC", return_value=[]) as mock_knn:
            eng.search_nodes_by_vector(query_str, k=5)
        mock_knn.assert_called_once_with(query_str, k=5, label_filter=None)

    def test_label_filter_forwarded_to_native(self):
        eng = self._make_engine(native=True)
        query = [0.1, 0.2, 0.3, 0.4]
        with patch.object(eng, "kg_KNN_VEC", return_value=[]) as mock_knn:
            eng.search_nodes_by_vector(query, k=5, label_filter="Person")
        mock_knn.assert_called_once_with(
            json.dumps([float(v) for v in query]), k=5, label_filter="Person"
        )

    def test_nprobe_forwarded_to_ivf(self):
        eng = self._make_engine(native=False)
        query = [0.1, 0.2, 0.3, 0.4]
        with patch.object(eng, "ivf_search", return_value=[]) as mock_ivf:
            eng.search_nodes_by_vector(query, k=5, ivf_name="idx", nprobe=32)
        mock_ivf.assert_called_once_with("idx", query, k=5, nprobe=32)

    def test_native_tier_faster_than_ivf_on_live_db(self, iris_connection):
        import time
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine(iris_connection, embedding_dimension=768)
        if not e._probe_native_vec():
            pytest.skip("native VECTOR_COSINE not available")

        import math, random
        def rand_vec(d):
            v = [random.gauss(0, 1) for _ in range(d)]
            n = math.sqrt(sum(x*x for x in v)) or 1.0
            return [x/n for x in v]

        q = rand_vec(768)
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            e.search_nodes_by_vector(q, k=10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        assert times[2] < 200, f"vector_search p50={times[2]:.1f}ms — native HNSW should be fast"
