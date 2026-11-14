"""A migration step's commit (4.7.0, row 3b's audit).

Each migration module had its own `_commit` that ran `conn.commit()` and logged a failure at DEBUG ("Commit not
needed", meant for an autocommit connection, where there is nothing to fail): a commit IRIS refused mid-upgrade went on
to the next step as if the last one were written. Now a failed commit rolls the step back and raises, naming the
migration.

A caller's open transaction is refused once, at the migration's entry (`refuse_caller_transaction`), and never at a
step's commit (4.7.1): with the driver's autocommit off, `$TLEVEL` is 0 at the entry and the migration's own first
write raises it to 1, so 4.7.0's second check at each commit took the migration's own open writes for the caller's
and refused them ("Nothing was done") instead of committing them.
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
    """Commit ``migration``'s step on ``conn``; a failure rolls it back and raises ``RuntimeError`` naming it.

    The open transaction here is the migration's own: the caller's was refused at the migration's entry.
    """
    try:
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception as rb:
            logger.error("%s: the rollback after the failed commit failed too: %s", migration, rb)
        raise RuntimeError(f"{migration}: a step's commit failed, and the step was rolled back: {e}") from e
