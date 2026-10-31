"""Data-changing writers raise instead of logging and returning False, and write in one
transaction (Tom, 2026-10-31).

`create_edge` used to log a failed insert and return False, the value it also returns for a
duplicate, and its endpoint node inserts had already committed (the connection autocommits
each statement outside START TRANSACTION): a 256 + 256-character name pair left two orphan
node rows and no edge. The live proof is tests/e2e/test_create_edge_atomic_e2e.py.

Covered here with doubles: create_edge, store_node, store_edge, and the store's write_nodes /
write_edges. A duplicate stays what it was (False / skipped), never an error.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.errors import GraphWriteError

IRIS_415 = (
    "<SQL ERROR>; Details: [SQLCODE: <-415>:<Fatal error occurred within the SQL filer>]\r\n"
    "[Error: <<SUBSCRIPT>%SQLInsert+21^Graph.KG.rdfedges.1 ^t52S.BWr6.3(...)>]"
)
IRIS_119 = "[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check upon INSERT>]"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    return eng, conn, cursor


def _fail_on(cursor, needle: str, message: str):
    sql_log = []

    def execute(sql, *a, **k):
        sql_log.append(sql)
        if needle in sql:
            raise RuntimeError(message)

    cursor.execute.side_effect = execute
    return sql_log


def test_graph_write_error_carries_sqlcode_and_message():
    e = GraphWriteError("create_edge", RuntimeError(IRIS_415))
    assert e.operation == "create_edge" and e.sqlcode == -415
    assert "-415" in str(e) and "create_edge" in str(e)
    assert isinstance(e, RuntimeError)
    from iris_vector_graph import GraphWriteError as exported, IVGError

    assert exported is GraphWriteError and isinstance(e, IVGError)


def test_create_edge_raises_the_iris_error_and_rolls_back_everything():
    eng, conn, cursor = _engine()
    log = _fail_on(cursor, "INSERT INTO Graph_KG.rdf_edges", IRIS_415)
    with patch.object(eng, "_ekey_guard", return_value=" AND ekey = 0"):
        with pytest.raises(GraphWriteError) as info:
            eng.create_edge("a" * 256, "P", "b" * 256)
    assert info.value.sqlcode == -415
    assert isinstance(info.value.__cause__, RuntimeError)
    # one transaction around the endpoint rows and the edge: opened before the first node insert
    first_node = next(i for i, s in enumerate(log) if "Graph_KG.nodes" in s and "INSERT" in s)
    assert log.index("START TRANSACTION") < first_node
    assert "ROLLBACK" in log and "COMMIT" not in log
    conn.commit.assert_not_called()


def test_create_edge_still_answers_false_for_a_duplicate():
    eng, conn, cursor = _engine()
    _fail_on(cursor, "INSERT INTO Graph_KG.rdf_edges", IRIS_119)
    with patch.object(eng, "_ekey_guard", return_value=""), patch.object(eng, "_adopt_temporal_row", return_value=False):
        assert eng.create_edge("a", "P", "b") is False


def test_create_edge_raises_when_adopting_a_temporal_row_fails():
    eng, conn, cursor = _engine()
    cursor.rowcount = 0

    def execute(sql, *a, **k):
        if sql.startswith("UPDATE"):
            raise RuntimeError("[SQLCODE: <-110>:<Locking conflict>]")

    cursor.execute.side_effect = execute
    cursor.fetchall.return_value = [(7, '{"temporal": true}')]
    with patch.object(eng, "_ekey_guard", return_value=""):
        with pytest.raises(GraphWriteError) as info:
            eng.create_edge("a", "P", "b")
    assert info.value.sqlcode == -110


def test_store_node_raises_a_failed_property_write_and_writes_in_one_transaction():
    eng, conn, cursor = _engine()
    log = _fail_on(cursor, "INSERT INTO Graph_KG.rdf_props", "[SQLCODE: <-104>:<Field validation failed>]")
    with pytest.raises(GraphWriteError) as info:
        eng.store_node("n1", properties={"k": "v"}, labels=["L"])
    assert info.value.sqlcode == -104
    assert log[0] == "START TRANSACTION"
    assert "ROLLBACK" in log and "COMMIT" not in log
    conn.commit.assert_not_called()


def test_store_node_tolerates_a_duplicate_node_row():
    eng, conn, cursor = _engine()
    _fail_on(cursor, "INSERT INTO Graph_KG.nodes", IRIS_119)
    assert eng.store_node("n1") is True


def test_store_edge_leaves_no_endpoint_rows_when_the_edge_fails():
    eng, conn, cursor = _engine()
    log = _fail_on(cursor, "INSERT INTO Graph_KG.rdf_edges", IRIS_415)
    with pytest.raises(GraphWriteError):
        eng.store_edge("a", "P", "b")
    first_node = next(i for i, s in enumerate(log) if "Graph_KG.nodes" in s and "INSERT" in s)
    assert log.index("START TRANSACTION") < first_node
    assert "ROLLBACK" in log and "COMMIT" not in log
    conn.commit.assert_not_called()


def _store():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    cursor = MagicMock()
    store.conn.cursor.return_value = cursor
    return store, cursor


@pytest.mark.parametrize(
    "method,arg,needle",
    [
        ("write_nodes", [{"id": "n1", "labels": ["L"], "properties": {"k": 1}}], "rdf_labels"),
        ("write_edges", [{"source": "a", "predicate": "P", "target": "b"}], "INSERT INTO Graph_KG.rdf_edges ("),
    ],
)
def test_store_writers_raise_and_roll_back_instead_of_counting_a_failed_row(method, arg, needle):
    store, cursor = _store()
    log = _fail_on(cursor, needle, IRIS_415)
    with pytest.raises(GraphWriteError):
        getattr(store, method)(arg)
    assert log[0] == "START TRANSACTION"
    assert "ROLLBACK" in log and "COMMIT" not in log
    store.conn.commit.assert_not_called()


@pytest.mark.parametrize("method,arg", [("write_nodes", [{"id": "n1", "labels": ["L"]}]),
                                        ("write_edges", [{"source": "a", "predicate": "P", "target": "b"}])])
def test_store_writers_still_skip_duplicates(method, arg):
    store, cursor = _store()
    log = _fail_on(cursor, "INSERT", IRIS_119)
    result = getattr(store, method)(arg)
    assert result.rows == [[1]]
    assert log[0] == "START TRANSACTION" and log[-1] == "COMMIT"
