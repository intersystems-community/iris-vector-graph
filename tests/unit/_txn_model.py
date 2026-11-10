"""What a fake cursor's statements would leave behind, as a transaction would: the writes committed and those of a
transaction still open (4.7.0). Unit tests of a writer assert this effect, not which commit call was made.

The unit suite's writes run as tests/unit/conftest.py makes them, IVG's own plain transaction (`START TRANSACTION`
... `COMMIT` / `ROLLBACK` on the writing cursor); a caller's transaction around them is tested against IRIS
(tests/e2e/test_470_caller_txn_e2e.py, test_470_caller_txn_3b_e2e.py).
"""

from __future__ import annotations


class TxnModel:
    """Attach to a MagicMock cursor (``execute`` side effect) and its connection (``commit`` / ``rollback``)."""

    WRITES = ("INSERT", "UPDATE", "DELETE")

    def __init__(self, cursor, conn=None):
        self.committed, self.pending, self.open = [], [], False
        cursor.execute.side_effect = self._execute
        if conn is not None:
            conn.commit.side_effect = lambda: self._end("COMMIT")
            conn.rollback.side_effect = lambda: self._end("ROLLBACK")

    def _end(self, verb):
        if verb == "COMMIT":
            self.committed.extend(self.pending)
        self.pending, self.open = [], False

    def _execute(self, sql, params=None, *a, **k):
        verb = sql.split()[0].upper()
        if verb == "START":
            self.open = True
        elif verb in ("COMMIT", "ROLLBACK"):
            self._end(verb)
        elif verb in self.WRITES:
            (self.pending if self.open else self.committed).append((verb, tuple(params or ())))

    def committed_verbs(self):
        return [verb for verb, _ in self.committed]
