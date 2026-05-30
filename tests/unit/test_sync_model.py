import warnings
from unittest.mock import MagicMock, patch, call

import pytest


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine

    cursor = MagicMock()
    cursor.description = [("node_id",)]
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = "Graph_KG"
    engine.embedder = None
    engine.embedding_dimension = 768
    engine._connection_params = None
    engine._nkg_dirty = False
    engine._arno_available = False
    engine._arno_capabilities = {}
    from iris_vector_graph.capabilities import IRISCapabilities
    engine.capabilities = IRISCapabilities()
    return engine, cursor


class TestSyncMethod:

    def test_sync_method_exists(self):
        engine, _ = _make_engine()
        assert hasattr(engine, "sync")
        assert callable(engine.sync)

    def test_sync_returns_bool(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_kg", return_value=True), \
             patch.object(engine, "_sync_nkg", return_value=True):
            result = engine.sync()
        assert result is True

    def test_sync_calls_both_kg_and_nkg(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_kg", return_value=True) as mock_kg, \
             patch.object(engine, "_sync_nkg", return_value=True) as mock_nkg:
            engine.sync()
        mock_kg.assert_called_once()
        mock_nkg.assert_called_once()

    def test_sync_returns_false_if_kg_fails(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_kg", return_value=False), \
             patch.object(engine, "_sync_nkg", return_value=True):
            assert engine.sync() is False

    def test_sync_returns_false_if_nkg_fails(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_kg", return_value=True), \
             patch.object(engine, "_sync_nkg", return_value=False):
            assert engine.sync() is False

    def test_rebuild_kg_deprecated(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_kg", return_value=True), \
             warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            engine.rebuild_kg()
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) >= 1
        assert "sync()" in str(dep[0].message).lower() or "deprecated" in str(dep[0].message).lower()

    def test_rebuild_nkg_deprecated(self):
        engine, _ = _make_engine()
        with patch.object(engine, "_sync_nkg", return_value=True), \
             warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            engine.rebuild_nkg()
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) >= 1

    def test_private_sync_methods_exist(self):
        engine, _ = _make_engine()
        assert hasattr(engine, "_sync_kg")
        assert hasattr(engine, "_sync_nkg")
        assert not hasattr(engine.__class__, "_rebuild_kg")


class TestIndexNotSyncedError:

    def test_index_not_synced_error_raised_not_warned(self):
        from iris_vector_graph.errors import IndexNotSyncedError
        engine, _ = _make_engine()
        engine._nkg_dirty = True

        mock_sql_query = MagicMock()
        mock_sql_query.var_length_paths = [{"types": [], "max_hops": 2, "min_hops": 1, "properties": {}}]
        mock_sql_query.parameters = [[]]

        with pytest.raises(IndexNotSyncedError):
            engine._execute_var_length_cypher(mock_sql_query)

    def test_index_not_synced_error_not_raised_when_clean(self):
        engine, _ = _make_engine()
        engine._nkg_dirty = False

        mock_sql_query = MagicMock()
        mock_sql_query.var_length_paths = [{"types": [], "max_hops": 2, "min_hops": 1, "properties": {}}]
        mock_sql_query.parameters = [[]]

        try:
            engine._execute_var_length_cypher(mock_sql_query)
        except Exception as e:
            from iris_vector_graph.errors import IndexNotSyncedError
            assert not isinstance(e, IndexNotSyncedError)


class TestAutoSyncParam:

    def test_bulk_create_edges_has_auto_sync(self):
        import inspect
        from iris_vector_graph.engine import IRISGraphEngine
        sig = inspect.signature(IRISGraphEngine.bulk_create_edges)
        assert "auto_sync" in sig.parameters
        assert sig.parameters["auto_sync"].default is True

    def test_bulk_ingest_edges_has_auto_sync(self):
        import inspect
        from iris_vector_graph.engine import IRISGraphEngine
        sig = inspect.signature(IRISGraphEngine.bulk_ingest_edges)
        assert "auto_sync" in sig.parameters
        assert sig.parameters["auto_sync"].default is True

    def test_auto_rebuild_kg_deprecated_in_bulk_create(self):
        engine, cursor = _make_engine()
        cursor.fetchall.return_value = []
        conn = engine.conn
        conn.cursor.return_value = cursor

        with patch.object(engine, "sync"), \
             warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                engine.bulk_create_edges([], auto_rebuild_kg=False)
            except Exception:
                pass
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) >= 1
        assert "auto_sync" in str(dep[0].message) or "deprecated" in str(dep[0].message).lower()
