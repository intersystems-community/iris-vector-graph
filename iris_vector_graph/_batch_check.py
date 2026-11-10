"""The backstop for a batch write the driver answered as complete when IRIS refused part of it (4.7.0 row 14).

The driver's `executemany` raises IRIS's refusal when the statement has one row and skips the refused row of several
without raising (a value wider than its column, SQLCODE -104; a NULL in a NOT NULL column, -108). The width checks
(iris_vector_graph/_widths.py) refuse the first kind before anything is sent; this reads back what a batch should
have left and names the rows that are not there, whatever refused them.

For a plain INSERT the cursor's `rowcount` is the rows written (measured on IRIS 2026.3, driver 5.4.0: 2 for a batch
of 3 with one refused), and a writer compares it with the batch (`check_rowcount`). A guarded INSERT (`... WHERE NOT
EXISTS`) writes nothing for a row that is there already, so its count cannot tell a refusal from a duplicate: those
batches are read back (`missing_rows`).

Both run only on an IRIS connection (`is_iris_connection`): a stand-in has no table to read back and no count the
driver gives, and is left alone, as the caller-transaction probe leaves it (iris_vector_graph/_write_txn.py).
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

#: ids per read-back statement: IRIS fails to prepare IN lists near 2000 parameters
_CHUNK = 500


class BatchShortfall(RuntimeError):
    """IRIS wrote fewer rows than the batch held, and the driver said nothing."""


def _live(conn) -> bool:
    from iris_vector_graph.schema_name import is_iris_connection

    return conn is not None and is_iris_connection(conn)


def check_rowcount(cursor, rows: Sequence, what: str, conn) -> None:
    """Raise :class:`BatchShortfall` when a plain INSERT batch on ``conn`` wrote fewer rows than ``rows`` (a count the
    driver does not give, negative or missing, is not checked)."""
    if not _live(conn):
        return
    n = getattr(cursor, "rowcount", -1)
    if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(rows):
        raise BatchShortfall(
            f"IRIS refused {len(rows) - n} of the {len(rows)} {what} rows of the batch without an error (the "
            "driver's executemany skips a refused row of several)"
        )


def missing_rows(cursor, table: str, columns: Sequence[str], wanted: Iterable[Tuple], conn) -> List[Tuple]:
    """The tuples of ``wanted`` that ``table`` does not hold, read on ``conn``'s ``cursor``. ``columns`` names the
    tuple's columns, the first being ``graph_id`` (compared as ``COALESCE(graph_id, '')``) and the second the column
    the read goes by (``s`` or ``node_id``)."""
    if not _live(conn):
        return []
    wanted = list(dict.fromkeys(tuple("" if v is None else v for v in w) for w in wanted))
    if not wanted:
        return []
    keys = list(dict.fromkeys(w[1] for w in wanted))
    select = ", ".join(["COALESCE(graph_id, '')"] + [f'"{c}"' if c == "key" else c for c in columns[1:]])
    present = set()
    for i in range(0, len(keys), _CHUNK):
        chunk = keys[i : i + _CHUNK]
        marks = ", ".join("?" * len(chunk))
        cursor.execute(f"SELECT {select} FROM {table} WHERE {columns[1]} IN ({marks})", chunk)
        present.update(tuple(r) for r in cursor.fetchall())
    return [w for w in wanted if w not in present]


def raise_if_missing(writer: str, missing: List[Tuple], what: str) -> None:
    """``GraphWriteError`` for ``writer`` naming the first five of ``missing`` (a writer raises it before its
    rollback, so nothing of the batch is written)."""
    if not missing:
        return
    from iris_vector_graph.errors import GraphWriteError

    shown = ", ".join(repr(m[1:]) for m in missing[:5]) + (" ..." if len(missing) > 5 else "")
    shortfall = BatchShortfall(
        f"IRIS refused {len(missing)} {what} row(s) of the batch without an error (the driver's executemany "
        f"skips a refused row of several): {shown}"
    )
    raise GraphWriteError(writer, shortfall)
