"""4.6.0 R245: ``materialize_inference`` raises ``GraphWriteError`` when IRIS refuses an inferred edge's INSERT.

It caught every failure (``except Exception: continue``) and answered ``{"inferred": 0}``, which is also its answer for
a graph with nothing to infer: a data-changing path answering a refusal with a value (TESTING_POLICY, "Assert the
state"). A duplicate (SQLCODE -119, another writer got there first) is still skipped. The mock graph holds
A subClassOf B and B subClassOf C, so one edge is inferred: A subClassOf C. Live:
tests/e2e/test_460_r245_materialize_inference_refused_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.engine import IRISGraphEngine

SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
REFUSED = "[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]"
DUPLICATE = "[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check upon INSERT>]"


def _engine(insert_error):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    log = []
    state = {"last": ""}

    def execute(sql, params=None, *a, **k):
        sql = str(sql)
        log.append(sql)
        state["last"] = sql
        state["params"] = params
        if sql.lstrip().upper().startswith("INSERT") and insert_error:
            raise RuntimeError(insert_error)

    def fetchall():
        if "SELECT s, o_id" in state["last"] and state["params"] and state["params"][0] == SUBCLASS:
            return [("A", "B"), ("B", "C")]
        return []

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = fetchall
    cursor.fetchone.return_value = (0,)  # _exists: no such edge yet
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._adjacency_indexed_cached = True
    return eng, log


def test_a_refused_insert_raises_and_rolls_back():
    eng, log = _engine(REFUSED)
    with pytest.raises(GraphWriteError) as raised:
        eng.materialize_inference()
    assert raised.value.operation == "materialize_inference"
    assert raised.value.sqlcode == -99
    assert "nothing written" in str(raised.value)
    inserts = [s for s in log if s.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1 and "ROLLBACK" in log and "COMMIT" not in log


def test_a_duplicate_is_skipped_and_counted_as_not_inferred():
    eng, log = _engine(DUPLICATE)
    assert eng.materialize_inference() == {"inferred": 0}
    assert "ROLLBACK" not in log


def test_the_inferred_edge_is_written_and_committed():
    eng, log = _engine(None)
    assert eng.materialize_inference() == {"inferred": 1}
    (insert,) = [s for s in log if s.lstrip().upper().startswith("INSERT")]
    assert "rdf_edges" in insert
    assert log.index("START TRANSACTION") < log.index(insert) < log.index("COMMIT")
