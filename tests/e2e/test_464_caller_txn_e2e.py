"""4.6.4: an IVG write never commits or rolls back a transaction its caller opened.

Measured on 4.6.3 (IRIS 2026.3, driver 5.4.0): after `START TRANSACTION` and an INSERT of the caller's own,
`create_node` ran `COMMIT`, which commits every level, so the caller's `ROLLBACK` took back neither the caller's row
nor the node. A failed write ran `ROLLBACK`, which took back the caller's earlier work with its own.

Each write here runs four ways, its result read from a second connection once the caller's transaction is over:

  (a) caller's transaction, then the caller rolls back: nothing of either remains
  (b) caller's transaction, then the caller commits: both remain
  (c) caller's transaction, and IVG's write fails: IVG raises (create_node answers its documented False), its own
      partial rows are gone, and the caller's earlier row is still in the open transaction and commits
  (d) no caller transaction: IVG's write is committed when the call returns (the connection is closed without a
      commit, and the second connection still reads it)

and a caller opens a transaction two ways: `START TRANSACTION` over SQL (`txn` "sql"), or the driver's
`setAutoCommit(False)` (`txn` "driver"; the driver's connection autocommits by default, and then starts a transaction at
the first statement that writes).

    IVG_R464_TXN_NS=IVG463X pytest tests/e2e/test_464_caller_txn_e2e.py
"""

from __future__ import annotations

import os
import re
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R464_TXN_NS", "")
CALLER_TABLE = "SQLUser.ivg464_caller"
#: rdf_labels.label is VARCHAR(128) and rdf_edges.p VARCHAR(128): a longer one is SQLCODE -104, after the rows
#: written before it in the same call
TOO_LONG = "L" * 200

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R464_TXN_NS"),
]

TXNS = ("sql", "driver")


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=rr.DIM)


@pytest.fixture(scope="module")
def observer():
    """A second connection, which reads what the caller's connection left once its transaction is over."""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(f"DROP TABLE {CALLER_TABLE}")
    except Exception:  # noqa: BLE001 - absent on the first run
        pass
    cur.execute(f"CREATE TABLE {CALLER_TABLE} (k VARCHAR(80) PRIMARY KEY)")
    yield conn
    cur.execute(f"DROP TABLE {CALLER_TABLE}")
    conn.close()


@pytest.fixture
def caller():
    conn = _connect()
    yield conn
    try:
        conn.rollback()
    finally:
        conn.close()


def _level(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT $TLEVEL")
    return int(cur.fetchone()[0])


def _open(conn, txn: str) -> None:
    if txn == "sql":
        conn.cursor().execute("START TRANSACTION")
    else:
        conn.setAutoCommit(False)


def _end(conn, txn: str, how: str) -> None:
    if txn == "sql":
        conn.cursor().execute(how.upper())
    else:
        getattr(conn, how)()
        conn.setAutoCommit(True)


def _caller_row(conn, key: str) -> None:
    conn.cursor().execute(f"INSERT INTO {CALLER_TABLE} (k) VALUES (?)", [key])


def _has_caller_row(obs, key: str) -> bool:
    cur = obs.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {CALLER_TABLE} WHERE k = ?", [key])
    return cur.fetchone()[0] == 1


def _count(obs, sql: str, params) -> int:
    cur = obs.cursor()
    cur.execute(sql, list(params))
    return int(cur.fetchone()[0])


def _node(obs, nid: str) -> bool:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ''", [nid]) == 1


def _label(obs, nid: str, label: str) -> bool:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ? AND label = ? AND graph_id = ''",
                  [nid, label]) == 1


def _prop(obs, nid: str, key: str):
    cur = obs.cursor()
    cur.execute('SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ? AND graph_id = \'\'', [nid, key])
    row = cur.fetchone()
    return None if row is None else row[0]


def _edge(obs, s: str, p: str, o: str) -> bool:
    rows = _count(obs, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ''",
                  [s, p, o])
    import iris

    kg = iris.createIRIS(obs).isDefined("^KG", "out", 0, s, p, o)
    # the row and its ^KG entry move together; a row without its adjacency (or the reverse) is a defect of its own
    assert bool(rows) == bool(kg), f"rdf_edges has {rows} row(s) of {s}-{p}->{o}, ^KG $Data {kg}"
    return rows == 1


# --------------------------------------------------------------------------------------------- the writes under test
#
# Each is (setup, write, done, fail, failed_left_nothing):
#   setup(engine, u)       committed state the write needs, before the caller's transaction
#   write(engine, u)       the write
#   done(obs, u)           whether the write's effect is in the database (False: the state before it)
#   fail(engine, u)        a write of the same call that IRIS refuses after it has written part of it; it raises
#   left_nothing(obs, u)   none of the failed write's rows are there


def _w_create_node():
    return dict(
        setup=lambda e, u: None,
        write=lambda e, u: e.create_node(f"{u}n", labels=["T464"], properties={"v": "1"}),
        done=lambda o, u: _node(o, f"{u}n") and _label(o, f"{u}n", "T464") and _prop(o, f"{u}n", "v") == "1",
        undone=lambda o, u: not _node(o, f"{u}n") and not _label(o, f"{u}n", "T464"),
        # node_id is VARCHAR(256): IRIS refuses the row (-104), and create_node answers False, nothing written.
        # (A too-long label is no failure to test with: the driver's executemany skips a row IRIS refuses without
        # raising, so create_node answers True without the label; see the 4.6.4 changelog.)
        fail=lambda e, u: e.create_node(f"{u}x" + "x" * 300, labels=["T464"]),
        left_nothing=lambda o, u: _count(o, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id %STARTSWITH ?",
                                         [f"{u}x"]) == 0,
    )


def _w_create_edge():
    return dict(
        setup=lambda e, u: None,
        write=lambda e, u: e.create_edge(f"{u}a", "R464", f"{u}b"),
        done=lambda o, u: _edge(o, f"{u}a", "R464", f"{u}b") and _node(o, f"{u}a") and _node(o, f"{u}b"),
        undone=lambda o, u: not _edge(o, f"{u}a", "R464", f"{u}b") and not _node(o, f"{u}a"),
        # the endpoint rows are written first, then the edge row is refused
        fail=lambda e, u: e.create_edge(f"{u}xa", TOO_LONG, f"{u}xb"),
        left_nothing=lambda o, u: not _node(o, f"{u}xa") and not _node(o, f"{u}xb"),
    )


def _setup_pair(e, u):
    assert e.create_node(f"{u}n", labels=["T464"], properties={"v": "1"})
    assert e.create_edge(f"{u}n", "R464", f"{u}m")


def _w_delete_node():
    return dict(
        setup=_setup_pair,
        write=lambda e, u: e.delete_node(f"{u}n", graph=None),
        done=lambda o, u: not _node(o, f"{u}n") and not _edge(o, f"{u}n", "R464", f"{u}m"),
        undone=lambda o, u: _node(o, f"{u}n") and _edge(o, f"{u}n", "R464", f"{u}m")
        and _label(o, f"{u}n", "T464"),
        fail=None,
        left_nothing=None,
    )


def _w_delete_edge():
    return dict(
        setup=_setup_pair,
        write=lambda e, u: e.delete_edge(f"{u}n", "R464", f"{u}m"),
        done=lambda o, u: not _edge(o, f"{u}n", "R464", f"{u}m") and _node(o, f"{u}n"),
        undone=lambda o, u: _edge(o, f"{u}n", "R464", f"{u}m"),
        fail=None,
        left_nothing=None,
    )


def _w_store_node():
    return dict(
        setup=lambda e, u: e.create_node(f"{u}n", labels=["T464"], properties={"v": "1"}),
        write=lambda e, u: e.store_node(f"{u}n", properties={"v": "2"}, labels=["T464B"]),
        done=lambda o, u: _prop(o, f"{u}n", "v") == "2" and _label(o, f"{u}n", "T464B"),
        undone=lambda o, u: _prop(o, f"{u}n", "v") == "1" and not _label(o, f"{u}n", "T464B"),
        # the properties are replaced first, then the label is refused
        fail=lambda e, u: e.store_node(f"{u}n", properties={"v": "3"}, labels=[TOO_LONG]),
        left_nothing=lambda o, u: _prop(o, f"{u}n", "v") == "1",
    )


def _w_bulk_create_nodes():
    return dict(
        setup=lambda e, u: None,
        write=lambda e, u: e.bulk_create_nodes([{"id": f"{u}n{i}", "labels": ["T464"]} for i in range(3)]),
        done=lambda o, u: all(_node(o, f"{u}n{i}") and _label(o, f"{u}n{i}", "T464") for i in range(3)),
        undone=lambda o, u: not any(_node(o, f"{u}n{i}") for i in range(3)),
        # bad input, refused before anything is written (executemany skips a row IRIS refuses without raising)
        fail=lambda e, u: e.bulk_create_nodes([{"id": f"{u}x0", "labels": ["T464"]}, {"id": f"{u}x1", "bogus": 1}]),
        left_nothing=lambda o, u: not _node(o, f"{u}x0") and not _node(o, f"{u}x1"),
    )


def _w_bulk_ingest_edges():
    return dict(
        setup=lambda e, u: None,
        write=lambda e, u: e.bulk_ingest_edges([(f"{u}a{i}", f"{u}b{i}") for i in range(3)], predicate="R464"),
        done=lambda o, u: all(_edge(o, f"{u}a{i}", "R464", f"{u}b{i}") for i in range(3)),
        undone=lambda o, u: not any(_edge(o, f"{u}a{i}", "R464", f"{u}b{i}") or _node(o, f"{u}a{i}")
                                    for i in range(3)),
        fail=None,
        left_nothing=None,
    )


def _cy(query):
    """`$k_<suffix>` is the test's id with that suffix."""
    names = set(re.findall(r"\$k_(\w+)", query))

    def run(e, u):
        return e.execute_cypher(query, {f"k_{n}": u + n for n in names}, raise_on_error=True)
    return run


def _w_cypher_create():
    return dict(
        setup=lambda e, u: None,
        write=_cy("CREATE (a:T464 {id: $k_a, v: '1'})-[:R464]->(b:T464 {id: $k_b})"),
        done=lambda o, u: _node(o, f"{u}a") and _label(o, f"{u}a", "T464") and _edge(o, f"{u}a", "R464", f"{u}b"),
        undone=lambda o, u: not _node(o, f"{u}a") and not _node(o, f"{u}b"),
        # the first node's rows are written, then the second node's label is refused
        fail=_cy(f"CREATE (a:T464 {{id: $k_xa}}), (b:{TOO_LONG} {{id: $k_xb}})"),
        left_nothing=lambda o, u: not _node(o, f"{u}xa") and not _node(o, f"{u}xb"),
    )


def _w_cypher_merge():
    return dict(
        setup=lambda e, u: None,
        write=_cy("MERGE (a:T464 {id: $k_m}) ON CREATE SET a.v = '1'"),
        done=lambda o, u: _node(o, f"{u}m") and _label(o, f"{u}m", "T464") and _prop(o, f"{u}m", "v") == "1",
        undone=lambda o, u: not _node(o, f"{u}m"),
        fail=_cy(f"MERGE (a:T464 {{id: $k_xm}}) ON CREATE SET a:{TOO_LONG}"),
        left_nothing=lambda o, u: not _node(o, f"{u}xm"),
    )


def _w_cypher_set():
    return dict(
        setup=lambda e, u: e.create_node(f"{u}n", labels=["T464"], properties={"v": "1"}),
        write=_cy("MATCH (n:T464 {id: $k_n}) SET n.v = '2'"),
        done=lambda o, u: _prop(o, f"{u}n", "v") == "2",
        undone=lambda o, u: _prop(o, f"{u}n", "v") == "1",
        fail=_cy(f"MATCH (n:T464 {{id: $k_n}}) SET n.v = '3' SET n:{TOO_LONG}"),
        left_nothing=lambda o, u: _prop(o, f"{u}n", "v") == "1",
    )


def _w_cypher_delete():
    return dict(
        setup=_setup_pair,
        write=_cy("MATCH (n:T464 {id: $k_n}) DETACH DELETE n"),
        done=lambda o, u: not _node(o, f"{u}n") and not _edge(o, f"{u}n", "R464", f"{u}m"),
        undone=lambda o, u: _node(o, f"{u}n") and _edge(o, f"{u}n", "R464", f"{u}m"),
        # a node with a relationship is refused by DELETE without DETACH, after the SET before it ran
        fail=_cy("MATCH (n:T464 {id: $k_n}) SET n.v = '9' DELETE n"),
        left_nothing=lambda o, u: _node(o, f"{u}n") and _prop(o, f"{u}n", "v") == "1",
    )


WRITES = {
    "create_node": _w_create_node(),
    "create_edge": _w_create_edge(),
    "delete_node": _w_delete_node(),
    "delete_edge": _w_delete_edge(),
    "store_node": _w_store_node(),
    "bulk_create_nodes": _w_bulk_create_nodes(),
    "bulk_ingest_edges": _w_bulk_ingest_edges(),
    "cypher_create": _w_cypher_create(),
    "cypher_merge": _w_cypher_merge(),
    "cypher_set": _w_cypher_set(),
    "cypher_delete": _w_cypher_delete(),
}
FAILING = [name for name, w in WRITES.items() if w["fail"] is not None]


def _uid() -> str:
    return "t464" + uuid.uuid4().hex[:10]


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("name", list(WRITES))
def test_a_caller_rollback_takes_back_both(observer, caller, name, txn):
    w, u = WRITES[name], _uid()
    eng = _engine(caller)
    w["setup"](eng, u)
    _open(caller, txn)
    _caller_row(caller, u)
    w["write"](eng, u)
    assert _level(caller) == 1, "the write left the caller's transaction at another level"
    _end(caller, txn, "rollback")
    assert not _has_caller_row(observer, u), "the caller's row outlived the caller's rollback"
    assert w["undone"](observer, u), f"{name}'s write outlived the caller's rollback"


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("name", list(WRITES))
def test_b_caller_commit_keeps_both(observer, caller, name, txn):
    w, u = WRITES[name], _uid()
    eng = _engine(caller)
    w["setup"](eng, u)
    _open(caller, txn)
    _caller_row(caller, u)
    w["write"](eng, u)
    assert _level(caller) == 1
    _end(caller, txn, "commit")
    assert _has_caller_row(observer, u)
    assert w["done"](observer, u), f"{name}'s write is not there after the caller's commit"


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("name", FAILING)
def test_c_a_failed_write_keeps_the_callers_work(observer, caller, name, txn):
    from iris_vector_graph.errors import GraphWriteError

    w, u = WRITES[name], _uid()
    eng = _engine(caller)
    w["setup"](eng, u)
    _open(caller, txn)
    _caller_row(caller, u)
    try:
        answer = w["fail"](eng, u)
    except (GraphWriteError, RuntimeError, ValueError):
        pass
    else:
        # only create_node answers a refused row instead of raising: False, its "nothing written"
        assert name == "create_node" and answer is False, f"{name}'s failing write answered {answer!r}"
    assert _level(caller) == 1, "the failed write ended the caller's transaction (or left a level of its own open)"
    _end(caller, txn, "commit")
    assert _has_caller_row(observer, u), "the failed write took the caller's earlier row with it"
    assert w["left_nothing"](observer, u), f"{name}'s failed write left part of itself behind"


@pytest.mark.parametrize("name", list(WRITES))
def test_d_without_a_caller_transaction_the_write_is_committed(observer, name):
    w, u = WRITES[name], _uid()
    conn = _connect()
    try:
        eng = _engine(conn)
        w["setup"](eng, u)
        w["write"](eng, u)
        assert _level(conn) == 0, "the write left a transaction open"
    finally:
        # closed without a commit: a write left uncommitted is rolled back here
        conn.close()
    assert w["done"](observer, u), f"{name}'s write is not committed when the call returns"


def test_create_node_duplicate_in_the_callers_transaction_answers_false_and_keeps_the_callers_row(observer, caller):
    u = _uid()
    eng = _engine(caller)
    assert eng.create_node(f"{u}n")
    _open(caller, "sql")
    _caller_row(caller, u)
    assert eng.create_node(f"{u}n") is False
    assert _level(caller) == 1
    _end(caller, "sql", "commit")
    assert _has_caller_row(observer, u)
