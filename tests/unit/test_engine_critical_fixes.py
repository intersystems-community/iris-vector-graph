"""Unit tests for engine critical fixes (spec 206).

Covers: A9 BuildKG preserves temporal globals, A12 bulk_delete_nodes no OR,
A8.3 purge_bucket_range wrapper.
No IRIS container required.
"""
import os
from unittest.mock import MagicMock, call, patch

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine_with_cursor():
    """Engine with a real-ish conn mock that captures cursor.execute calls."""
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e._schema_prefix = "Graph_KG"
    e._in_bulk_load = False

    cursor_mock = MagicMock()
    cursor_mock.__enter__ = lambda s: s
    cursor_mock.__exit__ = MagicMock(return_value=False)

    conn_mock = MagicMock()
    conn_mock.cursor.return_value = cursor_mock
    e.conn = conn_mock

    store_mock = MagicMock()
    store_mock.conn = conn_mock
    store_mock._schema_prefix = "Graph_KG"
    e._store = store_mock

    return e, store_mock, cursor_mock


# ─── US1: A9 — BuildKG must not Kill ^KG at root ─────────────────────────────


class TestBuildKGPreservesTemporalGlobals:
    def test_kill_kg_root_absent_from_source(self):
        """The one-line fix: 'Kill ^KG' must not appear in TraversalBuild.cls."""
        import pathlib

        src = pathlib.Path(
            "iris_src/src/Graph/KG/TraversalBuild.cls"
        ).read_text()
        lines = src.splitlines()
        # Strip comments, look for bare 'Kill ^KG' (not Kill ^KG("..."))
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Allow Kill ^KG("...") but not bare Kill ^KG
            if stripped.startswith("Kill ^KG") and not stripped.startswith('Kill ^KG("'):
                raise AssertionError(
                    f"TraversalBuild.cls line {lineno}: found bare 'Kill ^KG' — "
                    f"must be replaced with five explicit subscript kills. Got: {stripped!r}"
                )

    def test_five_subscript_kills_present(self):
        """All five adjacency subscripts must appear in the Kill statement."""
        import pathlib

        src = pathlib.Path(
            "iris_src/src/Graph/KG/TraversalBuild.cls"
        ).read_text()
        # The fix uses a comma-separated Kill: Kill ^KG("label"), ^KG("prop"), ...
        # Check each subscript name appears in a ^KG kill context
        for sub in ("label", "prop", "out", "in", "deg"):
            assert f'^KG("{sub}")' in src, (
                f'Missing ^KG("{sub}") in TraversalBuild.cls Kill statement'
            )


# ─── US2: A12 — bulk_delete_nodes must not use OR ────────────────────────────


# ─── US3: A8.3 — purge_bucket_range wrapper ──────────────────────────────────


class TestPurgeBucketRangeWrapper:
    def _make_store(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value=3)
        return store

    def test_passes_args_to_classmethod(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="5")
        result = store.purge_bucket_range(100, 200)
        store._call_classmethod.assert_called_once_with(
            "Graph.KG.TemporalIndex", "PurgeBucketRange", "", 100, 200
        )
        assert result == 5

    def test_returns_int(self):
        store = self._make_store()
        result = store.purge_bucket_range(0, 999)
        assert isinstance(result, int)

    def test_invalid_range_returns_zero(self):
        """bucket_start > bucket_end: ObjectScript returns 0."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        store = IRISGraphStore.__new__(IRISGraphStore)
        store._call_classmethod = MagicMock(return_value="0")
        result = store.purge_bucket_range(500, 100)
        assert result == 0

    def test_engine_delegates_to_store(self):
        """IRISGraphEngine.purge_bucket_range delegates to _store.purge_bucket_range."""
        from iris_vector_graph.engine import IRISGraphEngine

        e = IRISGraphEngine.__new__(IRISGraphEngine)
        e._store = MagicMock()
        e._store.purge_bucket_range.return_value = 7
        result = e.purge_bucket_range(10, 20)
        e._store.purge_bucket_range.assert_called_once_with(10, 20, graph=None)
        assert result == 7
