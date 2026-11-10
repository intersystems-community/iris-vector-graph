"""4.6.0 R203: a write IRIS refuses raises GraphWriteError, never an answer a caller reads as "nothing to do".

`create_node` and `delete_edge` answered `False` on SQLCODE -99 (walkthrough 1 W6), the answer they give for a node
already there and an edge that is not. The same was true of `set_edge_weight` (False = no row), `reify_edge`
(None = no edge), `delete_reification` (False), `create_edge_temporal` (False), `bulk_create_edges_temporal` (a count
of 0) and `enqueue_for_embedding` (0). Each mock below refuses the statement or the class method that writes, so the
branch under test is the refusal; the duplicate and missing-row answers keep their own cases. Live proof:
tests/e2e/test_460_r203_refused_writes_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

REFUSED = ("[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]")
PROTECT = '<PROTECT>WriteStores+10^Graph.KG.TemporalIndex.1 ^KG("tlast",0),/db/'


def _engine(refuse=("INSERT", "UPDATE", "DELETE"), rows=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = rows if rows is not None else [(7, None)]
    log = []

    def execute(sql, *a, **k):
        log.append(sql)
        if str(sql).lstrip().upper().startswith(refuse):
            raise RuntimeError(REFUSED)

    cursor.execute.side_effect = execute
    cursor.executemany.side_effect = execute
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._adjacency_indexed_cached = True
    return eng, conn, cursor, log


def _assert_refusal(raised, operation, sqlcode=-99):
    assert raised.value.operation == operation
    assert raised.value.sqlcode == sqlcode
    assert f"{operation} failed" in str(raised.value) and "nothing written" in str(raised.value)


def test_create_node_raises_on_a_refused_insert_and_rolls_back():
    eng, conn, cursor, log = _engine()
    with pytest.raises(GraphWriteError) as raised:
        eng.create_node("z", labels=["P"])
    _assert_refusal(raised, "create_node")
    assert "ROLLBACK" in log and "COMMIT" not in log


def test_create_node_still_answers_false_for_a_node_already_there():
    eng, conn, cursor, log = _engine(refuse=())

    def execute(sql, *a, **k):
        log.append(sql)
        if str(sql).startswith("INSERT INTO") and "nodes" in sql:
            raise RuntimeError("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check>]")

    cursor.execute.side_effect = execute
    assert eng.create_node("a") is False


def test_delete_edge_raises_on_a_refused_delete():
    eng, conn, cursor, log = _engine()
    with pytest.raises(GraphWriteError) as raised:
        eng.delete_edge("a", "KNOWS", "b")
    _assert_refusal(raised, "delete_edge")
    # 4.6.3: a transaction of its own (the driver autocommits, so conn.rollback() took nothing back)
    assert "ROLLBACK" in log and "COMMIT" not in log
    conn.commit.assert_not_called()


def test_delete_edge_of_no_row_still_answers_true():
    eng, conn, cursor, log = _engine(refuse=())
    assert eng.delete_edge("a", "KNOWS", "nope") is True


def test_set_edge_weight_raises_on_a_refused_update():
    eng, conn, cursor, log = _engine()
    with pytest.raises(GraphWriteError) as raised:
        eng.set_edge_weight("a", "KNOWS", "b", 2.0)
    _assert_refusal(raised, "set_edge_weight")
    assert "ROLLBACK" in log and "COMMIT" not in log


def test_set_edge_weight_of_no_row_still_answers_false():
    eng, conn, cursor, log = _engine(rows=[])
    assert eng.set_edge_weight("a", "KNOWS", "nope", 2.0) is False


def test_reify_edge_raises_on_a_refused_insert():
    eng, conn, cursor, log = _engine()
    with pytest.raises(GraphWriteError) as raised:
        eng.reify_edge(7, "r9")
    _assert_refusal(raised, "reify_edge")


def _modelled(cursor, conn, refuse):
    """A tests/unit/_txn_model.TxnModel on the fake, which also refuses (REFUSED) a statement `refuse(sql)` names."""
    from tests.unit._txn_model import TxnModel

    model = TxnModel(cursor, conn)
    record = cursor.execute.side_effect

    def execute(sql, *a, **k):
        if refuse(str(sql)):
            raise RuntimeError(REFUSED)
        return record(sql, *a, **k)

    cursor.execute.side_effect = execute
    cursor.executemany.side_effect = lambda sql, rows: [execute(sql, r) for r in rows]
    return model


def test_reify_edge_takes_back_the_node_it_created_when_a_later_insert_is_refused():
    """A refused link leaves nothing, its reifier node included. 4.7.0: create_node's transaction is nested in the
    call's own, and the refusal takes that back (it committed on its own, and a delete_node took it back after).
    Here IVG's transactions are tests/unit/conftest.py's plain START TRANSACTION / COMMIT / ROLLBACK, which do not
    nest, so what this shows is the refusal and that no delete_node runs; the node gone is
    tests/e2e/test_470_caller_txn_3b_e2e.py::test_a_refused_reify_edge_leaves_no_reifier_node."""
    eng, conn, cursor, log = _engine(refuse=())
    _modelled(cursor, conn, lambda sql: "rdf_reifications" in sql and sql.startswith("INSERT"))
    eng.delete_node = MagicMock(return_value=True)
    with pytest.raises(GraphWriteError) as raised:
        eng.reify_edge(7, "r9")
    _assert_refusal(raised, "reify_edge")
    eng.delete_node.assert_not_called()
    conn.rollback.assert_not_called()


def test_reify_edge_of_an_existing_reifier_node_does_not_delete_it():
    """A reifier node that was there before the call is not this call's to take back."""
    eng, conn, cursor, log = _engine(refuse=())

    def execute(sql, *a, **k):
        if str(sql).startswith("INSERT INTO") and "nodes" in str(sql):
            raise RuntimeError("[SQLCODE: <-119>:<UNIQUE or PRIMARY KEY constraint failed uniqueness check>]")
        if "rdf_reifications" in str(sql) and str(sql).startswith("INSERT"):
            raise RuntimeError(REFUSED)

    cursor.execute.side_effect = execute
    eng.delete_node = MagicMock()
    with pytest.raises(GraphWriteError):
        eng.reify_edge(7, "r9")
    eng.delete_node.assert_not_called()


def test_reify_edge_of_a_missing_edge_still_answers_none():
    eng, conn, cursor, log = _engine()
    cursor.fetchone.return_value = None
    assert eng.reify_edge(7, "r9") is None


def test_delete_reification_raises_on_a_refused_delete():
    eng, conn, cursor, log = _engine(refuse=())
    model = _modelled(cursor, conn, lambda sql: sql.startswith("DELETE FROM") and "rdf_labels" in sql)
    with pytest.raises(GraphWriteError) as raised:
        eng.delete_reification("r0")
    _assert_refusal(raised, "delete_reification")
    # the two deletes before the refused one are taken back with it (4.7.0: IVG's own transaction, not
    # conn.rollback(), which took a caller's work too)
    assert model.committed == [] and model.pending == [], model.committed


def test_create_edge_temporal_raises_when_the_temporal_write_is_refused():
    eng, conn, cursor, log = _engine()
    eng._store = MagicMock()
    eng._store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error=PROTECT)
    with pytest.raises(GraphWriteError) as raised:
        eng.create_edge_temporal("a", "SEEN", "b", timestamp=1000)
    _assert_refusal(raised, "create_edge_temporal", sqlcode=None)
    assert "<PROTECT>" in str(raised.value)


def test_bulk_create_edges_temporal_raises_when_the_batch_is_refused():
    eng, conn, cursor, log = _engine()
    eng._store = MagicMock()
    eng._store.bulk_write_temporal_edges.return_value = IVGResult(columns=[], rows=[], error=PROTECT)
    with pytest.raises(GraphWriteError) as raised:
        eng.bulk_create_edges_temporal([{"s": "a", "p": "SEEN", "o": "b", "ts": 1000}])
    _assert_refusal(raised, "bulk_create_edges_temporal", sqlcode=None)


def test_enqueue_for_embedding_raises_when_the_queue_write_is_refused(monkeypatch):
    eng, conn, cursor, log = _engine()
    monkeypatch.setattr(eng, "enforce_embedding_identity", lambda *a, **k: None)
    import iris_vector_graph.schema as schema_mod

    def refuse(_conn, cls, method, *args):
        assert (cls, method) == ("Graph.KG.EmbedQueue", "BulkEnqueue")
        raise RuntimeError('<PROTECT>Enqueue+3^Graph.KG.EmbedQueue.1 ^EmbedQueue(1),/db/')

    monkeypatch.setattr(schema_mod, "_call_classmethod", refuse)
    with pytest.raises(GraphWriteError) as raised:
        eng.enqueue_for_embedding(["a"])
    _assert_refusal(raised, "enqueue_for_embedding", sqlcode=None)


def test_enqueue_for_embedding_without_the_queue_class_still_answers_zero(monkeypatch):
    """The documented degrade: a namespace without Graph.KG.EmbedQueue queues nothing and says so."""
    eng, conn, cursor, log = _engine()
    monkeypatch.setattr(eng, "enforce_embedding_identity", lambda *a, **k: None)
    import iris_vector_graph.schema as schema_mod

    def absent(_conn, cls, method, *args):
        raise RuntimeError("<CLASS DOES NOT EXIST> *Graph.KG.EmbedQueue")

    monkeypatch.setattr(schema_mod, "_call_classmethod", absent)
    assert eng.enqueue_for_embedding(["a"]) == 0
