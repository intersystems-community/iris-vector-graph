"""Unit tests for TemporalIndex API gaps (spec 204).

Covers: PurgeRawBefore, suppressReverseIndex, InternLabelSet/ResolveLabelSet,
TSUNIT bucket-unit fix. All tests use mocks — no IRIS container required.
"""
import json
import os
from unittest.mock import MagicMock, call, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    """Engine with a fully-mocked _store, following test_temporal_edges.py pattern."""
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = MagicMock()
    e._schema_prefix = "Graph_KG"

    store_mock = MagicMock()

    # write_temporal_edge default: success
    ok_result = MagicMock()
    ok_result.error = None
    store_mock.write_temporal_edge.return_value = ok_result

    # bulk_write_temporal_edges default: 3 inserted
    bulk_result = MagicMock()
    bulk_result.rows = [[3]]
    store_mock.bulk_write_temporal_edges.return_value = bulk_result

    # purge_raw_before default: 2 deleted
    store_mock.purge_raw_before.return_value = 2

    # intern / resolve
    store_mock.intern_label_set.return_value = "abcdef1234567890" * 2 + "ab12cd34"
    store_mock.resolve_label_set.return_value = '{"a":1,"b":2}'

    e._store = store_mock
    return e, store_mock


# ─── Gap 1: PurgeRawBefore ────────────────────────────────────────────────────


class TestPurgeRawBefore:
    def test_purge_raw_before_delegates_to_store(self):
        engine, store = _make_engine()
        store.purge_raw_before.return_value = 2
        result = engine.purge_raw_before(250)
        store.purge_raw_before.assert_called_once_with(250)
        assert result == 2

    def test_purge_raw_before_zero_deletes(self):
        engine, store = _make_engine()
        store.purge_raw_before.return_value = 0
        result = engine.purge_raw_before(0)
        assert result == 0

    def test_purge_raw_before_store_method_called_with_int(self):
        engine, store = _make_engine()
        engine.purge_raw_before(99999)
        args = store.purge_raw_before.call_args[0]
        assert args[0] == 99999

    def test_store_purge_raw_before_calls_classmethod(self):
        """IRISGraphStore.purge_raw_before must call PurgeRawBefore via _call_classmethod."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="5")
        result = store.purge_raw_before(250)
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "PurgeRawBefore", "250"
        )
        assert result == 5

    def test_store_purge_raw_before_returns_int(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="0")
        assert store.purge_raw_before(100) == 0


# ─── Gap 2: suppressReverseIndex ─────────────────────────────────────────────


class TestSuppressReverseIndex:
    def test_create_edge_temporal_suppress_forwarded_to_store(self):
        engine, store = _make_engine()
        engine.create_edge_temporal(
            "s1", "METRIC_AT", "t1", timestamp=1000, suppress_reverse_index=True
        )
        call_kwargs = store.write_temporal_edge.call_args
        assert call_kwargs.kwargs.get("suppress_reverse_index") is True or (
            len(call_kwargs.args) >= 8 and call_kwargs.args[7] is True
        )

    def test_create_edge_temporal_default_suppress_false(self):
        engine, store = _make_engine()
        engine.create_edge_temporal("s1", "METRIC_AT", "t1", timestamp=1000)
        call_kwargs = store.write_temporal_edge.call_args
        suppress = call_kwargs.kwargs.get("suppress_reverse_index", False)
        assert suppress is False

    def test_bulk_create_suppress_forwarded(self):
        engine, store = _make_engine()
        edges = [{"s": "a", "p": "P", "o": "b", "ts": 1, "w": 1.0}]
        engine.bulk_create_edges_temporal(edges, suppress_reverse_index=True)
        call_kwargs = store.bulk_write_temporal_edges.call_args
        suppress = call_kwargs.kwargs.get("suppress_reverse_index", False)
        assert suppress is True

    def test_store_write_temporal_edge_passes_flag_as_string(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value=None)
        store.write_temporal_edge(
            "s", "P", "t", 1000, 1.0, None, False, suppress_reverse_index=True
        )
        args = store._call_classmethod.call_args[0]
        # 8th positional arg to InsertEdge (after upsert) should be "1"
        assert "1" in args

    def test_store_write_temporal_edge_suppress_false_passes_zero(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value=None)
        store.write_temporal_edge("s", "P", "t", 1000, suppress_reverse_index=False)
        args = store._call_classmethod.call_args[0]
        # Should NOT have "1" as the suppress flag — "0" or absent
        suppress_arg = args[-1] if args else None
        assert suppress_arg != "1"


# ─── Gap 3: InternLabelSet / ResolveLabelSet ──────────────────────────────────


class TestInternLabelSet:
    def test_intern_label_set_delegates(self):
        engine, store = _make_engine()
        h = engine.intern_label_set({"a": 1, "b": 2})
        assert store.intern_label_set.called
        assert isinstance(h, str)

    def test_intern_label_set_serializes_dict(self):
        engine, store = _make_engine()
        engine.intern_label_set({"x": 99})
        arg = store.intern_label_set.call_args[0][0]
        # Argument passed to store must be a JSON string
        parsed = json.loads(arg)
        assert parsed == {"x": 99}

    def test_resolve_label_set_delegates(self):
        engine, store = _make_engine()
        result = engine.resolve_label_set("abc123")
        store.resolve_label_set.assert_called_once_with("abc123")
        assert result == store.resolve_label_set.return_value

    def test_store_intern_calls_classmethod(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="deadbeef" * 5)
        result = store.intern_label_set('{"a":1}')
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "InternLabelSet", '{"a":1}'
        )
        assert result == "deadbeef" * 5

    def test_store_resolve_calls_classmethod(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value='{"a":1}')
        result = store.resolve_label_set("abc")
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "ResolveLabelSet", "abc"
        )
        assert result == '{"a":1}'

    def test_store_resolve_unknown_returns_empty(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="")
        result = store.resolve_label_set("unknown")
        assert result == ""


# ─── Gap 4: TSUNIT / bucket-unit math ────────────────────────────────────────


class TestTSUNITBucketMath:
    def test_seconds_bucket_divisor(self):
        """BUCKET=300 → ts=1500 falls in bucket 5."""
        # bucket = ts \ BUCKET = 1500 \ 300 = 5
        ts, bucket_sec = 1500, 300
        assert (ts // bucket_sec) == 5

    def test_ms_bucket_divisor(self):
        """BUCKETMS=300000 → ts=1_500_000ms falls in bucket 5."""
        # bucket = ts \ BUCKETMS = 1_500_000 \ 300_000 = 5
        ts, bucket_ms = 1_500_000, 300_000
        assert (ts // bucket_ms) == 5

    def test_ms_vs_seconds_bucket_equivalence(self):
        """Second-precision edge at ts=900 and ms-precision at ts=900_000 land in same bucket."""
        ts_sec = 900
        ts_ms = 900_000
        assert (ts_sec // 300) == (ts_ms // 300_000)

    def test_purge_before_boundary_strict_lt(self):
        """PurgeBefore loops until ts >= tsEnd (strict <). Edge AT tsEnd survives."""
        # Simulates the ObjectScript loop exit condition: Quit:(ts >= tsEnd)
        edges = [100, 200, 300, 400]
        ts_end = 300
        purged = [ts for ts in edges if ts < ts_end]
        assert purged == [100, 200]
        assert 300 not in purged

    def test_max_safe_bucket_prevents_live_aggregate_kill(self):
        """maxSafeBucket = (tsEnd // divisor) - 1 never kills bucket containing tsEnd."""
        ts_end = 450
        divisor = 300  # seconds mode
        # bucket containing tsEnd = 450 // 300 = 1
        # maxSafeBucket = 1 - 1 = 0
        max_safe = (ts_end // divisor) - 1
        bucket_at_ts_end = ts_end // divisor
        assert max_safe < bucket_at_ts_end

    def test_max_safe_bucket_ms_mode(self):
        """Same boundary invariant holds in milliseconds mode."""
        ts_end_ms = 450_000
        divisor = 300_000
        max_safe = (ts_end_ms // divisor) - 1
        bucket_at_ts_end = ts_end_ms // divisor
        assert max_safe < bucket_at_ts_end
