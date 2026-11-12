"""Ledger head-lock timeouts and the chunked large-result path (Graph.KG.Ledger).

A Native API lock is invisible to ObjectScript's Lock command, so the head lock is held by
an ObjectScript process (tests/integration/_tcp.py, over TCP) in the run's namespace (IVG_NAMESPACE; it was always USER,
so in a scratch namespace the lock guarded nothing, 4.6.0 R129) for longer than
Ledger.LOCKTIMEOUT (30 s). Each lock test therefore takes about 35 s.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import LedgerLockTimeoutError
from tests.conftest import TEST_NAMESPACE
from tests.integration import _tcp
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

HOLD_SECONDS = 38


def HeldHeadLock(seconds=HOLD_SECONDS, namespace=TEST_NAMESPACE):
    return _tcp.HeldLock('^IVG.Ledger("head")', namespace, seconds)


@pytest.fixture
def engine(iris_connection, ledger_reset):
    return make_engine(iris_connection)


def test_commit_gives_up_when_another_process_holds_the_head_lock(engine):
    engine.ledger.enable()
    held = HeldHeadLock()
    try:
        cs = Changeset(actor="t", actor_type="human")
        cs.create_node("locked-out")
        with pytest.raises(LedgerLockTimeoutError):
            engine.ledger.commit(cs)
    finally:
        held.release()
    # once the holder is gone the same changeset commits
    cs = Changeset(actor="t", actor_type="human")
    cs.create_node("locked-out")
    assert engine.ledger.commit(cs).revision.seq == 2


def test_enable_gives_up_when_another_process_holds_the_head_lock(engine):
    held = HeldHeadLock()
    try:
        with pytest.raises(LedgerLockTimeoutError):
            engine.ledger.enable()
    finally:
        held.release()
    assert engine.ledger.enable().seq == 1


def test_large_results_are_staged_in_chunks_and_read_back_whole(engine):
    obj = engine._iris_obj()
    big = "x" * 2_500_000
    head = str(obj.classMethodValue("Graph.KG.Ledger", "LargeOut", big))
    prefix, tag, n = head.split(":")
    assert prefix == "CHUNKED" and int(n) == 3
    parts = [str(obj.classMethodValue("Graph.KG.Ledger", "ReadLargeOutChunk", tag, i)) for i in range(1, 4)]
    assert [len(p) for p in parts] == [1_000_000, 1_000_000, 500_000]
    assert "".join(parts) == big
    assert str(obj.classMethodValue("Graph.KG.Ledger", "LargeOut", "small")) == "small"


def test_a_changeset_larger_than_one_chunk_is_staged_and_committed(engine, iris_connection):
    from iris_vector_graph.ledger import Changeset
    from tests.integration._ledger_helpers import canonical_tables

    engine.ledger.enable()
    cs = Changeset(actor="t", actor_type="human")
    blobs = {f"blob{i:02d}": chr(ord("a") + i % 26) * 30_000 for i in range(40)}  # 1.2 MB, over CHUNK_CHARS
    cs.create_node("staged-node", properties=blobs)
    res = engine.ledger.commit(cs)
    assert res.revision.seq == 2
    assert canonical_tables(iris_connection)["nodes"]["staged-node"]["props"] == blobs
