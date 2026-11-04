"""R110: the Bolt server writes no query text to stdout; it logs it at DEBUG.

`BoltSession` printed every RUN's query (and the handshake, each message tag and the
result fields) to stdout, whatever the log level: a query's literals (a patient id, a
name) went to the container log of every deployment. They now go through `logging`
on `iris_vector_graph.bolt_server` at DEBUG, off unless an operator turns it on.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")

from iris_vector_graph.bolt_server import (  # noqa: E402
    TAG_GOODBYE,
    TAG_HELLO,
    TAG_PULL,
    TAG_RUN,
    BoltSession,
    PackStream,
    encode_message,
)
from iris_vector_graph.result import IVGResult  # noqa: E402

MARKER = "patient-r110-4711"
QUERY = f"MATCH (n {{id: '{MARKER}'}}) RETURN n.id AS id"


def _msg(tag, *fields):
    body = b"".join(PackStream.pack(f) for f in fields)
    return encode_message(bytes([0xB0 | len(fields), tag]) + body)


def _session():
    ws = AsyncMock()
    ws.receive_bytes = AsyncMock(side_effect=[
        b"\x60\x60\xb0\x17" + struct.pack(">IIII", 0x0404, 0, 0, 0),
        _msg(TAG_HELLO, {"user_agent": "t"}),
        _msg(TAG_RUN, QUERY, {}, {}),
        _msg(TAG_PULL, {"n": -1}),
        _msg(TAG_GOODBYE),
        Exception("end"),
    ])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = MagicMock()
    engine.execute_cypher.return_value = IVGResult(columns=["id"], rows=[[MARKER]])
    asyncio.run(BoltSession(ws, lambda: engine).run())
    return engine


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)


def test_no_bolt_output_on_stdout(capsys):
    engine = _session()
    engine.execute_cypher.assert_called_once()
    out = capsys.readouterr()
    assert MARKER not in out.out
    assert "[BOLT-WS]" not in out.out
    assert MARKER not in out.err


def test_the_query_is_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph.bolt_server"):
        _session()
    hits = [r for r in caplog.records if MARKER in r.getMessage()]
    assert hits and all(r.levelno == logging.DEBUG for r in hits)


def test_the_query_is_not_logged_at_info(caplog):
    with caplog.at_level(logging.INFO, logger="iris_vector_graph.bolt_server"):
        _session()
    assert not [r for r in caplog.records if MARKER in r.getMessage()]
