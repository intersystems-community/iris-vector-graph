"""Immutable graph revision ledger and atomic changesets (spec 213).

Usage::

    from iris_vector_graph.ledger import Changeset
    engine.ledger.enable()
    cs = Changeset(actor="ingest:etl", actor_type="ingest")
    cs.create_node("pump-7", labels=["Equipment"])
    result = engine.ledger.commit(cs)
"""

from .changeset import ACTOR_TYPES, RESERVED_PROPERTIES, Changeset, OpRef, canonical_dumps
from .client import (
    CommitResult,
    GraphLedger,
    HistoryPage,
    LedgerCounters,
    RedactionReceipt,
    Revision,
    RevisionInfo,
)
from .errors import (
    ChangesetOperationError,
    ChangesetTooLargeError,
    EmptyChangesetError,
    IdempotencyConflictError,
    IdempotencyRedactedError,
    InvalidRedactionTargetError,
    LedgerDisabledError,
    LedgerError,
    LedgerInconsistencyError,
    LedgerLockTimeoutError,
    LedgerNotEnabledError,
    LedgerStrictModeError,
    LedgerTransactionOpenError,
    NodeNotFoundError,
    ReconstructionTooLargeError,
    RedactionInProgressError,
    RedactionTargetLiveError,
    StaleHeadError,
    UnknownRevisionError,
)

__all__ = [
    "Changeset",
    "OpRef",
    "RESERVED_PROPERTIES",
    "ACTOR_TYPES",
    "canonical_dumps",
    "GraphLedger",
    "RevisionInfo",
    "CommitResult",
    "HistoryPage",
    "Revision",
    "LedgerCounters",
    "RedactionReceipt",
    "IdempotencyRedactedError",
    "InvalidRedactionTargetError",
    "RedactionInProgressError",
    "RedactionTargetLiveError",
    "LedgerError",
    "NodeNotFoundError",
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
