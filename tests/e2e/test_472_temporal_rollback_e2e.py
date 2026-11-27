"""4.7.2: a rolled-back temporal write leaves nothing in the temporal index.

`create_edge_temporal` and `bulk_create_edges_temporal` write `^KG("tout"/"tin"/"bucket"/"tagg"/"edgeprop"/"tlast")`
and the structural shadow from the server process that runs `Graph.KG.TemporalIndex`. A caller's transaction (SQL
`START TRANSACTION`, or the driver's `setAutoCommit(False)`) that rolled back used to leave those globals behind, and
a temporal query then read an edge no committed write made.

Each case reads the globals and the temporal queries from a second connection once the caller's transaction is over.

    IVG_R472_NS=IVG470W3 pytest tests/e2e/test_472_temporal_rollback_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R472_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R472_NS"),
]

TXNS = ("sql", "driver")
TS = 1_700_000_000
GRAPHS = (None, "g472")


def _connect(kind: str = "native"):
    """The caller's connection: `iris.connect` (the tests' usual) or `iris.dbapi.connect`, which the servers and
    `IRISGraphEngine.from_connect` make; the Native API object an engine makes on each is its own to answer for."""
    import iris

    if kind == "dbapi":
        return iris.dbapi.connect(hostname="localhost", port=rr.PORT, namespace=NS, username="_SYSTEM", password="SYS")
    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=rr.DIM)


@pytest.fixture
def observer():
    conn = _connect()
    yield conn
    conn.close()


@pytest.fixture(params=("native", "dbapi"))
def caller(request):
    conn = _connect(request.param)
    yield conn
    try:
        conn.rollback()
    finally:
        conn.close()


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


def _gkey(graph):
    return 0 if graph is None else graph


def _globals(obs, graph, ts, s, p, o) -> dict:
    """Every temporal global the write puts at these coordinates, by name: True when it is there."""
    import iris

    n = iris.createIRIS(obs)
    g = _gkey(graph)
    # the bucket width is the class's own parameter: read it from the class so a change there cannot hide a ghost
    unit = n.classMethodValue("Graph.KG.TemporalIndex", "%GetParameter", "TSUNIT")
    width = n.classMethodValue("Graph.KG.TemporalIndex", "%GetParameter", "BUCKETMS" if unit == "ms" else "BUCKET")
    bucket = ts // int(width)
    return {
        "tout": bool(n.isDefined("^KG", "tout", g, ts, s, p, o)),
        "tin": bool(n.isDefined("^KG", "tin", g, ts, o, p, s)),
        "bucket": bool(n.isDefined("^KG", "bucket", g, bucket, s)),
        "tagg": bool(n.isDefined("^KG", "tagg", g, bucket, s, p, "count")),
        "edgeprop": bool(n.isDefined("^KG", "edgeprop", g, ts, s, p, o, "k")),
        "out": bool(n.isDefined("^KG", "out", g, s, p, o)),
    }


def _window(engine, s, p, graph) -> list:
    return engine.get_edges_in_window(s, p, TS - 10, TS + 10, graph=graph)


def _write_one(e, u, graph):
    return e.create_edge_temporal(f"{u}a", "R472", f"{u}b", timestamp=TS, weight=2.5,
                                  attrs={"k": "v"}, graph=graph)


def _write_bulk(e, u, graph):
    return e.bulk_create_edges_temporal(
        [{"s": f"{u}a", "p": "R472", "o": f"{u}b", "ts": TS, "w": 2.5, "attrs": {"k": "v"}}], graph=graph)


WRITES = {"create_edge_temporal": _write_one, "bulk_create_edges_temporal": _write_bulk}


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("graph", GRAPHS)
@pytest.mark.parametrize("write", sorted(WRITES))
def test_a_rolled_back_temporal_write_leaves_no_ghost_edge(caller, observer, txn, graph, write):
    u = f"t472{uuid.uuid4().hex[:8]}"
    engine = _engine(caller)
    _open(caller, txn)
    WRITES[write](engine, u, graph)
    _end(caller, txn, "rollback")

    seen = _globals(observer, graph, TS, f"{u}a", "R472", f"{u}b")
    assert not any(seen.values()), f"after the caller's ROLLBACK these temporal globals are still there: {seen}"
    obs_engine = _engine(observer)
    assert _window(obs_engine, f"{u}a", "R472", graph) == []
    every = obs_engine.get_edges_in_window("", "R472", TS - 10, TS + 10, graph=graph)
    assert [x for x in every if x["s"] == f"{u}a"] == []


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("graph", GRAPHS)
@pytest.mark.parametrize("write", sorted(WRITES))
def test_a_committed_temporal_write_is_all_there(caller, observer, txn, graph, write):
    u = f"t472{uuid.uuid4().hex[:8]}"
    engine = _engine(caller)
    _open(caller, txn)
    WRITES[write](engine, u, graph)
    _end(caller, txn, "commit")

    seen = _globals(observer, graph, TS, f"{u}a", "R472", f"{u}b")
    assert all(seen.values()), f"after the caller's COMMIT these temporal globals are missing: {seen}"
    edges = _window(_engine(observer), f"{u}a", "R472", graph)
    assert [(x["s"], x["p"], x["o"], x["ts"]) for x in edges] == [(f"{u}a", "R472", f"{u}b", TS)]


BUCKET = (TS // 300 * 300, TS // 300 * 300 + 299)


def _counts(obs, graph, u) -> dict:
    """What the bucket's counters and the aggregate reader say about u's R472 edges in TS's bucket."""
    import iris

    n = iris.createIRIS(obs)
    g = _gkey(graph)
    bucket = TS // 300
    e = _engine(obs)
    return {
        "bucket": n.get("^KG", "bucket", g, bucket, f"{u}a") if n.isDefined("^KG", "bucket", g, bucket, f"{u}a") else None,
        "tagg_count": n.get("^KG", "tagg", g, bucket, f"{u}a", "R472", "count")
        if n.isDefined("^KG", "tagg", g, bucket, f"{u}a", "R472", "count") else None,
        "count": e.get_temporal_aggregate(f"{u}a", "R472", "count", *BUCKET, graph=graph),
        "sum": e.get_temporal_aggregate(f"{u}a", "R472", "sum", *BUCKET, graph=graph),
    }


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("graph", GRAPHS)
def test_a_rolled_back_write_into_a_bucket_that_has_an_edge_leaves_its_counters_as_they_were(caller, observer, txn,
                                                                                            graph):
    """The counters are `$Increment`s, which a rollback does not undo: the rolled-back edge stayed counted."""
    u = f"t472{uuid.uuid4().hex[:8]}"
    first = _engine(observer)
    first.create_edge_temporal(f"{u}a", "R472", f"{u}b", timestamp=TS, weight=2.5, graph=graph)
    before = _counts(observer, graph, u)
    assert (before["count"], before["sum"], before["tagg_count"], before["bucket"]) == (1, 2.5, 1, 1)

    engine = _engine(caller)
    _open(caller, txn)
    engine.create_edge_temporal(f"{u}a", "R472", f"{u}c", timestamp=TS + 1, weight=4.0, graph=graph)
    engine.bulk_create_edges_temporal(
        [{"s": f"{u}a", "p": "R472", "o": f"{u}d", "ts": TS + 2, "w": 8.0}], graph=graph)
    _end(caller, txn, "rollback")

    assert _counts(observer, graph, u) == before
    # the counters still count: a committed write after the rollback is the second edge
    first.create_edge_temporal(f"{u}a", "R472", f"{u}e", timestamp=TS + 3, weight=1.0, graph=graph)
    after = _counts(observer, graph, u)
    assert (after["count"], after["sum"], after["tagg_count"], after["bucket"]) == (2, 3.5, 2, 2)


@pytest.mark.parametrize("txn", TXNS)
@pytest.mark.parametrize("graph", GRAPHS)
def test_a_rolled_back_delete_of_a_temporal_edge_leaves_the_edge_and_its_counters(caller, observer, txn, graph):
    u = f"t472{uuid.uuid4().hex[:8]}"
    first = _engine(observer)
    first.create_edge_temporal(f"{u}a", "R472", f"{u}b", timestamp=TS, weight=2.5, graph=graph)
    before = _counts(observer, graph, u)
    assert (before["count"], before["bucket"]) == (1, 1)

    engine = _engine(caller)
    _open(caller, txn)
    assert engine.delete_edge_temporal(f"{u}a", "R472", f"{u}b", TS, graph=graph)
    _end(caller, txn, "rollback")

    seen = _globals(observer, graph, TS, f"{u}a", "R472", f"{u}b")
    assert all(v for k, v in seen.items() if k != "edgeprop"), f"after the rollback of a delete: {seen}"
    assert _counts(observer, graph, u) == before
    assert [x["o"] for x in _window(_engine(observer), f"{u}a", "R472", graph)] == [f"{u}b"]


@pytest.mark.parametrize("graph", GRAPHS)
def test_a_batch_the_server_refuses_part_way_writes_none_of_it(observer, graph):
    """The second edge's source is wider than a node id: its structural row is refused after the first edge's stores
    were written. 4.6.0 to 4.7.1 kept those and said how many; the batch is one transaction now."""
    from iris_vector_graph.errors import GraphWriteError

    u = f"t472{uuid.uuid4().hex[:8]}"
    engine = _engine(observer)
    batch = [{"s": f"{u}a", "p": "R472", "o": f"{u}b", "ts": TS, "w": 2.5},
             {"s": f"{u}x" + "x" * 300, "p": "R472", "o": f"{u}c", "ts": TS + 1, "w": 1.0}]
    with pytest.raises((GraphWriteError, ValueError)) as caught:
        engine.bulk_create_edges_temporal(batch, graph=graph)
    assert f"{u}b" not in str(caught.value)
    seen = _globals(observer, graph, TS, f"{u}a", "R472", f"{u}b")
    assert not any(seen.values()), f"a refused batch left these temporal globals: {seen}"
    assert [x for x in _engine(observer).get_edges_in_window(f"{u}a", "R472", TS - 10, TS + 10, graph=graph)] == []


def _b_waits_for_a(caller, observer, how, expected_count, graph=None):
    """A writer in an open transaction and a second writer of the same source and bucket: the second waits for the
    first to end (the counters are read and written under a lock the transaction holds), and neither's count is lost."""
    import threading
    import time

    u = f"t472{uuid.uuid4().hex[:8]}"
    a_engine = _engine(caller)
    _open(caller, "sql")
    a_engine.create_edge_temporal(f"{u}a", "R472", f"{u}b", timestamp=TS, weight=2.5, graph=graph)

    done = threading.Event()
    errors = []

    def second():
        conn = _connect()
        try:
            _engine(conn).create_edge_temporal(f"{u}a", "R472", f"{u}c", timestamp=TS + 1, weight=4.0, graph=graph)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            conn.close()
            done.set()

    t = threading.Thread(target=second)
    t.start()
    time.sleep(1.5)
    waited = not done.is_set()
    _end(caller, "sql", how)
    t.join(30)
    assert waited, "the second writer did not wait for the open transaction that holds its bucket's counters"
    assert not errors, errors
    got = _counts(observer, graph, u)
    assert got["count"] == expected_count and got["tagg_count"] == expected_count, got
    assert got["bucket"] == expected_count, got
    return got


@pytest.mark.parametrize("graph", GRAPHS)
def test_a_second_writer_of_the_bucket_waits_for_a_transaction_that_commits(caller, observer, graph):
    got = _b_waits_for_a(caller, observer, "commit", 2, graph)
    assert got["sum"] == 6.5


@pytest.mark.parametrize("graph", GRAPHS)
def test_a_second_writer_of_the_bucket_keeps_its_count_when_the_first_rolls_back(caller, observer, graph):
    got = _b_waits_for_a(caller, observer, "rollback", 1, graph)
    assert got["sum"] == 4.0
