"""Ledger head-lock timeouts and the chunked large-result path (Graph.KG.Ledger).

A Native API lock is invisible to ObjectScript's Lock command, so the head lock is held by
an ObjectScript terminal process in USER for longer than Ledger.LOCKTIMEOUT (30 s). Each
lock test therefore takes about 35 s.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import LedgerLockTimeoutError
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

HOLD_SECONDS = 38


class HeldHeadLock:
    def __init__(self, seconds=HOLD_SECONDS):
        container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
        self.proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "iris", "session", "IRIS", "-U", "USER"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        self.proc.stdin.write(f'lock +^IVG.Ledger("head")\nhang {seconds}\nhalt\n')
        self.proc.stdin.flush()
        self.until = time.time() + seconds
        time.sleep(3)

    def release(self):
        self.proc.terminate()
        self.proc.wait(timeout=30)
        time.sleep(max(0.0, self.until - time.time()) + 1)


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
