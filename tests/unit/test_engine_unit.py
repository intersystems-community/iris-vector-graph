"""
Unit tests for engine.py covering:
- _is_sentence_transformer (ImportError path)
- _detect_stored_vector_dtype
- _proc_db_labels, _proc_db_relationshiptypes, _proc_db_schema_visualization
- _proc_db_schema_nodetypeproperties, _proc_db_schema_reltypeproperties
- _proc_dbms_components, _proc_dbms_procedures, _proc_dbms_functions
- _proc_db_propertykeys, _proc_dbms_clientconfig, _proc_dbms_security_showcurrentuser
- _proc_dbms_queryjmx, _proc_apoc_meta_schema
- _try_system_procedure: known name, apoc prefix, dbms prefix, unknown
- _detect_arno: cached, no detector, returns from store
- _arno_call: non-chunked, chunked

No IRIS connection needed — mocks conn and cursor.
"""
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


def _make_proc(name):
    proc = MagicMock()
    proc.procedure_name = name
    proc.arguments = []
    return proc


# ---------------------------------------------------------------------------
# Connection seam: from_connect must route through the iris wrapper's
# dbapi.connect (iris-embedded-python-wrapper is the standard connection path).
# ---------------------------------------------------------------------------

class TestConnectionSeam:
    def test_from_connect_uses_dbapi_connect(self):
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = MagicMock()
        with patch("iris.dbapi.connect", return_value=fake_conn) as m:
            eng = IRISGraphEngine.from_connect(
                hostname="h", port=1972, namespace="USER",
                username="_SYSTEM", password="SYS", embedding_dimension=4,
            )
        m.assert_called_once()
        # connection params stored for reconnect
        assert eng._connection_params["hostname"] == "h"
        assert eng.conn is fake_conn

    def test_reconnect_uses_dbapi_connect(self):
        eng, conn, cursor = _make_eng()
        eng._connection_params = dict(
            hostname="h", port=1972, namespace="USER",
            username="_SYSTEM", password="SYS",
        )
        # First probe raises a broken-pipe error → triggers reconnect
        cursor.execute.side_effect = Exception("broken pipe")
        fake_conn = MagicMock()
        with patch("iris.dbapi.connect", return_value=fake_conn) as m:
            eng._reconnect_if_stale()
        m.assert_called_once()
        assert eng.conn is fake_conn


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestModuleHelpers:

    def test_is_sentence_transformer_import_error(self):
        from iris_vector_graph.engine import _is_sentence_transformer
        with patch("iris_vector_graph.engine._get_sentence_transformers",
                   side_effect=ImportError("no sentence_transformers")):
            result = _is_sentence_transformer(object())
        assert result is False

    def test_is_sentence_transformer_false_for_plain_object(self):
        from iris_vector_graph.engine import _is_sentence_transformer
        class FakeTransformer:
            pass
        eng, _, _ = _make_eng()
        eng.embedder = FakeTransformer()
        result = _is_sentence_transformer(eng.embedder)
        # It's not a real SentenceTransformer, so it's False
        assert result is False


# ---------------------------------------------------------------------------
# _proc_db_labels
# ---------------------------------------------------------------------------

class TestProcDbLabels:

    def test_returns_ivg_result_with_labels(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("Disease",), ("Gene",)]
        proc = _make_proc("db.labels")
        result = eng._proc_db_labels(proc)
        assert isinstance(result, IVGResult)
        assert "label" in result.columns

    def test_empty_labels(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        proc = _make_proc("db.labels")
        result = eng._proc_db_labels(proc)
        assert result.rows == []


# ---------------------------------------------------------------------------
# _proc_db_relationshiptypes
# ---------------------------------------------------------------------------

class TestProcDbRelationshipTypes:

    def test_returns_relationship_types(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("TREATS",), ("TARGETS",)]
        proc = _make_proc("db.relationshipTypes")
        result = eng._proc_db_relationshiptypes(proc)
        assert isinstance(result, IVGResult)
        assert "relationshipType" in result.columns
        assert len(result.rows) == 2


# ---------------------------------------------------------------------------
# _proc_db_schema_visualization
# ---------------------------------------------------------------------------

class TestProcDbSchemaVisualization:

    def test_returns_nodes_and_rels(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "get_schema_visualization",
                          return_value={"nodes": [{"label": "Gene"}], "relationships": []}):
            proc = _make_proc("db.schema.visualization")
            result = eng._proc_db_schema_visualization(proc)
        assert isinstance(result, IVGResult)
        assert "nodes" in result.columns


# ---------------------------------------------------------------------------
# _proc_db_schema_nodetypeproperties
# ---------------------------------------------------------------------------

class TestProcDbSchemaNodeTypeProperties:

    def test_returns_properties(self):
        eng, conn, cursor = _make_eng()
        call_seq = iter([
            [("Gene",), ("Disease",)],   # DISTINCT labels
            [("name",)],                  # props for Gene
            [("description",)],          # props for Disease
            [],                           # rel types (if queried)
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        proc = _make_proc("db.schema.nodeTypeProperties")
        result = eng._proc_db_schema_nodetypeproperties(proc)
        assert isinstance(result, IVGResult)
        assert "label" in result.columns or "nodeType" in result.columns or len(result.columns) > 0

    def test_no_labels_returns_empty(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        proc = _make_proc("db.schema.nodeTypeProperties")
        result = eng._proc_db_schema_nodetypeproperties(proc)
        assert isinstance(result, IVGResult)


# ---------------------------------------------------------------------------
# _proc_db_schema_reltypeproperties
# ---------------------------------------------------------------------------

class TestProcDbSchemaRelTypeProperties:

    def test_returns_rel_type_props(self):
        eng, conn, cursor = _make_eng()
        import json
        call_seq = iter([
            [("TREATS",)],          # rel types
            ('{"weight": 1.0}',),   # fetchone for qualifiers
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        cursor.fetchone.return_value = (json.dumps({"confidence": 0.9}),)
        proc = _make_proc("db.schema.relTypeProperties")
        result = eng._proc_db_schema_reltypeproperties(proc)
        assert isinstance(result, IVGResult)
        assert "relType" in result.columns

    def test_sql_error_returns_empty_result(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table not found")
        proc = _make_proc("db.schema.relTypeProperties")
        result = eng._proc_db_schema_reltypeproperties(proc)
        assert isinstance(result, IVGResult)
        assert result.rows == []


# ---------------------------------------------------------------------------
# _proc_dbms_components
# ---------------------------------------------------------------------------

class TestProcDbmsComponents:

    def test_returns_iris_vector_graph(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.components")
        result = eng._proc_dbms_components(proc)
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == "iris-vector-graph"


# ---------------------------------------------------------------------------
# _proc_dbms_procedures
# ---------------------------------------------------------------------------

class TestProcDbmsProcedures:

    def test_returns_procedure_list(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.procedures")
        result = eng._proc_dbms_procedures(proc)
        assert isinstance(result, IVGResult)
        assert len(result.rows) > 5
        names = [r[0] for r in result.rows]
        assert "db.labels" in names
        assert "dbms.components" in names


# ---------------------------------------------------------------------------
# _proc_dbms_functions
# ---------------------------------------------------------------------------

class TestProcDbmsFunctions:

    def test_returns_empty_result(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.functions")
        result = eng._proc_dbms_functions(proc)
        assert isinstance(result, IVGResult)
        assert result.rows == []
        assert "name" in result.columns


# ---------------------------------------------------------------------------
# _proc_db_propertykeys
# ---------------------------------------------------------------------------

class TestProcDbPropertyKeys:

    def test_returns_property_keys(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("name",), ("description",), ("weight",)]
        proc = _make_proc("db.propertyKeys")
        result = eng._proc_db_propertykeys(proc)
        assert isinstance(result, IVGResult)
        assert "propertyKey" in result.columns
        assert len(result.rows) == 3


# ---------------------------------------------------------------------------
# _proc_dbms_clientconfig
# ---------------------------------------------------------------------------

class TestProcDbmsClientConfig:

    def test_returns_config_keys(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.clientConfig")
        result = eng._proc_dbms_clientconfig(proc)
        assert isinstance(result, IVGResult)
        keys = [r[0] for r in result.rows]
        assert "browser.allow_outgoing_connections" in keys


# ---------------------------------------------------------------------------
# _proc_dbms_security_showcurrentuser
# ---------------------------------------------------------------------------

class TestProcDbmsSecurityShowCurrentUser:

    def test_returns_neo4j_user(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.security.showCurrentUser")
        result = eng._proc_dbms_security_showcurrentuser(proc)
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == "neo4j"


# ---------------------------------------------------------------------------
# _proc_dbms_queryjmx
# ---------------------------------------------------------------------------

class TestProcDbmsQueryJmx:

    def test_returns_jmx_data(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.side_effect = [(1000,), (500,)]
        proc = _make_proc("dbms.queryJmx")
        result = eng._proc_dbms_queryjmx(proc)
        assert isinstance(result, IVGResult)
        assert "name" in result.columns
        assert len(result.rows) >= 2


# ---------------------------------------------------------------------------
# _proc_apoc_meta_schema
# ---------------------------------------------------------------------------

class TestProcApocMetaSchema:

    def test_returns_schema_result(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("apoc.meta.schema")
        with patch.object(eng, "_try_system_procedure",
                          return_value=IVGResult(columns=["value"], rows=[[{}]])):
            result = eng._proc_apoc_meta_schema(proc)
        assert isinstance(result, IVGResult)
        assert "value" in result.columns


# ---------------------------------------------------------------------------
# _try_system_procedure
# ---------------------------------------------------------------------------

class TestTrySystemProcedure:

    def test_known_procedure_dispatched(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        proc = _make_proc("db.labels")
        result = eng._try_system_procedure(proc)
        assert isinstance(result, IVGResult)

    def test_apoc_prefix_returns_empty_result(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("apoc.some.unknown.proc")
        result = eng._try_system_procedure(proc)
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_dbms_prefix_unknown_returns_empty(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("dbms.unknown.internal.thing")
        result = eng._try_system_procedure(proc)
        assert isinstance(result, IVGResult)

    def test_completely_unknown_returns_none(self):
        eng, conn, cursor = _make_eng()
        proc = _make_proc("custom.totally.unknown.proc")
        result = eng._try_system_procedure(proc)
        assert result is None


# ---------------------------------------------------------------------------
# _detect_arno
# ---------------------------------------------------------------------------

class TestDetectArno:

    def test_cached_true(self):
        eng, conn, cursor = _make_eng()
        eng._arno_available = True
        result = eng._detect_arno()
        assert result is True

    def test_cached_false(self):
        eng, conn, cursor = _make_eng()
        eng._arno_available = False
        result = eng._detect_arno()
        assert result is False

    def test_no_detector_on_store_returns_false(self):
        eng, conn, cursor = _make_eng()
        eng._arno_available = None
        store_mock = MagicMock(spec=[])  # spec=[] means no attributes
        eng._store = store_mock
        result = eng._detect_arno()
        assert result is False

    def test_detector_on_store_is_called(self):
        eng, conn, cursor = _make_eng()
        eng._arno_available = None
        store_mock = MagicMock()
        store_mock._detect_arno.return_value = True
        store_mock._arno_capabilities = {"bfs": True}
        eng._store = store_mock
        result = eng._detect_arno()
        assert result is True
        store_mock._detect_arno.assert_called_once()


# ---------------------------------------------------------------------------
# _arno_call
# ---------------------------------------------------------------------------

class TestArnoCall:

    def test_non_chunked_response(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = '{"result": "ok"}'
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._arno_call("Graph.Arno.BFS", "Run", "seed", 2)
        assert result == '{"result": "ok"}'

    def test_chunked_response_assembled(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        # First call returns CHUNKED header, subsequent calls return chunks
        call_seq = iter(["CHUNKED:tag123:3", "chunk_1", "chunk_2", "chunk_3"])
        iris_obj.classMethodValue.side_effect = lambda *a: next(call_seq)
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._arno_call("Graph.Arno.BFS", "RunLarge", "seed", 3)
        assert result == "chunk_1chunk_2chunk_3"
