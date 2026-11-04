"""4.6.0 R222: `IVGClient(url)` and `AsyncIVGClient(url)` read `IVG_API_KEY` from the environment.

ADMIN_API ("Authentication") says `IVGClient`, `AsyncIVGClient` and the `ivg` command send the key given with
`--api-key`, or `IVG_API_KEY` in the client's environment. The `ivg` command did (click's `envvar=`); the two clients
took only `api_key=`, so with the variable exported `IVGClient(url).server_info()` got 401 (walkthrough 2, W11).
An explicit `api_key=` still wins. The live check against a server started with a key is
tests/e2e/test_460_r222_server_key_and_version_e2e.py.

`GET /server` and the store's `server_info()` asked IRIS `SELECT %Version.GetVersion()`, which is not SQL IRIS
accepts, and swallowed the error, so `iris_version` was always "unknown" (W12). They ask `%SYSTEM.Version` instead.
"""

from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("httpx")

from iris_vector_graph.sdk import AsyncIVGClient, IVGClient  # noqa: E402


@pytest.mark.parametrize("cls", [IVGClient, AsyncIVGClient])
def test_the_environment_key_is_sent(cls, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", "from-env")
    assert cls("http://127.0.0.1:1")._headers == {"X-Api-Key": "from-env"}


@pytest.mark.parametrize("cls", [IVGClient, AsyncIVGClient])
def test_an_explicit_key_wins_over_the_environment(cls, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", "from-env")
    assert cls("http://127.0.0.1:1", api_key="given")._headers == {"X-Api-Key": "given"}


@pytest.mark.parametrize("cls", [IVGClient, AsyncIVGClient])
def test_no_key_anywhere_sends_none(cls, monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    assert cls("http://127.0.0.1:1")._headers == {}


@pytest.mark.parametrize("cls", [IVGClient, AsyncIVGClient])
def test_an_empty_environment_key_sends_none(cls, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", "")
    assert cls("http://127.0.0.1:1")._headers == {}


VERSION = "IRIS for UNIX (Ubuntu Server LTS for ARM64 Containers) 2026.1 (Build 234U)"


def test_server_route_asks_system_version():
    from iris_vector_graph import cypher_api

    eng = mock.Mock()
    with mock.patch("iris_vector_graph.schema._call_classmethod", return_value=VERSION) as call:
        assert cypher_api._iris_version(eng) == VERSION
    call.assert_called_once_with(eng.conn, "%SYSTEM.Version", "GetVersion")


def test_store_server_info_asks_system_version():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = mock.Mock()
    with mock.patch("iris_vector_graph.schema._call_classmethod", return_value=VERSION) as call:
        result = store.server_info()
    call.assert_called_once_with(store.conn, "%SYSTEM.Version", "GetVersion")
    assert result.rows[0][0] == VERSION
