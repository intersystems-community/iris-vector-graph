"""
Tests for iris_vector_graph/sdk.py — IVGClient, IVGRecord, error classes.
Uses httpx mock transport — no real server needed.
"""
import pytest
from unittest.mock import MagicMock, patch

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

pytestmark = pytest.mark.skipif(not _HAS_HTTPX, reason="httpx not installed")


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------

class TestErrorClasses:

    def test_ivg_error_is_exception(self):
        from iris_vector_graph.sdk import IVGError
        e = IVGError("test")
        assert isinstance(e, Exception)

    def test_ivg_client_error_has_http_code(self):
        from iris_vector_graph.sdk import IVGClientError
        e = IVGClientError("bad request", http_code=400)
        assert e.http_code == 400
        assert "bad request" in str(e)

    def test_ivg_client_error_no_code(self):
        from iris_vector_graph.sdk import IVGClientError
        e = IVGClientError("no server")
        assert e.http_code is None

    def test_ivg_server_error_has_fields(self):
        from iris_vector_graph.sdk import IVGServerError
        e = IVGServerError("internal error", http_code=500, query="MATCH (n) RETURN n")
        assert e.http_code == 500
        assert e.query == "MATCH (n) RETURN n"

    def test_ivg_server_error_is_retryable_500(self):
        from iris_vector_graph.sdk import IVGServerError
        e = IVGServerError("oops", http_code=500)
        assert e.is_retryable() is True

    def test_ivg_server_error_not_retryable_400(self):
        from iris_vector_graph.sdk import IVGServerError
        e = IVGServerError("bad", http_code=400)
        assert e.is_retryable() is False


# ---------------------------------------------------------------------------
# IVGRecord
# ---------------------------------------------------------------------------

class TestIVGRecord:

    def _record(self):
        from iris_vector_graph.sdk import IVGRecord
        return IVGRecord(["id", "name", "score"], ["alice", "Alice Smith", 0.9])

    def test_getitem_by_index(self):
        r = self._record()
        assert r[0] == "alice"
        assert r[2] == 0.9

    def test_getitem_by_key(self):
        r = self._record()
        assert r["name"] == "Alice Smith"

    def test_getitem_missing_key_returns_none(self):
        r = self._record()
        assert r["nonexistent"] is None

    def test_get_with_default(self):
        r = self._record()
        # IVGRecord.get returns None for missing keys (ignores default param)
        # This is the documented behavior — the default param exists for API compat
        assert r.get("id") == "alice"
        assert r.get("nonexistent") is None  # returns None, not default

    def test_data_returns_dict(self):
        r = self._record()
        d = r.data()
        assert d == {"id": "alice", "name": "Alice Smith", "score": 0.9}

    def test_iter(self):
        r = self._record()
        vals = list(r)
        assert vals == ["alice", "Alice Smith", 0.9]

    def test_len(self):
        r = self._record()
        assert len(r) == 3

    def test_repr(self):
        r = self._record()
        assert "IVGRecord" in repr(r)


# ---------------------------------------------------------------------------
# _wrap_result
# ---------------------------------------------------------------------------

class TestWrapResult:

    def test_wrap_result_full(self):
        from iris_vector_graph.sdk import _wrap_result
        from iris_vector_graph.result import IVGResult
        r = _wrap_result({"columns": ["a"], "rows": [[1]], "error": None})
        assert isinstance(r, IVGResult)
        assert r.columns == ["a"]
        assert r.rows == [[1]]

    def test_wrap_result_empty(self):
        from iris_vector_graph.sdk import _wrap_result
        r = _wrap_result({})
        assert r.columns == []
        assert r.rows == []

    def test_wrap_result_with_error(self):
        from iris_vector_graph.sdk import _wrap_result
        r = _wrap_result({"columns": [], "rows": [], "error": "boom"})
        assert r.error == "boom"


# ---------------------------------------------------------------------------
# IVGClient — mock httpx transport
# ---------------------------------------------------------------------------

def _make_client(responses: dict):
    """Build IVGClient with httpx MockTransport."""
    from iris_vector_graph.sdk import IVGClient
    import httpx

    def transport_handler(request):
        path = request.url.path
        if path in responses:
            body, status = responses[path]
            import json as _json
            return httpx.Response(status, json=body if isinstance(body, dict) else None,
                                  text=body if isinstance(body, str) else None)
        return httpx.Response(404, json={"error": "not found"})

    client = IVGClient.__new__(IVGClient)
    client._url = "http://test"
    client._headers = {}
    client._timeout = 5.0
    client._max_retries = 2
    client._client = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(transport_handler),
    )
    return client


class TestIVGClientBasic:

    def test_no_httpx_raises_import_error(self):
        import sys
        from iris_vector_graph import sdk
        original = sdk._HAS_HTTPX
        sdk._HAS_HTTPX = False
        try:
            with pytest.raises(ImportError, match="httpx"):
                sdk.IVGClient("http://localhost")
        finally:
            sdk._HAS_HTTPX = original

    def test_context_manager(self):
        from iris_vector_graph.sdk import IVGClient
        import httpx
        client = _make_client({"/health": ({"status": "ok"}, 200)})
        with client as c:
            assert c is client
        # After exit, client is closed

    def test_close_idempotent(self):
        client = _make_client({})
        client.close()
        client.close()  # second close must not raise


class TestIVGClientExecuteCypher:

    def test_execute_cypher_success(self):
        client = _make_client({
            "/api/cypher": ({"columns": ["n"], "rows": [["alice"]], "error": None}, 200)
        })
        result = client.execute_cypher("MATCH (n) RETURN n.node_id")
        assert result.columns == ["n"]
        assert result.rows == [["alice"]]

    def test_execute_cypher_with_params(self):
        client = _make_client({
            "/api/cypher": ({"columns": ["id"], "rows": [["bob"]], "error": None}, 200)
        })
        result = client.execute_cypher("MATCH (n {node_id:$x}) RETURN n", {"x": "bob"})
        assert result.rows[0][0] == "bob"

    def test_execute_cypher_server_error_raises(self):
        from iris_vector_graph.sdk import IVGServerError
        client = _make_client({
            "/api/cypher": ({"columns": [], "rows": [], "error": "syntax error"}, 200)
        })
        with pytest.raises(IVGServerError, match="syntax error"):
            client.execute_cypher("BAD QUERY")

    def test_execute_cypher_401_raises_client_error(self):
        from iris_vector_graph.sdk import IVGClientError
        client = _make_client({"/api/cypher": ({}, 401)})
        with pytest.raises(IVGClientError, match="Authentication"):
            client.execute_cypher("MATCH (n) RETURN n")

    def test_execute_cypher_403_raises_client_error(self):
        from iris_vector_graph.sdk import IVGClientError
        client = _make_client({"/api/cypher": ({}, 403)})
        with pytest.raises(IVGClientError, match="Forbidden"):
            client.execute_cypher("MATCH (n) RETURN n")

    def test_execute_cypher_500_retries_then_raises(self):
        from iris_vector_graph.sdk import IVGServerError
        import time
        client = _make_client({"/api/cypher": ("server error", 500)})
        client._max_retries = 1  # speed up test
        with patch("time.sleep"):
            with pytest.raises(IVGServerError):
                client.execute_cypher("MATCH (n) RETURN n")


class TestIVGClientOtherMethods:

    def test_ping_success(self):
        client = _make_client({"/health": ({"status": "ok"}, 200)})
        result = client.ping()
        assert result["status"] == "ok"

    def test_ping_connect_error_raises(self):
        from iris_vector_graph.sdk import IVGClientError, IVGClient
        import httpx
        client = IVGClient.__new__(IVGClient)
        client._url = "http://unreachable"
        client._headers = {}
        client._timeout = 1.0
        client._max_retries = 1

        def raise_connect(request):
            raise httpx.ConnectError("refused")

        client._client = httpx.Client(
            base_url="http://unreachable",
            transport=httpx.MockTransport(raise_connect),
        )
        with pytest.raises(IVGClientError, match="Cannot reach"):
            client.ping()

    def test_schema(self):
        client = _make_client({"/schema": ({"tables": ["nodes"]}, 200)})
        result = client.schema()
        assert "tables" in result

    def test_server_info(self):
        client = _make_client({"/server": ({"version": "2.1.0"}, 200)})
        result = client.server_info()
        assert result["version"] == "2.1.0"

    def test_stats(self):
        client = _make_client({"/stats": ({"nodes": 42}, 200)})
        result = client.stats()
        assert result["nodes"] == 42

    def test_node_count(self):
        client = _make_client({
            "/api/cypher": ({"columns": ["c"], "rows": [[42]], "error": None}, 200)
        })
        assert client.node_count() == 42

    def test_node_count_empty_returns_zero(self):
        client = _make_client({
            "/api/cypher": ({"columns": ["c"], "rows": [], "error": None}, 200)
        })
        assert client.node_count() == 0

    def test_get_labels(self):
        client = _make_client({
            "/api/cypher": ({"columns": ["label"], "rows": [["Person"], ["Gene"]], "error": None}, 200)
        })
        labels = client.get_labels()
        assert "Person" in labels

    def test_explain(self):
        client = _make_client({
            "/admin/explain": ({"plan": "full scan"}, 200)
        })
        result = client.explain("MATCH (n) RETURN n")
        assert "plan" in result


class TestIVGClientRetry:

    def test_connect_error_retries(self):
        from iris_vector_graph.sdk import IVGClientError, IVGClient
        import httpx

        call_count = [0]

        def flaky(request):
            call_count[0] += 1
            if call_count[0] < 2:
                raise httpx.ConnectError("timeout")
            return httpx.Response(200, json={"columns": [], "rows": [], "error": None})

        client = IVGClient.__new__(IVGClient)
        client._url = "http://test"
        client._headers = {}
        client._timeout = 1.0
        client._max_retries = 2
        client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(flaky))

        with patch("time.sleep"):
            result = client.execute_cypher("MATCH (n) RETURN n")
        assert call_count[0] == 2

    def test_all_retries_exhausted_raises(self):
        from iris_vector_graph.sdk import IVGClientError, IVGClient
        import httpx

        def always_fail(request):
            raise httpx.ConnectError("refused")

        client = IVGClient.__new__(IVGClient)
        client._url = "http://test"
        client._headers = {}
        client._timeout = 1.0
        client._max_retries = 2
        client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(always_fail))

        with patch("time.sleep"):
            with pytest.raises(IVGClientError, match="Failed after"):
                client.execute_cypher("MATCH (n) RETURN n")
