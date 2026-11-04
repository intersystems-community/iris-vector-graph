"""4.6.0 R225 (walkthrough 2, papercuts 15 and 16).

15. `ivg indexes list` against a server that wants a key, sent without one, printed `Failed: 'columns'`: the command
    read the 401 body as an index list. `ivg query` said `Authentication failed — check api_key`. Every GET of
    `IVGClient` now reads 401 and 403 as `_post` does, and the command uses it.
16. `ivg server start --host 127.0.0.1` bound the HTTP server to 127.0.0.1 and the Bolt listener to every interface
    (`*:7687`). The Bolt listener takes the same host, unless `BOLT_TCP_HOST` names another.
"""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("click")

from click.testing import CliRunner  # noqa: E402

from iris_vector_graph.sdk import IVGClient, IVGClientError  # noqa: E402


def _client(status: int, body: dict) -> IVGClient:
    c = IVGClient("http://ivg.test")
    c._client = httpx.Client(base_url="http://ivg.test",
                             transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    return c


UNAUTHORIZED = {"error": "unauthorized", "message": "X-Api-Key header required"}


@pytest.mark.parametrize("status, text", [(401, "Authentication failed"), (403, "Forbidden")])
def test_a_get_the_server_refuses_raises_naming_why(status, text):
    with pytest.raises(IVGClientError) as exc:
        _client(status, UNAUTHORIZED)._get("/indexes")
    assert text in str(exc.value) and exc.value.http_code == status


def _indexes_list(client):
    from iris_vector_graph.cli import cli

    with mock.patch("iris_vector_graph.cli._client", return_value=client):
        return CliRunner().invoke(cli, ["--url", "http://ivg.test", "indexes", "list"])


def test_indexes_list_without_the_key_says_authentication_failed():
    result = _indexes_list(_client(401, UNAUTHORIZED))
    assert result.exit_code == 1
    assert "Authentication failed — check api_key" in result.output
    assert "'columns'" not in result.output


def test_indexes_list_prints_the_indexes():
    result = _indexes_list(_client(200, {"columns": ["name", "type"], "indexes": [["bios", "bm25"]]}))
    assert result.exit_code == 0, result.output
    assert "| bios | bm25 |" in result.output


def test_server_start_gives_the_bolt_listener_its_host(monkeypatch):
    from iris_vector_graph.cli import cli

    # set, then removed: monkeypatch then removes what the command sets, so it does not leak into later tests
    monkeypatch.setenv("BOLT_TCP_HOST", "unset")
    monkeypatch.delenv("BOLT_TCP_HOST")
    seen = {}
    with mock.patch("uvicorn.run", lambda app, host, port, workers: seen.update(bolt=os.environ.get("BOLT_TCP_HOST"))):
        result = CliRunner().invoke(cli, ["server", "start", "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert seen == {"bolt": "127.0.0.1"}


def test_an_explicit_bolt_host_wins(monkeypatch):
    from iris_vector_graph.cli import cli

    monkeypatch.setenv("BOLT_TCP_HOST", "10.0.0.5")
    seen = {}
    with mock.patch("uvicorn.run", lambda app, host, port, workers: seen.update(bolt=os.environ.get("BOLT_TCP_HOST"))):
        CliRunner().invoke(cli, ["server", "start", "--host", "127.0.0.1"])
    assert seen == {"bolt": "10.0.0.5"}


@pytest.mark.parametrize("env, host", [({}, "0.0.0.0"), ({"BOLT_TCP_HOST": "127.0.0.1"}, "127.0.0.1")])
def test_the_bolt_listener_binds_bolt_tcp_host(monkeypatch, env, host):
    pytest.importorskip("fastapi")
    from iris_vector_graph import cypher_api

    monkeypatch.delenv("BOLT_TCP_HOST", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BOLT_ENABLED", "1")
    calls = []

    async def fake_start(get_engine, host="0.0.0.0", port=7687):
        calls.append((host, port))
        return None

    async def run():
        async with cypher_api._lifespan(None):
            pass

    with mock.patch("iris_vector_graph.bolt_server.start_tcp_bolt_server", fake_start):
        asyncio.run(run())
    assert calls == [(host, 7687)]
