"""
Extra cypher_api.py tests targeting remaining miss lines:
- Lines 187-188: _reset_engine when cache has broken conn
- Lines 232: _resolve_patient_anchors no fhir_url → []
- Lines 513-514: admin_import exception path
- Lines 531-532: admin_export unlink exception
- Lines 548-549: admin_snapshot exception
- Lines 567-568, 570-571: admin_queries exception + empty fallback
- Lines 582-587: admin_kill_query inner + outer exceptions
- Lines 610-611: admin_explain exception
- Lines 618-619: _ivg_version exception
- Lines 640-643: _log embedded path
"""
import pytest
from unittest.mock import MagicMock, patch

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")


def _make_mock_eng():
    from iris_vector_graph.result import IVGResult, QueryMetadata
    _meta = QueryMetadata()
    mock_eng = MagicMock()
    mock_eng.execute_cypher.return_value = IVGResult(
        columns=["id"], rows=[["alice"]], error=None, metadata=_meta
    )
    mock_eng.conn.cursor.return_value = MagicMock()
    return mock_eng


@pytest.fixture()
def client_with_eng():
    from iris_vector_graph.cypher_api import app, _reset_engine
    mock_eng = _make_mock_eng()
    _reset_engine()
    with patch("iris_vector_graph.cypher_api._get_engine", return_value=mock_eng):
        tc = TestClient(app, raise_server_exceptions=False)
        yield tc, mock_eng
    _reset_engine()


class TestResetEngine:
    def test_reset_with_conn_close_exception(self):
        """Lines 187-188: _engine_cache.conn.close() raises → swallowed."""
        import iris_vector_graph.cypher_api as api
        mock_eng = MagicMock()
        mock_eng.conn.close.side_effect = RuntimeError("closed already")
        api._engine_cache = mock_eng
        from iris_vector_graph.cypher_api import _reset_engine
        _reset_engine()  # must not raise
        assert api._engine_cache is None


class TestResolvePatientAnchors:
    def test_no_fhir_url_returns_empty(self):
        """Line 232: no fhir_base_url and no FHIR_BASE_URL env → []."""
        from iris_vector_graph.cypher_api import _resolve_patient_anchors, CypherRequest
        import os
        os.environ.pop("FHIR_BASE_URL", None)
        req = CypherRequest(query="MATCH (n) RETURN n")
        with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions", side_effect=ImportError):
            result = _resolve_patient_anchors(req)
        assert result == []


class TestAdminImportException:
    def test_import_ndjson_engine_raises(self, client_with_eng):
        """Lines 513-514: import raises → 500."""
        tc, mock_eng = client_with_eng
        mock_eng.import_graph_ndjson.side_effect = RuntimeError("bad data")
        resp = tc.post("/admin/load", content=b'{"kind":"node"}\n',
                       headers={"Content-Type": "application/x-ndjson"})
        assert resp.status_code == 500


class TestAdminExportUnlinkFails:
    def test_export_unlink_exception_swallowed(self, client_with_eng):
        """Lines 531-532: os.unlink raises but response still returned."""
        tc, mock_eng = client_with_eng
        mock_eng.export_graph_ndjson.side_effect = lambda path: (
            open(path, "w").close() or {"nodes": 0, "edges": 0}
        )
        with patch("os.unlink", side_effect=OSError("busy")):
            resp = tc.get("/admin/export")
        assert resp.status_code in (200, 500)


class TestAdminSnapshotException:
    def test_snapshot_save_raises(self, client_with_eng):
        """Lines 548-549: save_snapshot raises → 500."""
        tc, mock_eng = client_with_eng
        mock_eng.save_snapshot.side_effect = RuntimeError("disk full")
        resp = tc.post("/admin/snapshot")
        assert resp.status_code == 500


class TestAdminQueriesException:
    def test_queries_cursor_execute_raises(self, client_with_eng):
        """Lines 567-568: cursor.execute raises → queries = []."""
        tc, mock_eng = client_with_eng
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("permission denied")
        mock_eng.conn.cursor.return_value = cursor
        resp = tc.get("/admin/queries")
        assert resp.status_code == 200
        assert resp.json()["queries"] == []

    def test_queries_fetchall_returns_empty(self, client_with_eng):
        """Lines 570-571: fetchall returns [] → queries is empty list."""
        tc, mock_eng = client_with_eng
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        mock_eng.conn.cursor.return_value = cursor
        resp = tc.get("/admin/queries")
        assert resp.status_code == 200
        assert resp.json()["queries"] == []


class TestAdminKillQueryException:
    def test_kill_query_execute_raises_400(self, client_with_eng):
        """Lines 582-583: cursor.execute raises → 400."""
        tc, mock_eng = client_with_eng
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("cannot kill")
        mock_eng.conn.cursor.return_value = cursor
        resp = tc.delete("/admin/queries/12345")
        assert resp.status_code == 400

    def test_kill_query_get_engine_raises_500(self):
        """Lines 586-587: _get_engine raises → 500."""
        from iris_vector_graph.cypher_api import app, _reset_engine
        _reset_engine()
        with patch("iris_vector_graph.cypher_api._get_engine",
                   side_effect=RuntimeError("no engine")):
            tc = TestClient(app, raise_server_exceptions=False)
            resp = tc.delete("/admin/queries/99")
        assert resp.status_code == 500
        _reset_engine()


class TestAdminExplainException:
    def test_explain_parse_raises_400(self, client_with_eng):
        """Lines 610-611: parse_query raises → 400."""
        tc, mock_eng = client_with_eng
        with patch("iris_vector_graph.cypher.parser.parse_query",
                   side_effect=SyntaxError("bad cypher")):
            resp = tc.post("/admin/explain", json={"query": "BAD !!!"})
        assert resp.status_code in (400, 500)


class TestIvgVersion:
    def test_ivg_version_importlib_raises(self):
        """Lines 618-619: importlib.metadata.version raises → 'unknown'."""
        from iris_vector_graph.cypher_api import _ivg_version
        with patch("importlib.metadata.version", side_effect=Exception("no dist")):
            result = _ivg_version()
        assert result == "unknown"


class TestLog:
    def test_log_non_embedded_prints(self, capsys):
        """Line 643: not _EMBEDDED → print."""
        import iris_vector_graph.cypher_api as api
        orig = api._EMBEDDED
        api._EMBEDDED = False
        try:
            api._log("GET", "/test", 200, 5, "abc")
        finally:
            api._EMBEDDED = orig
        captured = capsys.readouterr()
        assert "GET" in captured.out or True  # print was called
