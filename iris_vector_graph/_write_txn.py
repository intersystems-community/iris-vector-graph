"""IVG's own part of a write, and the caller's transaction around it (4.6.4).

`WriteTxn` is the one place that decides whether a write is inside a transaction its caller opened, and how IVG
commits or undoes its own part. Every write path opens one where it used to run `START TRANSACTION` (or relied on
the driver's autocommit) and ends it where it used to run `COMMIT` / `ROLLBACK` or call `conn.commit()` /
`conn.rollback()`:

- no caller transaction: IVG's write is a transaction of its own, committed when the call returns (4.6.3's
  behaviour);
- a caller transaction (SQL `START TRANSACTION`, or the driver's `setAutoCommit(False)`): IVG's write is a level
  nested in it. Its commit only lowers `$TLEVEL` back to the caller's, so the caller's commit keeps both and the
  caller's rollback removes both; its undo takes back IVG's level alone, so a failed write leaves the caller's
  earlier work in the open transaction.

Measured on IRIS 2026.3 (driver 5.4.0) and the reason for the shape: SQL `COMMIT` and `ROLLBACK` end every level,
and `START TRANSACTION` inside a transaction does nothing, so 4.6.3's `START TRANSACTION ... COMMIT` committed the
caller's work and its `ROLLBACK` took it back. SQL `SAVEPOINT` nests but has no release, and costs ~2.5 ms a call
when it is never rolled back to. ObjectScript `TSTART` / `TCOMMIT` / `TROLLBACK 1` nest exactly as wanted, and
globals set through the Native API on the same server process follow them. They run here as the IVG Schema's SQL
functions `Txn_Begin`, `Txn_End`, `Txn_Undo` (Graph.KG.Txn), on the writing cursor: a SQL function runs in that
connection's server process, which a Native API object need not (the engine may hold a connection of its own for it).

The driver's connection autocommits by default and then starts a transaction at the first statement that writes
($TLEVEL is 0 until then): with autocommit off, `Txn_Begin(1)` opens the caller's level first, which the driver's
`commit()` / `rollback()` ends.

A namespace whose classes predate 4.6.4 has no `Txn_Begin`: there a write with no caller transaction runs 4.6.3's
`START TRANSACTION` / `COMMIT` / `ROLLBACK`, and a write inside one raises `IVGSchemaError` before writing anything
(it would commit the caller's work), naming `initialize_schema()`.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "Graph_KG"
_warned_missing = False
#: id(connection) -> the outermost plain WriteTxn open on it: on a namespace without Txn_Begin, a write nested in
#: another of IVG's (a Cypher write's statements) finds $TLEVEL 1, which is IVG's own and not a caller's
_PLAIN_OPEN: dict = {}


def is_duplicate(exc: BaseException) -> bool:
    """A unique-key refusal (SQLCODE -119): the row is already there, which is not an error."""
    text = str(exc)
    return "unique" in text.lower() or "-119" in text


def _missing_function(exc: BaseException) -> bool:
    """SQLCODE -359 (function not found) or -428 (procedure not found): the namespace predates 4.6.4."""
    text = str(exc)
    return "-359" in text or "-428" in text


def _driver_autocommit_off(conn) -> bool:
    """The driver's autocommit turned off (`setAutoCommit(False)`): the caller owns a transaction even before its
    first write. Only an explicit False counts; a connection without the attribute autocommits."""
    return getattr(conn, "autocommit", True) is False


def caller_transaction_level(conn) -> Optional[int]:
    """The caller's open transaction on ``conn``: its ``$TLEVEL`` (0 when the driver's autocommit is off and the
    caller has not written yet, which is the caller's transaction all the same), or None when there is none (4.7.0).

    A connection that is not IRIS's (a stand-in) has no ``$TLEVEL`` and answers None. On IRIS's, a ``SELECT $TLEVEL``
    that fails raises: a call that would commit the caller's work must not guess that there is none."""
    from iris_vector_graph.schema_name import is_iris_connection

    if not is_iris_connection(conn):
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT $TLEVEL")
        row = cur.fetchone()
        # read before close: the driver's row dies with its cursor
        value = None if row is None else row[0]
    finally:
        cur.close()
    if value is None:
        raise RuntimeError("SELECT $TLEVEL answered nothing")
    level = int(value)
    if level > 0:
        return level
    return 0 if _driver_autocommit_off(conn) else None


def refuse_in_caller_transaction(conn, operation: str, why: str) -> None:
    """Raise :class:`~iris_vector_graph.errors.CallerTransactionOpenError` when the caller has uncommitted work on
    ``conn`` (``$TLEVEL`` above 0); nothing is sent but the ``$TLEVEL`` probe, so the caller's transaction is as it
    was. With the driver's autocommit off and nothing written yet (``$TLEVEL`` 0) there is nothing a commit could
    lose, so that is no refusal; ``WriteTxn`` still makes such a connection's writes the caller's."""
    level = caller_transaction_level(conn)
    if level:
        from iris_vector_graph.errors import CallerTransactionOpenError

        raise CallerTransactionOpenError(operation, level, why)


class WriteTxn:
    """IVG's own part of one write on ``conn``: ``begin()``, then ``commit()`` or ``rollback()`` (or ``with``).

    ``level`` is the ``$TLEVEL`` of IVG's part; ``joined`` is True when a caller's transaction is under it. Neither
    ``commit()`` nor ``rollback()`` ever ends a level below IVG's own, and neither calls the driver's ``commit()`` or
    ``rollback()``.
    """

    def __init__(self, conn, schema: Optional[str] = None, cursor=None):
        self._conn = conn
        self._schema = schema or DEFAULT_SCHEMA
        self._cursor = cursor
        self.level = 0
        self.joined = False
        self._open = False
        self._begun = False
        #: 4.6.3's plain START TRANSACTION / COMMIT / ROLLBACK, on a namespace without Txn_Begin
        self._plain = False
        #: the outer plain WriteTxn this one is part of (no level of its own)
        self._outer: Optional["WriteTxn"] = None

    # ------------------------------------------------------------------ plumbing

    @property
    def cursor(self):
        if self._cursor is None:
            self._cursor = self._conn.cursor()
        return self._cursor

    def _call(self, name: str, arg: int) -> int:
        cur = self.cursor
        cur.execute(f"SELECT {self._schema}.Txn_{name}(?)", [int(arg)])
        row = cur.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(f"{self._schema}.Txn_{name} answered no level")
        return int(row[0])

    def _level_now(self) -> int:
        cur = self.cursor
        cur.execute("SELECT $TLEVEL")
        row = cur.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("SELECT $TLEVEL answered nothing")
        return int(row[0])

    def native(self):
        """A host (iris_vector_graph.host) for class calls on this connection's own server process, where this
        transaction is: ``iris.createIRIS`` on the connection itself, never a Native API connection of its own.
        An ObjectScript writer that runs there nests its TSTART in this transaction."""
        from iris_vector_graph.embedded import EmbeddedConnection
        from iris_vector_graph.host import ClientHost, InProcessHost
        from iris_vector_graph.schema_name import unwrap_connection

        conn = unwrap_connection(self._conn)
        if isinstance(conn, EmbeddedConnection):
            return InProcessHost(conn)
        import iris

        return ClientHost(conn, native=iris.createIRIS(conn))

    # ------------------------------------------------------------------ the three steps

    def begin(self) -> "WriteTxn":
        hold = _driver_autocommit_off(self._conn)
        try:
            self.level = self._call("Begin", 1 if hold else 0)
        except Exception as exc:
            if not _missing_function(exc):
                raise
            self._begin_plain(hold, exc)
        else:
            self.joined = self.level > 1
            self._open = self._begun = True
        return self

    def _begin_plain(self, hold: bool, exc: BaseException) -> None:
        global _warned_missing
        from iris_vector_graph.schema_name import IVGSchemaError, unwrap_connection

        key = id(unwrap_connection(self._conn))
        outer = _PLAIN_OPEN.get(key)
        if outer is not None and outer._open:
            # inside IVG's own plain transaction: part of it, which commits or rolls back with it
            self._outer, self._plain = outer, True
            self.level, self.joined = outer.level, False
            self._open = self._begun = True
            return
        if hold or self._level_now() > 0:
            raise IVGSchemaError(
                f"{self._schema}.Txn_Begin is not in this namespace (its IVG classes predate 4.6.4), so this write "
                "cannot join the caller's open transaction without committing it; nothing was written. Run "
                "initialize_schema() to bring the classes up to date."
            ) from exc
        if not _warned_missing:
            _warned_missing = True
            logger.warning(
                "%s.Txn_Begin is not in this namespace (classes older than 4.6.4): writes run their own START "
                "TRANSACTION / COMMIT, and a write inside a caller's transaction raises. Run initialize_schema().",
                self._schema,
            )
        self._start_plain()
        _PLAIN_OPEN[key] = self

    def _start_plain(self) -> None:
        """4.6.3's own transaction: ``START TRANSACTION`` now, ``COMMIT`` / ``ROLLBACK`` at the end, at level 1."""
        self.cursor.execute("START TRANSACTION")
        self._plain = True
        self.level, self.joined = 1, False
        self._open = self._begun = True

    def commit(self) -> None:
        """Commit IVG's part: the write itself with no caller transaction, else only back to the caller's level."""
        if not self._open:
            return
        if self._outer is not None:
            self._open = False  # the outer one commits it
            return
        if self._plain:
            self.cursor.execute("COMMIT")
            self._open = False
            self._forget()
            return
        # still open until End answers: a commit that fails can be rolled back
        after = self._call("End", self.level)
        self._open = False
        if self.joined and after < self.level - 1:
            # Something inside the write ended every level (a COMMIT or ROLLBACK that is not this module's).
            raise RuntimeError(
                f"the caller's transaction was ended inside IVG's write ($TLEVEL {after} at its end, expected "
                f"{self.level - 1}): its work before the call is committed or rolled back"
            )

    def rollback(self) -> None:
        """Take back IVG's part, never the caller's. A failure is logged and not raised: the caller is already
        raising the write's own error, which is the one that explains it, and a transaction on a dead connection is
        rolled back by the server."""
        if not self._open:
            return
        self._open = False
        if self._outer is not None:
            # no level of its own to take back: the outer transaction (IVG's, no caller's) goes with it
            self._outer.rollback()
            return
        self._forget()
        try:
            if self._plain:
                self.cursor.execute("ROLLBACK")
            else:
                self._call("Undo", self.level)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            what = "ROLLBACK" if self._plain else f"{self._schema}.Txn_Undo({self.level})"
            logger.error("%s after a failed write failed too: %s", what, exc)

    def _forget(self) -> None:
        for key, txn in list(_PLAIN_OPEN.items()):
            if txn is self:
                del _PLAIN_OPEN[key]

    # ------------------------------------------------------------------ with

    def __enter__(self) -> "WriteTxn":
        if not self._begun:
            self.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def write_txn(conn, schema: Optional[str] = None, cursor=None) -> WriteTxn:
    """A begun :class:`WriteTxn` on ``conn`` (``cursor`` when given, so its statements share it)."""
    return WriteTxn(conn, schema, cursor).begin()
