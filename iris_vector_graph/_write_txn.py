"""Shared pieces of the transactional writers (create_edge, store_node, store_edge, the store's
write_nodes / write_edges): which errors are a duplicate, and the ROLLBACK on the way out."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def is_duplicate(exc: BaseException) -> bool:
    """A unique-key refusal (SQLCODE -119): the row is already there, which is not an error."""
    text = str(exc)
    return "unique" in text.lower() or "-119" in text


def rollback(cursor) -> None:
    """Roll back the cursor's transaction on the way to raising the error that caused it.

    A ROLLBACK that itself fails (the connection is gone) is logged and not raised: the
    caller is already raising the write's own error, which is the one that explains it,
    and a transaction on a dead connection is rolled back by the server.
    """
    try:
        cursor.execute("ROLLBACK")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error("ROLLBACK after a failed write failed too: %s", exc)
