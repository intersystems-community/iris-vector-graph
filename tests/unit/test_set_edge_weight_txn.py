"""set_edge_weight updates every row of a triple or none (Tom, Q5 = b, 2026-10-31).

A multigraph triple has one rdf_edges row per edge key, and set_edge_weight updates them one
UPDATE at a time. Outside START TRANSACTION each UPDATE committed on its own, so a failure on
the second row left the first at the new weight. True on success, False when there is no row; since 4.6.0
(R203) a failed update raises GraphWriteError instead of answering False. Live proof:
tests/e2e/test_set_edge_weight_txn_e2e.py.

Also: engine.graph.upsert_node delegated to a method no module defines.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.engine import IRISGraphEngine, _GraphSubEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._adjacency_indexed_cached = True
    return eng, conn, cursor


def test_a_failed_second_update_rolls_back_the_first_and_raises():
    eng, conn, cursor = _engine()
    cursor.fetchall.return_value = [(1, '{"weight": 1.0}'), (2, '{"weight": 2.0}')]
    log, updates = [], []

    def execute(sql, *a, **k):
        log.append(sql)
        if sql.startswith("UPDATE"):
            updates.append(a)
            if len(updates) == 2:
                raise RuntimeError("[SQLCODE: <-110>:<Locking conflict>]")

    cursor.execute.side_effect = execute
    with pytest.raises(GraphWriteError) as raised:
        eng.set_edge_weight("a", "P", "b", 9.0)
    assert raised.value.sqlcode == -110
    assert log[0] == "START TRANSACTION"
    assert "ROLLBACK" in log and "COMMIT" not in log
    conn.commit.assert_not_called()


def test_every_row_is_updated_in_one_transaction():
    eng, conn, cursor = _engine()
    cursor.fetchall.return_value = [(1, '{"weight": 1.0}'), (2, None), (3, "not json")]
    log = []
    cursor.execute.side_effect = lambda sql, *a, **k: log.append(sql)
    assert eng.set_edge_weight("a", "P", "b", 9.0) is True
    assert log[0] == "START TRANSACTION" and log[-1] == "COMMIT"
    assert sum(1 for s in log if s.startswith("UPDATE")) == 3


def test_no_row_answers_false_and_leaves_no_open_transaction():
    eng, conn, cursor = _engine()
    cursor.fetchall.return_value = []
    log = []
    cursor.execute.side_effect = lambda sql, *a, **k: log.append(sql)
    assert eng.set_edge_weight("a", "P", "b", 9.0) is False
    assert log[0] == "START TRANSACTION" and log[-1] in ("ROLLBACK", "COMMIT")


def test_the_graph_sub_engine_has_no_upsert_node():
    """It returned self._e.upsert_node(...), which nothing defines: AttributeError for every caller."""
    assert not hasattr(_GraphSubEngine, "upsert_node")
    assert not hasattr(IRISGraphEngine, "upsert_node")
