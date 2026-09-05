"""Spec 213 SC-013 — observability: log per outcome, status counters, stats view, hook (live). T104."""

import logging
import os

import pytest

from iris_vector_graph.ledger import (
    Changeset,
    ChangesetOperationError,
    ChangesetTooLargeError,
    IdempotencyConflictError,
    LedgerDisabledError,
    LedgerStrictModeError,
    StaleHeadError,
)
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable(max_ops=3)
    return eng


def _stats_row(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT commits_ok, replays_idem, rej_stale_head, rej_unknown_head, rej_idem_conflict, rej_failed_op, "
            "rej_strict_block, rej_size_limit, rej_disabled, head_seq, state, strict FROM Graph_KG.ledger_stats"
        )
        row = tuple(cur.fetchall()[0])
    finally:
        cur.close()
    keys = [
        "commits_ok",
        "replays_idem",
        "rej_stale_head",
        "rej_unknown_head",
        "rej_idem_conflict",
        "rej_failed_op",
        "rej_strict_block",
        "rej_size_limit",
        "rej_disabled",
        "head_seq",
        "state",
        "strict",
    ]
    return dict(zip(keys, row))


def _cs(**kw):
    cs = Changeset(actor="obs", actor_type="agent", **kw)
    cs.create_node("obs-node")
    return cs


class TestObservability:
    def test_every_outcome_is_logged_counted_and_hooked(self, engine, caplog):
        events = []
        engine.ledger.register_metrics_hook(events.append)
        head0 = engine.ledger.head().revision_id
        with caplog.at_level(logging.INFO, logger="iris_vector_graph.ledger"):
            engine.ledger.commit(_cs(idempotency_key="k"))  # success
            engine.ledger.commit(_cs(idempotency_key="k"))  # replayed
            with pytest.raises(IdempotencyConflictError):
                other = Changeset(actor="obs", actor_type="agent", idempotency_key="k")
                other.create_node("zzz")
                engine.ledger.commit(other)  # idempotency_conflict
            with pytest.raises(StaleHeadError):
                engine.ledger.commit(
                    Changeset(actor="obs", actor_type="agent", expected_head=head0).create_node("s")
                )  # stale_head
        bad = Changeset(actor="obs", actor_type="agent")
        bad.create_relationship("obs-node", "R", "nope")
        with caplog.at_level(logging.INFO, logger="iris_vector_graph.ledger"):
            with pytest.raises(ChangesetOperationError):
                engine.ledger.commit(bad)
            big = Changeset(actor="obs", actor_type="agent")
            for i in range(4):
                big.create_node(f"big{i}")
            with pytest.raises(ChangesetTooLargeError):
                engine.ledger.commit(big)  # too_large
        engine.ledger.set_strict(True)
        with pytest.raises(LedgerStrictModeError):
            engine.create_node("legacy")  # strict_block (guard, not a commit)
        engine.ledger.set_strict(False)
        engine.ledger.disable()
        with pytest.raises(LedgerDisabledError):
            engine.ledger.commit(_cs())  # disabled

        outcomes = [r.ledger_event["outcome"] for r in caplog.records if hasattr(r, "ledger_event")]
        for expected in (
            "success",
            "replayed",
            "idempotency_conflict",
            "stale_head",
            "failed_op",
            "too_large",
        ):
            assert expected in outcomes, outcomes
        success = [
            r.ledger_event
            for r in caplog.records
            if hasattr(r, "ledger_event") and r.ledger_event["outcome"] == "success"
        ][0]
        assert (
            success["revision_id"]
            and success["actor"] == "obs"
            and success["op_count"] == 1
            and success["duration_ms"] > 0
        )
        hook_outcomes = [e["outcome"] for e in events]
        for expected in (
            "success",
            "replayed",
            "idempotency_conflict",
            "stale_head",
            "failed_op",
            "too_large",
            "disabled",
        ):
            assert expected in hook_outcomes

        row = _stats_row(engine.conn)
        assert row["commits_ok"] == 2  # genesis + one
        assert (
            row["replays_idem"] == 1
            and row["rej_idem_conflict"] == 1
            and row["rej_stale_head"] == 1
        )
        assert (
            row["rej_failed_op"] >= 1
            and row["rej_size_limit"] == 1
            and row["rej_strict_block"] == 1
            and row["rej_disabled"] == 1
        )
        assert row["state"] == "disabled" and row["head_seq"] == 2

        counters = engine.ledger.stats()
        assert (
            counters.commits_ok == row["commits_ok"]
            and counters.replays_idem == row["replays_idem"]
        )
        assert counters.rejections["rej_stale_head"] == row["rej_stale_head"]
        status = engine.status()
        assert (
            status.ledger is not None
            and status.ledger.state == "disabled"
            and status.ledger.head_seq == 2
        )
        assert (
            status.ledger.commits_ok == row["commits_ok"]
            and status.ledger.rejections["rej_size_limit"] == 1
        )
        assert "ledger" in status.report().lower()


class TestStatusWithoutLedger:
    def test_status_shows_never_enabled(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        st = eng.status()
        assert (
            st.ledger is not None and st.ledger.state == "never-enabled" and st.ledger.head_seq == 0
        )
