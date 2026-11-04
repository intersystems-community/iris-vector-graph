"""R097, Bolt: with IVG_API_KEY set, a Bolt session runs nothing until it has authenticated.

Bolt-over-WebSocket (`/` on cypher_api) and the TCP Bolt listener authenticate inside
the protocol (HELLO credentials on 4.x, LOGON on 5.x), because a browser cannot put a
header on a WebSocket upgrade. The session used to enter READY straight after the
version handshake, so a client that skipped HELLO, or sent RESET after a refused
HELLO, could RUN any query without the key.
"""
from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")

from iris_vector_graph.bolt_server import (  # noqa: E402
    TAG_FAILURE,
    TAG_GOODBYE,
    TAG_HELLO,
    TAG_LOGON,
    TAG_PULL,
    TAG_RESET,
    TAG_RUN,
    TAG_SUCCESS,
    BoltSession,
    PackStream,
    decode_messages,
    encode_message,
)
from iris_vector_graph.result import IVGResult  # noqa: E402

KEY = "bolt-key"
BOLT_44 = 0x00000404
BOLT_50 = 0x00000005


def _msg(tag, *fields):
    body = b"".join(PackStream.pack(f) for f in fields)
    return encode_message(bytes([0xB0 | len(fields), tag]) + body)


def _run(version: int, messages, monkeypatch, key: str | None = KEY):
    if key is None:
        monkeypatch.delenv("IVG_API_KEY", raising=False)
    else:
        monkeypatch.setenv("IVG_API_KEY", key)
    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", version, 0, 0, 0)
    chunks = [_msg(tag, *fields) for tag, fields in messages] + [_msg(TAG_GOODBYE)]
    ws.receive_bytes = AsyncMock(side_effect=[handshake] + chunks + [Exception("end")])
    ws.send_bytes = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = MagicMock()
    engine.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[42]])
    asyncio.run(BoltSession(ws, lambda: engine).run())
    tags = []
    for call in ws.send_bytes.call_args_list[1:]:  # [0] is the handshake reply
        for m in decode_messages(call[0][0]):
            tags.append(m[1])
    return engine, tags


RUN = (TAG_RUN, ["MATCH (n) RETURN count(n) AS c", {}, {}])
PULL = (TAG_PULL, [{"n": -1}])


def test_run_without_hello_is_refused(monkeypatch):
    engine, tags = _run(BOLT_44, [RUN, PULL], monkeypatch)
    assert engine.execute_cypher.call_count == 0
    assert TAG_FAILURE in tags


def test_reset_after_a_refused_hello_does_not_authenticate(monkeypatch):
    engine, _ = _run(BOLT_44, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": "wrong"}]),
        (TAG_RESET, []),
        RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 0


def test_bolt5_hello_without_logon_cannot_run(monkeypatch):
    engine, tags = _run(BOLT_50, [(TAG_HELLO, [{"user_agent": "t"}]), RUN, PULL], monkeypatch)
    assert engine.execute_cypher.call_count == 0
    assert TAG_FAILURE in tags


def test_bolt5_wrong_logon_then_reset_cannot_run(monkeypatch):
    engine, _ = _run(BOLT_50, [
        (TAG_HELLO, [{"user_agent": "t"}]),
        (TAG_LOGON, [{"scheme": "basic", "principal": "neo4j", "credentials": "wrong"}]),
        (TAG_RESET, []),
        RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 0


def test_bolt4_hello_with_the_key_runs(monkeypatch):
    engine, tags = _run(BOLT_44, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": KEY}]), RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1
    assert TAG_FAILURE not in tags


def test_bolt5_logon_with_the_key_runs(monkeypatch):
    engine, tags = _run(BOLT_50, [
        (TAG_HELLO, [{"user_agent": "t"}]),
        (TAG_LOGON, [{"scheme": "basic", "principal": "neo4j", "credentials": KEY}]),
        RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1
    assert TAG_FAILURE not in tags
    assert tags.count(TAG_SUCCESS) >= 3


def test_reset_keeps_an_authenticated_session_authenticated(monkeypatch):
    engine, _ = _run(BOLT_44, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": KEY}]),
        (TAG_RESET, []),
        RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1


def test_no_key_configured_needs_no_credentials(monkeypatch):
    engine, _ = _run(BOLT_50, [(TAG_HELLO, [{"user_agent": "t"}]), RUN, PULL],
                     monkeypatch, key=None)
    assert engine.execute_cypher.call_count == 1


def test_a_non_string_credential_is_refused_not_crashed(monkeypatch):
    engine, tags = _run(BOLT_44, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": 12345}]), RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 0
    assert TAG_FAILURE in tags
