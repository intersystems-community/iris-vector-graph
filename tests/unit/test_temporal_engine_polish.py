"""Unit tests for spec 207: temporal engine polish.

Covers A11 (DeleteResult + dynamic chunking), A13 (InsertEdge mode param),
A10 (BulkDeleteAdjacency wrapper), A8.2 (GetVelocity now_ts).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table(name: str, prefix: str = "Graph_KG") -> str:
    return f"{prefix}.{name}"


def _make_mixin_with_cursor(execute_side_effects=None):
    """Build a NodesEdgesMixin with a mock connection/cursor."""
    from iris_vector_graph._engine.nodes_edges import NodesEdgesMixin

    mixin = NodesEdgesMixin.__new__(NodesEdgesMixin)
    mixin._t = lambda name: _table(name)
    mixin._nkg_dirty = False

    cursor = MagicMock()
    if execute_side_effects is not None:
        cursor.execute.side_effect = execute_side_effects

    conn = MagicMock()
    conn.cursor.return_value = cursor
    mixin.conn = conn
    return mixin, cursor


# ---------------------------------------------------------------------------
# Phase 3 — US2: DeleteResult + dynamic chunking
# ---------------------------------------------------------------------------


class TestBulkDeleteNodesDeleteResult:
    def test_returns_delete_result_namedtuple(self):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        mixin, _ = _make_mixin_with_cursor()
        result = mixin.bulk_delete_nodes(["a", "b", "c"])
        assert isinstance(result, DeleteResult), (
            f"Expected DeleteResult, got {type(result)}"
        )

    def test_failed_count_on_batch_exception(self):
        """Batch 2 raises; batch 1 succeeds. deleted==3, failed==3."""
        from iris_vector_graph._engine.nodes_edges import DeleteResult, _batch_size_for

        batch1 = ["a", "b", "c"]
        batch2 = ["x", "y", "z"]
        all_ids = batch1 + batch2

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            # Each batch fires 8 cursor.execute calls; raise on the 9th (first of batch 2)
            if call_count[0] == 9:
                raise RuntimeError("SQLCODE -202")

        mixin, cursor = _make_mixin_with_cursor(execute_side_effects=side_effect)
        # Force batch size = 3 so we get exactly 2 batches
        with patch(
            "iris_vector_graph._engine.nodes_edges._batch_size_for",
            return_value=3,
        ):
            result = mixin.bulk_delete_nodes(all_ids)

        assert isinstance(result, DeleteResult)
        assert result.deleted == len(batch1)
        assert result.failed == len(batch2)

    def test_empty_returns_zero_zero(self):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        mixin, _ = _make_mixin_with_cursor()
        result = mixin.bulk_delete_nodes([])
        assert result == DeleteResult(0, 0)

    def test_dynamic_chunk_size_short_ids(self):
        from iris_vector_graph._engine.nodes_edges import _batch_size_for

        result = _batch_size_for(["a"] * 5)
        assert result > 100, f"Expected large batch for short IDs, got {result}"

    def test_dynamic_chunk_size_long_ids(self):
        from iris_vector_graph._engine.nodes_edges import _batch_size_for

        short_batch = _batch_size_for(["x"] * 20)
        long_ids = ["x" * 60] * 20
        long_batch = _batch_size_for(long_ids)
        assert long_batch < short_batch, (
            f"Long IDs should produce smaller batch ({long_batch}) than short ({short_batch})"
        )

    def test_int_compat_backward(self):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        assert int(DeleteResult(5, 0)) == 5

    def test_bool_compat_true(self):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        assert bool(DeleteResult(3, 0)) is True

    def test_bool_compat_false(self):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        assert bool(DeleteResult(0, 0)) is False


# ---------------------------------------------------------------------------
# Phase 4 — US1: InsertEdge mode param
# ---------------------------------------------------------------------------


class TestInsertEdgeModeParam:
    def _make_engine_with_mock_store(self):
        """Build a minimal TemporalMixin with mocked _store."""
        from iris_vector_graph._engine.temporal import TemporalMixin

        eng = TemporalMixin.__new__(TemporalMixin)
        store = MagicMock()
        store.write_temporal_edge.return_value = MagicMock(error=None)
        eng._store = store
        eng.conn = MagicMock()
        eng._t = lambda name: _table(name)
        return eng, store

    def test_mode_skip_passes_to_classmethod(self):
        eng, store = self._make_engine_with_mock_store()
        eng.create_edge_temporal("s", "p", "t", timestamp=1000, mode="skip")
        _, kwargs = store.write_temporal_edge.call_args
        assert kwargs.get("mode") == "skip" or "skip" in store.write_temporal_edge.call_args[0], (
            f"Expected mode='skip' forwarded. call_args={store.write_temporal_edge.call_args}"
        )

    def test_mode_update_passes_to_classmethod(self):
        eng, store = self._make_engine_with_mock_store()
        eng.create_edge_temporal("s", "p", "t", timestamp=1000, mode="update")
        _, kwargs = store.write_temporal_edge.call_args
        assert kwargs.get("mode") == "update" or "update" in store.write_temporal_edge.call_args[0]

    def test_upsert_true_backward_compat(self):
        """upsert=True still accepted without error."""
        eng, store = self._make_engine_with_mock_store()
        eng.create_edge_temporal("s", "p", "t", timestamp=1000, upsert=True)
        store.write_temporal_edge.assert_called_once()

    def test_upsert_false_backward_compat(self):
        """upsert=False still accepted without error."""
        eng, store = self._make_engine_with_mock_store()
        eng.create_edge_temporal("s", "p", "t", timestamp=1000, upsert=False)
        store.write_temporal_edge.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 5 — US3: BulkDeleteAdjacency wrapper
# ---------------------------------------------------------------------------


class TestBulkDeleteAdjacency:
    def _make_engine_with_mock_iris(self, return_value=3):
        from iris_vector_graph._engine.nodes_edges import NodesEdgesMixin

        eng = NodesEdgesMixin.__new__(NodesEdgesMixin)
        eng.conn = MagicMock()
        eng._t = lambda name: _table(name)
        eng._nkg_dirty = False

        iris_obj = MagicMock()
        iris_obj.classMethodValue = MagicMock(return_value=return_value)
        eng._iris_obj = lambda: iris_obj
        return eng, iris_obj

    def test_wrapper_passes_json_to_classmethod(self):
        eng, iris_obj = self._make_engine_with_mock_iris()
        node_ids = ["a", "b", "c"]
        eng.bulk_delete_adjacency(node_ids)
        iris_obj.classMethodValue.assert_called_once()
        args = iris_obj.classMethodValue.call_args[0]
        assert args[0] == "Graph.KG.EdgeScan"
        assert args[1] == "BulkDeleteAdjacency"
        parsed = json.loads(args[2])
        assert parsed == node_ids

    def test_wrapper_returns_int(self):
        eng, _ = self._make_engine_with_mock_iris(return_value=2)
        result = eng.bulk_delete_adjacency(["a", "b"])
        assert isinstance(result, int)

    def test_empty_list_returns_zero(self):
        eng, _ = self._make_engine_with_mock_iris(return_value=0)
        result = eng.bulk_delete_adjacency([])
        assert result == 0


# ---------------------------------------------------------------------------
# Phase 6 — US4: GetVelocity now_ts forwarding
# ---------------------------------------------------------------------------


class TestGetVelocityNowTs:
    def _make_engine_with_mock_iris(self, return_value=5):
        from iris_vector_graph._engine.temporal import TemporalMixin

        eng = TemporalMixin.__new__(TemporalMixin)
        iris_obj = MagicMock()
        iris_obj.classMethodValue = MagicMock(return_value=return_value)
        eng._iris_obj = lambda: iris_obj
        return eng, iris_obj

    def test_now_ts_forwarded_to_classmethod(self):
        eng, iris_obj = self._make_engine_with_mock_iris()
        eng.get_edge_velocity("node1", window=300, now_ts=1_800_000_000)
        iris_obj.classMethodValue.assert_called_once()
        args = iris_obj.classMethodValue.call_args[0]
        assert 1_800_000_000 in args, (
            f"Expected now_ts=1_800_000_000 in classMethodValue args, got {args}"
        )

    def test_default_now_ts_zero(self):
        eng, iris_obj = self._make_engine_with_mock_iris()
        eng.get_edge_velocity("node1", window=300)
        args = iris_obj.classMethodValue.call_args[0]
        assert 0 in args, f"Expected default now_ts=0 in args, got {args}"
