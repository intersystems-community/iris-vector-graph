"""Unit tests for Bolt 4.4 server: PackStream codec + chunk encoding + BoltSession.

T001-T004: PackStream + chunking (no IRIS required)
T011-T012: Handshake
T015-T018: BoltSession state machine (mocked WebSocket)
T021-T022: Graph object encoding
"""
from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from iris_vector_graph.bolt_server import (
    BoltSession,
    PackStream,
    decode_messages,
    encode_message,
    pack_node,
    pack_relationship,
)


# ── T001: PackStream pack ─────────────────────────────────────────────────────

class TestPackStreamPack:
    def test_null(self):
        assert PackStream.pack(None) == b'\xc0'

    def test_true(self):
        assert PackStream.pack(True) == b'\xc3'

    def test_false(self):
        assert PackStream.pack(False) == b'\xc2'

    def test_tiny_int_zero(self):
        assert PackStream.pack(0) == b'\x00'

    def test_tiny_int_positive(self):
        assert PackStream.pack(42) == b'\x2a'

    def test_tiny_int_negative(self):
        assert PackStream.pack(-1) == b'\xff'

    def test_int8(self):
        b = PackStream.pack(-128)
        assert b[0:1] == b'\xc8'
        assert len(b) == 2

    def test_int16(self):
        b = PackStream.pack(1000)
        assert b[0:1] == b'\xc9'
        assert len(b) == 3

    def test_int32(self):
        b = PackStream.pack(100000)
        assert b[0:1] == b'\xca'
        assert len(b) == 5

    def test_int64(self):
        b = PackStream.pack(10**15)
        assert b[0:1] == b'\xcb'
        assert len(b) == 9

    def test_float64(self):
        b = PackStream.pack(3.14)
        assert b[0:1] == b'\xc1'
        assert len(b) == 9

    def test_empty_string(self):
        b = PackStream.pack("")
        assert b == b'\x80'

    def test_tiny_string(self):
        b = PackStream.pack("hello")
        assert b[0] == 0x80 | 5
        assert b[1:] == b'hello'

    def test_string8(self):
        s = "x" * 200
        b = PackStream.pack(s)
        assert b[0:1] == b'\xd0'
        assert b[1] == 200

    def test_empty_list(self):
        assert PackStream.pack([]) == b'\x90'

    def test_tiny_list(self):
        b = PackStream.pack([1, 2, 3])
        assert b[0] == 0x90 | 3

    def test_empty_map(self):
        assert PackStream.pack({}) == b'\xa0'

    def test_tiny_map(self):
        b = PackStream.pack({"a": 1})
        assert b[0] == 0xa0 | 1


# ── T002: PackStream unpack (round-trip) ──────────────────────────────────────

class TestPackStreamRoundTrip:
    def _rt(self, value):
        packed = PackStream.pack(value)
        unpacked, consumed = PackStream.unpack(packed, 0)
        assert unpacked == value, f"round-trip failed for {value!r}: got {unpacked!r}"
        assert consumed == len(packed)

    def test_null(self): self._rt(None)
    def test_true(self): self._rt(True)
    def test_false(self): self._rt(False)
    def test_zero(self): self._rt(0)
    def test_tiny_int(self): self._rt(42)
    def test_negative_tiny(self): self._rt(-1)
    def test_int8(self): self._rt(-50)
    def test_int16(self): self._rt(500)
    def test_int32(self): self._rt(100000)
    def test_int64(self): self._rt(10**12)
    def test_float(self): self._rt(1.5)
    def test_empty_string(self): self._rt("")
    def test_tiny_string(self): self._rt("hello")
    def test_medium_string(self): self._rt("x" * 200)
    def test_empty_list(self): self._rt([])
    def test_list(self): self._rt([1, "two", None, True])
    def test_nested_list(self): self._rt([[1, 2], [3, 4]])
    def test_empty_map(self): self._rt({})
    def test_map(self): self._rt({"key": "value", "n": 42})
    def test_nested_map(self): self._rt({"a": {"b": "c"}})


# ── T003-T004: Chunk encoding/decoding ───────────────────────────────────────

class TestChunking:
    def test_encode_short_message(self):
        msg = b'\x01\x02\x03'
        chunked = encode_message(msg)
        assert chunked[:2] == struct.pack('>H', 3)
        assert chunked[2:5] == msg
        assert chunked[5:] == b'\x00\x00'

    def test_encode_exact_max_chunk(self):
        msg = bytes(range(256)) * 256
        chunked = encode_message(msg, max_chunk=0xFFFF)
        assert len(chunked) > 0

    def test_decode_single_message(self):
        msg = b'\xaa\xbb\xcc'
        chunked = encode_message(msg)
        messages = decode_messages(chunked)
        assert len(messages) == 1
        assert messages[0] == msg

    def test_decode_two_messages(self):
        msg1 = b'\x01\x02'
        msg2 = b'\x03\x04\x05'
        raw = encode_message(msg1) + encode_message(msg2)
        messages = decode_messages(raw)
        assert len(messages) == 2
        assert messages[0] == msg1
        assert messages[1] == msg2

    def test_partial_buffer_returns_empty(self):
        msg = b'\x01\x02\x03'
        chunked = encode_message(msg)
        messages = decode_messages(chunked[:4])
        assert messages == []

    def test_empty_message(self):
        chunked = encode_message(b'')
        messages = decode_messages(chunked)
        assert len(messages) == 1
        assert messages[0] == b''


# ── T011-T012: Handshake ──────────────────────────────────────────────────────

class TestHandshake:
    def _make_handshake_bytes(self, versions):
        magic = b'\x60\x60\xb0\x17'
        proposals = b''
        for v in versions[:4]:
            proposals += struct.pack('>I', v)
        while len(proposals) < 16:
            proposals += b'\x00\x00\x00\x00'
        return magic + proposals

    def test_negotiates_bolt_44(self):
        from iris_vector_graph.bolt_server import negotiate_version
        hs = self._make_handshake_bytes([0x00000404])
        chosen = negotiate_version(hs[4:])
        assert chosen == 0x00000404

    def test_negotiates_bolt_44_from_range(self):
        from iris_vector_graph.bolt_server import negotiate_version
        # Browser sends range: minor=4, range=4, major=4 → versions 4.0-4.4 supported
        hs = self._make_handshake_bytes([0x00040404, 0x00000404, 0x00000003, 0x00000000])
        chosen = negotiate_version(hs[4:])
        assert chosen == 0x00000404

    def test_no_shared_version(self):
        from iris_vector_graph.bolt_server import negotiate_version
        hs = self._make_handshake_bytes([0x00000006, 0x00000000, 0x00000000, 0x00000000])
        chosen = negotiate_version(hs[4:])
        assert chosen == 0x00000000


# ── T015-T018: BoltSession (mocked WebSocket) ─────────────────────────────────

def _make_bolt_message(tag, *fields):
    fields_bytes = b''.join(PackStream.pack(f) for f in fields)
    struct_byte = bytes([0xB0 | len(fields)])
    msg = struct_byte + bytes([tag]) + fields_bytes
    return encode_message(msg)


class TestBoltSessionHello:
    def _make_ws(self, messages_in):
        ws = AsyncMock()
        chunks = [_make_bolt_message(tag, *fields) for tag, fields in messages_in]
        handshake = b'\x60\x60\xb0\x17' + struct.pack('>IIII', 0x00000404, 0, 0, 0)
        all_data = [handshake] + chunks + [_make_bolt_message(0x02)]
        ws.receive_bytes = AsyncMock(side_effect=all_data + [Exception("end")])
        ws.send_bytes = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.scope = {"subprotocols": ["graphql-ws"]}
        return ws

    def test_hello_success_no_api_key(self, monkeypatch):
        import os
        monkeypatch.delenv("IVG_API_KEY", raising=False)
        ws = self._make_ws([(0x01, [{"user_agent": "test/1.0", "routing": None}])])
        mock_engine = MagicMock()
        mock_engine.execute_cypher.return_value = {"columns": ["c"], "rows": [[0]]}
        session = BoltSession(ws, lambda: mock_engine)
        try:
            asyncio.get_event_loop().run_until_complete(session.run())
        except Exception:
            pass
        sent = ws.send_bytes.call_args_list
        assert len(sent) >= 2
        first_response = sent[1][0][0]
        msgs = decode_messages(first_response)
        assert len(msgs) >= 1
        val, _ = PackStream.unpack(msgs[0], 0)
        assert isinstance(val, list) and len(val) >= 1
        assert isinstance(val[0], dict) and "server" in val[0]

    def test_hello_failure_wrong_key(self, monkeypatch):
        monkeypatch.setenv("IVG_API_KEY", "correct-key")
        ws = self._make_ws([(0x01, [{"user_agent": "test/1.0", "credentials": "wrong-key"}])])
        mock_engine = MagicMock()
        session = BoltSession(ws, lambda: mock_engine)
        try:
            asyncio.get_event_loop().run_until_complete(session.run())
        except Exception:
            pass
        sent = ws.send_bytes.call_args_list
        assert len(sent) >= 2
        response_bytes = sent[1][0][0]
        msgs = decode_messages(response_bytes)
        msg_data, _ = PackStream.unpack(msgs[0], 0)
        assert isinstance(msg_data, list) and isinstance(msg_data[0], dict) and "code" in msg_data[0]


# ── T021-T022: Graph object encoding ─────────────────────────────────────────

class TestGraphEncoding:
    def test_pack_node_structure(self):
        b = pack_node(1, ["Gene"], {"name": "BRCA1"})
        assert isinstance(b, bytes)
        assert len(b) > 0
        val, _ = PackStream.unpack(b, 0)
        assert isinstance(val, list)
        assert val[0] == 1
        assert val[1] == ["Gene"]
        assert val[2] == {"name": "BRCA1"}

    def test_pack_relationship_structure(self):
        b = pack_relationship(10, 1, 2, "TARGETS", {"confidence": 0.9})
        assert isinstance(b, bytes)
        val, _ = PackStream.unpack(b, 0)
        assert isinstance(val, list)
        assert val[0] == 10
        assert val[1] == 1
        assert val[2] == 2
        assert val[3] == "TARGETS"
        assert val[4] == {"confidence": 0.9}
