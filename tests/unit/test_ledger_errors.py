"""Spec 213 — unit tests for iris_vector_graph.ledger.errors (no IRIS required).

T007 / T031 / T037: error hierarchy, attributes, and CommitError → exception mapping.
"""

import os

import pytest

from iris_vector_graph.ledger import errors as E
from iris_vector_graph.sdk import IVGError

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


ALL_CLASSES = [
    "LedgerNotEnabledError",
    "LedgerDisabledError",
    "LedgerStrictModeError",
    "StaleHeadError",
    "UnknownRevisionError",
    "IdempotencyConflictError",
    "ChangesetOperationError",
    "ChangesetTooLargeError",
    "EmptyChangesetError",
    "ReconstructionTooLargeError",
    "LedgerLockTimeoutError",
    "LedgerTransactionOpenError",
    "LedgerInconsistencyError",
]


class TestHierarchy:
    @pytest.mark.parametrize("name", ALL_CLASSES)
    def test_class_exists_and_subclasses_ledger_error(self, name):
        cls = getattr(E, name)
        assert issubclass(cls, E.LedgerError)
        assert issubclass(cls, IVGError)

    def test_stale_head_carries_current_head(self):
        e = E.StaleHeadError("stale", current_head="a" * 32)
        assert e.current_head == "a" * 32
        assert isinstance(e, E.LedgerError)

    def test_operation_error_carries_index_and_reason(self):
        e = E.ChangesetOperationError(op_index=4, reason="node_not_found: x")
        assert e.op_index == 4
        assert e.reason == "node_not_found: x"
        assert "4" in str(e) and "node_not_found" in str(e)

    def test_reconstruction_too_large_carries_bound_and_estimate(self):
        e = E.ReconstructionTooLargeError(bound=250_000, estimate=300_001)
        assert e.bound == 250_000 and e.estimate == 300_001

    def test_too_large_carries_limit(self):
        e = E.ChangesetTooLargeError(limit=50_000)
        assert e.limit == 50_000


class TestFromCommitError:
    @pytest.mark.parametrize(
        "code,cls",
        [
            ("not_enabled", E.LedgerNotEnabledError),
            ("disabled", E.LedgerDisabledError),
            ("empty", E.EmptyChangesetError),
            ("too_large", E.ChangesetTooLargeError),
            ("stale_head", E.StaleHeadError),
            ("unknown_head", E.UnknownRevisionError),
            ("idempotency_conflict", E.IdempotencyConflictError),
            ("failed_op", E.ChangesetOperationError),
            ("lock_timeout", E.LedgerLockTimeoutError),
            ("transaction_open", E.LedgerTransactionOpenError),
            ("inconsistent", E.LedgerInconsistencyError),
        ],
    )
    def test_every_wire_code_maps(self, code, cls):
        payload = {"ok": False, "error": code, "reason": "r", "op_index": 0, "head": None}
        exc = E.from_commit_error(payload)
        assert isinstance(exc, cls)

    def test_unknown_code_maps_to_base(self):
        exc = E.from_commit_error({"ok": False, "error": "???", "reason": "x"})
        assert type(exc) is E.LedgerError


class TestOperationError:
    def test_failed_op_maps_index(self):
        exc = E.from_commit_error(
            {"ok": False, "error": "failed_op", "op_index": 4, "reason": "boom"}
        )
        assert isinstance(exc, E.ChangesetOperationError)
        assert exc.op_index == 4 and exc.reason == "boom"

    def test_transaction_open(self):
        exc = E.from_commit_error({"ok": False, "error": "transaction_open", "reason": "tlevel=1"})
        assert isinstance(exc, E.LedgerTransactionOpenError)

    def test_too_large_reason_carries_limit(self):
        exc = E.from_commit_error(
            {"ok": False, "error": "too_large", "reason": "limit 5", "limit": 5}
        )
        assert isinstance(exc, E.ChangesetTooLargeError) and exc.limit == 5


class TestHeadErrors:
    def test_stale_head_payload(self):
        exc = E.from_commit_error(
            {"ok": False, "error": "stale_head", "reason": "s", "head": "b" * 32}
        )
        assert isinstance(exc, E.StaleHeadError) and exc.current_head == "b" * 32

    def test_unknown_head(self):
        exc = E.from_commit_error({"ok": False, "error": "unknown_head", "reason": "u"})
        assert isinstance(exc, E.UnknownRevisionError)

    def test_lock_timeout(self):
        exc = E.from_commit_error({"ok": False, "error": "lock_timeout", "reason": "l"})
        assert isinstance(exc, E.LedgerLockTimeoutError)
