"""A rejected operation always names its index and its reason (FR-008).

Two specs met here and nobody reconciled them.

Spec 213 FR-008: "The commit error for a failed operation MUST identify the
zero-based operation index and the reason." Its `ChangesetOperationError` carries
both, and `from_commit_error` reads them off the wire.

Spec 217 then added `NodeNotFoundError(LedgerError)` — a *sibling*, not a subclass
— for the one failed operation that names a missing node. IRIS still sends the op
index (`Graph.KG.LedgerApply` wraps every rejection as
`Ledger.Err("failed_op", tReason, "", tI)`), and the client still reads the reason
in order to parse the node id out of it. Then it dropped both on the floor.

So the most common authoring mistake — pointing a relationship at a node you
forgot to create — was the one rejection that would not tell you *which* of your
operations was at fault. In a 200-operation changeset that is the whole diagnostic.

These tests pin the reconciliation: `NodeNotFoundError` is a
`ChangesetOperationError`, so FR-008 holds for every rejection with no exceptions,
while spec 217's own contract (a `LedgerError` with `.missing_node` and its
actionable message) is untouched. Widening is safe in the other direction too:
nothing in the package or the suite catches `ChangesetOperationError`, so no
existing handler changes meaning.

Live companion: tests/integration/test_ledger_us02_rollback.py, which asserts the
same thing against a real rejected commit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import (
    ChangesetOperationError,
    LedgerError,
    NodeNotFoundError,
    from_commit_error,
)

WIRE_REASON = "node_not_found: 'does-not-exist'"


# ---------------------------------------------------------------------------
# The hierarchy
# ---------------------------------------------------------------------------


def test_a_missing_node_is_a_failed_operation():
    """FR-008 has no exceptions: every rejected operation reports its index."""
    assert issubclass(NodeNotFoundError, ChangesetOperationError)


def test_it_is_still_a_ledger_error():
    """Spec 217 FR-001 named `LedgerError` as the base. That must keep holding."""
    assert issubclass(NodeNotFoundError, LedgerError)


# ---------------------------------------------------------------------------
# What the error carries
# ---------------------------------------------------------------------------


def test_it_carries_the_operation_index():
    err = NodeNotFoundError(missing_node="b", op_index=4, reason=WIRE_REASON)

    assert err.op_index == 4


def test_it_carries_the_reason_the_server_sent():
    err = NodeNotFoundError(missing_node="b", op_index=4, reason=WIRE_REASON)

    assert err.reason == WIRE_REASON


def test_it_keeps_the_missing_node():
    err = NodeNotFoundError(missing_node="b", op_index=4, reason=WIRE_REASON)

    assert err.missing_node == "b"


def test_the_message_still_tells_the_author_what_to_do():
    """Inheriting a base's `__init__` would replace this with "operation 4 failed"."""
    err = NodeNotFoundError(missing_node="b", op_index=4, reason=WIRE_REASON)

    text = str(err)
    assert "upsert_node('b')" in text, (
        "the actionable part of spec 217's message was lost to the base class"
    )
    assert "auto_stub_missing_nodes=True" in text


def test_an_explicit_message_still_wins():
    err = NodeNotFoundError("custom", missing_node="b")

    assert str(err) == "custom"


def test_it_still_constructs_with_nothing_but_a_missing_node():
    """The spec 217 call shape. Absent an index, -1 means "the server did not say"."""
    err = NodeNotFoundError(missing_node="b")

    assert err.missing_node == "b"
    assert err.op_index == -1
    assert err.reason == ""


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


def test_the_wire_mapper_threads_the_index_through():
    err = from_commit_error(
        {"error": "failed_op", "reason": WIRE_REASON, "op_index": 4}
    )

    assert isinstance(err, NodeNotFoundError)
    assert err.op_index == 4
    assert err.missing_node == "does-not-exist"


def test_an_ordinary_failed_operation_is_not_a_missing_node():
    """Only the `node_not_found:` reason may narrow the type."""
    err = from_commit_error(
        {"error": "failed_op", "reason": "node_exists: 'a'", "op_index": 2}
    )

    assert type(err) is ChangesetOperationError
    assert err.op_index == 2


def _ledger_raising(wire: str):
    eng = MagicMock()
    eng.conn = MagicMock()
    eng.namespace = "USER"
    eng._schema_prefix = "Graph_KG"

    from iris_vector_graph.ledger.client import GraphLedger

    gl = GraphLedger(eng)
    cs = Changeset(actor="test", actor_type="test")
    cs.create_node("a")
    with patch.object(gl, "_call", return_value=wire):
        with pytest.raises(NodeNotFoundError) as ei:
            gl.commit(cs)
    return ei.value


def test_commit_reports_the_index_the_server_sent():
    """The client already parsed the reason to get the node id. Keep the rest."""
    err = _ledger_raising(
        '{"ok":false,"error":"failed_op","op_index":4,'
        '"reason":"node_not_found: \'does-not-exist\'"}'
    )

    assert err.op_index == 4
    assert err.missing_node == "does-not-exist"
    assert "does-not-exist" in err.reason


def test_commit_survives_a_server_that_omits_the_index():
    err = _ledger_raising(
        '{"ok":false,"error":"failed_op",' '"reason":"node_not_found: \'nope\'"}'
    )

    assert err.op_index == -1
    assert err.missing_node == "nope"
