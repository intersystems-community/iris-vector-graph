"""R107: cypher_api honours IVG_READ_ONLY on every route that writes.

`api.main` refused writes under `IVG_READ_ONLY=true` (its Cypher router and the
fhir-event middleware), but `cypher_api` never read the variable: `/admin/load`,
`/admin/schema/init`, `/admin/indexes/rebuild`, `/admin/embed`, `/admin/snapshot`, the
query kill and a `CREATE` sent to `/api/cypher`, the Neo4j transaction commit or Query
API v2 all ran in read-only mode, and so did a `CREATE` over Bolt.

The write routes are enumerated from the app (every route that takes a method other
than GET/HEAD), so a write route added later is held to the mode without anyone
editing this file. The one non-GET route that only reads is named here, and is checked
to answer.

No IRIS: the engine is a MagicMock; a refused request must leave it untouched.
"""
from __future__ import annotations

import asyncio
import re
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount  # noqa: E402

KEY = "r107-key"
GOOD = {"X-Api-Key": KEY}
WRITE = "CREATE (n:Probe {id: 'r107'}) RETURN n"
READ = "MATCH (n) RETURN count(n) AS c"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Non-GET routes that only read. /admin/explain parses and translates; it runs nothing.
READ_ONLY_POSTS = {("POST", "/admin/explain")}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    monkeypatch.delenv("IVG_ALLOW_FHIR_WRITES", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")


def _engine() -> MagicMock:
    eng = MagicMock(name="engine")
    eng.execute_cypher.return_value = MagicMock(
        columns=["c"], rows=[[1]], error=None, bolt_column_types=None,
    )
    return eng


@pytest.fixture
def api():
    eng = _engine()
    from iris_vector_graph import cypher_api as mod

    with patch.object(mod, "_make_engine", return_value=eng), \
         patch.object(mod, "_get_engine", return_value=eng), \
         patch.object(mod, "_engine_cache", eng):
        yield TestClient(mod.app, raise_server_exceptions=False), mod.app, eng


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "123", path)


def _write_routes(app):
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if isinstance(r, Mount) or not methods:
            continue
        for m in sorted(set(methods) - READ_METHODS):
            out.append((m, _concrete(r.path)))
    return out


# A body that carries the write in every field a Cypher route reads.
WRITE_BODY = {
    "query": WRITE,
    "statement": WRITE,
    "statements": [{"statement": WRITE}],
    "parameters": {},
}


def test_the_enumeration_finds_the_admin_and_cypher_writes(api):
    _, app, _ = api
    found = set(_write_routes(app))
    for expected in [
        ("POST", "/admin/load"), ("POST", "/admin/schema/init"), ("POST", "/admin/embed"),
        ("POST", "/admin/indexes/rebuild"), ("POST", "/admin/snapshot"),
        ("DELETE", "/admin/queries/123"), ("POST", "/api/cypher"),
        ("POST", "/db/neo4j/tx/commit"), ("POST", "/db/123/query/v2"),
    ]:
        assert expected in found


def test_every_write_route_refuses_under_read_only(api):
    client, app, eng = api
    reqs = [rq for rq in _write_routes(app) if rq not in READ_ONLY_POSTS]
    ran = []
    for method, path in reqs:
        eng.reset_mock()
        kwargs = {"headers": GOOD}
        if method != "DELETE":
            kwargs["json"] = WRITE_BODY
        resp = client.request(method, path, **kwargs)
        if resp.status_code != 403 or eng.mock_calls:
            ran.append((method, path, resp.status_code, eng.mock_calls[:2]))
    assert not ran, f"write routes that ran under IVG_READ_ONLY=true: {ran}"


def test_the_refusal_names_read_only_mode(api):
    client, _, eng = api
    resp = client.post("/admin/load", content=b'{"kind":"node","id":"x"}\n', headers=GOOD)
    assert resp.status_code == 403
    assert "read_only" in resp.text
    eng.import_graph_ndjson.assert_not_called()


def test_the_key_check_runs_before_read_only(api):
    client, _, eng = api
    resp = client.post("/admin/load", content=b"{}\n")
    assert resp.status_code == 401
    assert eng.mock_calls == []


def test_admin_load_runs_when_read_only_is_off(api, monkeypatch):
    monkeypatch.delenv("IVG_READ_ONLY")
    client, _, eng = api
    eng.import_graph_ndjson.return_value = {"nodes": 1}
    resp = client.post("/admin/load", content=b'{"kind":"node","id":"x"}\n', headers=GOOD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == {"nodes": 1}


def test_explain_still_answers_under_read_only(api):
    client, _, eng = api
    resp = client.post("/admin/explain", json={"query": READ}, headers=GOOD)
    assert resp.status_code == 200, resp.text
    assert "SELECT" in resp.json()["sql"].upper()
    eng.execute_cypher.assert_not_called()


@pytest.mark.parametrize("path,body", [
    ("/api/cypher", {"query": READ}),
    ("/db/neo4j/tx/commit", {"statements": [{"statement": READ}]}),
    ("/db/neo4j/query/v2", {"statement": READ}),
])
def test_reads_still_run_under_read_only(api, path, body):
    client, _, eng = api
    resp = client.post(path, json=body, headers=GOOD)
    assert resp.status_code == 200, resp.text
    eng.execute_cypher.assert_called_once()
    assert eng.execute_cypher.call_args[0][0] == READ


def test_tx_commit_with_a_write_runs_none_of_its_statements(api):
    client, _, eng = api
    resp = client.post("/db/neo4j/tx/commit", headers=GOOD, json={
        "statements": [{"statement": READ}, {"statement": WRITE}],
    })
    assert resp.status_code == 403
    assert resp.json()["errors"][0]["code"] == "Neo.ClientError.Statement.AccessMode"
    eng.execute_cypher.assert_not_called()


# ---------------------------------------------------------------------------
# Bolt (the WebSocket route at `/` and the TCP listener share BoltSession)
# ---------------------------------------------------------------------------

def _bolt(query: str):
    from iris_vector_graph.bolt_server import (
        TAG_FAILURE, TAG_GOODBYE, TAG_HELLO, TAG_PULL, TAG_RUN, BoltSession, PackStream,
        decode_messages, encode_message,
    )
    from iris_vector_graph.result import IVGResult

    def msg(tag, *fields):
        body = b"".join(PackStream.pack(f) for f in fields)
        return encode_message(bytes([0xB0 | len(fields), tag]) + body)

    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", 0x0404, 0, 0, 0)
    ws.receive_bytes = AsyncMock(side_effect=[
        handshake,
        msg(TAG_HELLO, {"user_agent": "t", "credentials": KEY}),
        msg(TAG_RUN, query, {}, {}),
        msg(TAG_PULL, {"n": -1}),
        msg(TAG_GOODBYE),
        Exception("end"),
    ])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = MagicMock()
    engine.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[1]])
    asyncio.run(BoltSession(ws, lambda: engine).run())
    failures = []
    for call in ws.send_bytes.call_args_list[1:]:
        for m in decode_messages(call[0][0]):
            if m[1] == TAG_FAILURE:
                failures.append(PackStream.unpack(m, 2)[0])
    return engine, failures


def test_bolt_refuses_a_write_under_read_only():
    engine, failures = _bolt(WRITE)
    engine.execute_cypher.assert_not_called()
    assert failures and failures[0]["code"] == "Neo.ClientError.Statement.AccessMode"


def test_bolt_runs_a_read_under_read_only():
    engine, failures = _bolt(READ)
    engine.execute_cypher.assert_called_once()
    assert failures == []
