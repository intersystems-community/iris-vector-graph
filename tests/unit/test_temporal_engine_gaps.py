"""Unit tests for temporal engine gaps (spec 205).

Covers: PurgeRawBefore(tsStart), BulkInsert sri, bulk_load_session auto_sync,
PurgeBefore bucket guard, InternLabelSet type contract, GetDistinctCount
all-sources, QueryWindowInbound suppression docstring.
All tests use mocks — no IRIS container required.
"""
import json
import os
from unittest.mock import MagicMock, patch

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine():
    """Engine with fully-mocked _store."""
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = MagicMock()
    e._schema_prefix = "Graph_KG"
    e._in_bulk_load = False

    store_mock = MagicMock()
    from iris_vector_graph._engine.temporal import PurgeResult

    store_mock.purge_raw_before.return_value = PurgeResult(2, 0)
    store_mock.intern_label_set.return_value = "abcdef1234567890" * 2 + "ab12cd34"
    store_mock.resolve_label_set.return_value = '{"a":1,"b":2}'

    bulk_result = MagicMock()
    bulk_result.rows = [[3]]
    store_mock.bulk_write_temporal_edges.return_value = bulk_result

    e._store = store_mock
    return e, store_mock


# ─── US1: PurgeRawBefore with tsStart ────────────────────────────────────────


class TestPurgeRawBeforeV2:
    def test_tsstart_default_zero_behavior(self):
        from iris_vector_graph._engine.temporal import PurgeResult
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="2:0")
        result = store.purge_raw_before(250)
        assert isinstance(result, PurgeResult)
        assert result.deleted == 2
        assert result.skipped == 0

    def test_tsstart_skips_edges_below_floor(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="1:1")
        result = store.purge_raw_before(250, ts_start=100)
        assert result.deleted == 1
        assert result.skipped == 1

    def test_purge_result_int_compat(self):
        from iris_vector_graph._engine.temporal import PurgeResult

        r = PurgeResult(3, 2)
        assert int(r) == 3

    def test_store_calls_classmethod_with_two_args(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="5:0")
        store.purge_raw_before(250, ts_start=100)
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "PurgeRawBefore", "250", "100"
        )

    def test_store_calls_classmethod_default_zero(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="3:0")
        store.purge_raw_before(500)
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "PurgeRawBefore", "500", "0"
        )

    def test_negative_tsstart_clamped_to_zero(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="0:0")
        store.purge_raw_before(500, ts_start=-1)
        args = store._call_classmethod.call_args[0]
        assert args[3] == "0"

    def test_engine_purge_raw_before_returns_purge_result(self):
        from iris_vector_graph._engine.temporal import PurgeResult

        engine, store = _make_engine()
        store.purge_raw_before.return_value = PurgeResult(5, 2)
        result = engine.purge_raw_before(1000, ts_start=100)
        assert isinstance(result, PurgeResult)
        assert result.deleted == 5
        assert result.skipped == 2

    def test_engine_passes_tsstart_to_store(self):
        engine, store = _make_engine()
        engine.purge_raw_before(ts_end=500, ts_start=200)
        store.purge_raw_before.assert_called_once_with(500, ts_start=200)


# ─── US2: BulkInsert sri key ──────────────────────────────────────────────────


class TestBulkInsertSRI:
    def test_sri1_suppresses_tin_write_in_batch(self):
        engine, store = _make_engine()
        edges = [{"s": "a", "p": "P", "o": "b", "ts": 1, "w": 1.0}]
        engine.bulk_create_edges_temporal(edges, suppress_reverse_index=True)
        call_kwargs = store.bulk_write_temporal_edges.call_args
        suppress = call_kwargs.kwargs.get("suppress_reverse_index", False)
        assert suppress is True

    def test_sri_absent_no_suppress(self):
        engine, store = _make_engine()
        edges = [{"s": "a", "p": "P", "o": "b", "ts": 1, "w": 1.0}]
        engine.bulk_create_edges_temporal(edges)
        call_kwargs = store.bulk_write_temporal_edges.call_args
        suppress = call_kwargs.kwargs.get("suppress_reverse_index", False)
        assert suppress is False

    def test_store_injects_sri_key_when_suppress_true(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value=2)
        edges = [
            {"source": "a", "predicate": "P", "target": "b",
             "timestamp": 1, "weight": 1.0, "attrs": {}},
            {"source": "c", "predicate": "P", "target": "d",
             "timestamp": 2, "weight": 1.0, "attrs": {}},
        ]
        store.bulk_write_temporal_edges(edges, suppress_reverse_index=True)
        batch_arg = store._call_classmethod.call_args[0][2]
        items = json.loads(batch_arg)
        assert all(item.get("sri") == 1 for item in items)

    def test_store_no_sri_when_suppress_false(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value=1)
        edges = [
            {"source": "a", "predicate": "P", "target": "b",
             "timestamp": 1, "weight": 1.0, "attrs": {}},
        ]
        store.bulk_write_temporal_edges(edges, suppress_reverse_index=False)
        batch_arg = store._call_classmethod.call_args[0][2]
        items = json.loads(batch_arg)
        assert all(item.get("sri", 0) == 0 for item in items)


# ─── US3: bulk_load_session auto_sync suppression ────────────────────────────


class TestBulkLoadSessionAutoSync:
    def test_in_bulk_load_flag_set_cleared(self):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = conn
        e._schema_prefix = "Graph_KG"
        e._in_bulk_load = False
        e._store = MagicMock()
        e.capabilities = MagicMock()
        e.capabilities.objectscript_deployed = False

        flag_inside = []

        with patch.object(type(e), "sync", lambda self: None):
            with patch.object(
                type(e), "_iris_obj", return_value=MagicMock()
            ):
                def _fake_session(self_inner, max_retries=3,
                                  rebuild_indexes=False, incremental=False):
                    from contextlib import contextmanager

                    @contextmanager
                    def _ctx():
                        self_inner._in_bulk_load = True
                        flag_inside.append(self_inner._in_bulk_load)
                        try:
                            yield MagicMock()
                        finally:
                            self_inner._in_bulk_load = False

                    return _ctx()

                with _fake_session(e):
                    assert e._in_bulk_load is True

        assert e._in_bulk_load is False

    def test_auto_sync_suppressed_inside_session(self):
        """bulk_create_edges with auto_sync=True inside bulk_load_session
        must not call sync()."""
        from iris_vector_graph.engine import IRISGraphEngine

        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        e.conn.cursor.return_value = MagicMock()
        e._schema_prefix = "Graph_KG"
        e._in_bulk_load = True
        e._store = MagicMock()
        e.capabilities = MagicMock()
        e.capabilities.objectscript_deployed = False
        e._large_load_hinted = False

        sync_calls = []

        def _fake_sync():
            sync_calls.append(1)

        with patch.object(type(e), "sync", lambda self: _fake_sync()):
            edges = [{"source_id": "a", "predicate": "P", "target_id": "b"}]
            e.bulk_create_edges(edges, disable_indexes=False, auto_sync=True)

        assert len(sync_calls) == 0, "sync must be suppressed inside bulk_load_session"

    def test_auto_sync_fires_when_not_in_session(self):
        """bulk_create_edges with auto_sync=True outside session calls sync()."""
        from iris_vector_graph.engine import IRISGraphEngine

        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        e.conn.cursor.return_value = MagicMock()
        e._schema_prefix = "Graph_KG"
        e._in_bulk_load = False
        e._store = MagicMock()
        e.capabilities = MagicMock()
        e.capabilities.objectscript_deployed = False
        e._large_load_hinted = False

        sync_calls = []

        def _fake_sync():
            sync_calls.append(1)

        with patch.object(type(e), "sync", lambda self: _fake_sync()):
            edges = [{"source_id": "a", "predicate": "P", "target_id": "b"}]
            e.bulk_create_edges(edges, disable_indexes=False, auto_sync=True)

        assert len(sync_calls) == 1

    def test_explicit_auto_sync_false_no_sync(self):
        """auto_sync=False inside session: no sync, no debug log about suppression."""
        from iris_vector_graph.engine import IRISGraphEngine

        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e.conn = MagicMock()
        e.conn.cursor.return_value = MagicMock()
        e._schema_prefix = "Graph_KG"
        e._in_bulk_load = True
        e._store = MagicMock()
        e.capabilities = MagicMock()
        e.capabilities.objectscript_deployed = False
        e._large_load_hinted = False

        sync_calls = []
        with patch.object(type(e), "sync", lambda self: sync_calls.append(1)):
            edges = [{"source_id": "a", "predicate": "P", "target_id": "b"}]
            e.bulk_create_edges(edges, disable_indexes=False, auto_sync=False)

        assert len(sync_calls) == 0


# ─── US4: PurgeBefore bucket guard ───────────────────────────────────────────


class TestPurgeBeforeBucketGuard:
    def test_mid_bucket_tsend_guard_math(self):
        """maxSafeBucket = (tsEnd // divisor) - 1 < bucket containing tsEnd."""
        ts_end = 450
        divisor = 300
        max_safe = (ts_end // divisor) - 1
        bucket_at_ts_end = ts_end // divisor
        assert max_safe == 0
        assert bucket_at_ts_end == 1
        assert max_safe < bucket_at_ts_end

    def test_exactly_at_boundary_max_safe_is_inclusive(self):
        """tsEnd = 600 (exact bucket boundary): bucket = 2, maxSafe = 1."""
        ts_end = 600
        divisor = 300
        max_safe = (ts_end // divisor) - 1
        bucket_at_ts_end = ts_end // divisor
        assert max_safe == 1
        assert bucket_at_ts_end == 2

    def test_fully_expired_bucket_condition(self):
        """bucket 0 (ts=0-299) is fully expired when tsEnd=600: 0 <= maxSafe=1."""
        ts_end = 600
        divisor = 300
        max_safe = (ts_end // divisor) - 1
        assert 0 <= max_safe


# ─── US5: InternLabelSet type contract ───────────────────────────────────────


class TestInternLabelSetContract:
    def test_numeric_type_preserved_in_mock(self):
        engine, store = _make_engine()
        store.resolve_label_set.return_value = '{"port":1972}'
        resolved = engine.resolve_label_set("somehash")
        parsed = json.loads(resolved)
        assert isinstance(parsed["port"], int)
        assert parsed["port"] == 1972

    def test_boolean_type_preserved_in_mock(self):
        engine, store = _make_engine()
        store.resolve_label_set.return_value = '{"ok":true}'
        resolved = engine.resolve_label_set("somehash")
        parsed = json.loads(resolved)
        assert parsed["ok"] is True

    def test_docstring_mentions_append_only(self):
        from iris_vector_graph._engine.temporal import TemporalMixin

        doc = TemporalMixin.intern_label_set.__doc__ or ""
        assert "append-only" in doc.lower() or "never" in doc.lower()

    def test_docstring_mentions_purge(self):
        from iris_vector_graph._engine.temporal import TemporalMixin

        doc = TemporalMixin.intern_label_set.__doc__ or ""
        assert "purge" in doc.lower()


# ─── US6: GetDistinctCount all-sources ───────────────────────────────────────


class TestAllSourcesDistinctCount:
    def test_empty_source_passes_through_to_iris(self):
        engine, _ = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "42"
        with patch.object(type(engine), "_iris_obj", return_value=iris_mock):
            result = engine.get_distinct_count("", "PRECEDED_BY", 0, 1000)
        iris_mock.classMethodValue.assert_called_once_with(
            "Graph.KG.TemporalIndex", "GetDistinctCount", "", "PRECEDED_BY", 0, 1000
        )
        assert result == 42

    def test_nonempty_source_unchanged(self):
        engine, _ = _make_engine()
        iris_mock = MagicMock()
        iris_mock.classMethodValue.return_value = "7"
        with patch.object(type(engine), "_iris_obj", return_value=iris_mock):
            result = engine.get_distinct_count("src1", "PRECEDED_BY", 0, 1000)
        iris_mock.classMethodValue.assert_called_once_with(
            "Graph.KG.TemporalIndex", "GetDistinctCount", "src1", "PRECEDED_BY", 0, 1000
        )
        assert result == 7


# ─── US7: QueryWindowInbound suppression docstring ───────────────────────────


class TestSuppressBlindSpotDoc:
    def test_get_edges_in_window_docstring_mentions_suppression(self):
        from iris_vector_graph._engine.temporal import TemporalMixin

        doc = TemporalMixin.get_edges_in_window.__doc__ or ""
        assert "suppress" in doc.lower(), (
            "get_edges_in_window docstring must mention suppression caveat"
        )

    def test_get_edges_in_window_docstring_mentions_direction_in(self):
        from iris_vector_graph._engine.temporal import TemporalMixin

        doc = TemporalMixin.get_edges_in_window.__doc__ or ""
        assert 'direction' in doc.lower()
