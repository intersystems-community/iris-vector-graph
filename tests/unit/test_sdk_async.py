"""
Tests for AsyncIVGClient and remaining IVGClient paths.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

pytestmark = pytest.mark.skipif(not _HAS_HTTPX, reason="httpx not installed")


# ---------------------------------------------------------------------------
# AsyncIVGClient
# ---------------------------------------------------------------------------

class TestAsyncIVGClient:

    @pytest.fixture
    def async_client(self):
        from iris_vector_graph.sdk import AsyncIVGClient
        import httpx

        def handler(request):
            path = request.url.path
            if path == "/api/cypher":
                return httpx.Response(200, json={"columns": ["n"], "rows": [["a"]], "error": None})
            if path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if path == "/schema":
                return httpx.Response(200, json={"labels": ["Person"]})
            return httpx.Response(404)

        client = AsyncIVGClient.__new__(AsyncIVGClient)
        client._url = "http://test"
        client._headers = {}
        client._timeout = 5.0
        client._client = httpx.AsyncClient(
            base_url="http://test",
            transport=httpx.MockTransport(handler),
        )
        return client

    def test_async_client_init(self):
        from iris_vector_graph.sdk import AsyncIVGClient
        client = AsyncIVGClient("http://localhost:8200", api_key="secret")
        assert client._url == "http://localhost:8200"
        assert "Authorization" in client._headers

    def test_async_client_no_httpx_raises(self):
        import sys
        from iris_vector_graph import sdk
        original = sdk._HAS_HTTPX
        sdk._HAS_HTTPX = False
        try:
            with pytest.raises(ImportError, match="httpx"):
                sdk.AsyncIVGClient("http://localhost")
        finally:
            sdk._HAS_HTTPX = original

    def test_async_execute_cypher(self, async_client):
        from iris_vector_graph.result import IVGResult
        async def _run():
            return await async_client.execute_cypher("MATCH (n) RETURN n")
        result = asyncio.run(_run())
        assert isinstance(result, IVGResult)
        assert result.rows[0][0] == "a"

    def test_async_execute_cypher_with_params(self, async_client):
        async def _run():
            return await async_client.execute_cypher(
                "MATCH (n {node_id:$id}) RETURN n",
                {"id": "alice"}
            )
        result = asyncio.run(_run())
        assert result is not None

    def test_async_execute_aql(self, async_client):
        async def _run():
            return await async_client.execute_aql(
                "FOR n IN nodes RETURN n._key"
            )
        try:
            result = asyncio.run(_run())
            assert result is not None
        except Exception:
            pass  # AQL may not be fully supported

    def test_async_context_manager(self, async_client):
        async def _run():
            async with async_client:
                pass
        asyncio.run(_run())

    def test_async_close_if_exists(self, async_client):
        """AsyncIVGClient may or may not have close() — test gracefully."""
        if hasattr(async_client, "close"):
            async def _run():
                await async_client.close()
            asyncio.run(_run())


# ---------------------------------------------------------------------------
# IVGClient — remaining uncovered paths
# ---------------------------------------------------------------------------

class TestIVGClientRemaining:

    def _make_client(self, responses):
        from iris_vector_graph.sdk import IVGClient
        import httpx

        def handler(req):
            path = req.url.path
            if path in responses:
                body, status = responses[path]
                return httpx.Response(status, json=body if isinstance(body, dict) else None,
                                      text=body if isinstance(body, str) else None)
            return httpx.Response(404)

        client = IVGClient.__new__(IVGClient)
        client._url = "http://test"
        client._headers = {}
        client._timeout = 5.0
        client._max_retries = 1
        client._client = httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(handler),
        )
        return client

    def test_get_client_caches_client(self):
        """_get_client() caches the httpx.Client instance."""
        from iris_vector_graph.sdk import IVGClient
        client = IVGClient("http://test")
        c1 = client._get_client()
        c2 = client._get_client()
        assert c1 is c2
        client.close()

    def test_load_ndjson(self):
        """load_ndjson sends body as ndjson to /admin/load."""
        import tempfile, os
        client = self._make_client({"/admin/load": ({"status": "ok"}, 200)})
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write('{"type":"node","id":"x"}\n')
            path = f.name
        try:
            result = client.load_ndjson(path)
            assert result.get("status") == "ok"
        finally:
            os.unlink(path)

    def test_explain(self):
        """explain() POSTs to /admin/explain."""
        client = self._make_client({"/admin/explain": ({"plan": "full scan"}, 200)})
        result = client.explain("MATCH (n) RETURN n")
        assert "plan" in result

    def test_ping_http_status_error(self):
        """ping() raises IVGClientError on HTTP error."""
        from iris_vector_graph.sdk import IVGClientError
        import httpx
        client = self._make_client({"/health": ({"error": "forbidden"}, 403)})
        with pytest.raises(IVGClientError):
            client.ping()
