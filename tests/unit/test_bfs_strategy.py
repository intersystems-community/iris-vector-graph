"""Unit tests for spec-arch-4 — internal BFS strategy protocol in iris_sql_store.

All tests use mocks — no container required.
"""
from unittest.mock import MagicMock, patch
import pytest


def _get_private(name):
    """Import a private class from iris_sql_store."""
    import iris_vector_graph.stores.iris_sql_store as _m
    return getattr(_m, name)


class TestBfsStrategyProtocol:
    def test_bfs_strategy_protocol_exists(self):
        _BfsStrategy = _get_private("_BfsStrategy")
        assert _BfsStrategy is not None

    def test_bfs_strategy_has_run_method(self):
        _BfsStrategy = _get_private("_BfsStrategy")
        import inspect
        members = {n for n, _ in inspect.getmembers(_BfsStrategy)}
        assert "run" in members

    def test_arno_adapter_exists(self):
        assert _get_private("_ArnoBfsAdapter") is not None

    def test_objectscript_adapter_exists(self):
        assert _get_private("_ObjectScriptBfsAdapter") is not None

    def test_sql_fallback_adapter_exists(self):
        assert _get_private("_SqlBfsFallbackAdapter") is not None


class TestBfsStrategyAdaptersSatisfyProtocol:
    def _mock_store(self):
        s = MagicMock()
        s._arno_capabilities = {"bfs": True}
        s._detect_arno.return_value = True
        return s

    def test_arno_adapter_satisfies_protocol(self):
        _BfsStrategy = _get_private("_BfsStrategy")
        _ArnoBfsAdapter = _get_private("_ArnoBfsAdapter")
        adapter = _ArnoBfsAdapter(self._mock_store())
        assert isinstance(adapter, _BfsStrategy)

    def test_objectscript_adapter_satisfies_protocol(self):
        _BfsStrategy = _get_private("_BfsStrategy")
        _ObjectScriptBfsAdapter = _get_private("_ObjectScriptBfsAdapter")
        adapter = _ObjectScriptBfsAdapter(self._mock_store())
        assert isinstance(adapter, _BfsStrategy)

    def test_sql_fallback_adapter_satisfies_protocol(self):
        _BfsStrategy = _get_private("_BfsStrategy")
        _SqlBfsFallbackAdapter = _get_private("_SqlBfsFallbackAdapter")
        adapter = _SqlBfsFallbackAdapter(self._mock_store())
        assert isinstance(adapter, _BfsStrategy)


class TestSelectBfsStrategy:
    def _store_with_capabilities(self, arno=False, arno_bfs=False):
        import iris_vector_graph.stores.iris_sql_store as _m
        s = MagicMock(spec=_m.IRISGraphStore)
        s._arno_capabilities = {"bfs": arno_bfs}
        s._detect_arno.return_value = arno
        s._select_bfs_strategy = _m.IRISGraphStore._select_bfs_strategy.__get__(s)
        return s

    def test_select_returns_arno_when_capable(self):
        _ArnoBfsAdapter = _get_private("_ArnoBfsAdapter")
        s = self._store_with_capabilities(arno=True, arno_bfs=True)
        strategy = s._select_bfs_strategy()
        assert isinstance(strategy, _ArnoBfsAdapter)

    def test_select_returns_objectscript_when_no_arno(self):
        _ObjectScriptBfsAdapter = _get_private("_ObjectScriptBfsAdapter")
        s = self._store_with_capabilities(arno=False, arno_bfs=False)
        strategy = s._select_bfs_strategy()
        assert isinstance(strategy, _ObjectScriptBfsAdapter)

    def test_select_returns_objectscript_when_arno_no_bfs(self):
        _ObjectScriptBfsAdapter = _get_private("_ObjectScriptBfsAdapter")
        s = self._store_with_capabilities(arno=True, arno_bfs=False)
        strategy = s._select_bfs_strategy()
        assert isinstance(strategy, _ObjectScriptBfsAdapter)
