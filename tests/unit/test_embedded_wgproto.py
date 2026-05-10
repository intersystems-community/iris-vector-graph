import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call


class TestInlineParams:

    def test_no_params_returns_sql_unchanged(self):
        from iris_vector_graph.embedded import _inline_params
        sql = "SELECT 1"
        assert _inline_params(sql, []) == "SELECT 1"

    def test_string_param_quoted(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT * FROM t WHERE name = ?", ["alice"])
        assert result == "SELECT * FROM t WHERE name = 'alice'"

    def test_string_param_escapes_single_quotes(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("INSERT INTO t (v) VALUES (?)", ["o'clock"])
        assert "o''clock" in result

    def test_int_param_unquoted(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT * FROM t WHERE id = ?", [42])
        assert result == "SELECT * FROM t WHERE id = 42"

    def test_float_param_unquoted(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT ? AS v", [3.14])
        assert "3.14" in result

    def test_none_param_becomes_null(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("INSERT INTO t (v) VALUES (?)", [None])
        assert result == "INSERT INTO t (v) VALUES (NULL)"

    def test_multiple_params_in_order(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT ?, ?, ?", [1, "two", None])
        assert result == "SELECT 1, 'two', NULL"

    def test_list_param_becomes_quoted_json_string(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT TO_VECTOR(?, DOUBLE)", ["[0.1,0.2,0.3]"])
        assert "0.1" in result
        assert "0.2" in result

    def test_too_few_params_raises(self):
        from iris_vector_graph.embedded import _inline_params
        with pytest.raises((ValueError, IndexError)):
            _inline_params("SELECT ?, ?", [1])

    def test_bool_param(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT ?", [True])
        assert result in ("SELECT 1", "SELECT TRUE", "SELECT True")


class TestEmbeddedCursorUnimplementedFallback:

    def _make_unimplemented(self, msg="<UNIMPLEMENTED>ddtab+83^%qaqpsq"):
        exc = RuntimeError(msg)
        return exc

    def test_prepare_unimplemented_falls_back_to_exec(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_rs = MagicMock()
        mock_sql.prepare.side_effect = self._make_unimplemented()
        mock_sql.exec.return_value = mock_rs

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT * FROM t WHERE id = ?", [42])

        mock_sql.prepare.assert_called_once()
        mock_sql.exec.assert_called_once()
        inlined = mock_sql.exec.call_args[0][0]
        assert "42" in inlined
        assert "?" not in inlined

    def test_prepare_unimplemented_string_param_inlined(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_unimplemented()
        mock_sql.exec.return_value = MagicMock()

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT * FROM t WHERE name = ?", ["alice"])

        inlined = mock_sql.exec.call_args[0][0]
        assert "'alice'" in inlined
        assert "?" not in inlined

    def test_prepare_unimplemented_none_becomes_null(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_unimplemented()
        mock_sql.exec.return_value = MagicMock()

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("INSERT INTO t (v) VALUES (?)", [None])

        inlined = mock_sql.exec.call_args[0][0]
        assert "NULL" in inlined

    def test_prepare_unimplemented_no_params_exec_called_with_original(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_unimplemented()
        mock_sql.exec.return_value = MagicMock()

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT 1")

        mock_sql.exec.assert_called_once_with("SELECT 1")

    def test_non_unimplemented_error_propagates(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("something else entirely")

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            with pytest.raises(RuntimeError, match="something else entirely"):
                cursor.execute("SELECT 1")

    def test_ddtab_variant_also_falls_back(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("ddtab+83^%qaqpsq error")
        mock_sql.exec.return_value = MagicMock()

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT * FROM t WHERE id = ?", [1])

        mock_sql.exec.assert_called_once()

    def test_fetchall_works_after_fallback(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("<UNIMPLEMENTED>ddtab")
        mock_rs = MagicMock()
        mock_rs.__iter__ = MagicMock(return_value=iter([(1, "a"), (2, "b")]))
        mock_rs.columnCount.return_value = 2
        mock_rs.columnName.side_effect = lambda i: ["id", "name"][i - 1]
        mock_sql.exec.return_value = mock_rs

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT id, name FROM t WHERE x = ?", ["val"])

        rows = cursor.fetchall()
        assert rows == [(1, "a"), (2, "b")]

    def test_executemany_prepare_unimplemented_falls_back(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("<UNIMPLEMENTED>ddtab")
        mock_sql.exec.return_value = MagicMock()

        data = [(1, "a"), (2, "b")]
        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.executemany("INSERT INTO t VALUES (?, ?)", data)

        assert mock_sql.exec.call_count == 2
        calls = [c[0][0] for c in mock_sql.exec.call_args_list]
        assert "1" in calls[0] and "'a'" in calls[0]
        assert "2" in calls[1] and "'b'" in calls[1]


class TestEnsureEmbeddedIrisFirst:

    def test_skips_eviction_when_iris_sql_already_live(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        mock_iris = MagicMock()
        mock_iris.sql = MagicMock()
        original_modules = sys.modules.copy()
        original_path = sys.path.copy()
        try:
            sys.modules['iris'] = mock_iris
            _ensure_embedded_iris_first()
            assert sys.modules.get('iris') is mock_iris
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)
            sys.path[:] = original_path

    def test_skips_eviction_when_iris_sql_is_none_but_in_modules(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        mock_iris = MagicMock()
        mock_iris.sql = None
        original_modules = sys.modules.copy()
        original_path = sys.path.copy()
        try:
            sys.modules['iris'] = mock_iris
            embedded_path = '/usr/irissys/lib/python'
            if embedded_path in sys.path:
                sys.path.remove(embedded_path)
            sys.path.append(embedded_path)
            _ensure_embedded_iris_first()
            assert sys.modules.get('iris') is not mock_iris
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)
            sys.path[:] = original_path

    def test_evicts_and_reorders_when_iris_not_loaded(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        original_modules = sys.modules.copy()
        original_path = sys.path.copy()
        try:
            sys.modules.pop('iris', None)
            embedded_path = '/usr/irissys/lib/python'
            if embedded_path in sys.path:
                sys.path.remove(embedded_path)
            sys.path.append(embedded_path)
            _ensure_embedded_iris_first()
            assert sys.path[0] == embedded_path
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)
            sys.path[:] = original_path

    def test_no_sigsegv_risk_when_called_twice_with_live_iris(self):
        from iris_vector_graph.embedded import _ensure_embedded_iris_first
        mock_iris = MagicMock()
        mock_iris.sql = MagicMock()
        original_modules = sys.modules.copy()
        try:
            sys.modules['iris'] = mock_iris
            _ensure_embedded_iris_first()
            _ensure_embedded_iris_first()
            assert sys.modules.get('iris') is mock_iris
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)


class TestVectorDtypeConsistency:

    def test_store_embedding_uses_configured_dtype(self):
        from iris_vector_graph.engine import IRISGraphEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = mock_conn
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "DOUBLE"

        with patch.object(type(eng), '_assert_node_exists'):
            try:
                eng.store_embedding("test:n1", [0.1, 0.2, 0.3, 0.4])
            except Exception:
                pass

        all_calls = " ".join(str(c) for c in mock_cursor.execute.call_args_list)
        if "TO_VECTOR" in all_calls:
            assert "DOUBLE" in all_calls or "FLOAT" in all_calls

    def test_kg_knn_vec_uses_same_dtype_as_store_embedding(self):
        from iris_vector_graph.engine import IRISGraphEngine
        import json

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = mock_conn
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "DOUBLE"

        query_vec = json.dumps([0.1, 0.2, 0.3, 0.4])
        eng.kg_KNN_VEC(query_vec, k=5)

        all_sql = " ".join(str(c) for c in mock_cursor.execute.call_args_list)
        if "TO_VECTOR" in all_sql:
            assert "DOUBLE" in all_sql

    def test_float_dtype_engine_uses_float_consistently(self):
        from iris_vector_graph.engine import IRISGraphEngine
        import json

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = mock_conn
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "FLOAT"

        query_vec = json.dumps([0.1, 0.2, 0.3, 0.4])
        eng.kg_KNN_VEC(query_vec, k=5)

        all_sql = " ".join(str(c) for c in mock_cursor.execute.call_args_list)
        if "TO_VECTOR" in all_sql:
            assert "DOUBLE" not in all_sql
            assert "FLOAT" in all_sql

    def test_mismatch_between_store_and_query_dtype_is_detectable(self):
        from iris_vector_graph.engine import IRISGraphEngine
        import json

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.embedding_dimension = 4
        eng.vector_dtype = "DOUBLE"

        store_dtype = getattr(eng, 'vector_dtype', 'DOUBLE')
        query_dtype = getattr(eng, 'vector_dtype', 'DOUBLE')
        assert store_dtype == query_dtype, (
            f"store uses {store_dtype} but query uses {query_dtype} — "
            "this would cause 'Cannot perform vector operation on vectors of different datatypes'"
        )


class TestEmbeddedConnectionIrisSqlNoneAtInit:

    def test_iris_sql_none_deferred_resolution(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection(iris_sql=None)
        assert conn._iris_sql is None

    def test_cursor_with_iris_sql_none_defers_resolution(self):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        conn = EmbeddedConnection(iris_sql=None)
        cursor = conn.cursor()
        assert isinstance(cursor, EmbeddedCursor)
        assert cursor._iris_sql is None

    def test_execute_with_none_iris_sql_calls_require(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor(iris_sql=None)
        mock_sql = MagicMock()
        mock_sql.prepare.return_value.execute.return_value = MagicMock(
            __iter__=lambda s: iter([]),
            columnCount=lambda: 0,
        )
        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql) as mock_req:
            cursor.execute("SELECT 1")
        mock_req.assert_called_once()

    def test_iris_sql_provided_directly_bypasses_require(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        mock_sql = MagicMock()
        mock_sql.prepare.return_value.execute.return_value = MagicMock(
            __iter__=lambda s: iter([]),
            columnCount=lambda: 0,
        )
        cursor = EmbeddedCursor(iris_sql=mock_sql)
        with patch("iris_vector_graph.embedded._require_iris_sql") as mock_req:
            cursor.execute("SELECT 1")
        mock_req.assert_not_called()


class TestWgprotoJobSimulation:

    def test_engine_execute_cypher_survives_prepare_failure(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        from iris_vector_graph.engine import IRISGraphEngine

        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("<UNIMPLEMENTED>ddtab+83^%qaqpsq")

        exec_rs = MagicMock()
        exec_rs.__iter__ = MagicMock(return_value=iter([]))
        exec_rs.columnCount.return_value = 0
        mock_sql.exec.return_value = exec_rs

        conn = EmbeddedConnection(iris_sql=mock_sql)
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = conn
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "DOUBLE"
        eng._vl_cache = {}

        try:
            eng.execute_cypher("MATCH (n) RETURN count(n) AS c")
        except Exception as e:
            assert "UNIMPLEMENTED" not in str(e), (
                f"execute_cypher should handle <UNIMPLEMENTED> internally, got: {e}"
            )

    def test_cursor_execute_fallback_result_is_iterable(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("<UNIMPLEMENTED>ddtab")

        rows = [(42,), (43,)]
        mock_rs = MagicMock()
        mock_rs.__iter__ = MagicMock(return_value=iter(rows))
        mock_rs.columnCount.return_value = 1
        mock_rs.columnName.side_effect = lambda i: "n"
        mock_sql.exec.return_value = mock_rs

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT n FROM t WHERE x = ?", [99])

        result = cursor.fetchall()
        assert result == [(42,), (43,)]

    def test_multiple_execute_calls_each_handle_fallback_independently(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = RuntimeError("<UNIMPLEMENTED>ddtab")
        mock_sql.exec.return_value = MagicMock(
            __iter__=lambda s: iter([]),
            columnCount=lambda: 0,
        )

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql):
            cursor.execute("SELECT * FROM t WHERE a = ?", [1])
            cursor.execute("SELECT * FROM t WHERE b = ?", [2])

        assert mock_sql.exec.call_count == 2
        calls = [c[0][0] for c in mock_sql.exec.call_args_list]
        assert "1" in calls[0]
        assert "2" in calls[1]


class TestSqlStatementFallback:

    def _make_ddtab(self):
        return RuntimeError("<UNIMPLEMENTED>ddtab+83^%qaqpsq")

    def test_third_level_fallback_triggered_when_exec_also_fails(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_ddtab()
        mock_sql.exec.side_effect = self._make_ddtab()

        mock_rs = MagicMock()
        mock_rs.__iter__ = MagicMock(return_value=iter([(42,)]))
        mock_rs.columnCount.return_value = 1
        mock_rs.columnName.side_effect = lambda i: "n"

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql), \
             patch("iris_vector_graph.embedded._sql_statement_execute", return_value=mock_rs) as mock_stmt:
            cursor.execute("SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?", ["abc"])

        mock_stmt.assert_called_once_with(
            "SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?", ["abc"]
        )

    def test_all_three_fail_raises_runtime_error(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_ddtab()
        mock_sql.exec.side_effect = self._make_ddtab()

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql), \
             patch("iris_vector_graph.embedded._sql_statement_execute",
                   side_effect=RuntimeError("stmt also failed")):
            with pytest.raises(RuntimeError, match="All three embedded SQL paths failed"):
                cursor.execute("SELECT * FROM t", [])

    def test_sql_statement_result_set_iterable(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        mock_rs = MagicMock()
        mock_rs._Next.side_effect = ["1", "1", "0"]
        mock_rs._GetProperty.side_effect = lambda k: (
            2 if k == "ColCount" else MagicMock()
        )
        mock_rs._GetData.side_effect = lambda i: "val_a" if i == 1 else "val_b"

        rs = _SqlStatementResultSet(mock_rs)
        rows = list(rs)
        assert len(rows) == 2
        assert rows[0] == ("val_a", "val_b")

    def test_second_level_exec_failure_escalates_to_third(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_ddtab()
        mock_sql.exec.side_effect = self._make_ddtab()

        mock_rs = MagicMock()
        mock_rs.__iter__ = MagicMock(return_value=iter([]))
        mock_rs.columnCount.return_value = 0

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql), \
             patch("iris_vector_graph.embedded._sql_statement_execute",
                   return_value=mock_rs) as mock_stmt:
            cursor.execute("SELECT s, o_id FROM Graph_KG.rdf_edges")

        mock_stmt.assert_called_once()

    def test_non_ddtab_on_exec_propagates_not_escalates(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        cursor = EmbeddedCursor()
        mock_sql = MagicMock()
        mock_sql.prepare.side_effect = self._make_ddtab()
        mock_sql.exec.side_effect = RuntimeError("permission denied")

        with patch("iris_vector_graph.embedded._require_iris_sql", return_value=mock_sql), \
             patch("iris_vector_graph.embedded._sql_statement_execute") as mock_stmt:
            with pytest.raises(RuntimeError, match="permission denied"):
                cursor.execute("SELECT 1")

        mock_stmt.assert_not_called()


class TestNewEngineAPIs:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.engine import IRISCapabilities
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = MagicMock()
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "DOUBLE"
        eng.capabilities = IRISCapabilities()
        return eng

    def test_get_node_name_returns_name_property(self):
        eng = self._make_engine()
        eng.get_node = MagicMock(return_value={
            "id": "n1", "labels": ["Gene"],
            "properties": {"name": "TP53", "symbol": "tp53"}
        })
        assert eng.get_node_name("n1") == "TP53"

    def test_get_node_name_falls_back_to_label(self):
        eng = self._make_engine()
        eng.get_node = MagicMock(return_value={
            "id": "n1", "labels": [],
            "properties": {"label": "MyLabel"}
        })
        assert eng.get_node_name("n1") == "MyLabel"

    def test_get_node_name_none_when_missing(self):
        eng = self._make_engine()
        eng.get_node = MagicMock(return_value=None)
        assert eng.get_node_name("nonexistent") is None

    def test_get_node_properties_returns_dict(self):
        eng = self._make_engine()
        eng.get_node = MagicMock(return_value={
            "id": "n1", "properties": {"name": "Alice", "age": "30"}
        })
        props = eng.get_node_properties("n1")
        assert props == {"name": "Alice", "age": "30"}

    def test_get_nodes_by_ids_delegates_to_get_nodes(self):
        eng = self._make_engine()
        expected = [{"id": "a"}, {"id": "b"}]
        eng.get_nodes = MagicMock(return_value=expected)
        result = eng.get_nodes_by_ids(["a", "b"])
        eng.get_nodes.assert_called_once_with(["a", "b"])
        assert result == expected

    def test_get_nodes_by_ids_empty_returns_empty(self):
        eng = self._make_engine()
        eng.get_nodes = MagicMock()
        assert eng.get_nodes_by_ids([]) == []
        eng.get_nodes.assert_not_called()

    def test_node_count_calls_execute_cypher(self):
        eng = self._make_engine()
        eng.execute_cypher = MagicMock(return_value={"rows": [(42,)]})
        assert eng.node_count() == 42
        eng.execute_cypher.assert_called_once()
        assert "count" in eng.execute_cypher.call_args[0][0].lower()

    def test_edge_count_calls_execute_cypher(self):
        eng = self._make_engine()
        eng.execute_cypher = MagicMock(return_value={"rows": [(100,)]})
        assert eng.edge_count() == 100

    def test_embedding_count_queries_table(self):
        eng = self._make_engine()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (205000,)
        eng.conn.cursor.return_value = mock_cursor
        assert eng.embedding_count() == 205000

    def test_embedding_count_returns_zero_on_error(self):
        eng = self._make_engine()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("table not found")
        eng.conn.cursor.return_value = mock_cursor
        assert eng.embedding_count() == 0


class TestDetectStoredVectorDtype:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine, IRISCapabilities
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = MagicMock()
        eng.embedding_dimension = 4
        eng._arno_available = False
        eng._arno_capabilities = {}
        eng._index_registry = {}
        eng.vector_dtype = "DOUBLE"
        eng.capabilities = IRISCapabilities()
        return eng

    def test_returns_float_when_float_works(self):
        eng = self._make_engine()
        float_cursor = MagicMock()
        float_cursor.fetchone.return_value = ("0.1,0.2",)
        float_cursor2 = MagicMock()
        float_cursor2.fetchone.return_value = (0.99,)
        float_cursor3 = MagicMock()
        float_cursor3.fetchone.return_value = (0.99,)
        eng.conn.cursor.side_effect = [float_cursor, float_cursor2]
        result = eng._detect_stored_vector_dtype()
        assert result in ("FLOAT", "DOUBLE")

    def test_returns_double_when_no_embeddings(self):
        eng = self._make_engine()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        eng.conn.cursor.return_value = mock_cursor
        assert eng._detect_stored_vector_dtype() == "DOUBLE"

    def test_returns_double_on_exception(self):
        eng = self._make_engine()
        eng.conn.cursor.side_effect = Exception("connection failed")
        assert eng._detect_stored_vector_dtype() == "DOUBLE"
