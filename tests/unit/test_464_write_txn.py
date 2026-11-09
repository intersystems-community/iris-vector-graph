"""4.6.4: `_write_txn.WriteTxn`, the one owner of "am I inside the caller's transaction, and how do I commit or undo
my own part".

A fake server process keeps `$TLEVEL` the way IRIS 2026.3 was measured to (tests/e2e/test_464_caller_txn_e2e.py runs
the real one): TSTART/TCOMMIT/TROLLBACK 1 nest, SQL `START TRANSACTION` inside a transaction does nothing, SQL
`COMMIT` and `ROLLBACK` end every level. The helper must never end a level below its own, and never call the driver's
`commit()` or `rollback()`.
"""

from __future__ import annotations

import logging
import re

import pytest

from iris_vector_graph._write_txn import WriteTxn, write_txn
from iris_vector_graph.schema_name import IVGSchemaError


class Server:
    """One server process: its $TLEVEL and the statements it ran."""

    def __init__(self, level=0, procs=True):
        self.level = level
        self.procs = procs
        self.ran = []


class Cursor:
    def __init__(self, server):
        self.s = server
        self._row = None

    def execute(self, sql, params=None):
        s = self.s
        s.ran.append((sql, list(params or [])))
        m = re.fullmatch(r"SELECT (\w+)\.Txn_(Begin|End|Undo)\(\?\)", sql)
        if m:
            if not s.procs:
                raise RuntimeError("[SQLCODE: <-359>:<SQL Function (function stored procedure) not found>]")
            arg = int(params[0])
            if m.group(2) == "Begin":
                if arg and s.level == 0:
                    s.level += 1
                s.level += 1
            elif m.group(2) == "End":
                while arg >= 1 and s.level >= arg:
                    s.level -= 1
            else:
                while arg >= 1 and s.level >= arg:
                    s.level -= 1
            self._row = (s.level,)
        elif sql == "SELECT $TLEVEL":
            self._row = (s.level,)
        elif sql == "START TRANSACTION":
            s.level = max(s.level, 1)
        elif sql in ("COMMIT", "ROLLBACK"):
            s.level = 0
        else:
            self._row = None

    def fetchone(self):
        return self._row


class Conn:
    def __init__(self, server, autocommit=True):
        self.s = server
        self.autocommit = autocommit

    def cursor(self):
        return Cursor(self.s)

    def commit(self):  # pragma: no cover - the test fails if it is called
        raise AssertionError("WriteTxn called the driver's commit()")

    def rollback(self):  # pragma: no cover
        raise AssertionError("WriteTxn called the driver's rollback()")


def test_no_caller_transaction_the_write_is_its_own_and_commits():
    s = Server()
    txn = write_txn(Conn(s), "Graph_KG")
    assert (txn.level, txn.joined, s.level) == (1, False, 1)
    txn.commit()
    assert s.level == 0
    assert s.ran[0] == ("SELECT Graph_KG.Txn_Begin(?)", [0])


def test_inside_the_callers_transaction_commit_leaves_the_callers_level_open():
    s = Server(level=1)
    txn = write_txn(Conn(s), "Graph_KG")
    assert (txn.level, txn.joined) == (2, True)
    txn.commit()
    assert s.level == 1


def test_rollback_takes_back_its_own_level_only():
    s = Server(level=1)
    txn = write_txn(Conn(s), "Graph_KG")
    txn.rollback()
    assert s.level == 1
    assert s.ran[-1] == ("SELECT Graph_KG.Txn_Undo(?)", [2])


def test_driver_autocommit_off_holds_a_level_for_the_caller():
    s = Server(level=0)
    txn = write_txn(Conn(s, autocommit=False), "Graph_KG")
    assert s.ran[0] == ("SELECT Graph_KG.Txn_Begin(?)", [1])
    assert (txn.level, txn.joined) == (2, True)
    txn.commit()
    assert s.level == 1, "the caller's level, which the driver's commit or rollback ends"


def test_the_context_manager_commits_on_success_and_undoes_on_an_error():
    s = Server(level=1)
    with WriteTxn(Conn(s), "Graph_KG"):
        assert s.level == 2
    assert s.level == 1
    with pytest.raises(ValueError, match="boom"):
        with WriteTxn(Conn(s), "Graph_KG"):
            raise ValueError("boom")
    assert s.level == 1
    assert s.ran[-1] == ("SELECT Graph_KG.Txn_Undo(?)", [2])


def test_rollback_after_commit_does_nothing():
    s = Server()
    txn = write_txn(Conn(s), "Graph_KG")
    txn.commit()
    n = len(s.ran)
    txn.rollback()
    assert len(s.ran) == n


def test_a_failed_undo_is_logged_not_raised(caplog):
    s = Server(level=1)
    txn = write_txn(Conn(s), "Graph_KG")
    s.procs = False  # the next call fails
    with caplog.at_level(logging.ERROR, logger="iris_vector_graph._write_txn"):
        txn.rollback()
    assert any("Txn_Undo" in r.getMessage() for r in caplog.records)


def test_a_callers_transaction_ended_inside_the_write_raises():
    """Something inside IVG's write ran COMMIT (every level): the caller's transaction is gone, which is said."""
    s = Server(level=1)
    txn = write_txn(Conn(s), "Graph_KG")
    s.level = 0
    with pytest.raises(RuntimeError, match="caller's transaction"):
        txn.commit()


def test_the_function_is_the_engines_schemas():
    s = Server()
    write_txn(Conn(s), "IVGALT").commit()
    assert s.ran[0][0] == "SELECT IVGALT.Txn_Begin(?)"
    assert s.ran[-1][0] == "SELECT IVGALT.Txn_End(?)"


def test_a_namespace_without_the_functions_keeps_4_6_3_behaviour_with_no_caller_transaction():
    s = Server(procs=False)
    txn = write_txn(Conn(s), "Graph_KG")
    assert [sql for sql, _ in s.ran[1:]] == ["SELECT $TLEVEL", "START TRANSACTION"]
    txn.commit()
    assert s.ran[-1][0] == "COMMIT" and s.level == 0
    txn2 = write_txn(Conn(s), "Graph_KG")
    txn2.rollback()
    assert s.ran[-1][0] == "ROLLBACK"


@pytest.mark.parametrize("level,autocommit", [(1, True), (0, False)])
def test_a_namespace_without_the_functions_refuses_to_write_inside_a_callers_transaction(level, autocommit):
    s = Server(level=level, procs=False)
    with pytest.raises(IVGSchemaError, match="initialize_schema"):
        write_txn(Conn(s, autocommit=autocommit), "Graph_KG")
    assert s.level == level
    assert not any(sql in ("START TRANSACTION", "COMMIT", "ROLLBACK") for sql, _ in s.ran)


def test_an_unreadable_answer_raises_rather_than_guess():
    class Silent(Cursor):
        def fetchone(self):
            return None

    s = Server()

    class C(Conn):
        def cursor(self):
            return Silent(self.s)

    with pytest.raises(RuntimeError, match="Txn_Begin"):
        write_txn(C(s), "Graph_KG")
