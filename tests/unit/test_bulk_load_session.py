from unittest.mock import MagicMock, patch, call

import pytest

from iris_vector_graph.engine import IRISGraphEngine, _BulkLoadSession


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._connection_params = {"hostname": "h", "port": 1972, "namespace": "USER",
                              "username": "u", "password": "p"}
    return eng


class TestIsConnDrop:
    def test_matches_communication_link(self):
        assert IRISGraphEngine._is_conn_drop(Exception("<COMMUNICATION LINK ERROR>"))

    def test_matches_epipe(self):
        assert IRISGraphEngine._is_conn_drop(Exception("send() returned error EPIPE"))

    def test_matches_broken_pipe_type(self):
        assert IRISGraphEngine._is_conn_drop(BrokenPipeError("broken"))

    def test_rejects_value_error(self):
        assert not IRISGraphEngine._is_conn_drop(ValueError("bad arg"))


class TestWithReconnect:
    def test_passthrough_on_success(self):
        eng = _engine()
        fn = MagicMock(return_value=42)
        assert eng._with_reconnect(fn, "a", k=1) == 42
        fn.assert_called_once_with("a", k=1)

    def test_retries_on_conn_drop_then_succeeds(self):
        eng = _engine()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("<COMMUNICATION LINK ERROR> EPIPE")
            return "ok"

        with patch.object(eng, "_reconnect_if_stale") as reconn, \
             patch("time.sleep"):
            result = eng._with_reconnect(flaky, max_retries=3)
        assert result == "ok"
        assert calls["n"] == 2
        reconn.assert_called_once()

    def test_reraises_non_conn_error_immediately(self):
        eng = _engine()
        fn = MagicMock(side_effect=ValueError("nope"))
        with patch.object(eng, "_reconnect_if_stale") as reconn:
            with pytest.raises(ValueError):
                eng._with_reconnect(fn, max_retries=3)
        reconn.assert_not_called()
        assert fn.call_count == 1

    def test_gives_up_after_max_retries(self):
        eng = _engine()
        fn = MagicMock(side_effect=Exception("EPIPE broken pipe"))
        with patch.object(eng, "_reconnect_if_stale"), patch("time.sleep"):
            with pytest.raises(Exception, match="EPIPE"):
                eng._with_reconnect(fn, max_retries=2)
        assert fn.call_count == 3


class TestBulkLoadSession:
    def test_disable_once_rebuild_once_sync_once(self):
        eng = _engine()
        eng.bulk_ingest_edges = MagicMock(side_effect=lambda e, *a, **k: len(e))
        with patch("iris_vector_graph.schema.GraphSchema.disable_indexes") as dis, \
             patch("iris_vector_graph.schema.GraphSchema.rebuild_indexes") as reb, \
             patch.object(eng, "sync") as sync:
            with eng.bulk_load_session() as s:
                s.add_edges([{"s": "a", "p": "R", "o": "b"}])
                s.add_edges([{"s": "b", "p": "R", "o": "c"}])
                s.add_edges([{"s": "c", "p": "R", "o": "d"}])
        dis.assert_called_once()
        reb.assert_called_once()
        sync.assert_called_once()

    def test_stats_accumulate(self):
        eng = _engine()
        eng.bulk_ingest_edges = MagicMock(side_effect=lambda e, *a, **k: len(e))
        with patch("iris_vector_graph.schema.GraphSchema.disable_indexes"), \
             patch("iris_vector_graph.schema.GraphSchema.rebuild_indexes"), \
             patch.object(eng, "sync"):
            with eng.bulk_load_session() as s:
                s.add_edges([{"s": "a", "p": "R", "o": "b"}, {"s": "b", "p": "R", "o": "c"}])
            stats = s.stats
        assert stats["edges"] == 2
        assert "load_seconds" in stats
        assert "index_rebuild_seconds" in stats
        assert "sync_seconds" in stats

    def test_rebuild_runs_even_on_exception(self):
        eng = _engine()
        eng.bulk_ingest_edges = MagicMock(side_effect=RuntimeError("boom"))
        with patch("iris_vector_graph.schema.GraphSchema.disable_indexes"), \
             patch("iris_vector_graph.schema.GraphSchema.rebuild_indexes") as reb, \
             patch.object(eng, "sync"):
            with pytest.raises(RuntimeError):
                with eng.bulk_load_session() as s:
                    s.add_edges([{"s": "a", "p": "R", "o": "b"}])
        reb.assert_called_once()

    def test_in_session_edges_use_auto_sync_false(self):
        eng = _engine()
        eng.bulk_ingest_edges = MagicMock(return_value=1)
        with patch("iris_vector_graph.schema.GraphSchema.disable_indexes"), \
             patch("iris_vector_graph.schema.GraphSchema.rebuild_indexes"), \
             patch.object(eng, "sync"):
            with eng.bulk_load_session() as s:
                s.add_edges([{"s": "a", "p": "R", "o": "b"}])
        _, kwargs = eng.bulk_ingest_edges.call_args
        assert kwargs.get("auto_sync") is False

    def test_rebuild_indexes_false_skips_index_ops(self):
        eng = _engine()
        eng.bulk_ingest_edges = MagicMock(return_value=1)
        with patch("iris_vector_graph.schema.GraphSchema.disable_indexes") as dis, \
             patch("iris_vector_graph.schema.GraphSchema.rebuild_indexes") as reb, \
             patch.object(eng, "sync"):
            with eng.bulk_load_session(rebuild_indexes=False) as s:
                s.add_edges([{"s": "a", "p": "R", "o": "b"}])
        dis.assert_not_called()
        reb.assert_not_called()
