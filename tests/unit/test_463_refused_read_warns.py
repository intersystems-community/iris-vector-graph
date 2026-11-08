"""4.6.3: a read IRIS refuses for a missing privilege is logged at WARNING, with its SQLCODE and privilege text.

`IRISGraphStore.execute_sql` answers a failed statement in the result's `error` and logged it at DEBUG only (round 3,
python W6: at WARNING logging's last-resort handler printed every failed read to a caller's stderr). A refusal for a
missing privilege is not a failed query a caller handles: it is an operator's setup, and nothing at WARNING said so.
Other failures stay at DEBUG. The live case is tests/e2e/test_463_refused_reads_e2e.py.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

REFUSED = ("<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]\r\n[Location: <Prepare>]\r\n"
           "[%msg: <User R is not privileged for the operation>]")
PROTECT = "<PROTECT>zExecute+3^Graph.KG.Foo.1 ^KG,/usr/irissys/mgr/user/"
SYNTAX = "<SQL ERROR>; Details: [SQLCODE: <-1>:<Invalid SQL statement>]"


def _store(error):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError(error)
    conn.cursor.return_value = cursor
    return IRISGraphStore(conn)


@pytest.mark.parametrize("error, code", [(REFUSED, "SQLCODE -99"), (PROTECT, "<PROTECT>")], ids=["-99", "protect"])
def test_a_refused_read_warns_with_the_privilege_text(error, code, caplog):
    store = _store(error)
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        result = store.execute_sql("SELECT node_id FROM Graph_KG.nodes", [])
    assert result.error == error
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1, [(r.levelname, r.getMessage()) for r in caplog.records]
    message = warned[0].getMessage()
    assert message.startswith("execute_sql refused for a missing privilege"), message
    assert code in message, message
    assert "not privileged for the operation" in message or "PROTECT" in message, message


def test_any_other_failure_stays_at_debug(caplog):
    store = _store(SYNTAX)
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        result = store.execute_sql("SELECT nope", [])
    assert result.error == SYNTAX
    assert [r.levelname for r in caplog.records if "execute_sql" in r.getMessage()] == ["DEBUG"]
