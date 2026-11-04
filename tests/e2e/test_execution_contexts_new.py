import os

import pytest
from unittest.mock import MagicMock

from tests.conftest import TEST_NAMESPACE, requires_running_container


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
        r = e.execute_cypher("MATCH (n:CtxPerson) RETURN n.name AS name LIMIT 1")
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
        cursor.execute("SELECT name, age FROM Graph_KG.nodes")
        assert cursor.description is not None
        assert len(cursor.description) == 2
        assert cursor.description[0][0] == "name"
        assert cursor.description[1][0] == "age"

    def test_embed_nodes_where_is_gone_not_deprecated(self):
        """`where=` was removed, so it raises rather than warning.

        It was a raw SQL fragment, deprecated in 2.x in favour of the typed
        `label=` / `node_ids=` / `exclude_pattern=` selection that `EmbedSelector`
        builds. This test asserted a `DeprecationWarning` and failed with `TypeError`
        once the parameter was dropped — the removal is real, and 4.0.0 records it.
        """
        engine = self._make_mock_engine({})
        engine.embedder = None
        engine.embedding_dimension = 768
        engine._connection_params = None
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        with pytest.raises(TypeError, match="where"):
            engine.embed_nodes(where="node_id LIKE 'test:%'", model=mock_model)

    def test_embed_nodes_label_param_accepted(self):
        engine = self._make_mock_engine({})
        engine.embedder = None
        engine.embedding_dimension = 768
        engine._connection_params = None
        mock_model = MagicMock()
        mock_model.encode.return_value = []
        result = engine.embed_nodes(label="Gene", model=mock_model)
        assert isinstance(result, dict)


# The ObjectScript engine (IVG.CypherEngine), run by `iris session` in the test container and this run's namespace.
# It named a container no setup has, and USER, so it never ran (4.6.0 R171).
_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


@pytest.fixture(scope="class")
def checkout_in_iris_python(request):
    """This checkout's `iris_vector_graph`, copied into the container for the session's embedded Python.

    `IVG.CypherEngine.Local()` imports the package inside IRIS, where nothing installs it (ADMIN_GUIDE, "IVG's Python
    package inside IRIS"). Each session puts the copy first on `sys.path` and imports it before the class does, so the
    checkout's code answers, not a copy someone installed in mgr/python (R234: a fresh container has none, and the
    tests failed with `No module named 'iris_vector_graph'`).
    """
    import subprocess
    import uuid
    from pathlib import Path

    dest = f"/tmp/ivg_ce_{uuid.uuid4().hex[:8]}"
    root = Path(__file__).resolve().parents[2]

    def docker(*args):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)

    docker("exec", "-u", "root", _CONTAINER, "mkdir", "-p", dest).check_returncode()
    docker("cp", str(root / "iris_vector_graph"), f"{_CONTAINER}:{dest}/iris_vector_graph").check_returncode()
    docker("exec", "-u", "root", _CONTAINER, "chmod", "-R", "a+rwX", dest).check_returncode()
    request.cls.checkout = dest
    yield dest
    docker("exec", "-u", "root", _CONTAINER, "rm", "-r", "-f", dest)


@requires_running_container(_CONTAINER)
@pytest.mark.usefixtures("iris_connection", "checkout_in_iris_python")  # the classes in TEST_NAMESPACE; the package
class TestObjectScriptCypherEngine:
    checkout = ""

    def _iris_exec(self, *statements: str) -> str:
        import subprocess

        pin = (
            'Set sys=##class(%SYS.Python).Import("sys")',
            f'Do sys.path.insert(0,"{self.checkout}")',
            'Set ivg=##class(%SYS.Python).Import("iris_vector_graph")',
            'Write "ivg_from=",ivg."__file__",!',
        )
        script = "\n".join(pin + statements) + "\nHalt\n"
        result = subprocess.run(
            ["docker", "exec", "-i", _CONTAINER, "iris", "session", "IRIS", "-U", TEST_NAMESPACE],
            input=script.encode(), capture_output=True, timeout=60,
        )
        return result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')

    def test_cypher_engine_class_compiled(self):
        out = self._iris_exec('Write "exists=",##class(%Dictionary.CompiledClass).%ExistsId("IVG.CypherEngine"),!')
        assert "exists=1" in out, f"IVG.CypherEngine not compiled. Output: {out}"

    def test_smoke_test_passes(self):
        out = self._iris_exec('Do ##class(IVG.CypherEngine).SmokeTest()')
        assert f"ivg_from={self.checkout}/" in out, f"the checkout's package did not load. Output: {out}"
        assert 'SmokeTest PASSED' in out, f"Smoke test failed. Output: {out}"

    def test_query_answers_a_known_count(self, iris_connection):
        import uuid

        from iris_vector_graph.engine import IRISGraphEngine

        label = f"CtxOS{uuid.uuid4().hex[:8]}"
        e = IRISGraphEngine(iris_connection)
        ids = [f"{label}:a", f"{label}:b"]
        try:
            for nid in ids:
                assert e.create_node(nid, labels=[label])
            out = self._iris_exec(
                f'Set r=##class(IVG.CypherEngine).Local().Query("MATCH (n:{label}) RETURN count(n) AS cnt")',
                'Write "error=",r.error,!',
                'Write "col=",r.columns.%Get(0),!',
                'Write "cnt=",r.rows.%Get(0).%Get(0),!',
            )
            assert "error=\n" in out.replace("\r", "") and "col=cnt" in out and "cnt=2" in out, out
        finally:
            for nid in ids:
                e.delete_node(nid)

    def test_get_labels_lists_a_known_label(self, iris_connection):
        import uuid

        from iris_vector_graph.engine import IRISGraphEngine

        label = f"CtxOS{uuid.uuid4().hex[:8]}"
        e = IRISGraphEngine(iris_connection)
        assert e.create_node(f"{label}:a", labels=[label])
        try:
            out = self._iris_exec(
                'Set labels=##class(IVG.CypherEngine).Local().GetLabels(), found=0',
                f'For i=0:1:labels.%Size()-1 {{ Set:labels.%Get(i)="{label}" found=1 }}',
                'Write "found=",found,!',
            )
            assert "found=1" in out, out
        finally:
            e.delete_node(f"{label}:a")
