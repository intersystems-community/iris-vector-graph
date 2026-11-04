"""R098: an IVG client with a key can authenticate to an IVG server with that key.

`IVGClient`, `AsyncIVGClient` and the `ivg` CLI sent `Authorization: Bearer <key>`; the
server read only `X-Api-Key`, so every authenticated call got 401. Now the client sends
`X-Api-Key` (which servers before 4.6.0 read too) and the server accepts either header
(so clients before 4.6.0, and Bearer-only tools such as a Prometheus scraper, work).

The clients are run against the real cypher_api app through Starlette's in-process
transport: what is asserted is the answer the server returned, not the header dict.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEY = "r098-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    monkeypatch.setenv("BOLT_ENABLED", "0")


@pytest.fixture
def server():
    eng = MagicMock(name="engine")
    eng.execute_cypher.return_value = MagicMock(
        columns=["name"], rows=[["alice"], ["bob"]], error=None,
        metadata=MagicMock(warnings=[], index_usage=[], optimization_applied=[]),
    )
    eng.rebuild_kg.return_value = True
    eng.rebuild_nkg.return_value = True
    from iris_vector_graph import cypher_api as mod

    with patch.object(mod, "_make_engine", return_value=eng), \
         patch.object(mod, "_get_engine", return_value=eng), \
         patch.object(mod, "_engine_cache", eng):
        yield mod.app, eng


def _old_server() -> FastAPI:
    """A server before 4.6.0: reads `X-Api-Key` only."""
    app = FastAPI()

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.headers.get("X-Api-Key", "") != KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.post("/api/cypher")
    def cypher():
        return {"columns": ["name"], "rows": [["carol"]]}

    return app


def _sync_client_on(app):
    def factory(*, base_url, headers, timeout=None, **_):
        return TestClient(app, base_url=base_url, headers=headers)
    return factory


def _async_client_on(app):
    real = httpx.AsyncClient  # captured before the patch replaces it
    def factory(*, base_url, headers, timeout=None, **_):
        return real(transport=httpx.ASGITransport(app=app),
                                 base_url=base_url, headers=headers)
    return factory


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def test_ivgclient_with_the_key_gets_an_answer(server):
    from iris_vector_graph import sdk

    app, eng = server
    with patch.object(sdk._httpx, "Client", _sync_client_on(app)):
        result = sdk.IVGClient("http://testserver", api_key=KEY).execute_cypher(
            "MATCH (n) RETURN n.name AS name")
    assert result.rows == [["alice"], ["bob"]]
    eng.execute_cypher.assert_called_once()


def test_ivgclient_with_a_wrong_key_is_refused(server):
    from iris_vector_graph import sdk

    app, eng = server
    with patch.object(sdk._httpx, "Client", _sync_client_on(app)):
        client = sdk.IVGClient("http://testserver", api_key="wrong", max_retries=1)
        with pytest.raises(Exception):
            client.execute_cypher("MATCH (n) RETURN n")
    eng.execute_cypher.assert_not_called()


def test_async_ivgclient_with_the_key_gets_an_answer(server):
    from iris_vector_graph import sdk

    app, eng = server

    async def go():
        with patch.object(sdk._httpx, "AsyncClient", _async_client_on(app)):
            c = sdk.AsyncIVGClient("http://testserver", api_key=KEY)
            return await c.execute_cypher("MATCH (n) RETURN n.name AS name")

    assert asyncio.run(go()).rows == [["alice"], ["bob"]]


def test_new_client_authenticates_to_a_pre_460_server():
    from iris_vector_graph import sdk

    with patch.object(sdk._httpx, "Client", _sync_client_on(_old_server())):
        result = sdk.IVGClient("http://testserver", api_key=KEY).execute_cypher(
            "MATCH (n) RETURN n.name AS name")
    assert result.rows == [["carol"]]


# ---------------------------------------------------------------------------
# CLI: one command through IVGClient, one that calls httpx directly
# ---------------------------------------------------------------------------

def test_cli_query_with_the_key_prints_the_rows(server):
    click_testing = pytest.importorskip("click.testing")
    from iris_vector_graph import cli as cli_mod
    from iris_vector_graph import sdk

    app, _ = server
    with patch.object(sdk._httpx, "Client", _sync_client_on(app)):
        res = click_testing.CliRunner().invoke(
            cli_mod.cli,
            ["--url", "http://testserver", "--api-key", KEY, "query", "--json-output",
             "MATCH (n) RETURN n.name AS name"],
        )
    assert res.exit_code == 0, res.output
    assert '"rows": [["alice"], ["bob"]]' in res.output


def test_cli_indexes_rebuild_with_the_key_reaches_the_engine(server):
    click_testing = pytest.importorskip("click.testing")
    from iris_vector_graph import cli as cli_mod

    app, eng = server
    tc = TestClient(app, base_url="http://testserver")

    def post(url, **kw):
        return tc.post(url, **kw)

    with patch.object(httpx, "post", post):
        res = click_testing.CliRunner().invoke(
            cli_mod.cli, ["--url", "http://testserver", "--api-key", KEY, "indexes", "rebuild"])
    assert res.exit_code == 0, res.output
    eng.rebuild_kg.assert_called_once()
    eng.rebuild_nkg.assert_called_once()


# ---------------------------------------------------------------------------
# Server: both headers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {"X-Api-Key": KEY},
    {"Authorization": f"Bearer {KEY}"},
    {"Authorization": f"bearer {KEY}"},
    {"Authorization": "Bearer wrong", "X-Api-Key": KEY},
    {"Authorization": f"Bearer {KEY}", "X-Api-Key": "wrong"},
])
def test_server_accepts_the_key_in_either_header(server, headers):
    app, _ = server
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/stats", headers=headers).status_code not in (401, 403)


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer"},
    {"Authorization": KEY},
    {"Authorization": f"Basic {KEY}"},
    {"Authorization": f"Token {KEY}"},
])
def test_server_refuses_a_bad_authorization_header(server, headers):
    app, eng = server
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/stats", headers=headers).status_code == 401
    assert eng.mock_calls == []


def test_server_bearer_also_opens_the_graphql_websocket(monkeypatch):
    monkeypatch.setenv("IRIS_HOST", "127.0.0.1")
    monkeypatch.setenv("IRIS_PORT", "1")
    from api.main import create_app

    client = TestClient(create_app(engine=MagicMock()))
    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"],
                                  headers={"Authorization": f"Bearer {KEY}"}) as ws:
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json()["type"] == "connection_ack"
