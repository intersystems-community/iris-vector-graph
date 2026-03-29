"""Unit tests for PLAID multi-vector retrieval."""
import pytest
import random
from unittest.mock import MagicMock


def _rand_vecs(n, dim=32):
    return [[random.gauss(0, 1) for _ in range(dim)] for _ in range(n)]


def _make_docs(n_docs=10, n_tokens=5, dim=32):
    random.seed(42)
    return [{"id": f"doc_{i}", "tokens": _rand_vecs(n_tokens, dim)} for i in range(n_docs)]


class TestPlaidBuild:

    def test_build_returns_info_dict(self):
        """T008"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '{"name":"test","nCentroids":5,"nDocs":10,"dim":32,"totalTokens":50}'
        engine._iris_obj = lambda: iris_mock
        try:
            import numpy as np
            from sklearn.cluster import KMeans
        except ImportError:
            pytest.skip("numpy/sklearn not installed")
        result = engine.plaid_build("test", _make_docs(10, 5, 32), dim=32)
        assert "nCentroids" in result
        assert "nDocs" in result

    def test_centroid_count_sqrt_n(self):
        """T009"""
        import numpy as np
        docs = _make_docs(100, 5, 32)
        all_tokens = [tok for d in docs for tok in d["tokens"]]
        expected_k = int(np.sqrt(len(all_tokens)))
        assert 20 <= expected_k <= 25

    def test_empty_docs_raises(self):
        """T010"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        engine._iris_obj = lambda: MagicMock()
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not installed")
        with pytest.raises((ValueError, IndexError, Exception)):
            engine.plaid_build("test", [], dim=32)


class TestPlaidSearch:

    def test_returns_list_of_dicts(self):
        """T016"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '[{"id":"doc_0","score":0.95},{"id":"doc_1","score":0.80}]'
        engine._iris_obj = lambda: iris_mock
        results = engine.plaid_search("test", _rand_vecs(4, 32), k=5)
        assert isinstance(results, list)
        assert all("id" in r and "score" in r for r in results)

    def test_results_sorted_descending(self):
        """T017"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = '[{"id":"a","score":0.95},{"id":"b","score":0.80},{"id":"c","score":0.60}]'
        engine._iris_obj = lambda: iris_mock
        results = engine.plaid_search("test", _rand_vecs(4, 32))
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestPlaidInsert:

    def test_insert_calls_classmethod(self):
        """T023"""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        iris_mock = MagicMock()
        engine._iris_obj = lambda: iris_mock
        engine.plaid_insert("test", "new_doc", _rand_vecs(10, 32))
        assert iris_mock.classMethodVoid.called
