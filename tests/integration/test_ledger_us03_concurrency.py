"""Spec 213 US3 — concurrent writers with the same expected_head (live ivg-iris-enterprise).

T042: (1) two-process race; (2) eight processes × fifty commits with retry; (3) writer without
expected_head; (4) unknown expected_head. Workers are separate OS processes with their own
DB-API connections because the engine is declared single-threaded.
"""

import multiprocessing as mp
import os
import socket

import pytest

from iris_vector_graph.ledger import Changeset, StaleHeadError, UnknownRevisionError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _conn_params(conn):
    host = getattr(conn, "hostname", None)
    port = getattr(conn, "port", None)
    ns = getattr(conn, "namespace", None) or "USER"
    if not host or not port:
        host = socket.gethostbyname("ivg-iris-enterprise.orb.local")
        port = 1972
    return {
        "hostname": host,
        "port": int(port),
        "namespace": ns,
        "username": "_SYSTEM",
        "password": "SYS",
    }


def _worker_engine(params):
    import iris.dbapi as dbapi

    from iris_vector_graph.engine import IRISGraphEngine

    conn = dbapi.connect(**params)
    return conn, IRISGraphEngine(conn, embedding_dimension=4)


def _race_worker(params, idx, barrier, out):
    conn, eng = _worker_engine(params)
    try:
        head = eng.ledger.head()
        cs = Changeset(actor=f"agent:{idx}", actor_type="agent", expected_head=head.revision_id)
        cs.create_node(f"race-{idx}")
        barrier.wait(timeout=60)
        try:
            res = eng.ledger.commit(cs)
            out.put(("ok", idx, res.revision.revision_id, res.revision.parent_id))
        except StaleHeadError as e:
            out.put(("stale", idx, e.current_head, head.revision_id))
    finally:
        conn.close()


def _load_worker(params, idx, n_commits, out):
    conn, eng = _worker_engine(params)
    ok = stale = 0
    try:
        for i in range(n_commits):
            while True:
                head = eng.ledger.head()
                cs = Changeset(
                    actor=f"agent:{idx}", actor_type="agent", expected_head=head.revision_id
                )
                cs.create_node(f"w{idx}-{i}")
                try:
                    eng.ledger.commit(cs)
                    ok += 1
                    break
                except StaleHeadError:
                    stale += 1
        out.put((idx, ok, stale))
    finally:
        conn.close()


def _free_writer(params, out):
    conn, eng = _worker_engine(params)
    try:
        cs = Changeset(actor="agent:free", actor_type="agent")  # no expected_head
        cs.create_node("free-node")
        res = eng.ledger.commit(cs)
        out.put((res.revision.revision_id, res.revision.parent_id, res.revision.seq))
    finally:
        conn.close()


def _revisions(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT seq, revision_id, parent_id FROM Graph_KG.ledger_revisions ORDER BY seq"
        )
        return cur.fetchall()
    finally:
        cur.close()


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


class TestUS3Concurrency:
    def test_two_writers_same_expected_head_exactly_one_wins(self, engine):
        params = _conn_params(engine.conn)
        ctx = mp.get_context("spawn")
        barrier = ctx.Barrier(2)
        out = ctx.Queue()
        procs = [ctx.Process(target=_race_worker, args=(params, i, barrier, out)) for i in range(2)]
        for p in procs:
            p.start()
        results = [out.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(timeout=30)
        oks = [r for r in results if r[0] == "ok"]
        stales = [r for r in results if r[0] == "stale"]
        assert len(oks) == 1 and len(stales) == 1, results
        winner_id = oks[0][2]
        assert stales[0][2] == winner_id  # StaleHeadError.current_head reports the winner
        assert oks[0][3] == stales[0][3]  # both started from the same head
        nodes = canonical_tables(engine.conn)["nodes"]
        assert sum(1 for n in nodes if n.startswith("race-")) == 1
        assert engine.ledger.head().revision_id == winner_id

    @pytest.mark.slow
    def test_eight_writers_fifty_commits_contiguous_single_parent_history(self, engine):
        params = _conn_params(engine.conn)
        ctx = mp.get_context("spawn")
        out = ctx.Queue()
        procs = [ctx.Process(target=_load_worker, args=(params, i, 50, out)) for i in range(8)]
        for p in procs:
            p.start()
        results = [out.get(timeout=900) for _ in procs]
        for p in procs:
            p.join(timeout=60)
        assert sum(r[1] for r in results) == 400
        revs = _revisions(engine.conn)
        assert [r[0] for r in revs] == list(range(1, 402))  # genesis + 400
        for prev, cur in zip(revs, revs[1:]):
            assert cur[2] == prev[1]
        nodes = canonical_tables(engine.conn)["nodes"]
        assert sum(1 for n in nodes if n.startswith("w")) == 400
        assert engine.ledger.head().seq == 401

    def test_writer_without_expected_head_serializes_after_in_flight(self, engine):
        params = _conn_params(engine.conn)
        ctx = mp.get_context("spawn")
        out_load = ctx.Queue()
        out_free = ctx.Queue()
        loaders = [
            ctx.Process(target=_load_worker, args=(params, i, 10, out_load)) for i in range(3)
        ]
        free = ctx.Process(target=_free_writer, args=(params, out_free))
        for p in loaders:
            p.start()
        free.start()
        free_id, free_parent, free_seq = out_free.get(timeout=300)
        for _ in loaders:
            out_load.get(timeout=300)
        for p in loaders + [free]:
            p.join(timeout=60)
        revs = {r[0]: r for r in _revisions(engine.conn)}
        assert revs[free_seq][1] == free_id
        assert revs[free_seq][2] == revs[free_seq - 1][1]  # parent is the preceding revision
        assert len(revs) == 1 + 30 + 1

    def test_unknown_expected_head_is_distinct_error(self, engine):
        cs = Changeset(actor="a", actor_type="human", expected_head="f" * 32)
        cs.create_node("x")
        with pytest.raises(UnknownRevisionError):
            engine.ledger.commit(cs)
        assert "x" not in canonical_tables(engine.conn)["nodes"]
