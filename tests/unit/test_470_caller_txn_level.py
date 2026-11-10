"""4.7.0: `_write_txn.caller_transaction_level`, which `initialize_schema()`, `bulk_load_session` and route creation read
to tell whether the caller has a transaction open (tests/e2e/test_470_caller_txn_e2e.py has the live answers).

The connection is a mock that claims to be an IRIS connection (`spec=iris.IRISConnection`), whose cursor answers
`SELECT $TLEVEL` with the level each case gives; anything else it is sent fails the test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import iris
import pytest

from iris_vector_graph._write_txn import caller_transaction_level, refuse_in_caller_transaction
from iris_vector_graph.errors import CallerTransactionOpenError


def _conn(level, autocommit=True):
    conn = MagicMock(spec=iris.IRISConnection)
    conn.autocommit = autocommit
    cur = conn.cursor.return_value
    sent = []

    def execute(sql, *a):
        sent.append(sql)
        assert sql == "SELECT $TLEVEL", f"sent {sql!r}"

    cur.execute.side_effect = execute
    cur.fetchone.return_value = None if level is None else (level,)
    conn.sent = sent
    return conn


@pytest.mark.parametrize(
    "level, autocommit, answer",
    [
        (0, True, None),  # no transaction
        (1, True, 1),  # START TRANSACTION
        (3, True, 3),
        (0, False, 0),  # autocommit off, before the caller's first write
        (1, False, 1),  # autocommit off, after it
    ],
)
def test_the_level_of_the_callers_transaction(level, autocommit, answer):
    conn = _conn(level, autocommit)
    assert caller_transaction_level(conn) == answer
    assert conn.sent == ["SELECT $TLEVEL"]
    conn.cursor.return_value.close.assert_called_once()


def test_a_connection_that_is_not_iris_has_none_and_is_sent_nothing():
    conn = MagicMock()
    conn.autocommit = False
    assert caller_transaction_level(conn) is None
    conn.cursor.assert_not_called()


def test_a_probe_that_answers_nothing_raises_rather_than_guess():
    with pytest.raises(RuntimeError, match=r"\$TLEVEL answered nothing"):
        caller_transaction_level(_conn(None))


def test_the_refusal_names_the_call_the_level_and_the_reason():
    with pytest.raises(CallerTransactionOpenError) as info:
        refuse_in_caller_transaction(_conn(2), "initialize_schema()", "setup commits as it goes")
    err = info.value
    assert (err.operation, err.level) == ("initialize_schema()", 2)
    assert str(err).startswith(
        "initialize_schema() cannot run inside the caller's open transaction ($TLEVEL 2): setup commits as it goes. "
        "Nothing was done, and the caller's transaction is still open"
    )


def test_autocommit_off_is_named_in_the_refusal():
    with pytest.raises(CallerTransactionOpenError, match="the driver's autocommit is off"):
        refuse_in_caller_transaction(_conn(0, autocommit=False), "initialize_schema()", "why")


def test_no_transaction_is_no_refusal():
    refuse_in_caller_transaction(_conn(0), "initialize_schema()", "why")
