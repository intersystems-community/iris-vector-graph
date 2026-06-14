"""
Unit tests for dbapi_utils.py — targeting miss lines 54-55, 61-70, 82, 91, 121-122,
145-146, 165-167, 203-204, 230-235, 264.
No live IRIS needed — cursor is mocked.
"""
import os
import pytest
from unittest.mock import MagicMock, patch


class TestNormalizeVector:

    def test_none_input_returns_none(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        assert normalize_vector(None, 4) is None

    def test_list_input_normalized(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([1.0, 2.0, 3.0, 4.0], 4)
        assert result == [1.0, 2.0, 3.0, 4.0]

    def test_list_padded_when_short(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([1.0, 2.0], 4)
        assert result == [1.0, 2.0, 0.0, 0.0]

    def test_list_truncated_when_long(self):
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([1.0, 2.0, 3.0, 4.0, 5.0], 4)
        assert result == [1.0, 2.0, 3.0, 4.0]

    def test_empty_list_returns_none(self):
        """Line 82: empty normalized → None."""
        from iris_vector_graph.dbapi_utils import normalize_vector
        result = normalize_vector([], 4)
        assert result is None

    def test_non_finite_coerced_to_zero(self):
        """Lines 86-87: NaN/Inf replaced with 0.0."""
        from iris_vector_graph.dbapi_utils import normalize_vector
        import math
        result = normalize_vector([1.0, float("nan"), float("inf"), 4.0], 4)
        assert result[1] == 0.0
        assert result[2] == 0.0

    def test_non_finite_with_debug_env(self):
        """Line 91: IRIS_VECTOR_DEBUG env var triggers warning log."""
        from iris_vector_graph.dbapi_utils import normalize_vector
        import math
        with patch.dict(os.environ, {"IRIS_VECTOR_DEBUG": "1"}):
            result = normalize_vector([1.0, float("nan"), 3.0, 4.0], 4)
        assert result[1] == 0.0

    def test_numpy_array_normalized(self):
        """Lines 51-53: numpy array path."""
        try:
            import numpy as np
            from iris_vector_graph.dbapi_utils import normalize_vector
            arr = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
            result = normalize_vector(arr, 4)
            assert result == [1.0, 2.0, 3.0, 4.0]
        except ImportError:
            pytest.skip("numpy not available")

    def test_numpy_import_error_falls_through(self):
        """Lines 54-55: numpy ImportError → swallowed, falls to next."""
        import builtins
        real_import = builtins.__import__

        def import_blocker(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("numpy blocked for test")
            return real_import(name, *args, **kwargs)

        from iris_vector_graph.dbapi_utils import normalize_vector
        with patch.object(builtins, "__import__", side_effect=import_blocker):
            result = normalize_vector([1.0, 2.0, 3.0, 4.0], 4)
        assert result is not None

    def test_torch_tensor_normalized(self):
        """Lines 59-68: torch tensor path."""
        try:
            import torch
            from iris_vector_graph.dbapi_utils import normalize_vector
            t = torch.tensor([1.0, 2.0, 3.0, 4.0])
            result = normalize_vector(t, 4)
            assert result == [1.0, 2.0, 3.0, 4.0]
        except ImportError:
            pytest.skip("torch not available")


class TestInsertVector:

    def test_cursor_none_returns_false(self):
        from iris_vector_graph.dbapi_utils import insert_vector
        assert insert_vector(None, "T", "emb", [1.0]*4, 4, {"id": "n1"}) is False

    def test_normalize_fails_returns_false(self):
        """Lines 121-122: normalize_vector returns None → False."""
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        with patch("iris_vector_graph.dbapi_utils.normalize_vector", return_value=None):
            result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"})
        assert result is False

    def test_successful_insert_returns_true(self):
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.return_value = None
        result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"})
        assert result is True

    def test_non_unique_exception_no_upsert_returns_false(self):
        """Lines 144-146: non-unique exception with upsert=False → False."""
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("some error")
        result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"}, upsert=False)
        assert result is False

    def test_non_unique_exception_with_upsert_returns_false(self):
        """Lines 147-149: non-UNIQUE exception with upsert=True → logged, False."""
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection error")
        result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"}, upsert=True)
        assert result is False

    def test_unique_exception_upsert_succeeds(self):
        """Lines 151-164: UNIQUE exception → upsert UPDATE path."""
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        call_count = [0]
        def execute_side(sql, params):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("UNIQUE constraint violation")
        cursor.execute.side_effect = execute_side
        result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"},
                               additional_columns={"name": "test"}, upsert=True)
        assert result is True

    def test_unique_exception_upsert_also_fails(self):
        """Lines 165-167: upsert UPDATE also fails → False."""
        from iris_vector_graph.dbapi_utils import insert_vector
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("UNIQUE constraint")
        result = insert_vector(cursor, "T", "emb", [1.0]*4, 4, {"id": "n1"}, upsert=True)
        assert result is False


class TestCreateHnswIndex:

    def test_success_returns_true(self):
        from iris_vector_graph.dbapi_utils import create_hnsw_index
        cursor = MagicMock()
        cursor.execute.return_value = None
        assert create_hnsw_index(cursor, "T", "emb", 4) is True

    def test_already_exists_returns_true(self):
        """Lines 201-202: ALREADY EXISTS error → True."""
        from iris_vector_graph.dbapi_utils import create_hnsw_index
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("ALREADY EXISTS")
        assert create_hnsw_index(cursor, "T", "emb", 4) is True

    def test_other_exception_returns_false(self):
        """Lines 203-204: other exception → False."""
        from iris_vector_graph.dbapi_utils import create_hnsw_index
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("permission denied")
        assert create_hnsw_index(cursor, "T", "emb", 4) is False


class TestCreateIvfflatIndex:

    def test_success_returns_true(self):
        from iris_vector_graph.dbapi_utils import create_ivfflat_index
        cursor = MagicMock()
        cursor.execute.return_value = None
        assert create_ivfflat_index(cursor, "T", "emb", 4) is True

    def test_duplicate_returns_true(self):
        """Line 232-233: DUPLICATE error → True."""
        from iris_vector_graph.dbapi_utils import create_ivfflat_index
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("DUPLICATE index")
        assert create_ivfflat_index(cursor, "T", "emb", 4) is True

    def test_other_exception_returns_false(self):
        """Line 234-235: other exception → False."""
        from iris_vector_graph.dbapi_utils import create_ivfflat_index
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("disk full")
        assert create_ivfflat_index(cursor, "T", "emb", 4) is False


class TestVectorSimilaritySearch:

    def test_basic_search_returns_results(self):
        from iris_vector_graph.dbapi_utils import vector_similarity_search
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n1", 0.95), ("n2", 0.80)]
        cursor.description = [("id", None), ("score", None)]
        results = vector_similarity_search(cursor, "T", "emb", [1.0]*4, top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == "n1"

    def test_with_return_columns(self):
        """Line 264: extra_cols path when return_columns given."""
        from iris_vector_graph.dbapi_utils import vector_similarity_search
        cursor = MagicMock()
        cursor.fetchall.return_value = [("n1", 0.95, "Gene")]
        cursor.description = [("id", None), ("score", None), ("label", None)]
        results = vector_similarity_search(
            cursor, "T", "emb", [1.0]*4, top_k=1, return_columns=["label"]
        )
        assert len(results) == 1
        assert "label" in results[0]
