"""Spec 213 FR-047 — fail-closed atomicity when a transaction is already open (T037).

Two layers:
* `TestTransactionOpenGuard` — always runs: opens a transaction on the same server process
  through the Native API (`tStart`), then asserts `commit()` joins it (4.7.0; it was refused before any apply) and
  `enable()` is still refused.
* `TestEmbeddedMode` — runs only inside an IRIS embedded-Python runtime (the same skip
  pattern as tests/unit/test_embedded.py::TestEmbeddedLive); outside IRIS it skips.
"""

import os

import pytest

from iris_vector_graph.ledger import Changeset, LedgerTransactionOpenError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


class TestTransactionOpenGuard:
    def test_commit_inside_an_open_transaction_joins_it_and_its_rollback_takes_it_back(self, engine):
        """4.7.0 (row 4): the commit nests in the transaction open on its server process, so that transaction's
        rollback takes back the revision and its writes. It used to be refused (FR-047); the invariant FR-047 keeps,
        a revision exists if and only if its mutations are visible, holds as both go together.
        tests/e2e/test_470_ledger_caller_txn_e2e.py has the caller's-connection cases."""
        io = engine._iris_obj()
        before = canonical_tables(engine.conn)
        head = engine.ledger.head().revision_id
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("txn-node")
        io.tStart()
        try:
            result = engine.ledger.commit(cs)
            assert result.revision.parent_id == head
            assert "txn-node" in canonical_tables(engine.conn)["nodes"]  # read uncommitted, from the session conn
        finally:
            io.tRollback()
        assert canonical_tables(engine.conn) == before
        assert engine.ledger.head().revision_id == head

    def test_enable_refused_when_transaction_open(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        io = eng._iris_obj()
        io.tStart()
        try:
            with pytest.raises(LedgerTransactionOpenError):
                eng.ledger.enable()
        finally:
            io.tRollback()
        assert eng.ledger.meta()["state"] == "never-enabled"


class TestEmbeddedMode:
    def test_failed_changeset_leaves_state_unchanged_in_embedded_mode(
        self, iris_connection, ledger_reset
    ):
        import sys

        if "irissys" not in sys.executable and os.environ.get("IVG_EMBEDDED", "0") != "1":
            pytest.skip(
                "embedded-Python mode only runs inside IRIS (irispython); set IVG_EMBEDDED=1 to force"
            )
        try:
            from iris_vector_graph.embedded import EmbeddedConnection

            econn = EmbeddedConnection()
            econn.cursor().execute("SELECT 1")
        except Exception as e:  # pragma: no cover - only inside IRIS
            pytest.skip(f"EmbeddedConnection not available outside IRIS: {e}")
        eng = make_engine(econn)
        eng.ledger.enable()
        before = canonical_tables(econn)
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("e1")
        cs.create_relationship("e1", "R", "missing")
        from iris_vector_graph.ledger import ChangesetOperationError

        with pytest.raises(ChangesetOperationError):
            eng.ledger.commit(cs)
        assert canonical_tables(econn) == before
