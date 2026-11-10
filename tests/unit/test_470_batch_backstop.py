"""4.7.0 row 14: BulkLoader's batch backstop. The driver's executemany skips a refused row of several and its rowcount
says so (2 for a batch of 3 with one refused, measured on IRIS 2026.3); a short count sends the batch down the row
path, which raises for the refused row and counts a duplicate as skipped.

No refusal other than a width or a NULL (both refused before anything is sent) reaches the loader on IRIS: under
%NOCHECK no constraint is checked, and with checks on a duplicate is the only other refusal. So the fake here gives
the short count. The connection claims to be IRIS's (`spec=iris.IRISConnection`), which the backstop asks for.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import iris
import pytest

from iris_vector_graph.bulk_loader import BulkLoader
from iris_vector_graph.exceptions import BulkLoadError

REFUSED = "[SQLCODE: <-108>:<'p' is a required field>]"


def _loader(refused_row):
    conn = MagicMock(spec=iris.IRISConnection)
    cur = MagicMock()
    written = []

    def executemany(sql, rows):
        kept = [r for r in rows if r != refused_row]
        written.extend(kept)
        cur.rowcount = len(kept)

    def execute(sql, row=None, *a):
        if row == refused_row:
            raise RuntimeError(REFUSED)
        if row in written:
            raise RuntimeError("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check>]")
        written.append(row)

    cur.executemany.side_effect = executemany
    cur.execute.side_effect = execute
    return BulkLoader(conn), cur, written


def test_a_short_batch_raises_for_the_refused_row():
    loader, cur, written = _loader(refused_row=["g", "c", None, "d"])
    rows = [["g", "a", "R", "b"], ["g", "c", None, "d"], ["g", "e", "R", "f"]]
    with pytest.raises(BulkLoadError, match="-108"):
        loader._executemany_batched(cur, "INSERT INTO t VALUES (?, ?, ?, ?)", rows, label="Edges",
                                    commit_per_batch=True)
    loader.conn.rollback.assert_called()  # the batch's two rows were taken back before the row path


def test_a_full_batch_is_one_statement():
    loader, cur, written = _loader(refused_row=None)
    rows = [["g", "a", "R", "b"], ["g", "e", "R", "f"]]
    assert loader._executemany_batched(cur, "INSERT INTO t VALUES (?, ?, ?, ?)", rows, label="Edges") == 2
    cur.execute.assert_not_called()
