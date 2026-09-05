"""Unit tests for namespace-aware IVG engine (spec 212).

Covers:
  TestNamespaceProperty    — engine.namespace property (US3)
  TestNamespaceProbe       — _check_namespace() probe (US1)
  TestNamespaceProbeSkip   — env-var suppression (US1)
  TestDetectArnoClassProbe — _detect_arno class-existence check (US2)
"""

import logging
import warnings
from unittest.mock import MagicMock, call, patch

import pytest

from iris_vector_graph.exceptions import NamespaceMismatchWarning

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(namespace="USER", conn_namespace=None):
    """Build a minimal IRISGraphEngine with mocked connection and store."""
    from iris_vector_graph.engine import IRISGraphEngine

    mock_conn = MagicMock()
    if conn_namespace is not None:
        mock_conn.namespace = conn_namespace
    else:
        # No .namespace attribute — getattr returns None
        if hasattr(mock_conn, "namespace"):
            del mock_conn.namespace

    mock_store = MagicMock()
    mock_store.capabilities.return_value = {}
    mock_store._arno_available = None
    mock_store._arno_capabilities = {}

    with patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", return_value=mock_store):
        with patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"):
            with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}):
                eng = IRISGraphEngine(mock_conn, namespace=namespace)
    eng._store = mock_store
    return eng, mock_conn, mock_store


_NO_CONN_NS = object()  # sentinel for "conn has no .namespace attribute"


def _make_store(engine_namespace="USER", conn_namespace=_NO_CONN_NS):
    """Build a bare IRISGraphStore with the given namespace.

    If conn_namespace is not provided, mock_conn has no .namespace attribute
    (getattr returns None). If provided, mock_conn.namespace == conn_namespace.
    """
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    mock_conn = MagicMock()
    # Remove auto-created .namespace so getattr(conn, "namespace", None) → None
    del mock_conn.namespace
    if conn_namespace is not _NO_CONN_NS:
        mock_conn.namespace = conn_namespace

    store = IRISGraphStore(mock_conn, namespace=engine_namespace)
    return store, mock_conn


# ---------------------------------------------------------------------------
# Phase 3 — US3: engine.namespace property
# ---------------------------------------------------------------------------


class TestNamespaceProperty:
    def test_default_namespace_is_user(self):
        eng, _, _ = _make_engine(namespace="USER")
        assert eng.namespace == "USER"

    def test_explicit_namespace_stored(self):
        eng, _, _ = _make_engine(namespace="HSANALYTICS")
        assert eng.namespace == "HSANALYTICS"

    def test_namespace_is_string(self):
        eng, _, _ = _make_engine(namespace="HSCUSTOM")
        assert isinstance(eng.namespace, str)

    def test_no_iris_cursor_call_on_construction(self):
        """Engine construction must not execute any SQL (no IRIS round trip)."""
        eng, mock_conn, _ = _make_engine()
        mock_conn.cursor.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 4 — US1: _check_namespace probe
# ---------------------------------------------------------------------------


class TestNamespaceProbe:
    def _store_with_cursor(self, fetchone_value, engine_namespace="USER", conn_namespace=None):
        store, mock_conn = _make_store(
            engine_namespace=engine_namespace, conn_namespace=conn_namespace
        )
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (fetchone_value,)
        mock_conn.cursor.return_value = mock_cursor
        return store, mock_conn, mock_cursor

    def test_probe_warns_when_kgdata_returns_zero(self, caplog):
        store, mock_conn, mock_cursor = self._store_with_cursor(0)
        mock_cursor.fetchone.return_value = (0,)
        with caplog.at_level(logging.WARNING):
            store._check_namespace()
        assert any("USER" in r.message for r in caplog.records)
        assert any("map ^KG globals" in r.message for r in caplog.records)

    def test_probe_strict_mode_raises_warning(self, caplog):
        store, mock_conn, mock_cursor = self._store_with_cursor(0)
        mock_cursor.fetchone.return_value = (0,)
        with patch.dict("os.environ", {"IVG_STRICT_NAMESPACE": "1"}):
            with pytest.warns(NamespaceMismatchWarning) as w_info:
                store._check_namespace()
        assert len(w_info) >= 1
        msg = str(w_info[0].message)
        assert "USER" in msg

    def test_probe_strict_message_contains_both_namespaces(self):
        store, mock_conn, mock_cursor = self._store_with_cursor(0, engine_namespace="HSCUSTOM")
        mock_cursor.fetchone.return_value = (0,)
        with patch.dict("os.environ", {"IVG_STRICT_NAMESPACE": "1"}):
            with pytest.warns(NamespaceMismatchWarning) as w_info:
                store._check_namespace()
        msg = str(w_info[0].message)
        assert "HSCUSTOM" in msg

    def test_probe_passes_when_kgdata_nonzero(self, caplog):
        store, mock_conn, mock_cursor = self._store_with_cursor(1)
        mock_cursor.fetchone.return_value = (1,)
        with caplog.at_level(logging.WARNING):
            store._check_namespace()
        assert not any("map ^KG globals" in r.message for r in caplog.records)

    def test_probe_warns_conn_namespace_mismatch(self, caplog):
        """FR-003: conn.namespace diverges from engine._namespace → warning logged."""
        store, mock_conn, mock_cursor = self._store_with_cursor(
            1, engine_namespace="USER", conn_namespace="HSANALYTICS"
        )
        mock_cursor.fetchone.return_value = (1,)
        with caplog.at_level(logging.WARNING):
            store._check_namespace()
        assert any("USER" in r.message and "HSANALYTICS" in r.message for r in caplog.records)

    def test_probe_runs_once(self):
        store, mock_conn, mock_cursor = self._store_with_cursor(1)
        mock_cursor.fetchone.return_value = (1,)
        store._check_namespace()
        store._check_namespace()
        # cursor.execute called exactly once (second call short-circuits)
        assert mock_cursor.execute.call_count == 1


class TestNamespaceProbeSkip:
    def _store_with_mock_cursor(self):
        store, mock_conn = _make_store()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_conn.cursor.return_value = mock_cursor
        return store, mock_conn, mock_cursor

    def test_ignore_env_skips_probe(self):
        store, mock_conn, mock_cursor = self._store_with_mock_cursor()
        with patch.dict("os.environ", {"IVG_IGNORE_NAMESPACE_CHECK": "1"}):
            store._check_namespace()
        mock_cursor.execute.assert_not_called()

    def test_ignore_takes_precedence_over_strict(self):
        store, mock_conn, mock_cursor = self._store_with_mock_cursor()
        env = {"IVG_IGNORE_NAMESPACE_CHECK": "1", "IVG_STRICT_NAMESPACE": "1"}
        with patch.dict("os.environ", env):
            # Should not raise NamespaceMismatchWarning despite strict mode
            with warnings.catch_warnings():
                warnings.simplefilter("error", NamespaceMismatchWarning)
                store._check_namespace()  # no exception
        mock_cursor.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 5 — US2: _detect_arno class-existence check
# ---------------------------------------------------------------------------


class TestDetectArnoClassProbe:
    def _store_with_iris_obj(self, exists_return, is_available_return=True):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        mock_conn = MagicMock()
        store = IRISGraphStore(mock_conn, namespace="USER")
        # Mark namespace checked so _detect_arno doesn't trigger the probe
        store._namespace_checked = True

        # Fake iris_obj
        mock_iris_obj = MagicMock()

        def classmethod_side_effect(cls_name, method, *args):
            if cls_name == "%SYSTEM.OBJ" and method == "Exists":
                return exists_return
            if cls_name == "Graph.KG.ArnoAccel" and method == "IsAvailable":
                return is_available_return
            if cls_name == "Graph.KG.ArnoAccel" and method == "Load":
                return 1
            if cls_name == "Graph.KG.NKGAccel":
                return '{"nkg_data": true}'
            return 0

        mock_iris_obj.classMethodValue.side_effect = classmethod_side_effect
        store._iris_obj = MagicMock(return_value=mock_iris_obj)
        return store, mock_iris_obj

    def test_arno_class_absent_returns_false(self):
        store, _ = self._store_with_iris_obj(exists_return=0)
        result = store._detect_arno()
        assert result is False

    def test_arno_class_absent_logs_not_found(self, caplog):
        store, _ = self._store_with_iris_obj(exists_return=0)
        with caplog.at_level(logging.WARNING):
            store._detect_arno()
        assert any("class not found in namespace" in r.message for r in caplog.records)

    def test_arno_class_absent_no_isavailable_call(self):
        store, mock_iris_obj = self._store_with_iris_obj(exists_return=0)
        store._detect_arno()
        # classMethodValue for IsAvailable must never be called
        for c in mock_iris_obj.classMethodValue.call_args_list:
            cls_arg = c.args[0] if c.args else c[0][0]
            method_arg = c.args[1] if len(c.args) > 1 else c[0][1]
            assert not (
                cls_arg == "Graph.KG.ArnoAccel" and method_arg == "IsAvailable"
            ), "IsAvailable must not be called when class is absent"

    def test_arno_class_present_proceeds_normally(self):
        store, mock_iris_obj = self._store_with_iris_obj(exists_return=1, is_available_return=True)
        result = store._detect_arno()
        # Should proceed past class check (result may vary but no class-not-found log)
        calls = [c.args[:2] for c in mock_iris_obj.classMethodValue.call_args_list]
        assert ("%Dictionary.CompiledClass", "%ExistsId") in calls

    def test_disable_arno_env_skips_class_probe(self):
        store, mock_iris_obj = self._store_with_iris_obj(exists_return=0)
        with patch.dict("os.environ", {"IVG_DISABLE_ARNO": "1"}):
            result = store._detect_arno()
        assert result is False
        # %SYSTEM.OBJ.Exists must not have been called
        for c in mock_iris_obj.classMethodValue.call_args_list:
            cls_arg = c.args[0] if c.args else c[0][0]
            assert cls_arg != "%SYSTEM.OBJ", "%SYSTEM.OBJ.Exists called despite IVG_DISABLE_ARNO=1"
