"""Exception hierarchy for the revision ledger (spec 213, data-model.md "Error kinds").

Every ledger error descends from :class:`LedgerError`, which descends from
:class:`iris_vector_graph.sdk.IVGError`, so callers can catch the package root.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..sdk import IVGError


class LedgerError(IVGError):
    """Base class for all revision-ledger errors."""


class LedgerNotEnabledError(LedgerError):
    """The operation requires an enabled ledger and none exists."""


class LedgerDisabledError(LedgerError):
    """The ledger exists but is disabled; commits are rejected (FR-001a)."""


class LedgerStrictModeError(LedgerError):
    """A non-ledger structural mutation was attempted while strict mode is on (FR-042)."""


class StaleHeadError(LedgerError):
    """``expected_head`` did not match the head at serialization (FR-015)."""

    def __init__(self, message: str = "stale head", current_head: Optional[str] = None):
        self.current_head = current_head
        super().__init__(f"{message} (current head: {current_head})")


class UnknownRevisionError(LedgerError):
    """A revision identifier does not exist in this ledger (FR-016, FR-026)."""


class IdempotencyConflictError(LedgerError):
    """Idempotency key reused with a different payload fingerprint (FR-020)."""


class ChangesetOperationError(LedgerError):
    """One operation in the changeset failed; nothing was applied (FR-008)."""

    def __init__(self, op_index: int, reason: str = ""):
        self.op_index = op_index
        self.reason = reason
        super().__init__(f"changeset operation {op_index} failed: {reason}")


class ChangesetTooLargeError(LedgerError):
    """Changeset exceeds the ledger's maximum operation count (FR-013)."""

    def __init__(self, limit: Optional[int] = None, message: str = ""):
        self.limit = limit
        super().__init__(message or f"changeset exceeds max_ops={limit}")


class EmptyChangesetError(LedgerError):
    """A changeset with zero operations was submitted (FR-013)."""


class ReconstructionTooLargeError(LedgerError):
    """Reconstruction exceeds the configured entity bound (FR-036)."""

    def __init__(self, bound: int, estimate: int):
        self.bound = bound
        self.estimate = estimate
        super().__init__(
            f"reconstruction of ~{estimate} entities exceeds bound {bound}; request stream=True"
        )


class LedgerLockTimeoutError(LedgerError):
    """Could not acquire the head lock within the timeout; retryable (R5)."""


class LedgerTransactionOpenError(LedgerError):
    """A transaction was already open in the server process; commit refused (FR-047)."""


class LedgerInconsistencyError(LedgerError):
    """The ledger's own structures disagree with each other."""


class NodeNotFoundError(LedgerError):
    """A relationship operation references a node that does not exist in the graph.

    Attributes:
        missing_node: The node ID that was not found.
    """

    def __init__(self, message: str = "", *, missing_node: str = ""):
        self.missing_node = missing_node
        if not message:
            message = (
                f"create_relationship failed: node '{missing_node}' does not exist. "
                f"Add upsert_node('{missing_node}') to the changeset, or set "
                f"auto_stub_missing_nodes=True on the Changeset."
            )
        super().__init__(message)


_WIRE_MAP = {
    "not_enabled": LedgerNotEnabledError,
    "disabled": LedgerDisabledError,
    "empty": EmptyChangesetError,
    "too_large": ChangesetTooLargeError,
    "stale_head": StaleHeadError,
    "unknown_head": UnknownRevisionError,
    "idempotency_conflict": IdempotencyConflictError,
    "failed_op": ChangesetOperationError,
    "lock_timeout": LedgerLockTimeoutError,
    "transaction_open": LedgerTransactionOpenError,
    "inconsistent": LedgerInconsistencyError,
}


def from_commit_error(payload: Dict[str, Any]) -> LedgerError:
    """Map a ``CommitError`` wire object (contracts/changeset.schema.json) to an exception."""
    code = str(payload.get("error", ""))
    reason = str(payload.get("reason", "") or "")
    cls = _WIRE_MAP.get(code)
    if cls is None:
        return LedgerError(f"{code or 'unknown'}: {reason}")
    if cls is StaleHeadError:
        return StaleHeadError(reason or "stale head", current_head=payload.get("head"))
    if cls is ChangesetOperationError:
        idx = payload.get("op_index")
        return ChangesetOperationError(int(idx) if idx is not None else -1, reason)
    if cls is ChangesetTooLargeError:
        limit = payload.get("limit")
        return ChangesetTooLargeError(int(limit) if limit is not None else None, reason)
    return cls(reason or code)


__all__ = [
    "LedgerError",
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
    "NodeNotFoundError",
    "from_commit_error",
]
