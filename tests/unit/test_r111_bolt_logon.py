"""R111: a failed Bolt LOGON ends the session's authentication; a 5.0 HELLO may carry the key.

1. A Bolt 5 session that logged on and then sent a second LOGON with a wrong key got a
   FAILURE but stayed authenticated: after RESET it ran queries again. A failed
   authentication now clears the session's authentication, whatever came before.
2. On Bolt 5 the key was read only from LOGON, so a 5.0 client that puts it in HELLO
   (as 4.x does) was refused on its first RUN. A HELLO that carries credentials is now
   checked on every version; one without them still waits for LOGON.
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
    BoltSession,
    PackStream,
    decode_messages,
    encode_message,
)
from iris_vector_graph.result import IVGResult  # noqa: E402

KEY = "bolt-r111-key"
BOLT_44 = 0x00000404
BOLT_50 = 0x00000005


def _msg(tag, *fields):
    body = b"".join(PackStream.pack(f) for f in fields)
    return encode_message(bytes([0xB0 | len(fields), tag]) + body)


def _run(version, messages, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", version, 0, 0, 0)
    chunks = [_msg(tag, *fields) for tag, fields in messages] + [_msg(TAG_GOODBYE)]
    ws.receive_bytes = AsyncMock(side_effect=[handshake] + chunks + [Exception("end")])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = MagicMock()
    engine.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[42]])
    asyncio.run(BoltSession(ws, lambda: engine).run())
    tags = [m[1] for call in ws.send_bytes.call_args_list[1:] for m in decode_messages(call[0][0])]
    return engine, tags


RUN = (TAG_RUN, ["MATCH (n) RETURN count(n) AS c", {}, {}])
PULL = (TAG_PULL, [{"n": -1}])
HELLO5 = (TAG_HELLO, [{"user_agent": "t"}])
RESET = (TAG_RESET, [])


def _logon(credentials):
    return (TAG_LOGON, [{"scheme": "basic", "principal": "neo4j", "credentials": credentials}])


def test_a_failed_second_logon_ends_authentication_after_reset(monkeypatch):
    engine, tags = _run(BOLT_50, [
        HELLO5, _logon(KEY), RUN, PULL,      # authenticated: runs
        _logon("wrong"), RESET, RUN, PULL,   # refused LOGON; RESET must not restore it
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1
    assert tags.count(TAG_FAILURE) >= 2


def test_a_failed_second_hello_ends_authentication_on_bolt4(monkeypatch):
    engine, _ = _run(BOLT_44, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": KEY}]), RUN, PULL,
        (TAG_HELLO, [{"user_agent": "t", "credentials": "wrong"}]), RESET, RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1


def test_logon_with_the_key_after_a_failed_one_runs(monkeypatch):
    engine, _ = _run(BOLT_50, [
        HELLO5, _logon("wrong"), RESET, _logon(KEY), RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1


def test_bolt50_key_in_hello_is_accepted(monkeypatch):
    engine, tags = _run(BOLT_50, [
        (TAG_HELLO, [{"user_agent": "t", "scheme": "basic", "principal": "neo4j",
                      "credentials": KEY}]),
        RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 1
    assert TAG_FAILURE not in tags


def test_bolt50_wrong_key_in_hello_is_refused(monkeypatch):
    engine, tags = _run(BOLT_50, [
        (TAG_HELLO, [{"user_agent": "t", "credentials": "wrong"}]), RESET, RUN, PULL,
    ], monkeypatch)
    assert engine.execute_cypher.call_count == 0
    assert TAG_FAILURE in tags
