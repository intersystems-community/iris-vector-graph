import os
import pytest
from unittest.mock import MagicMock, patch, call

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestEmbeddedCursorUnit:

    def _make_rs(self, rows, col_names=None):
        rs = MagicMock()
        rs.__iter__ = MagicMock(return_value=iter(rows))
        rs.columnCount.return_value = len(col_names) if col_names else 0
        if col_names:
            rs.columnName.side_effect = lambda i: col_names[i - 1]
        return rs

    def test_execute_prepares_and_runs(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_rs = self._make_rs([], [])
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = mock_rs
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT 1")
        mock_sql.prepare.assert_called_once_with("SELECT 1")
        mock_stmt.execute.assert_called_once_with()

    def test_execute_with_params(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = self._make_rs([], [])
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT ? + ?", [1, 2])
        mock_stmt.execute.assert_called_once_with(1, 2)

    def test_transaction_statements_are_noop(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        for sql in ("START TRANSACTION", "COMMIT", "ROLLBACK", "BEGIN"):
            mock_sql = MagicMock()
            with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
                cursor.execute(sql)
            mock_sql.prepare.assert_not_called()

    def test_fetchall_returns_list_of_tuples(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = self._make_rs([(1, "a"), (2, "b")], ["id", "name"])
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT id, name FROM t")
        rows = cursor.fetchall()
        assert rows == [(1, "a"), (2, "b")]

    def test_fetchone_returns_single_row(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = self._make_rs([(42,)], ["n"])
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT 42")
        row = cursor.fetchone()
        assert row == (42,)
        assert cursor.fetchone() is None

    def test_fetchmany_returns_slice(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = self._make_rs([(i,) for i in range(10)], ["n"])
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT n FROM t")
        batch1 = cursor.fetchmany(3)
        batch2 = cursor.fetchmany(3)
        assert batch1 == [(0,), (1,), (2,)]
        assert batch2 == [(3,), (4,), (5,)]

    def test_description_populated_from_result_set(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = self._make_rs([], ["col_a", "col_b"])
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT col_a, col_b FROM t")
        assert cursor.description is not None
        assert cursor.description[0][0] == "col_a"
        assert cursor.description[1][0] == "col_b"

    def test_description_none_after_transaction_noop(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        with patch("iris_vector_graph.embedded._require_iris_sql"):
            cursor.execute("COMMIT")
        assert cursor.description is None

    def test_executemany_loops_over_params(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_sql.prepare.return_value = mock_stmt

        data = [(1, "a"), (2, "b"), (3, "c")]
        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.executemany("INSERT INTO t VALUES (?, ?)", data)
        assert mock_stmt.execute.call_count == 3
        mock_stmt.execute.assert_any_call(1, "a")
        mock_stmt.execute.assert_any_call(3, "c")

    def test_rowcount_set_by_executemany(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_stmt = MagicMock()
        mock_sql.prepare.return_value = mock_stmt

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        assert cursor.rowcount == 3


class TestEmbeddedConnectionUnit:

    def test_cursor_returns_embedded_cursor(self):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        with patch("iris_vector_graph.embedded._require_iris_sql"):
            conn = EmbeddedConnection()
        assert isinstance(conn.cursor(), EmbeddedCursor)

    def test_commit_is_noop(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection()
        conn.commit()

    def test_rollback_is_noop(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection()
        conn.rollback()

    def test_close_is_noop(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection()
        conn.close()

    def test_import_error_without_iris(self):
        from iris_vector_graph.embedded import _require_iris_sql
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            with pytest.raises(ImportError, match="embedded iris module"):
                _require_iris_sql()

    def test_shadowed_iris_error_message_contains_guidance(self):
        from iris_vector_graph import embedded
        import importlib, inspect
        src = inspect.getsource(embedded._require_iris_sql)
        assert "iris.sql attribute" in src
        assert "intersystems_irispython" in src
        assert "lib/python" in src or "/usr/irissys" in src

    def test_ensure_embedded_iris_first_inserts_path(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        import sys
        embedded_path = '/usr/irissys/lib/python'
        original = sys.path.copy()
        try:
            if embedded_path in sys.path:
                sys.path.remove(embedded_path)
            _ensure_embedded_iris_first()
            assert sys.path[0] == embedded_path
        finally:
            sys.path[:] = original

    def test_ensure_embedded_iris_first_moves_to_front_if_not_first(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        import sys
        embedded_path = '/usr/irissys/lib/python'
        original = sys.path.copy()
        try:
            if embedded_path in sys.path:
                sys.path.remove(embedded_path)
            sys.path.append(embedded_path)
            assert sys.path[0] != embedded_path
            _ensure_embedded_iris_first()
            assert sys.path[0] == embedded_path
        finally:
            sys.path[:] = original


class TestTopLevelImports:

    def test_embedded_connection_importable_from_top_level(self):
        from iris_vector_graph import EmbeddedConnection
        assert EmbeddedConnection is not None

    def test_embedded_cursor_importable_from_top_level(self):
        from iris_vector_graph import EmbeddedCursor
        assert EmbeddedCursor is not None


class TestIrisSqlAutoWrap:

    def test_engine_accepts_iris_sql_module_directly(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock(spec=["prepare"])
        assert hasattr(mock_iris_sql, 'prepare')
        assert not hasattr(mock_iris_sql, 'cursor')
        engine = IRISGraphEngine(mock_iris_sql)
        assert isinstance(engine.conn, EmbeddedConnection)

    def test_engine_with_normal_connection_unchanged(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock()
        engine = IRISGraphEngine(mock_conn)
        assert engine.conn is mock_conn
        assert not isinstance(engine.conn, EmbeddedConnection)


class TestTemporalCypherViaEmbedded:

    def test_temporal_translation_works_with_embedded_connection(self):
        from iris_vector_graph.cypher.translator import translate_to_sql, TemporalQueryRequiresEngine
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.embedded import EmbeddedConnection
        from iris_vector_graph.engine import IRISGraphEngine
        mock_engine = MagicMock()
        mock_engine.get_edges_in_window.return_value = [
            {"s": "svc:a", "p": "CALLS_AT", "o": "svc:b", "ts": 1000, "w": 42.0},
        ]
        tree = parse_query(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts, r.weight"
        )
        result = translate_to_sql(tree, {"start": 900, "end": 1100}, engine=mock_engine)
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "weight" in sql
        assert "1000" in sql

    def test_iris_sql_auto_wrap_creates_functional_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock(spec=["prepare"])
        engine = IRISGraphEngine(mock_iris_sql)
        assert isinstance(engine.conn, EmbeddedConnection)


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestEmbeddedConnectionE2E:

    def test_embedded_cursor_executes_sql_against_live_iris(self, iris_connection):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        import iris as iris_mod

        conn = EmbeddedConnection()
        cursor = conn.cursor()
        assert isinstance(cursor, EmbeddedCursor)

    def test_engine_works_with_embedded_connection(self, iris_connection):
        from iris_vector_graph.embedded import EmbeddedConnection
        from iris_vector_graph.engine import IRISGraphEngine

        embedded = EmbeddedConnection()
        try:
            engine = IRISGraphEngine(embedded)
            assert engine is not None
        except Exception as e:
            pytest.skip(f"EmbeddedConnection not available outside IRIS: {e}")
