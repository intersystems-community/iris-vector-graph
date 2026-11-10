"""4.7.0: the writers 4.6.4 left out never commit a transaction their caller opened (row 3), and `bulk_create_nodes`
leaves the caller's own dicts as they were (row 13).

Row 3, measured on 4.6.5 (IRIS 2026.3, driver 5.4.0): `bulk_load_session` called `conn.commit()` after dropping the
indexes, after rebuilding them and after tuning the tables, and `embed_nodes` after every batch: the driver's commit
ends every level, so the caller's rollback took back neither its own row nor IVG's writes. `initialize_schema()` commits
on its way through setup too, and deploys classes, which no transaction takes back; with a caller transaction open it
now refuses before doing anything, and the caller's row is still in the open transaction.

Each write runs as test_464_caller_txn_e2e's do, its result read from a second connection once the caller's transaction
is over: (a) the caller rolls back, nothing of either remains; (b) the caller commits, both remain; (d) no caller
transaction, the write is committed when the call returns. A caller opens a transaction with `START TRANSACTION`
(`txn` "sql") or the driver's `setAutoCommit(False)` ("driver").

Row 13: `bulk_create_nodes`'s batch path wrote `props["id"] = node_id` into the caller's own properties dict
(`{'name': 'z'}` came back `{'name': 'z', 'id': 'z'}`). Every dict and list the caller passed compares equal to a
`copy.deepcopy` taken before the call, on the server loader (classes detected) and on the batch path (a fresh engine, and
a named graph), and the stored `id` property is still written (#12, 4.6.0 R089): `MATCH (n {id: $id}) RETURN n.name`.

    IVG_R470_TXN_NS=IVG470TXN pytest tests/e2e/test_470_caller_txn_e2e.py
"""

from __future__ import annotations

import copy
import logging
import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_TXN_NS", "")
CALLER_TABLE = "SQLUser.ivg470_caller"
#: the indexes bulk_load_session drops and rebuilds around a load of its own (GraphSchema.disable_indexes)
BULK_INDEXES = {
    "idx_labels_s": "rdf_labels",
    "idx_labels_label": "rdf_labels",
    "idx_props_s": "rdf_props",
    "idx_props_key": "rdf_props",
}

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_TXN_NS"),
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


def _edge(obs, s: str, p: str, o: str) -> bool:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ? AND graph_id = ''",
                  [s, p, o]) == 1


def _vector(obs, nid: str):
    cur = obs.cursor()
    cur.execute("SELECT emb FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ? AND graph_id = ''", [nid])
    row = cur.fetchone()
    if row is None:
        return None
    return [float(x) for x in str(row[0]).strip("[]").split(",")]


def _indexes_present(obs) -> set:
    cur = obs.cursor()
    cur.execute(
        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = 'Graph_KG' "
        "AND TABLE_NAME IN ('rdf_labels', 'rdf_props')"
    )
    return {str(r[0]) for r in cur.fetchall()} & set(BULK_INDEXES)


def _uid() -> str:
    return "t470" + uuid.uuid4().hex[:10]


class Fake:
    """An embedder whose every vector is 0.25 in each place: the known answer the stored vector is compared with."""

    VALUE = 0.25

    def encode(self, texts, **_):
        import numpy as np

        if isinstance(texts, str):
            return np.full(rr.DIM, self.VALUE)
        return np.full((len(texts), rr.DIM), self.VALUE)


# --------------------------------------------------------------------------------------------- row 3: the writes


def _load(e, u):
    with e.bulk_load_session() as s:
        s.add_nodes([{"id": f"{u}n{i}", "labels": ["T470"]} for i in range(3)])
        s.add_edges([(f"{u}n{i}", f"{u}n{(i + 1) % 3}") for i in range(3)], predicate="R470")


def _w_bulk_load_session():
    return dict(
        setup=lambda e, u: None,
        write=_load,
        done=lambda o, u: all(_node(o, f"{u}n{i}") and _label(o, f"{u}n{i}", "T470")
                              and _edge(o, f"{u}n{i}", "R470", f"{u}n{(i + 1) % 3}") for i in range(3)),
        undone=lambda o, u: not any(_node(o, f"{u}n{i}") or _edge(o, f"{u}n{i}", "R470", f"{u}n{(i + 1) % 3}")
                                    for i in range(3)),
    )


def _w_embed_nodes():
    return dict(
        setup=lambda e, u: e.create_node(f"{u}n", labels=["T470"]),
        write=lambda e, u: e.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=[f"{u}n"],
                                         force=True),
        done=lambda o, u: _vector(o, f"{u}n") == [Fake.VALUE] * rr.DIM,
        undone=lambda o, u: _vector(o, f"{u}n") is None and _node(o, f"{u}n"),
    )


WRITES = {"bulk_load_session": _w_bulk_load_session(), "embed_nodes": _w_embed_nodes()}


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


@pytest.mark.parametrize("how", ("rollback", "commit"))
def test_bulk_load_session_in_a_callers_transaction_leaves_the_indexes_and_flags_the_adjacency(observer, caller, how):
    """The session drops and rebuilds indexes (DDL) only outside a caller's transaction, so they are all there after
    the caller ends it either way; the adjacency rebuild it leaves for after the caller's commit is flagged
    (`status().pending_sync`), as `bulk_ingest_edges` flags it."""
    u = _uid()
    assert _indexes_present(observer) == set(BULK_INDEXES), "the namespace lacks a bulk index before the test"
    eng = _engine(caller)
    _open(caller, "sql")
    _caller_row(caller, u)
    _load(eng, u)
    assert eng._nkg_dirty is True
    _end(caller, "sql", how)
    assert _indexes_present(observer) == set(BULK_INDEXES)


def _routes(obs, graph: str) -> int:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.embedding_registry WHERE graph_id = ?", [graph])


@pytest.mark.parametrize("txn", TXNS)
def test_embed_nodes_refuses_to_make_a_route_in_a_callers_transaction(observer, caller, txn):
    """The first vector of a named graph (or of a new model_key) makes its route: a table, an index, a registry row,
    committed together (`_create_route`), which committed the caller's work. Inside a caller's transaction that is
    refused before the route is made; the caller's transaction stays open and follows its own rollback."""
    from iris_vector_graph.errors import CallerTransactionOpenError

    u = _uid()
    graph = "g470r" + u
    eng = _engine(caller)
    assert eng.create_node(f"{u}n", graph=graph)
    assert _routes(observer, graph) == 0
    _open(caller, txn)
    _caller_row(caller, u)
    with pytest.raises(CallerTransactionOpenError) as info:
        eng.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=[f"{u}n"], force=True, graph=graph)
    assert "route" in str(info.value), str(info.value)
    assert _level(caller) == 1, "the refusal ended the caller's transaction"
    _end(caller, txn, "rollback")
    assert not _has_caller_row(observer, u), "the caller's row outlived the caller's rollback"
    assert _routes(observer, graph) == 0
    # outside a transaction the same call makes the route and stores the vector
    r = eng.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=[f"{u}n"], force=True, graph=graph)
    assert r["embedded"] == 1 and r["errors"] == 0, r
    assert _routes(observer, graph) == 1


# ------------------------------------------------------------------------------- row 3: setup refuses, writes nothing


@pytest.mark.parametrize("how", ("rollback", "commit"))
@pytest.mark.parametrize("txn", TXNS)
def test_initialize_schema_refuses_a_callers_open_transaction(observer, caller, txn, how):
    from iris_vector_graph.errors import CallerTransactionOpenError

    u = _uid()
    eng = _engine(caller)
    _open(caller, txn)
    _caller_row(caller, u)
    with pytest.raises(CallerTransactionOpenError) as info:
        eng.initialize_schema()
    text = str(info.value).lower()
    assert "initialize_schema" in text and "open transaction" in text and "nothing was done" in text, text
    assert _level(caller) == 1, "the refusal ended the caller's transaction"
    _end(caller, txn, how)
    assert _has_caller_row(observer, u) is (how == "commit"), "the caller's row did not follow the caller's " + how


def test_initialize_schema_with_autocommit_off_runs_until_the_first_write(observer, caller):
    """setAutoCommit(False) with nothing written yet ($TLEVEL 0) has nothing uncommitted to lose, so setup runs; after
    the caller's first write ($TLEVEL 1) it refuses, and the caller's row is still in its open transaction."""
    from iris_vector_graph.errors import CallerTransactionOpenError

    u = _uid()
    eng = _engine(caller)
    caller.setAutoCommit(False)
    try:
        assert _level(caller) == 0
        status = eng.initialize_schema()
        assert status["schema"] == "Graph_KG", status
        _caller_row(caller, u)
        with pytest.raises(CallerTransactionOpenError, match=r"\$TLEVEL 1"):
            eng.initialize_schema()
        assert _level(caller) == 1
    finally:
        caller.rollback()
        caller.setAutoCommit(True)
    assert not _has_caller_row(observer, u)


def test_a_new_route_with_autocommit_off_is_made_until_the_first_write(observer, caller):
    """The new-route refusal follows the same rule: $TLEVEL 0 (autocommit off, nothing written) makes the route."""
    u = _uid()
    graph = "g470a" + u
    eng = _engine(caller)
    assert eng.create_node(f"{u}n", graph=graph)
    caller.setAutoCommit(False)
    try:
        assert _level(caller) == 0
        r = eng.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=[f"{u}n"], force=True,
                            graph=graph)
        assert r["embedded"] == 1 and r["errors"] == 0, r
        caller.commit()
    finally:
        caller.setAutoCommit(True)
    assert _routes(observer, graph) == 1


# ------------------------------------------------------------------------- row 13: the caller's dicts stay as given


def _nodes(u):
    return [
        {"id": f"{u}z", "labels": ["T470"], "properties": {"name": f"{u}z", "tags": ["a", "b"]}},
        {"id": f"{u}y", "labels": ["T470", "T470B"]},
        {"id": f"{u}x", "properties": {}},
    ]


def _name_by_id(eng, nid):
    r = eng.execute_cypher("MATCH (n {id: $id}) RETURN n.name", {"id": nid}, raise_on_error=True)
    return [row[0] for row in r.rows]


@pytest.mark.parametrize("path", ("server_loader", "batch", "batch_named_graph"))
def test_bulk_create_nodes_leaves_the_callers_dicts_as_given(observer, path, caplog):
    u = _uid()
    conn = _connect()
    try:
        eng = _engine(conn)
        if path == "server_loader":
            assert eng.capabilities.objectscript_deployed, "the classes are not deployed: no server loader to test"
        else:
            # a fresh engine has detected nothing, so it takes the batch path (bulk_create_nodes' docstring)
            assert not eng._known_capabilities.objectscript_deployed
        graph = "g470" + u if path == "batch_named_graph" else None
        nodes = _nodes(u)
        before = copy.deepcopy(nodes)
        with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
            ids = eng.bulk_create_nodes(nodes, graph=graph)
        assert "falling back" not in caplog.text, "the server loader failed and the batch path ran instead"
        assert ids == [f"{u}z", f"{u}y", f"{u}x"]
        assert nodes == before, f"the caller's nodes changed: {nodes!r}, given {before!r}"
        # the id property is still stored (#12, R089): it reads back as p_id (R207), and a match on it finds the node
        for nid, name in ((f"{u}z", f"{u}z"), (f"{u}x", None)):
            got = eng.get_node(nid, graph=graph)
            assert got["p_id"] == nid and got.get("name") == name, got
            if graph is None:
                assert _name_by_id(eng, nid) == [name]
    finally:
        conn.close()
