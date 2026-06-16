"""Tests covering remaining sdk.py gaps (target: ≥95% coverage).

Covers:
- httpx ImportError guard (lines 9-10)
- IVGRecord.get() TypeError fallback (lines 53-54)
- IVGClient._post() 401, 403, 500-retryable, 500-final paths (lines 195-196)
- AsyncIVGClient.execute_aql with bind_vars (line 225)
- AsyncIVGClient.aclose with and without open client (lines 261-264)
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# httpx ImportError guard (lines 6-10)
# ---------------------------------------------------------------------------

def test_httpx_not_installed_raises_on_client_creation(monkeypatch):
    """When httpx is missing, IVGClient.__init__ raises ImportError."""
    # Temporarily hide httpx from sys.modules
    real_httpx = sys.modules.get("httpx")
    sys.modules["httpx"] = None  # type: ignore[assignment]
    # Also clear cached _HAS_HTTPX in sdk module
    import importlib
    import iris_vector_graph.sdk as sdk_mod
    original_has_httpx = sdk_mod._HAS_HTTPX
    sdk_mod._HAS_HTTPX = False
    try:
        with pytest.raises(ImportError, match="httpx"):
            from iris_vector_graph.sdk import IVGClient
            IVGClient("http://localhost:52773")
    finally:
        sdk_mod._HAS_HTTPX = original_has_httpx
        if real_httpx is None:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = real_httpx


def test_async_client_httpx_not_installed_raises(monkeypatch):
    """AsyncIVGClient also raises ImportError when httpx absent."""
    import iris_vector_graph.sdk as sdk_mod
    original = sdk_mod._HAS_HTTPX
    sdk_mod._HAS_HTTPX = False
    try:
        with pytest.raises(ImportError, match="httpx"):
            from iris_vector_graph.sdk import AsyncIVGClient
            AsyncIVGClient("http://localhost:52773")
    finally:
        sdk_mod._HAS_HTTPX = original


# ---------------------------------------------------------------------------
# IVGRecord.get() fallback paths (lines 53-54)
# ---------------------------------------------------------------------------

def test_ivg_record_get_index_error_returns_default():
    """get() catches IndexError on out-of-range int index access."""
    from iris_vector_graph.sdk import IVGRecord
    r = IVGRecord(["a"], [1])
    # Integer key out of range triggers IndexError which get() catches
    assert r.get(99, "default") == "default"  # type: ignore[arg-type]



def test_ivg_record_get_existing_key():
    """get() on present key returns value."""
    from iris_vector_graph.sdk import IVGRecord
    r = IVGRecord(["name", "score"], ["alice", 0.9])
    assert r.get("name") == "alice"
    assert r.get("score") == 0.9


# ---------------------------------------------------------------------------
# IVGClient._post() error paths (lines 189-206)
# ---------------------------------------------------------------------------

def _make_response(status_code: int, text: str = "error", json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body or {"rows": [], "columns": [], "error": None}
    resp.raise_for_status = MagicMock()
    return resp


def _make_client(url="http://localhost:52773", api_key=None):
    from iris_vector_graph.sdk import IVGClient
    client = IVGClient.__new__(IVGClient)
    client._url = url
    client._headers = {}
    client._timeout = 10.0
    client._max_retries = 3
    client._client = None
    return client


def test_post_401_raises_client_error():
    from iris_vector_graph.sdk import IVGClientError
    client = _make_client()
    mock_http = MagicMock()
    mock_http.post.return_value = _make_response(401)
    client._client = mock_http
    with pytest.raises(IVGClientError) as exc_info:
        client._post("/api/cypher", {"query": "MATCH (n) RETURN n"})
    assert exc_info.value.http_code == 401
    assert "Authentication" in str(exc_info.value)


def test_post_403_raises_forbidden():
    from iris_vector_graph.sdk import IVGClientError
    client = _make_client()
    mock_http = MagicMock()
    mock_http.post.return_value = _make_response(403)
    client._client = mock_http
    with pytest.raises(IVGClientError) as exc_info:
        client._post("/api/cypher", {})
    assert exc_info.value.http_code == 403
    assert "Forbidden" in str(exc_info.value)


def test_post_500_retries_then_raises_server_error():
    """500 response retries up to max_retries then raises IVGServerError."""
    from iris_vector_graph.sdk import IVGServerError
    client = _make_client()
    client._max_retries = 2
    mock_http = MagicMock()
    mock_http.post.return_value = _make_response(500, text="internal error")
    client._client = mock_http
    with patch("time.sleep"):  # avoid actual sleep in tests
        with pytest.raises(IVGServerError) as exc_info:
            client._post("/api/cypher", {})
    assert exc_info.value.http_code == 500
    assert mock_http.post.call_count == 2  # retried once, then raised


def test_post_500_single_retry_exhausted():
    """With max_retries=1 a 500 raises immediately on first attempt."""
    from iris_vector_graph.sdk import IVGServerError
    client = _make_client()
    client._max_retries = 1
    mock_http = MagicMock()
    mock_http.post.return_value = _make_response(500, text="boom")
    client._client = mock_http
    with pytest.raises(IVGServerError):
        client._post("/api/cypher", {})


def test_post_connect_error_retries_then_raises():
    """ConnectError retries up to max_retries then raises IVGClientError."""
    import httpx
    from iris_vector_graph.sdk import IVGClientError
    client = _make_client()
    client._max_retries = 2
    mock_http = MagicMock()
    mock_http.post.side_effect = httpx.ConnectError("refused")
    client._client = mock_http
    with patch("time.sleep"):
        with pytest.raises(IVGClientError, match="Failed after"):
            client._post("/api/cypher", {})
    assert mock_http.post.call_count == 2


# ---------------------------------------------------------------------------
# AsyncIVGClient.execute_aql (line 225) + aclose (lines 261-264)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_client_execute_aql_with_bind_vars():
    """execute_aql translates AQL to Cypher then calls execute_cypher."""
    from iris_vector_graph.result import IVGResult
    from iris_vector_graph.sdk import AsyncIVGClient

    client = AsyncIVGClient.__new__(AsyncIVGClient)
    client._url = "http://localhost"
    client._headers = {}
    client._timeout = 10.0
    client._client = None

    expected = IVGResult(columns=["n"], rows=[[1]], parameters=[], error=None,
                         bolt_column_types=[])
    client.execute_cypher = AsyncMock(return_value=expected)

    result = await client.execute_aql(
        'FOR v, e IN 1..2 OUTBOUND "nodes/1" edges RETURN v',
        bind_vars={},
    )
    assert result is expected
    client.execute_cypher.assert_called_once()


@pytest.mark.asyncio
async def test_async_client_aclose_with_open_client():
    """aclose() closes underlying httpx client and sets _client to None."""
    from iris_vector_graph.sdk import AsyncIVGClient

    client = AsyncIVGClient.__new__(AsyncIVGClient)
    client._url = "http://localhost"
    client._headers = {}
    client._timeout = 10.0

    mock_http = AsyncMock()
    client._client = mock_http

    await client.aclose()

    mock_http.aclose.assert_called_once()
    assert client._client is None


@pytest.mark.asyncio
async def test_async_client_aclose_when_no_client():
    """aclose() is a no-op when _client is already None."""
    from iris_vector_graph.sdk import AsyncIVGClient

    client = AsyncIVGClient.__new__(AsyncIVGClient)
    client._client = None

    await client.aclose()  # should not raise


@pytest.mark.asyncio
async def test_async_client_get_client_creates_once():
    """_get_client creates a new AsyncClient on first call, reuses on second."""
    from iris_vector_graph.sdk import AsyncIVGClient

    client = AsyncIVGClient("http://localhost:52773")
    c1 = client._get_client()
    c2 = client._get_client()
    assert c1 is c2
    await client.aclose()


# ---------------------------------------------------------------------------
# IVGServerError.is_retryable
# ---------------------------------------------------------------------------

def test_is_retryable_500():
    from iris_vector_graph.sdk import IVGServerError
    err = IVGServerError("boom", http_code=500)
    assert err.is_retryable() is True


def test_is_retryable_404():
    from iris_vector_graph.sdk import IVGServerError
    err = IVGServerError("not found", http_code=404)
    assert err.is_retryable() is False
