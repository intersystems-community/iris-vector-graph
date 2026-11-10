"""4.7.0 row 4: a ledger commit inside a transaction the caller opened is part of it.

Measured on 4.6.5 (IRIS 2026.3, driver 5.4.0), the caller's transaction open (`START TRANSACTION` and an INSERT of its
own) on the engine's connection:

- an engine built on the caller's connection (`IRISGraphEngine(conn)`): `ledger.commit` raised
  `LedgerTransactionOpenError` (Graph.KG.Ledger.Commit refused any `$TLEVEL` above 0, spec 213 FR-047), so a ledger
  write could not be part of the caller's work at all;
- an engine made with `from_connect` (the Native API on a connection of its own, another server process): the commit
  ran outside the caller's transaction and was committed whatever the caller did, so the caller's rollback left the
  revision row and the node it made.

Now the commit nests in the caller's transaction (Graph.KG.Ledger.Commit's TSTART under the caller's, on the caller's
own server process): the caller's rollback leaves no `ledger_revisions` row, no head move and no graph change; the
caller's commit keeps both, and `ledger.verify()` answers equal. FR-047's invariant, a revision exists if and only if
its mutations are visible, still holds: both commit or roll back with the caller. The ledger head lock is held until
the caller's transaction ends (an unlock inside a transaction waits for its end), so no other commit builds on a head
the caller may yet roll back. `post_commit_properties` (a second write, which called `conn.commit()`) is in the
caller's transaction too.

The namespace's ledger is enabled for the module, adopting what is there, and disabled after it if it was not enabled
before; `verify()` compares the whole graph with the ledger, so nothing else may write to this namespace meanwhile.

    IVG_R470_LEDGER_NS=IVG470TXN pytest tests/e2e/test_470_ledger_caller_txn_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_LEDGER_NS", "")
CALLER_TABLE = "SQLUser.ivg470_ledger_caller"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_LEDGER_NS"),
]

KINDS = ("own_connection", "from_connect")


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(kind):
    from iris_vector_graph import IRISGraphEngine

    if kind == "own_connection":
        return IRISGraphEngine(_connect(), embedding_dimension=rr.DIM)
    return IRISGraphEngine.from_connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS", embedding_dimension=rr.DIM)


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
    eng = _engine("own_connection")
    was_enabled = eng.ledger.meta().get("state") == "enabled"
    if not was_enabled:
        eng.ledger.enable()
    try:
        # the ledger is put back as it was even when this fails: left enabled, the next run's precondition fails on
        # whatever other files wrote meanwhile
        report = eng.ledger.verify()
        assert report.equal, (
            f"the namespace's ledger does not match its graph before the test: {report.result}, "
            f"{report.differences[:3]} (an enabled ledger another file wrote around)"
        )
        yield conn
    finally:
        try:
            if not was_enabled:
                eng.ledger.disable()
            cur.execute(f"DROP TABLE {CALLER_TABLE}")
        finally:
            _close(eng)
            conn.close()


def _close(eng):
    for conn in (eng.conn, getattr(eng, "_native_conn", None)):
        if conn is not None and not conn.isClosed():
            conn.close()


@pytest.fixture(params=KINDS)
def engine(request):
    eng = _engine(request.param)
    yield eng
    try:
        if not eng.conn.isClosed():
            eng.conn.rollback()
    finally:
        _close(eng)


def _level(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT $TLEVEL")
    return int(cur.fetchone()[0])


def _count(obs, sql: str, params=()) -> int:
    cur = obs.cursor()
    cur.execute(sql, list(params))
    return int(cur.fetchone()[0])


def _has_caller_row(obs, key: str) -> bool:
    return _count(obs, f"SELECT COUNT(*) FROM {CALLER_TABLE} WHERE k = ?", [key]) == 1


def _revision(obs, revision_id: str) -> bool:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.ledger_revisions WHERE revision_id = ?", [revision_id]) == 1


def _node(obs, nid: str) -> bool:
    return _count(obs, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ''", [nid]) == 1


def _prop(obs, nid: str, key: str):
    cur = obs.cursor()
    cur.execute('SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ? AND graph_id = \'\'', [nid, key])
    row = cur.fetchone()
    return None if row is None else row[0]


def _head(obs) -> str:
    import iris

    seq = iris.createIRIS(obs).get("^IVG.Ledger", "head")
    return str(iris.createIRIS(obs).get("^IVG.Ledger", "idBySeq", seq))


def _uid() -> str:
    return "t470l" + uuid.uuid4().hex[:10]


def _changeset(u, **kw):
    from iris_vector_graph.ledger import Changeset

    cs = Changeset(actor="t470", actor_type="test", **kw)
    cs.create_node(f"{u}n", labels=["T470L"], properties={"v": "1"})
    return cs


def _caller_txn(engine, u):
    cur = engine.conn.cursor()
    cur.execute("START TRANSACTION")
    cur.execute(f"INSERT INTO {CALLER_TABLE} (k) VALUES (?)", [u])


def test_a_caller_rollback_takes_back_the_revision_and_its_writes(observer, engine):
    u = _uid()
    head = _head(observer)
    _caller_txn(engine, u)
    result = engine.ledger.commit(_changeset(u))
    assert _level(engine.conn) == 1, "the ledger commit left the caller's transaction at another level"
    engine.conn.cursor().execute("ROLLBACK")
    assert not _has_caller_row(observer, u), "the caller's row outlived the caller's rollback"
    assert not _revision(observer, result.revision.revision_id), "the revision outlived the caller's rollback"
    assert not _node(observer, f"{u}n"), "the node outlived the caller's rollback"
    assert _head(observer) == head
    assert engine.ledger.verify().equal


def test_b_caller_commit_keeps_both_and_the_ledger_verifies(observer, engine):
    u = _uid()
    _caller_txn(engine, u)
    result = engine.ledger.commit(_changeset(u))
    assert _level(engine.conn) == 1
    engine.conn.cursor().execute("COMMIT")
    assert _has_caller_row(observer, u)
    assert _revision(observer, result.revision.revision_id)
    assert _node(observer, f"{u}n") and _prop(observer, f"{u}n", "v") == "1"
    assert _head(observer) == result.revision.revision_id
    report = engine.ledger.verify()
    assert report.equal, (report.result, report.differences[:5])


def test_d_without_a_caller_transaction_the_commit_is_committed(observer, engine):
    u = _uid()
    result = engine.ledger.commit(_changeset(u))
    assert _level(engine.conn) == 0
    engine.conn.close()  # closed without a commit: anything left uncommitted is rolled back here
    assert _revision(observer, result.revision.revision_id) and _node(observer, f"{u}n")


@pytest.mark.parametrize("how", ("rollback", "commit"))
def test_post_commit_properties_follow_the_caller(observer, engine, how):
    """`post_commit_properties` are written after the revision, as plain SQL; they called `conn.commit()`."""
    u = _uid()
    _caller_txn(engine, u)
    result = engine.ledger.commit(_changeset(u, post_commit_properties={f"{u}n": {"tag": "after"}}))
    assert result.post_commit_applied and result.post_commit_error is None, result.post_commit_error
    assert _level(engine.conn) == 1, "post_commit_properties ended the caller's transaction"
    engine.conn.cursor().execute(how.upper())
    kept = how == "commit"
    assert _has_caller_row(observer, u) is kept
    assert _revision(observer, result.revision.revision_id) is kept
    assert _prop(observer, f"{u}n", "tag") == ("after" if kept else None)
    # post_commit_properties are written outside any revision (Approach A), so verify() reads the kept one as an
    # unrecorded write; taken back here so the module's later verify() calls compare the ledger's own writes
    observer.cursor().execute('DELETE FROM Graph_KG.rdf_props WHERE s = ? AND "key" = ? AND graph_id = \'\'',
                              [f"{u}n", "tag"])


def test_the_head_lock_is_held_until_the_callers_transaction_ends(observer):
    """Another process cannot take the head lock between the nested commit and the caller's end of it."""
    import iris

    eng = _engine("own_connection")
    try:
        u = _uid()
        _caller_txn(eng, u)
        eng.ledger.commit(_changeset(u))
        other = iris.createIRIS(observer)
        # the Native API answers a lock it could not take within the timeout with <TIMEOUT>
        with pytest.raises(RuntimeError, match="TIMEOUT"):
            other.lock("", 0, "^IVG.Ledger", "head")
        eng.conn.cursor().execute("ROLLBACK")
        other.lock("", 0, "^IVG.Ledger", "head")  # raises <TIMEOUT> if the lock outlived the caller's transaction
        other.unlock("", "^IVG.Ledger", "head")
    finally:
        _close(eng)


def _seq(obs, revision_id: str) -> int:
    return _count(obs, "SELECT seq FROM Graph_KG.ledger_revisions WHERE revision_id = ?", [revision_id])


def test_a_rolled_back_revision_leaves_no_gap_in_the_chain(observer, engine):
    """seq and the head are global sets inside the transaction (not $Increment), so a rolled-back revision takes them
    back: A, then a revision the caller rolls back, then B, gives B the seq after A's and A as its parent."""
    from iris_vector_graph.ledger import Changeset

    u = _uid()
    a = engine.ledger.commit(_changeset(u + "a"))
    _caller_txn(engine, u)
    rolled = Changeset(actor="t470", actor_type="test")
    rolled.create_node(f"{u}r")
    rolled.create_relationship(f"{u}an", "R470L", f"{u}r")
    r = engine.ledger.commit(rolled)
    assert r.revision.parent_id == a.revision.revision_id
    engine.conn.cursor().execute("ROLLBACK")
    assert not _revision(observer, r.revision.revision_id)
    b_cs = Changeset(actor="t470", actor_type="test")
    b_cs.create_node(f"{u}b")
    b_cs.create_relationship(f"{u}an", "R470L", f"{u}b")
    b = engine.ledger.commit(b_cs)
    assert b.revision.parent_id == a.revision.revision_id
    assert _seq(observer, b.revision.revision_id) == _seq(observer, a.revision.revision_id) + 1
    assert not _node(observer, f"{u}r")
    report = engine.ledger.verify()
    assert report.equal, (report.result, report.differences[:5])
