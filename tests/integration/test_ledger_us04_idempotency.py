"""Spec 213 US4 — idempotent retry (live ivg-iris-enterprise).

T047: scenarios 1–5 plus FR-019 precedence (a retry carrying a now-stale expected_head
still replays) and the concurrent same-key race.
"""

import multiprocessing as mp
import os

import pytest

from iris_vector_graph.ledger import Changeset, IdempotencyConflictError, StaleHeadError
from tests.integration._ledger_helpers import canonical_tables, make_engine
from tests.integration.test_ledger_us03_concurrency import _conn_params, _worker_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _cs(key=None, **kw):
    cs = Changeset(actor="ingest:etl", actor_type="ingest", idempotency_key=key, **kw)
    cs.create_node("idem-node", labels=["L"], properties={"k": "v"})
    return cs


def _rev_count(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.ledger_revisions")
        return cur.fetchone()[0]
    finally:
        cur.close()


def _same_key_worker(params, key, out):
    conn, eng = _worker_engine(params)
    try:
        res = eng.ledger.commit(_cs(key))
        out.put((res.revision.revision_id, res.replayed))
    finally:
        conn.close()


class TestUS4Idempotency:
    def test_replay_returns_original_outcome_and_applies_nothing(self, engine):
        first = engine.ledger.commit(_cs("K1"))
        assert first.replayed is False
        n_revs, tables = _rev_count(engine.conn), canonical_tables(engine.conn)
        second = engine.ledger.commit(_cs("K1"))
        assert second.replayed is True
        assert second.revision.revision_id == first.revision.revision_id
        assert second.revision.op_count == first.revision.op_count
        assert engine.ledger.head().revision_id == first.revision.revision_id
        assert _rev_count(engine.conn) == n_revs
        assert canonical_tables(engine.conn) == tables

    def test_replay_wins_over_stale_expected_head(self, engine):
        """FR-019 precedence: the retried changeset carries the pre-commit head, now stale."""
        h0 = engine.ledger.head()
        cs = _cs("K-prec", expected_head=h0.revision_id)
        first = engine.ledger.commit(cs)
        again = engine.ledger.commit(_cs("K-prec", expected_head=h0.revision_id))
        assert again.replayed is True and again.revision.revision_id == first.revision.revision_id

    def test_same_key_different_payload_is_conflict(self, engine):
        engine.ledger.commit(_cs("K1"))
        other = Changeset(actor="ingest:etl", actor_type="ingest", idempotency_key="K1")
        other.create_node("different")
        with pytest.raises(IdempotencyConflictError):
            engine.ledger.commit(other)
        assert "different" not in canonical_tables(engine.conn)["nodes"]
        # metadata-only differences are NOT a conflict (Q2 fingerprint scope)
        res = engine.ledger.commit(
            _cs("K1", message="retry with new message", correlation_id="run-2")
        )
        assert res.replayed is True

    def test_key_from_rejected_commit_is_reusable(self, engine):
        h0 = engine.ledger.head()
        engine.ledger.commit(_cs())  # advances head, no key
        stale = Changeset(
            actor="a", actor_type="human", idempotency_key="K2", expected_head=h0.revision_id
        )
        stale.create_node("s1")
        with pytest.raises(StaleHeadError):
            engine.ledger.commit(stale)
        fresh = Changeset(actor="a", actor_type="human", idempotency_key="K2")
        fresh.create_node("s2")
        res = engine.ledger.commit(fresh)
        assert res.replayed is False and res.revision.idempotency_key == "K2"

    def test_concurrent_same_key_same_payload_yields_one_revision(self, engine):
        params = _conn_params(engine.conn)
        ctx = mp.get_context("spawn")
        out = ctx.Queue()
        procs = [ctx.Process(target=_same_key_worker, args=(params, "K3", out)) for _ in range(2)]
        for p in procs:
            p.start()
        results = [out.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(timeout=30)
        ids = {r[0] for r in results}
        assert len(ids) == 1
        assert sorted(r[1] for r in results) == [False, True]
        assert _rev_count(engine.conn) == 2  # genesis + one

    def test_key_present_in_revision_metadata(self, engine):
        res = engine.ledger.commit(_cs("K1"))
        rev = engine.ledger.get_revision(res.revision.revision_id)
        assert rev.info.idempotency_key == "K1"
        assert engine.ledger.head().idempotency_key == "K1"
