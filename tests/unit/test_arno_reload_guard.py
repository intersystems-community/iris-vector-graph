"""Unit tests for spec 210: Arno auto-reload guard in _detect_arno / _arno_call.

No IRIS container required — _iris_obj() is mocked throughout.
These tests MUST fail on current iris_sql_store.py and pass after the fix.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
from iris_vector_graph.stores.arno_bridge import ArnoError


def _make_store() -> IRISGraphStore:
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = None
    store._arno_capabilities = {}
    store._nkg_dirty = False
    return store


def _iris_obj_that_looks_available() -> MagicMock:
    """iris_obj where IsAvailable=True, Load=1, Capabilities returns rust_callout=true."""
    iris_obj = MagicMock()
    iris_obj.classMethodValue.side_effect = _available_side_effect
    return iris_obj


def _available_side_effect(cls, method, *args):
    if cls == "Graph.KG.ArnoAccel" and method == "IsAvailable":
        return 1
    if cls == "Graph.KG.ArnoAccel" and method == "Load":
        return 1
    if cls == "Graph.KG.ArnoAccel" and method == "GetLibPath":
        return "/tmp/libarno.so"
    if cls == "Graph.KG.NKGAccel" and method == "Capabilities":
        return '{"rust_callout":true,"algorithms":["ppr"],"rust_algorithms":["pagerank","wcc","cdlp","bfs"],"nkg_data":true}'
    return ""


class TestReloadGuard:
    def test_detect_arno_reloads_when_unavailable(self, monkeypatch):
        """_detect_arno() MUST attempt Load() when IsAvailable() returns false."""
        store = _make_store()
        call_log = []

        def side_effect(cls, method, *args):
            call_log.append((cls, method))
            if method == "IsAvailable":
                return 0  # simulates stale worker — dllid gone
            if method == "GetLibPath":
                return "/tmp/libarno.so"
            if method == "Load":
                return 1  # reload succeeds
            if cls == "Graph.KG.NKGAccel" and method == "Capabilities":
                return '{"rust_callout":true,"algorithms":["ppr"],"rust_algorithms":["pagerank"],"nkg_data":true}'
            return ""

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = side_effect

        with patch.object(store, "_iris_obj", return_value=iris_obj):
            result = store._detect_arno()

        load_calls = [c for c in call_log if c[1] == "Load"]
        assert load_calls, "_detect_arno() must call Load() when IsAvailable() is false"
        assert result is True, f"_detect_arno() must return True after successful reload, got {result}"

    def test_arno_call_reloads_when_unavailable(self, monkeypatch):
        """_arno_call() MUST reload before dispatch when IsAvailable() is false."""
        store = _make_store()
        store._arno_available = True  # already probed — but worker went stale
        call_log = []

        def side_effect(cls, method, *args):
            call_log.append((cls, method))
            if method == "IsAvailable":
                return 0
            if method == "GetLibPath":
                return "/tmp/libarno.so"
            if method == "Load":
                return 1
            if method == "WCCJson":
                return '{"a":0}'
            return ""

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = side_effect

        with patch.object(store, "_iris_obj", return_value=iris_obj):
            store._arno_call("Graph.KG.Algorithms", "WCCJson")

        load_calls = [c for c in call_log if c[1] == "Load"]
        assert load_calls, "_arno_call() must call Load() when IsAvailable() is false"

    def test_arno_call_raises_if_reload_fails(self, monkeypatch):
        """_arno_call() MUST raise ArnoError when IsAvailable() false AND Load() returns 0."""
        store = _make_store()
        store._arno_available = True

        def side_effect(cls, method, *args):
            if method == "IsAvailable":
                return 0
            if method == "GetLibPath":
                return "/tmp/libarno_missing.so"
            if method == "Load":
                return 0  # reload fails
            return ""

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = side_effect

        with patch.object(store, "_iris_obj", return_value=iris_obj):
            with pytest.raises((ArnoError, Exception)):
                store._arno_call("Graph.KG.Algorithms", "WCCJson")

    def test_disable_env_skips_reload(self, monkeypatch):
        """IVG_DISABLE_ARNO=1 must return False from _detect_arno with no Load attempt."""
        store = _make_store()
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")

        call_log = []
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = lambda cls, method, *a: call_log.append(method) or 0

        with patch.object(store, "_iris_obj", return_value=iris_obj):
            result = store._detect_arno()

        load_calls = [m for m in call_log if m == "Load"]
        assert not load_calls, "IVG_DISABLE_ARNO=1 must not attempt Load()"
        assert result is False

    def test_reload_not_triggered_when_available(self, monkeypatch):
        """If IsAvailable() returns true, Load() must NOT be called."""
        store = _make_store()
        store._arno_available = True

        load_called = []

        def side_effect(cls, method, *args):
            if method == "Load":
                load_called.append(True)
                return 1
            if method == "IsAvailable":
                return 1
            if method == "WCCJson":
                return '{"a":0}'
            return ""

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = side_effect

        with patch.object(store, "_iris_obj", return_value=iris_obj):
            store._arno_call("Graph.KG.Algorithms", "WCCJson")

        assert not load_called, "Load() must not be called when IsAvailable() is true"
