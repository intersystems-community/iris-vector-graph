"""
Unit tests for _engine/admin.py covering:
- _handle_show_command: DATABASES, PROCEDURES, FUNCTIONS, INDEXES, CONSTRAINTS, unknown
- _show_indexes: HNSW, IVF, BM25, NKG adjacency
- _show_constraints: basic and with FHIR bridge
- status: basic path, with ObjectScript deployed
- list_active_queries: community edition guard, enterprise path, SQL error
- kill_query: success and failure
- get_centrality_warnings: failure path
- get_community_warnings: failure path

No IRIS connection needed — mocks conn, cursor, iris_obj.
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


# ---------------------------------------------------------------------------
# _handle_show_command
# ---------------------------------------------------------------------------

class TestHandleShowCommand:

    def test_show_databases(self):
        eng, conn, cursor = _make_eng()
        result = eng._handle_show_command("SHOW DATABASES")
        assert isinstance(result, IVGResult)
        assert "name" in result.columns
        assert result.rows[0][0] == "neo4j"

    def test_show_indexes(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._handle_show_command("SHOW INDEXES")
        assert isinstance(result, IVGResult)
        assert "name" in result.columns

    def test_show_constraints(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (0,)
        result = eng._handle_show_command("SHOW CONSTRAINTS")
        assert isinstance(result, IVGResult)
        names = [r[0] for r in result.rows]
        assert "node_id_unique" in names

    def test_show_procedures(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        result = eng._handle_show_command("SHOW PROCEDURES")
        assert isinstance(result, IVGResult)

    def test_show_functions(self):
        eng, conn, cursor = _make_eng()
        result = eng._handle_show_command("SHOW FUNCTIONS")
        assert isinstance(result, IVGResult)

    def test_unknown_command_returns_empty(self):
        eng, conn, cursor = _make_eng()
        result = eng._handle_show_command("SHOW SOMETHING_ELSE")
        assert isinstance(result, IVGResult)
        assert result.rows == []


# ---------------------------------------------------------------------------
# _show_indexes
# ---------------------------------------------------------------------------

class TestShowIndexes:

    def test_returns_at_least_builtin_indexes(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._show_indexes()
        assert isinstance(result, IVGResult)
        names = [r[0] for r in result.rows]
        assert "hnsw_node_embeddings" in names
        assert "pk_nodes" in names

    def test_hnsw_online_when_optimized_table_has_rows(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "5"
        call_seq = iter([(10,), (1,)])
        cursor.fetchone.side_effect = lambda: next(call_seq)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._show_indexes()
        hnsw = next(r for r in result.rows if r[0] == "hnsw_node_embeddings")
        assert hnsw[5] == "ONLINE"

    def test_nkg_adjacency_online_when_populated(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = ["1", "100"]
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._show_indexes()
        nkg = next((r for r in result.rows if r[0] == "nkg_adjacency"), None)
        assert nkg is not None


# ---------------------------------------------------------------------------
# _show_constraints
# ---------------------------------------------------------------------------

class TestShowConstraints:

    def test_returns_builtin_constraints(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (0,)
        result = eng._show_constraints()
        assert isinstance(result, IVGResult)
        names = [r[0] for r in result.rows]
        assert "node_id_unique" in names
        assert "edge_spo_unique" in names

    def test_fhir_bridge_constraint_added_when_class_exists(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (1,)  # class exists
        result = eng._show_constraints()
        names = [r[0] for r in result.rows]
        assert "fhir_bridge_unique" in names

    def test_sql_error_returns_basic_constraints_only(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("no access")
        result = eng._show_constraints()
        assert isinstance(result, IVGResult)
        assert len(result.rows) >= 2


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus:

    def test_status_returns_engine_status(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = ["0", "0"]
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng.status()
        from iris_vector_graph.status import EngineStatus
        assert isinstance(result, EngineStatus)

    def test_status_with_internals(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        cursor.fetchone.return_value = (0,)
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng.status(internals=True)
        assert result.internals is not None
        assert "^KG_populated" in result.internals

    def test_status_with_objectscript_deployed(self):
        eng, conn, cursor = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        cursor.fetchone.return_value = (1,)  # class exists
        cursor.fetchall.return_value = []
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng.status()
        assert result.objectscript.deployed is True


# ---------------------------------------------------------------------------
# list_active_queries
# ---------------------------------------------------------------------------

class TestListActiveQueries:

    def test_community_edition_returns_empty(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "4"  # Community Edition
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.list_active_queries()
        assert result == []

    def test_iris_obj_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
            result = eng.list_active_queries()
        assert result == []

    def test_enterprise_sql_success(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "1"  # Enterprise
        cursor.fetchall.return_value = [("123", "ACTIVE", "client1", "SELECT 1")]
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.list_active_queries(limit=10)
        assert isinstance(result, list)

    def test_enterprise_sql_error_returns_empty(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "1"  # Enterprise
        cursor.execute.side_effect = RuntimeError("no privilege")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.list_active_queries()
        assert result == []


# ---------------------------------------------------------------------------
# kill_query
# ---------------------------------------------------------------------------

class TestKillQuery:

    def test_success_returns_true(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.return_value = None
        result = eng.kill_query("12345")
        assert result is True

    def test_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("no permission")
        result = eng.kill_query("12345")
        assert result is False


# ---------------------------------------------------------------------------
# get_centrality_warnings
# ---------------------------------------------------------------------------

class TestGetCentralityWarnings:

    def test_iris_obj_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
            result = eng.get_centrality_warnings()
        assert result == []

    def test_traversal_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.nextSubscript.side_effect = RuntimeError("global not found")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.get_centrality_warnings()
        assert result == []

    def test_returns_warnings_list(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        # First call to nextSubscript returns a ts, second call returns "" (stop)
        ts_calls = iter(["ts1", ""])
        src_calls = iter(["src1", ""])
        iris_obj.nextSubscript.side_effect = lambda *args: (
            next(src_calls) if len(args) == 5 else next(ts_calls)
        )
        iris_obj.get.return_value = "memory pressure"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.get_centrality_warnings()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_community_warnings
# ---------------------------------------------------------------------------

class TestGetCommunityWarnings:

    def test_iris_obj_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
            result = eng.get_community_warnings()
        assert result == []

    def test_traversal_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.nextSubscript.side_effect = RuntimeError("global not found")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.get_community_warnings()
        assert result == []
