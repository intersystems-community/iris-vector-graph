"""R221: a Cypher write IRIS or IVG refuses raises ``GraphWriteError`` (an ``IVGError`` and a ``RuntimeError``),
after rolling the statement back.

Walkthrough 2 (W10): ``MATCH (p {node_id: 'hal'}) DELETE p`` on a node with relationships raised a bare
``Exception("ConstraintVerificationFailed: Cannot delete node with existing relationships. Use DETACH DELETE.")``,
not the ``RuntimeError`` USER_GUIDE "Errors" names. Any other refusal inside the transaction (a unique key, a value
too wide for its column) raised the driver's own exception type. The message text is kept: the TCK harness reads
``ConstraintVerificationFailed`` and ``Cannot delete node with existing relationships`` from it.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.errors import IVGError
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _store(execute):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.description = None
    cur.execute.side_effect = execute
    cur.fetchone.return_value = (2,)
    return IRISGraphStore(conn), conn


def test_a_delete_of_a_connected_node_raises_graph_write_error():
    store, conn = _store(lambda *a, **k: None)
    with pytest.raises(GraphWriteError) as exc:
        store.execute_transaction(
            ["__constraint_check_delete_connected__ SELECT COUNT(*) FROM rdf_edges", "DELETE FROM nodes"],
            [[], []],
        )
    assert isinstance(exc.value, RuntimeError) and isinstance(exc.value, IVGError)
    # its own words, first: the TCK harness reads the kind from a leading "ConstraintVerificationFailed:"
    assert str(exc.value).startswith("ConstraintVerificationFailed: Cannot delete node with existing relationships")
    assert "DETACH DELETE" in str(exc.value)
    # 4.6.4: IVG's own part is taken back on its cursor (_write_txn); the driver's rollback() would take a caller's
    # open transaction with it
    assert "ROLLBACK" in [c.args[0] for c in conn.cursor.return_value.execute.call_args_list]
    conn.rollback.assert_not_called()


def test_a_statement_iris_refuses_raises_graph_write_error_with_its_sqlcode():
    class DriverError(Exception):
        pass

    def execute(sql, *a):
        if sql.startswith("INSERT"):
            raise DriverError("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check>]")

    store, conn = _store(execute)
    with pytest.raises(GraphWriteError) as exc:
        store.execute_transaction(["INSERT INTO nodes (node_id) VALUES (?)"], [["a"]])
    assert exc.value.sqlcode == -119
    assert isinstance(exc.value.__cause__, DriverError)
    # 4.6.4: IVG's own part is taken back on its cursor (_write_txn); the driver's rollback() would take a caller's
    # open transaction with it
    assert "ROLLBACK" in [c.args[0] for c in conn.cursor.return_value.execute.call_args_list]
    conn.rollback.assert_not_called()


def test_a_dropped_connection_keeps_its_type():
    """Reconnect logic tells a dropped connection by its type; that is not a refusal."""

    def execute(sql, *a):
        if sql.startswith("INSERT"):
            raise BrokenPipeError("EPIPE")

    store, _ = _store(execute)
    with pytest.raises(BrokenPipeError):
        store.execute_transaction(["INSERT INTO nodes (node_id) VALUES (?)"], [["a"]])


def test_execute_cypher_passes_it_through(monkeypatch):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    eng = IRISGraphEngine(conn, store=MagicMock())
    eng._store.execute_transaction.side_effect = GraphWriteError(
        "execute_cypher", RuntimeError("ConstraintVerificationFailed: Cannot delete node with existing relationships.")
    )
    monkeypatch.setattr(eng, "_probe_if_idle", lambda: None, raising=True)
    eng.native_cypher = False
    with pytest.raises(RuntimeError, match="ConstraintVerificationFailed"):
        eng.execute_cypher("MATCH (p {node_id: 'hal'}) DELETE p")
