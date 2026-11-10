"""A migration step's commit (4.7.0, row 3b's audit).

Each migration module had its own `_commit` that ran `conn.commit()` and logged a failure at DEBUG ("Commit not
needed", meant for an autocommit connection, where there is nothing to fail): a commit IRIS refused mid-upgrade went on
to the next step as if the last one were written. Now a failed commit rolls the step back and raises, naming the
migration; a caller's open transaction is refused first, as every migration's entry refuses it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

WHY = "a migration runs DDL, rebuilds indexes and globals, and commits step by step"


def refuse_caller_transaction(conn, migration: str) -> None:
    """Raise ``CallerTransactionOpenError`` naming ``migration`` when the caller has an open transaction on ``conn``."""
    from iris_vector_graph._write_txn import refuse_in_caller_transaction

    refuse_in_caller_transaction(conn, f"{migration}()", WHY)


def commit_step(conn, migration: str) -> None:
    """Commit ``migration``'s step on ``conn``; a failure rolls it back and raises ``RuntimeError`` naming it."""
    refuse_caller_transaction(conn, migration)
    try:
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception as rb:
            logger.error("%s: the rollback after the failed commit failed too: %s", migration, rb)
        raise RuntimeError(f"{migration}: a step's commit failed, and the step was rolled back: {e}") from e
