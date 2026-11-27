"""4.7.2: Bolt BEGIN, COMMIT and ROLLBACK are a transaction on the session's own connection.

Before 4.7.2 each answered SUCCESS and did nothing: a `CREATE` run after BEGIN was committed as it ran, so a ROLLBACK
(or a managed `execute_write` that raised) kept it. The unit cases here run the real session against a stand-in
engine whose store follows the connection's transaction (START TRANSACTION, COMMIT, ROLLBACK), so each case reads
what the store holds afterwards, not what was sent. The live proof, with the Neo4j driver against IRIS, is
tests/e2e/test_472_bolt_transactions_e2e.py.
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")

from iris_vector_graph.bolt_server import (  # noqa: E402
    TAG_BEGIN,
    TAG_COMMIT,
    TAG_FAILURE,
    TAG_GOODBYE,
    TAG_HELLO,
    TAG_IGNORED,
    TAG_PULL,
    TAG_RESET,
    TAG_ROLLBACK,
    TAG_RUN,
    BoltSession,
    PackStream,
    TcpBoltSession,
    decode_messages,
    encode_message,
)
from iris_vector_graph.result import IVGResult  # noqa: E402

BOLT_44 = 0x00000404


class Store:
    """What the database holds: committed rows, and the open transaction's rows until it ends."""

    def __init__(self):
        self.committed = set()
        self.open = None  # None: no transaction; a set: the rows it has written


class Cursor:
    def __init__(self, store):
        self._store = store

    def execute(self, sql, *args):
        s = self._store
        text = sql.strip().upper()
        if text == "START TRANSACTION":
            if s.open is None:
                s.open = set()
        elif text == "COMMIT":
            s.committed |= s.open or set()
            s.open = None
        elif text == "ROLLBACK":
            s.open = None
        else:
            raise AssertionError(f"sent {sql!r}")

    def close(self):
        pass


class Conn:
    def __init__(self, store):
        self._store = store

    def cursor(self):
        return Cursor(self._store)


class Engine:
    """execute_cypher: `CREATE x` writes row x, `FAIL` raises, `MATCH` reads what this connection sees."""

    def __init__(self, store):
        self.store = store
        self.conn = Conn(store)

    def execute_cypher(self, query, parameters=None):
        s = self.store
        if query.startswith("CREATE "):
            (s.open if s.open is not None else s.committed).add(query.split()[1])
            return IVGResult(columns=["c"], rows=[[1]])
        if query == "FAIL":
            return IVGResult(columns=[], rows=[], error="boom")
        return IVGResult(columns=["c"], rows=[[x] for x in sorted(s.committed | (s.open or set()))])


def _msg(tag, *fields):
    body = b"".join(PackStream.pack(f) for f in fields)
    return encode_message(bytes([0xB0 | len(fields), tag]) + body)


HELLO = (TAG_HELLO, [{"user_agent": "t"}])
BEGIN = (TAG_BEGIN, [{}])
COMMIT = (TAG_COMMIT, [])
ROLLBACK = (TAG_ROLLBACK, [])
RESET = (TAG_RESET, [])
PULL = (TAG_PULL, [{"n": -1}])


def create(row):
    return (TAG_RUN, [f"CREATE {row}", {}, {}])


def _run(messages, store, monkeypatch, goodbye=True):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", BOLT_44, 0, 0, 0)
    chunks = [_msg(tag, *fields) for tag, fields in messages] + ([_msg(TAG_GOODBYE)] if goodbye else [])
    ws.receive_bytes = AsyncMock(side_effect=[handshake] + chunks + [Exception("the client went away")])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = Engine(store)
    asyncio.run(BoltSession(ws, lambda: engine).run())
    return [m[1] for call in ws.send_bytes.call_args_list[1:] for m in decode_messages(call[0][0])]


def test_a_rolled_back_create_does_not_exist(monkeypatch):
    store = Store()
    tags = _run([HELLO, BEGIN, create("n1"), PULL, ROLLBACK], store, monkeypatch)
    assert TAG_FAILURE not in tags
    assert store.committed == set() and store.open is None


def test_a_committed_create_stays(monkeypatch):
    store = Store()
    tags = _run([HELLO, BEGIN, create("n1"), PULL, COMMIT], store, monkeypatch)
    assert TAG_FAILURE not in tags
    assert store.committed == {"n1"} and store.open is None


def test_the_transaction_reads_its_own_writes_and_others_do_not_until_it_commits(monkeypatch):
    store = Store()
    seen = {}
    original = Engine.execute_cypher

    def spy(self, query, parameters=None):
        out = original(self, query, parameters)
        if query.startswith("MATCH"):
            seen["committed_at_read"] = set(self.store.committed)
            seen["read"] = [r[0] for r in out.rows]
        return out

    monkeypatch.setattr(Engine, "execute_cypher", spy)
    _run([HELLO, BEGIN, create("n1"), PULL, (TAG_RUN, ["MATCH (n) RETURN n", {}, {}]), PULL, COMMIT], store,
         monkeypatch)
    assert seen == {"committed_at_read": set(), "read": ["n1"]}
    assert store.committed == {"n1"}


def test_autocommit_runs_as_before(monkeypatch):
    store = Store()
    tags = _run([HELLO, create("n1"), PULL, create("n2"), PULL], store, monkeypatch)
    assert TAG_FAILURE not in tags
    assert store.committed == {"n1", "n2"} and store.open is None


def test_a_session_that_ends_in_a_transaction_rolls_it_back(monkeypatch):
    store = Store()
    _run([HELLO, BEGIN, create("n1"), PULL], store, monkeypatch, goodbye=False)
    assert store.committed == set() and store.open is None


def test_goodbye_in_a_transaction_rolls_it_back(monkeypatch):
    store = Store()
    _run([HELLO, BEGIN, create("n1"), PULL], store, monkeypatch, goodbye=True)
    assert store.committed == set() and store.open is None


def test_a_failed_statement_fails_the_transaction_and_reset_rolls_it_back(monkeypatch):
    store = Store()
    tags = _run([HELLO, BEGIN, create("n1"), PULL, (TAG_RUN, ["FAIL", {}, {}]), COMMIT, RESET], store, monkeypatch)
    # the failed RUN is a FAILURE, the COMMIT after it is IGNORED, RESET clears it
    assert tags.count(TAG_FAILURE) == 1 and TAG_IGNORED in tags
    assert store.committed == set() and store.open is None


def test_a_later_transaction_after_a_reset_is_a_fresh_one(monkeypatch):
    store = Store()
    _run([HELLO, BEGIN, create("n1"), PULL, (TAG_RUN, ["FAIL", {}, {}]), RESET,
          BEGIN, create("n2"), PULL, COMMIT], store, monkeypatch)
    assert store.committed == {"n2"}


def test_commit_and_rollback_without_begin_are_refused(monkeypatch):
    store = Store()
    tags = _run([HELLO, COMMIT, RESET, ROLLBACK], store, monkeypatch)
    assert tags.count(TAG_FAILURE) == 2 and store.open is None


def test_begin_inside_a_transaction_is_refused_and_the_first_is_not_lost_or_committed(monkeypatch):
    store = Store()
    tags = _run([HELLO, BEGIN, create("n1"), PULL, BEGIN, RESET], store, monkeypatch)
    assert tags.count(TAG_FAILURE) == 1
    assert store.committed == set() and store.open is None


def test_a_begin_the_database_refuses_is_a_failure_and_not_a_transaction(monkeypatch):
    store = Store()

    class Refusing(Cursor):
        def execute(self, sql, *args):
            raise RuntimeError("no transaction for you")

    monkeypatch.setattr(Conn, "cursor", lambda self: Refusing(self._store))
    tags = _run([HELLO, BEGIN, RESET], store, monkeypatch)
    assert tags.count(TAG_FAILURE) == 1
    # the next statement is not run as if it were in a transaction the server never opened
    assert store.open is None


def test_begin_success_carries_the_database_name_clients_read(monkeypatch):
    store = Store()
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    ws = AsyncMock()
    handshake = b"\x60\x60\xb0\x17" + struct.pack(">IIII", BOLT_44, 0, 0, 0)
    chunks = [_msg(TAG_HELLO, {"user_agent": "t"}), _msg(TAG_BEGIN, {}), _msg(TAG_GOODBYE)]
    ws.receive_bytes = AsyncMock(side_effect=[handshake] + chunks + [Exception("end")])
    ws.send_bytes = AsyncMock()
    ws.scope = {"subprotocols": []}
    engine = Engine(store)
    asyncio.run(BoltSession(ws, lambda: engine).run())
    answers = [decode_messages(c[0][0]) for c in ws.send_bytes.call_args_list[1:]]
    begin = PackStream.unpack(answers[1][0], 0)[0]
    assert begin[0] == {"db": "neo4j"}


def test_the_tcp_session_rolls_back_too(monkeypatch):
    store = Store()
    engine = Engine(store)
    chunks = [_msg(TAG_HELLO, {"user_agent": "t"}), _msg(TAG_BEGIN, {}), _msg(TAG_RUN, "CREATE n1", {}, {}),
              _msg(TAG_PULL, {"n": -1}), _msg(TAG_ROLLBACK)]
    data = b"\x60\x60\xb0\x17" + struct.pack(">IIII", BOLT_44, 0, 0, 0) + b"".join(chunks)

    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        writer = AsyncMock()
        writer.write = lambda b: None
        writer.close = lambda: None
        await TcpBoltSession(reader, writer, lambda: engine).run()

    asyncio.run(go())
    assert store.committed == set() and store.open is None
