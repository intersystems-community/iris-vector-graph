"""
Bolt 4.4 server over WebSocket for iris-vector-graph.

Spec: https://neo4j.com/docs/bolt/current/
PackStream: https://neo4j.com/docs/bolt/current/packstream/

Enables Neo4j Browser, neo4j Python driver, and LangChain Neo4jGraph
to connect to IVG via standard bolt:// URIs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
import uuid
from enum import Enum
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)

BOLT_MAGIC = b'\x60\x60\xb0\x17'
BOLT_44 = 0x00000404
MAX_CHUNK = 0xFFFF


class RawPackedBytes:
    __slots__ = ("data",)
    def __init__(self, data: bytes):
        self.data = data

# Message tags
TAG_HELLO = 0x01
TAG_GOODBYE = 0x02
TAG_RESET = 0x0F
TAG_RUN = 0x10
TAG_BEGIN = 0x11
TAG_COMMIT = 0x12
TAG_ROLLBACK = 0x13
TAG_DISCARD = 0x2F
TAG_PULL = 0x3F
TAG_SUCCESS = 0x70
TAG_RECORD = 0x71
TAG_IGNORED = 0x7E
TAG_FAILURE = 0x7F

# Structure tags
TAG_NODE = 0x4E
TAG_RELATIONSHIP = 0x52

# ── PackStream ────────────────────────────────────────────────────────────────


class PackStream:
    @staticmethod
    def pack(value: Any) -> bytes:
        if isinstance(value, RawPackedBytes):
            return value.data
        if value is None:
            return b'\xc0'
        if value is True:
            return b'\xc3'
        if value is False:
            return b'\xc2'
        if isinstance(value, bool):
            return b'\xc3' if value else b'\xc2'
        if isinstance(value, int):
            return PackStream._pack_int(value)
        if isinstance(value, float):
            return b'\xc1' + struct.pack('>d', value)
        if isinstance(value, str):
            return PackStream._pack_str(value)
        if isinstance(value, (list, tuple)):
            return PackStream._pack_list(value)
        if isinstance(value, dict):
            return PackStream._pack_map(value)
        if isinstance(value, bytes):
            return PackStream._pack_str(value.decode('utf-8', errors='replace'))
        return PackStream._pack_str(str(value))

    @staticmethod
    def _pack_int(v: int) -> bytes:
        if -16 <= v <= 127:
            return struct.pack('>b', v)
        if -128 <= v <= 127:
            return b'\xc8' + struct.pack('>b', v)
        if -32768 <= v <= 32767:
            return b'\xc9' + struct.pack('>h', v)
        if -2147483648 <= v <= 2147483647:
            return b'\xca' + struct.pack('>i', v)
        return b'\xcb' + struct.pack('>q', v)

    @staticmethod
    def _pack_str(s: str) -> bytes:
        data = s.encode('utf-8')
        n = len(data)
        if n <= 15:
            return bytes([0x80 | n]) + data
        if n <= 255:
            return b'\xd0' + bytes([n]) + data
        if n <= 65535:
            return b'\xd1' + struct.pack('>H', n) + data
        return b'\xd2' + struct.pack('>I', n) + data

    @staticmethod
    def _pack_list(lst: list) -> bytes:
        items = b''.join(PackStream.pack(x) for x in lst)
        n = len(lst)
        if n <= 15:
            return bytes([0x90 | n]) + items
        if n <= 255:
            return b'\xd4' + bytes([n]) + items
        if n <= 65535:
            return b'\xd5' + struct.pack('>H', n) + items
        return b'\xd6' + struct.pack('>I', n) + items

    @staticmethod
    def _pack_map(d: dict) -> bytes:
        items = b''
        for k, v in d.items():
            items += PackStream.pack(str(k)) + PackStream.pack(v)
        n = len(d)
        if n <= 15:
            return bytes([0xa0 | n]) + items
        if n <= 255:
            return b'\xd8' + bytes([n]) + items
        if n <= 65535:
            return b'\xd9' + struct.pack('>H', n) + items
        return b'\xda' + struct.pack('>I', n) + items

    @staticmethod
    def _pack_struct(tag: int, fields: list) -> bytes:
        n = len(fields)
        header = bytes([0xB0 | n, tag])
        body = b''.join(PackStream.pack(f) for f in fields)
        return header + body

    @staticmethod
    def unpack(data: bytes, offset: int = 0) -> tuple[Any, int]:
        if offset >= len(data):
            raise ValueError(f"Offset {offset} out of range (len={len(data)})")
        marker = data[offset]
        offset += 1

        if marker == 0xC0:
            return None, offset
        if marker == 0xC3:
            return True, offset
        if marker == 0xC2:
            return False, offset

        if marker == 0xC1:
            v = struct.unpack('>d', data[offset:offset+8])[0]
            return v, offset + 8

        if marker == 0xC8:
            v = struct.unpack('>b', data[offset:offset+1])[0]
            return v, offset + 1
        if marker == 0xC9:
            v = struct.unpack('>h', data[offset:offset+2])[0]
            return v, offset + 2
        if marker == 0xCA:
            v = struct.unpack('>i', data[offset:offset+4])[0]
            return v, offset + 4
        if marker == 0xCB:
            v = struct.unpack('>q', data[offset:offset+8])[0]
            return v, offset + 8

        if 0x00 <= marker <= 0x7F:
            return marker, offset
        if 0xF0 <= marker <= 0xFF:
            return struct.unpack('>b', bytes([marker]))[0], offset

        if 0x80 <= marker <= 0x8F:
            n = marker & 0x0F
            s = data[offset:offset+n].decode('utf-8')
            return s, offset + n
        if marker == 0xD0:
            n = data[offset]; offset += 1
            s = data[offset:offset+n].decode('utf-8')
            return s, offset + n
        if marker == 0xD1:
            n = struct.unpack('>H', data[offset:offset+2])[0]; offset += 2
            s = data[offset:offset+n].decode('utf-8')
            return s, offset + n
        if marker == 0xD2:
            n = struct.unpack('>I', data[offset:offset+4])[0]; offset += 4
            s = data[offset:offset+n].decode('utf-8')
            return s, offset + n

        if 0x90 <= marker <= 0x9F:
            n = marker & 0x0F
            return PackStream._unpack_list(data, offset, n)
        if marker == 0xD4:
            n = data[offset]; offset += 1
            return PackStream._unpack_list(data, offset, n)
        if marker == 0xD5:
            n = struct.unpack('>H', data[offset:offset+2])[0]; offset += 2
            return PackStream._unpack_list(data, offset, n)

        if 0xA0 <= marker <= 0xAF:
            n = marker & 0x0F
            return PackStream._unpack_map(data, offset, n)
        if marker == 0xD8:
            n = data[offset]; offset += 1
            return PackStream._unpack_map(data, offset, n)
        if marker == 0xD9:
            n = struct.unpack('>H', data[offset:offset+2])[0]; offset += 2
            return PackStream._unpack_map(data, offset, n)

        if 0xB0 <= marker <= 0xBF:
            n = marker & 0x0F
            tag = data[offset]; offset += 1
            fields = []
            for _ in range(n):
                v, offset = PackStream.unpack(data, offset)
                fields.append(v)
            return fields, offset

        raise ValueError(f"Unknown PackStream marker: 0x{marker:02X} at offset {offset-1}")

    @staticmethod
    def _unpack_list(data: bytes, offset: int, n: int) -> tuple[list, int]:
        lst = []
        for _ in range(n):
            v, offset = PackStream.unpack(data, offset)
            lst.append(v)
        return lst, offset

    @staticmethod
    def _unpack_map(data: bytes, offset: int, n: int) -> tuple[dict, int]:
        d = {}
        for _ in range(n):
            k, offset = PackStream.unpack(data, offset)
            v, offset = PackStream.unpack(data, offset)
            d[k] = v
        return d, offset


# ── Chunked message encoding/decoding ─────────────────────────────────────────


def encode_message(msg: bytes, max_chunk: int = MAX_CHUNK) -> bytes:
    out = b''
    if not msg:
        return b'\x00\x00'
    i = 0
    while i < len(msg):
        chunk = msg[i:i + max_chunk]
        out += struct.pack('>H', len(chunk)) + chunk
        i += max_chunk
    out += b'\x00\x00'
    return out


def decode_messages(raw: bytes) -> list[bytes]:
    messages = []
    offset = 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            return messages
        chunk_len = struct.unpack('>H', raw[offset:offset+2])[0]
        offset += 2
        if chunk_len == 0:
            if hasattr(decode_messages, '_current'):
                messages.append(bytes(decode_messages._current))
                del decode_messages._current
            else:
                messages.append(b'')
            continue
        if offset + chunk_len > len(raw):
            return messages
        chunk = raw[offset:offset+chunk_len]
        offset += chunk_len
        if not hasattr(decode_messages, '_current'):
            decode_messages._current = bytearray()
        decode_messages._current.extend(chunk)
    return messages


def _decode_messages_stateless(raw: bytes) -> list[bytes]:
    messages = []
    current = bytearray()
    offset = 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            break
        chunk_len = struct.unpack('>H', raw[offset:offset+2])[0]
        offset += 2
        if chunk_len == 0:
            messages.append(bytes(current))
            current = bytearray()
            continue
        if offset + chunk_len > len(raw):
            break
        current.extend(raw[offset:offset+chunk_len])
        offset += chunk_len
    return messages


decode_messages = _decode_messages_stateless


def bolt_message_bytes(tag: int, *fields) -> bytes:
    n = len(fields)
    header = bytes([0xB0 | n, tag])
    body = b''.join(PackStream.pack(f) for f in fields)
    return header + body


def encode_bolt_message(tag: int, *fields) -> bytes:
    return encode_message(bolt_message_bytes(tag, *fields))


# ── Handshake ─────────────────────────────────────────────────────────────────


def negotiate_version(proposals_bytes: bytes) -> int:
    supported = {BOLT_44}
    for i in range(4):
        raw = struct.unpack('>I', proposals_bytes[i*4:(i+1)*4])[0]
        if raw == 0:
            continue

        major = raw & 0xFF
        minor = (raw >> 8) & 0xFF
        rng = (raw >> 16) & 0xFF

        if major == 4:
            for m in range(minor, max(-1, minor - rng - 1), -1):
                candidate = (m << 8) | major
                if candidate == (BOLT_44 & 0xFFFF):
                    return BOLT_44
    return 0x00000000


# ── Graph object packing ──────────────────────────────────────────────────────


def _node_int_id(node_id: str) -> int:
    return hash(node_id) & 0x7FFFFFFF


def pack_node(node_id: str, labels: list, properties: dict) -> bytes:
    int_id = _node_int_id(node_id)
    return PackStream._pack_struct(TAG_NODE, [int_id, labels, properties])


def pack_relationship(rel_id: int, start_node_id: int, end_node_id: int,
                      rel_type: str, properties: dict) -> bytes:
    return PackStream._pack_struct(TAG_RELATIONSHIP,
                                   [rel_id, start_node_id, end_node_id, rel_type, properties])


# ── State machine ─────────────────────────────────────────────────────────────


class BoltState(Enum):
    CONNECTED = "CONNECTED"
    READY = "READY"
    STREAMING = "STREAMING"
    FAILED = "FAILED"
    DEFUNCT = "DEFUNCT"


class BoltSession:
    def __init__(self, websocket: WebSocket, get_engine_fn):
        self.ws = websocket
        self._get_engine_fn = get_engine_fn
        self._engine = None
        self.state = BoltState.CONNECTED
        self._buf = bytearray()
        self._msg_queue: list[bytes] = []
        self._pending_result: list | None = None
        self._pending_columns: list | None = None
        self._pending_col_types: list | None = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = self._get_engine_fn()
        return self._engine

    async def run(self) -> None:
        await self.ws.accept()
        print("[BOLT-WS] connection accepted")
        try:
            chosen = await self._do_handshake()
            if chosen == 0:
                await self.ws.close()
                return
            self.state = BoltState.READY

            while self.state != BoltState.DEFUNCT:
                try:
                    msg_bytes = await self._recv_message()
                    if not msg_bytes:
                        break
                    await self._dispatch(msg_bytes)
                except Exception as e:
                    if self.state != BoltState.DEFUNCT:
                        log.debug("BoltSession error: %s", e)
                    break
        except Exception as e:
            log.debug("BoltSession fatal: %s", e)
        finally:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def _do_handshake(self) -> int:
        print("[BOLT-WS] waiting for handshake bytes...")
        data = await self.ws.receive_bytes()
        print(f"[BOLT-WS] handshake received: {len(data)} bytes, magic={data[:4].hex() if len(data)>=4 else 'short'}")
        if len(data) < 20 or data[:4] != BOLT_MAGIC:
            await self.ws.send_bytes(struct.pack('>I', 0))
            return 0
        chosen = negotiate_version(data[4:20])
        await self.ws.send_bytes(struct.pack('>I', chosen))
        return chosen

    async def _recv_message(self) -> bytes:
        while True:
            if self._msg_queue:
                return self._msg_queue.pop(0)
            chunk = await self.ws.receive_bytes()
            self._buf.extend(chunk)
            msgs = decode_messages(bytes(self._buf))
            if msgs:
                consumed = 0
                for msg in msgs:
                    consumed += len(encode_message(msg))
                self._buf = self._buf[consumed:]
                if len(msgs) == 1:
                    return msgs[0]
                self._msg_queue.extend(msgs[1:])
                return msgs[0]

    async def _send_message(self, tag: int, *fields) -> None:
        await self.ws.send_bytes(encode_bolt_message(tag, *fields))

    async def _dispatch(self, msg: bytes) -> None:
        print(f"[BOLT-WS] dispatch tag=0x{msg[1]:02x} len={len(msg)}" if len(msg)>=2 else f"[BOLT-WS] dispatch short msg len={len(msg)}")
        if len(msg) < 2:
            return
        tag = msg[1]

        if self.state == BoltState.FAILED:
            if tag == TAG_RESET:
                await self._handle_reset()
            elif tag == TAG_GOODBYE:
                self.state = BoltState.DEFUNCT
            else:
                await self._send_message(TAG_IGNORED)
            return

        if tag == TAG_HELLO:
            fields_data, _ = PackStream.unpack(msg, 2)
            await self._handle_hello(fields_data)
        elif tag == TAG_RUN:
            query, off = PackStream.unpack(msg, 2)
            params, off = PackStream.unpack(msg, off)
            extra = {}
            if off < len(msg):
                extra, _ = PackStream.unpack(msg, off)
            await self._handle_run(query, params, extra)
        elif tag == TAG_PULL:
            extra, _ = PackStream.unpack(msg, 2)
            await self._handle_pull(extra)
        elif tag == TAG_BEGIN:
            await self._send_message(TAG_SUCCESS, {})
        elif tag == TAG_COMMIT:
            await self._send_message(TAG_SUCCESS, {})
            self.state = BoltState.READY
        elif tag == TAG_ROLLBACK:
            self._pending_result = None
            self._pending_columns = None
            self._pending_col_types = None
            await self._send_message(TAG_SUCCESS, {})
            self.state = BoltState.READY
        elif tag == TAG_DISCARD:
            self._pending_result = None
            self._pending_columns = None
            await self._send_message(TAG_SUCCESS, {"type": "r"})
            self.state = BoltState.READY
        elif tag == TAG_RESET:
            await self._handle_reset()
        elif tag == TAG_GOODBYE:
            self.state = BoltState.DEFUNCT
        else:
            print(f"[BOLT-WS] UNKNOWN tag=0x{tag:02x}")
            log.debug("Unknown Bolt tag: 0x%02X", tag)

    async def _handle_hello(self, extra: dict) -> None:
        api_key = os.environ.get("IVG_API_KEY", "")
        if api_key:
            provided = extra.get("credentials", "")
            if provided != api_key:
                await self._send_message(TAG_FAILURE, {
                    "code": "Neo.ClientError.Security.Unauthorized",
                    "message": "Unauthorized: invalid API key",
                })
                self.state = BoltState.FAILED
                return

        conn_id = str(uuid.uuid4())[:8]
        await self._send_message(TAG_SUCCESS, {
            "server": "iris-vector-graph/1.47.0",
            "connection_id": conn_id,
            "hints": {"connection.recv_timeout_seconds": 300},
        })

    async def _handle_run(self, query: str, params: dict, extra: dict) -> None:
        print(f"[BOLT-WS] RUN query={repr(query)[:500]}")
        try:
            engine = self._get_engine()
            result = engine.execute_cypher(query, parameters=params or {})
            columns = result.get("columns", [])
            rows = result.get("rows", [])
            col_types = result.get("_bolt_column_types", ["scalar"] * len(columns))

            graph_cols = self._detect_graph_columns(columns)
            if graph_cols:
                self._pending_columns = columns
                self._pending_graph_cols = graph_cols
                bolt_fields = [g["name"] for g in graph_cols]
            else:
                self._pending_graph_cols = None
                bolt_fields = columns

            self._pending_result = rows
            self._pending_col_types = col_types
            self.state = BoltState.STREAMING
            await self._send_message(TAG_SUCCESS, {
                "fields": bolt_fields,
                "qid": 0,
                "t_first": 1,
                "db": "neo4j",
            })
            print(f"[BOLT-WS] RUN SUCCESS sent, fields={bolt_fields}")
        except Exception as e:
            self._pending_result = None
            self.state = BoltState.FAILED
            await self._send_message(TAG_FAILURE, {
                "code": "Neo.ClientError.Statement.SyntaxError",
                "message": str(e),
            })

    async def _handle_pull(self, extra: dict) -> None:
        if self._pending_result is None:
            await self._send_message(TAG_SUCCESS, {"type": "r"})
            self.state = BoltState.READY
            return

        n = extra.get("n", -1) if isinstance(extra, dict) else -1
        rows = self._pending_result
        if n > 0:
            rows = rows[:n]
        columns = self._pending_columns or []
        graph_cols = getattr(self, "_pending_graph_cols", None)

        for row in rows:
            if graph_cols:
                _, encoded = self._recompose_graph_row(columns, row)
                await self._send_message(TAG_RECORD, encoded)
            else:
                await self._send_message(TAG_RECORD, list(row) if not isinstance(row, list) else row)

        bookmark = f"ivg:{uuid.uuid4().hex[:8]}"
        await self._send_message(TAG_SUCCESS, {
            "type": "r",
            "t_last": 1,
            "bookmark": bookmark,
            "db": "neo4j",
        })
        self._pending_result = None
        self._pending_columns = None
        self._pending_col_types = None
        self.state = BoltState.READY

    async def _handle_reset(self) -> None:
        self._pending_result = None
        self._pending_columns = None
        self._pending_col_types = None
        self.state = BoltState.READY
        await self._send_message(TAG_SUCCESS, {})

    def _detect_graph_columns(self, columns: list) -> list | None:
        groups = []
        i = 0
        has_node_triplet = False
        while i < len(columns):
            c = columns[i]
            if (i + 2 < len(columns) and
                    c.endswith("_id") and
                    columns[i+1].endswith("_labels") and
                    columns[i+2].endswith("_props") and
                    c[:-3] == columns[i+1][:-7] == columns[i+2][:-6]):
                groups.append({"type": "node", "name": c[:-3], "start": i, "span": 3})
                has_node_triplet = True
                i += 3
            elif not c.endswith("_id") and not c.endswith("_labels") and not c.endswith("_props") and "_" not in c:
                groups.append({"type": "rel", "name": c, "start": i, "span": 1})
                i += 1
            else:
                groups.append({"type": "scalar", "name": c, "start": i, "span": 1})
                i += 1
        return groups if has_node_triplet else None

    def _encode_row(self, row: list, col_types: list, engine) -> list:
        result = []
        for i, val in enumerate(row):
            ctype = col_types[i] if i < len(col_types) else "scalar"
            if ctype == "node" and isinstance(val, str) and engine:
                result.append(self._encode_node_value(val, engine))
            elif ctype == "rel":
                result.append(val)
            else:
                result.append(val)
        return result

    def _recompose_graph_row(self, columns: list, row) -> tuple[list, list]:
        """Detect _id/_labels/_props triplets and recompose into Bolt graph objects.

        Returns (new_columns, new_row) where nodes become single PackStream Node
        structures and relationships become PackStream Relationship structures.
        """
        row = list(row) if not isinstance(row, list) else row
        new_cols = []
        new_row = []
        i = 0
        prev_node_id = None

        while i < len(columns):
            col = columns[i]

            if (i + 2 < len(columns) and
                    col.endswith("_id") and
                    columns[i+1].endswith("_labels") and
                    columns[i+2].endswith("_props") and
                    col[:-3] == columns[i+1][:-7] == columns[i+2][:-6]):
                var_name = col[:-3]
                node_id = str(row[i]) if row[i] is not None else ""
                labels_raw = row[i+1]
                props_raw = row[i+2]

                labels = self._parse_json_field(labels_raw, [])
                props = self._parse_props_field(props_raw)
                props["id"] = node_id

                int_id = _node_int_id(node_id)
                node_struct = RawPackedBytes(PackStream._pack_struct(TAG_NODE, [int_id, labels, props]))

                new_cols.append(var_name)
                new_row.append(node_struct)
                prev_node_id = node_id
                i += 3

            elif (i + 1 < len(columns) and
                  not col.endswith("_id") and
                  not col.endswith("_labels") and
                  not col.endswith("_props") and
                  "_" not in col and
                  i + 1 < len(columns) and
                  columns[i+1].endswith("_id")):
                rel_type = str(row[i]) if row[i] is not None else "RELATED_TO"
                start_id = _node_int_id(prev_node_id) if prev_node_id else 0
                end_node_id_str = str(row[i+1]) if i+1 < len(row) else ""
                end_id = _node_int_id(end_node_id_str)
                rel_int_id = hash(f"{prev_node_id}-{rel_type}-{end_node_id_str}") & 0x7FFFFFFF

                rel_struct = RawPackedBytes(PackStream._pack_struct(TAG_RELATIONSHIP,
                    [rel_int_id, start_id, end_id, rel_type, {}]))

                new_cols.append(col)
                new_row.append(rel_struct)
                i += 1

            else:
                new_cols.append(col)
                new_row.append(row[i] if i < len(row) else None)
                i += 1

        return new_cols, new_row

    def _parse_json_field(self, raw, default):
        if raw is None:
            return default
        if isinstance(raw, list):
            return raw
        try:
            import json
            return json.loads(str(raw))
        except Exception:
            return default

    def _parse_props_field(self, raw) -> dict:
        if raw is None:
            return {}
        try:
            import json
            items = json.loads(str(raw)) if isinstance(raw, str) else raw
            if isinstance(items, list):
                props = {}
                for item in items:
                    if isinstance(item, str):
                        try:
                            kv = json.loads(item)
                            if isinstance(kv, dict) and "key" in kv:
                                val = kv.get("value", "")
                                if len(str(val)) > 200:
                                    val = str(val)[:200] + "..."
                                props[kv["key"]] = val
                        except Exception:
                            pass
                    elif isinstance(item, dict) and "key" in item:
                        val = item.get("value", "")
                        if len(str(val)) > 200:
                            val = str(val)[:200] + "..."
                        props[item["key"]] = val
                return props
            if isinstance(items, dict):
                return items
        except Exception:
            pass
        return {}

    def _encode_node_value(self, node_id: str, engine) -> bytes:
        try:
            node = engine.get_node(node_id)
            if node:
                labels = node.get("labels", [])
                props = {k: v for k, v in node.items() if k not in ("id", "labels")}
                return pack_node(node_id, labels, props)
        except Exception:
            pass
        return pack_node(node_id, [], {"id": node_id})


# ── TCP Bolt server (for Python driver / cypher-shell) ───────────────────────


class TcpBoltSession(BoltSession):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 get_engine_fn):
        self._reader = reader
        self._writer = writer
        self._get_engine_fn = get_engine_fn
        self._engine = None
        self.state = BoltState.CONNECTED
        self._buf = bytearray()
        self._pending_result = None
        self._pending_columns = None
        self._pending_col_types = None

    async def run(self) -> None:
        try:
            chosen = await self._do_handshake()
            if chosen == 0:
                self._writer.close()
                return
            self.state = BoltState.READY
            while self.state != BoltState.DEFUNCT:
                try:
                    msg_bytes = await self._recv_message()
                    if msg_bytes is None:
                        break
                    await self._dispatch(msg_bytes)
                except Exception as e:
                    log.debug("TcpBoltSession error: %s", e)
                    break
        except Exception as e:
            log.debug("TcpBoltSession fatal: %s", e)
        finally:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    async def _do_handshake(self) -> int:
        data = await self._reader.readexactly(20)
        if data[:4] != BOLT_MAGIC:
            self._writer.write(struct.pack('>I', 0))
            await self._writer.drain()
            return 0
        chosen = negotiate_version(data[4:20])
        self._writer.write(struct.pack('>I', chosen))
        await self._writer.drain()
        return chosen

    async def _recv_message(self):
        while True:
            try:
                header = await self._reader.readexactly(2)
            except asyncio.IncompleteReadError:
                return None
            chunk_len = struct.unpack('>H', header)[0]
            if chunk_len == 0:
                msg = bytes(self._buf)
                self._buf = bytearray()
                return msg
            data = await self._reader.readexactly(chunk_len)
            self._buf.extend(data)

    async def _send_message(self, tag: int, *fields) -> None:
        data = encode_bolt_message(tag, *fields)
        self._writer.write(data)
        await self._writer.drain()


async def start_tcp_bolt_server(get_engine_fn, host: str = "0.0.0.0", port: int = 7687):
    async def handle(reader, writer):
        session = TcpBoltSession(reader, writer, get_engine_fn)
        await session.run()

    server = await asyncio.start_server(handle, host, port)
    log.info("Bolt TCP server listening on %s:%d", host, port)
    return server
