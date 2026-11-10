"""4.7.0 row 3b: the FHIR graph tables are created (DDL) and committed on the first registration in a namespace with a
FHIR server; inside the caller's open transaction that is refused before any DDL (the live namespaces of the E2E
suite have no FHIR server, so tests/e2e/test_470_caller_txn_3b_e2e.py cannot reach it).

The connection is a mock that claims to be an IRIS connection (`spec=iris.IRISConnection`); its cursor answers the FHIR
repository probe with 1 (there is one) and `SELECT $TLEVEL` with the level each case gives, and records every
statement.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import iris
import pytest

from iris_vector_graph._engine.fhir_graph import FhirGraphMixin
from iris_vector_graph.errors import CallerTransactionOpenError


def _owner(level: int):
    conn = MagicMock(spec=iris.IRISConnection)
    conn.autocommit = True
    sent = []
    cur = conn.cursor.return_value

    def execute(sql, *a):
        sent.append(sql)

    def fetchone():
        return (level,) if sent[-1] == "SELECT $TLEVEL" else (1,)

    cur.execute.side_effect = execute
    cur.fetchone.side_effect = fetchone
    return SimpleNamespace(conn=conn, _schema_prefix="Graph_KG"), sent


def test_the_tables_are_not_created_inside_the_callers_transaction():
    owner, sent = _owner(level=1)
    with pytest.raises(CallerTransactionOpenError, match=r"FHIR graph tables .*\$TLEVEL 1"):
        FhirGraphMixin._ensure_fhir_graph_tables(owner)
    assert not [s for s in sent if s.lstrip().upper().startswith("CREATE")], sent
    owner.conn.commit.assert_not_called()


def test_without_a_caller_transaction_they_are_created_and_committed():
    owner, sent = _owner(level=0)
    FhirGraphMixin._ensure_fhir_graph_tables(owner)
    assert [s for s in sent if s.lstrip().upper().startswith("CREATE")], sent
    owner.conn.commit.assert_called_once()
