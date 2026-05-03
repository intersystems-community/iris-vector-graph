import pytest
import warnings
from unittest.mock import MagicMock, patch


@pytest.fixture
def dbapi_engine(iris_connection, iris_master_cleanup):
    import uuid
    from iris_vector_graph.engine import IRISGraphEngine
    pfx = f"ctx_{uuid.uuid4().hex[:8]}"
    e = IRISGraphEngine(iris_connection, embedding_dimension=768)
    e.initialize_schema()
    e.create_node(f"{pfx}:alice", labels=["CtxPerson"], properties={"name": "CtxAlice"})
    e.create_node(f"{pfx}:bob", labels=["CtxPerson"], properties={"name": "CtxBob"})
    e.create_edge(f"{pfx}:alice", "CTX_KNOWS", f"{pfx}:bob")
    return e, pfx


class TestExternalDBAPI:

    def test_execute_cypher_returns_columns(self, dbapi_engine):
        e, pfx = dbapi_engine
        r = e.execute_cypher(f"MATCH (n:CtxPerson) RETURN n.name AS name LIMIT 1")
        assert "columns" in r
        assert "name" in r["columns"]

    def test_get_labels(self, dbapi_engine):
        e, pfx = dbapi_engine
        labels = e.get_labels()
        assert isinstance(labels, list)
        assert "CtxPerson" in labels

    def test_get_node_count(self, dbapi_engine):
        e, pfx = dbapi_engine
        count = e.get_node_count()
        assert isinstance(count, int)
        assert count >= 2

    def test_get_node_count_with_label(self, dbapi_engine):
        e, pfx = dbapi_engine
        count = e.get_node_count(label="CtxPerson")
        assert count >= 2

    def test_get_edge_count(self, dbapi_engine):
        e, pfx = dbapi_engine
        count = e.get_edge_count()
        assert isinstance(count, int)
        assert count >= 1

    def test_get_label_distribution(self, dbapi_engine):
        e, pfx = dbapi_engine
        dist = e.get_label_distribution()
        assert isinstance(dist, dict)
        assert "CtxPerson" in dist

    def test_get_relationship_types(self, dbapi_engine):
        e, pfx = dbapi_engine
        types = e.get_relationship_types()
        assert isinstance(types, list)
        assert "CTX_KNOWS" in types

    def test_get_property_keys(self, dbapi_engine):
        e, pfx = dbapi_engine
        keys = e.get_property_keys()
        assert isinstance(keys, list)
        assert "name" in keys

    def test_node_exists_true(self, dbapi_engine):
        e, pfx = dbapi_engine
        assert e.node_exists(f"{pfx}:alice") is True

    def test_node_exists_false(self, dbapi_engine):
        e, pfx = dbapi_engine
        assert e.node_exists("ctx_never_exists_xyz_99999") is False

    def test_columns_never_empty_for_returning_query(self, dbapi_engine):
        e, pfx = dbapi_engine
        r = e.execute_cypher("RETURN 1 AS n")
        assert r["columns"] != []
        assert "n" in r["columns"]


class TestEmbeddedConnectionUnit:

    def _make_mock_engine(self, rows_by_sql=None):
        from iris_vector_graph.engine import IRISGraphEngine
        rows_by_sql = rows_by_sql or {}

        def cursor_factory():
            cursor = MagicMock()
            cursor.description = [("col",)]
            def execute(sql, params=None):
                for key, rows in rows_by_sql.items():
                    if key in sql:
                        cursor.fetchall.return_value = rows
                        cursor.fetchone.return_value = rows[0] if rows else None
                        return
                cursor.fetchall.return_value = []
                cursor.fetchone.return_value = None
            cursor.execute.side_effect = execute
            return cursor

        conn = MagicMock()
        conn.cursor.side_effect = cursor_factory
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine._schema_prefix = "Graph_KG"
        return engine

    def test_embedded_cursor_passes_iris_sql(self):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        mock_sql = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columnCount.return_value = 1
        mock_rs.columnName.return_value = "x"
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = mock_rs
        mock_sql.prepare.return_value = mock_stmt
        conn = EmbeddedConnection(iris_sql=mock_sql)
        cursor = conn.cursor()
        assert isinstance(cursor, EmbeddedCursor)
        cursor.execute("SELECT 1 AS x")
        mock_sql.prepare.assert_called_once_with("SELECT 1 AS x")

    def test_embedded_cursor_no_iris_sql_falls_back_to_require(self):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        conn = EmbeddedConnection()
        cursor = conn.cursor()
        assert isinstance(cursor, EmbeddedCursor)
        assert cursor._iris_sql is None

    def test_get_labels_via_mock(self):
        engine = self._make_mock_engine({"rdf_labels": [("Person",), ("Drug",)]})
        assert engine.get_labels() == ["Person", "Drug"]

    def test_get_node_count_via_mock(self):
        engine = self._make_mock_engine({"Graph_KG.nodes": [(42,)]})
        assert engine.get_node_count() == 42

    def test_node_exists_true_via_mock(self):
        engine = self._make_mock_engine({"Graph_KG.nodes": [(1,)]})
        assert engine.node_exists("n1") is True

    def test_node_exists_false_via_mock(self):
        engine = self._make_mock_engine({})
        assert engine.node_exists("missing") is False

    def test_columns_never_empty_with_description(self):
        from iris_vector_graph.embedded import EmbeddedCursor
        mock_sql = MagicMock()
        mock_rs = MagicMock()
        mock_rs.columnCount.return_value = 2
        mock_rs.columnName.side_effect = lambda i: ["name", "age"][i - 1]
        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = mock_rs
        mock_sql.prepare.return_value = mock_stmt
        cursor = EmbeddedCursor(iris_sql=mock_sql)
        cursor.execute("SELECT name, age FROM nodes")
        assert cursor.description is not None
        assert len(cursor.description) == 2
        assert cursor.description[0][0] == "name"
        assert cursor.description[1][0] == "age"

    def test_embed_nodes_where_deprecation(self):
        engine = self._make_mock_engine({})
        engine.embedder = None
        engine.embedding_dimension = 768
        engine._connection_params = None
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            engine.embed_nodes(where="node_id LIKE 'test:%'", model=mock_model)
        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1

    def test_embed_nodes_label_param_accepted(self):
        engine = self._make_mock_engine({})
        engine.embedder = None
        engine.embedding_dimension = 768
        engine._connection_params = None
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(label="Gene", model=mock_model)
        assert isinstance(result, dict)


@pytest.mark.skipif(
    __import__('subprocess').run(
        ['docker', 'inspect', 'iris-enterprise-2026'],
        capture_output=True
    ).returncode != 0,
    reason="iris-enterprise-2026 container not running"
)
class TestObjectScriptCypherEngine:

    def _iris_exec(self, *statements: str) -> str:
        import subprocess, textwrap
        script_lines = '\n'.join(statements) + '\nHalt\n'
        shell_script = textwrap.dedent(f'''
            /usr/irissys/bin/irissession IRIS -U USER << "EOF"
            {script_lines}
            EOF
        ''').strip()
        result = subprocess.run(
            ['docker', 'exec', 'iris-enterprise-2026', 'bash', '-c', shell_script],
            capture_output=True, timeout=60
        )
        return result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')

    def test_cypher_engine_class_compiled(self):
        out = self._iris_exec('Write ##class(%Dictionary.CompiledClass).%ExistsId("IVG.CypherEngine"),!')
        assert '1' in out, f"IVG.CypherEngine not compiled. Output: {out}"

    def test_smoke_test_passes(self):
        out = self._iris_exec('Do ##class(IVG.CypherEngine).SmokeTest()')
        assert 'SmokeTest PASSED' in out, f"Smoke test failed. Output: {out}"

    def test_query_returns_cnt_column(self):
        out = self._iris_exec(
            'Set r=##class(IVG.CypherEngine).Local().Query("MATCH (n) RETURN count(n) AS cnt")',
            'Write r.error,!',
            'Write r.columns.%Get(0),!'
        )
        assert 'cnt' in out, f"Expected 'cnt' column. Output: {out}"

    def test_get_labels_returns_array(self):
        out = self._iris_exec(
            'Set labels=##class(IVG.CypherEngine).Local().GetLabels()',
            'Write (labels.%Size()>=0),!'
        )
        assert '1' in out, f"GetLabels failed. Output: {out}"
