"""`IRISGraphStore.delete_nodes` / `delete_edges` are graph-scoped and bounded.

Found migrating a 3.2.0-era install to 4.0.0. `delete_nodes(node_ids)` built one
`WHERE x IN (?,?,…)` per table with a parameter per id and no `graph_id` predicate:

* since spec 227 a node id is unique per `(graph_id, node_id)`, so the delete took
  the id out of every graph that held it;
* a 2000-id batch failed to prepare, and the edge statement (`s IN … OR o_id IN …`)
  doubled the parameter count;
* it returned `len(node_ids)`, whatever was actually removed.

Two paths are pinned here. When `Graph.KG.Eraser` is deployed the store hands each
chunk to `EraseNodeIds`, which owns the transaction and the `^KG` cleanup. Without
it the store runs chunked SQL, and every statement names the graph.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class _RecordingCursor:
    """Records every statement; answers `rowcount` from a callable per statement."""

    def __init__(self, rowcount_for):
        self.statements = []
        self._rowcount_for = rowcount_for
        self.rowcount = -1

    def execute(self, sql, params=None):
        params = list(params or [])
        self.statements.append((sql, params))
        self.rowcount = self._rowcount_for(sql, params)

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)

    def close(self):
        pass


def _sql_store(rowcount_for=lambda sql, params: 0):
    conn = MagicMock()
    cursor = _RecordingCursor(rowcount_for)
    conn.cursor.return_value = cursor
    store = IRISGraphStore(conn)
    # No Eraser in this namespace: the SQL path.
    store._eraser_methods = {"EraseNodeIds": False, "EraseEdges": False, "EraseNodes": False}
    return store, conn, cursor


def _placeholders(sql):
    return sql.count("?")


# ── SQL path ──────────────────────────────────────────────────────────────────


def test_sql_path_chunks_a_2500_id_delete():
    store, _, cursor = _sql_store()
    ids = [f"n{i}" for i in range(2500)]

    store.delete_nodes(ids, graph="A")

    assert cursor.statements, "delete_nodes issued no statement"
    worst = max(_placeholders(sql) for sql, _ in cursor.statements)
    # One chunk of ids plus the graph binding(s); never the whole batch.
    assert worst <= IRISGraphStore._DELETE_CHUNK + 2, (
        f"a statement carried {worst} parameters; the batch was not chunked"
    )
    node_deletes = [p for s, p in cursor.statements if "DELETE FROM Graph_KG.nodes" in s]
    covered = [i for p in node_deletes for i in p if i in set(ids)]
    assert sorted(covered) == sorted(ids), "some ids were never deleted from nodes"


def test_sql_path_never_uses_or_across_both_endpoints():
    """`s IN (…) OR o_id IN (…)` doubled the parameters; each endpoint is its own statement."""
    store, _, cursor = _sql_store()
    store.delete_nodes([f"n{i}" for i in range(10)], graph="A")
    for sql, _ in cursor.statements:
        assert " OR o_id IN" not in sql


@pytest.mark.parametrize("graph, bound", [("A", "A"), (None, ""), ("", "")])
def test_sql_path_scopes_every_statement_to_the_graph(graph, bound):
    store, _, cursor = _sql_store()
    ids = [f"n{i}" for i in range(1200)]

    store.delete_nodes(ids, graph=graph)

    assert cursor.statements
    for sql, params in cursor.statements:
        assert "COALESCE(graph_id, '') = COALESCE(?, '')" in sql, (
            f"unscoped statement: {sql}"
        )
        assert bound in params, f"graph {bound!r} not bound in: {sql}"


def test_sql_path_returns_rows_actually_deleted():
    """Only the nodes that existed count; `len(node_ids)` was the old answer."""
    existing = {"n1", "n3"}

    def rowcount_for(sql, params):
        if "DELETE FROM Graph_KG.nodes" in sql:
            return sum(1 for p in params if p in existing)
        return 0

    store, _, _ = _sql_store(rowcount_for)
    result = store.delete_nodes(["n1", "n2", "n3", "n4"], graph="A")

    assert result.columns == ["deleted"]
    assert result.rows == [[2]]


def test_sql_path_sums_counts_across_chunks():
    def rowcount_for(sql, params):
        if "DELETE FROM Graph_KG.nodes" in sql:
            return len(params) - 1  # every id existed; minus the graph binding
        return 0

    store, _, _ = _sql_store(rowcount_for)
    result = store.delete_nodes([f"n{i}" for i in range(2100)])
    assert result.rows == [[2100]]


def test_duplicate_ids_are_deleted_once():
    store, _, cursor = _sql_store()
    store.delete_nodes(["a", "a", "b"], graph="A")
    node_deletes = [p for s, p in cursor.statements if "DELETE FROM Graph_KG.nodes" in s]
    assert [i for p in node_deletes for i in p if i in ("a", "b")] == ["a", "b"]


def test_graph_is_keyword_only():
    store, _, _ = _sql_store()
    with pytest.raises(TypeError):
        store.delete_nodes(["a"], "A")  # a positional graph would be a 3.2.0 surprise


def test_invalid_graph_is_refused_before_any_statement():
    store, _, cursor = _sql_store()
    with pytest.raises(ValueError):
        store.delete_nodes(["a"], graph="0")
    assert cursor.statements == []


def test_empty_id_list_touches_nothing():
    store, _, cursor = _sql_store()
    assert store.delete_nodes([], graph="A").rows == [[0]]
    assert cursor.statements == []


# ── ObjectScript path ─────────────────────────────────────────────────────────


def _os_store(answer=lambda graph, ids: len(ids)):
    conn = MagicMock()
    cursor = _RecordingCursor(lambda s, p: 0)
    conn.cursor.return_value = cursor
    store = IRISGraphStore(conn)
    store._eraser_methods = {"EraseNodeIds": True, "EraseEdges": True, "EraseNodes": True}
    calls = []

    def fake_call(cls, method, *args):
        calls.append((cls, method, args))
        if method == "EraseNodeIds":
            return answer(args[0], json.loads(args[1]))
        if method == "EraseEdges":
            return len(json.loads(args[1]))
        if method == "EraseNodes":
            return 7
        raise AssertionError(f"unexpected call {cls}.{method}")

    store._call_classmethod = fake_call
    return store, calls, cursor


def test_objectscript_path_chunks_and_scopes_every_call():
    store, calls, cursor = _os_store()
    ids = [f"n{i}" for i in range(2500)]

    result = store.delete_nodes(ids, graph="A")

    assert cursor.statements == [], "the Eraser path must not also run SQL deletes"
    assert all(c[:2] == ("Graph.KG.Eraser", "EraseNodeIds") for c in calls)
    assert len(calls) == 5
    for _, _, (graph, payload) in calls:
        assert graph == "A"
        assert len(json.loads(payload)) <= IRISGraphStore._DELETE_CHUNK
    assert result.rows == [[2500]]


def test_objectscript_path_default_graph_is_the_empty_name():
    store, calls, _ = _os_store()
    store.delete_nodes(["x"])
    assert calls[0][2][0] == ""


def test_objectscript_path_returns_what_the_eraser_reports():
    store, _, _ = _os_store(answer=lambda graph, ids: 1)
    assert store.delete_nodes(["a", "b", "c"], graph="A").rows == [[1]]


def test_objectscript_failure_propagates_rather_than_falling_back():
    """A half-run SQL fallback after a rolled-back erase is the drift this fixes."""
    store, _, cursor = _os_store()

    def boom(*a):
        raise RuntimeError("erase failed on rdf_edges with SQLCODE -124")

    store._call_classmethod = boom
    with pytest.raises(RuntimeError):
        store.delete_nodes(["a"], graph="A")
    assert cursor.statements == []


def test_the_ledger_guard_runs_first():
    store, calls, _ = _os_store()
    guard = MagicMock()
    guard.check_structural_write.side_effect = RuntimeError("strict")
    store._ledger_guard = guard
    with pytest.raises(RuntimeError):
        store.delete_nodes(["a"], graph="A")
    assert calls == []


# ── delete_edges ──────────────────────────────────────────────────────────────


def test_delete_edges_sql_path_is_graph_scoped_and_counts_rows():
    def rowcount_for(sql, params):
        if "DELETE FROM Graph_KG.rdf_edges" in sql:
            return 1 if params[0] == "a" else 0
        return 0

    store, _, cursor = _sql_store(rowcount_for)
    result = store.delete_edges([("a", "R", "b"), ("missing", "R", "b")], graph="A")

    edge_deletes = [(s, p) for s, p in cursor.statements if "DELETE FROM Graph_KG.rdf_edges" in s]
    assert edge_deletes
    for sql, params in edge_deletes:
        assert "COALESCE(graph_id, '') = COALESCE(?, '')" in sql
        assert "A" in params
    assert result.rows == [[1]]


def test_delete_edges_objectscript_path_is_graph_scoped():
    store, calls, _ = _os_store()
    edges = [("a", "R", f"b{i}") for i in range(1200)]
    result = store.delete_edges(edges, graph="B")
    assert [c[1] for c in calls] == ["EraseEdges"] * 3
    for _, _, (graph, payload) in calls:
        assert graph == "B"
        for e in json.loads(payload):
            assert set(e) == {"s", "p", "o"}
    assert result.rows == [[1200]]


# ── delete_nodes_by_prefix ────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["", None])
def test_prefix_erase_refuses_an_empty_prefix(prefix):
    store, calls, _ = _os_store()
    with pytest.raises(ValueError, match="erase_graph"):
        store.delete_nodes_by_prefix(prefix, graph="A")
    assert calls == []


def test_prefix_erase_calls_the_eraser_with_graph_and_prefix():
    store, calls, _ = _os_store()
    assert store.delete_nodes_by_prefix("tmp:", graph="A") == 7
    assert calls == [("Graph.KG.Eraser", "EraseNodes", ("A", "tmp:"))]


def test_prefix_erase_needs_the_eraser():
    store, _, _ = _sql_store()
    with pytest.raises(RuntimeError, match="EraseNodes"):
        store.delete_nodes_by_prefix("tmp:", graph="A")


def test_engine_exposes_prefix_erase():
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    store = MagicMock()
    store.delete_nodes_by_prefix.return_value = 3
    engine._store = store
    engine.__dict__["_ledger_guard_obj"] = MagicMock()  # permissive guard
    assert engine.delete_nodes_by_prefix("tmp:", graph="A") == 3
    store.delete_nodes_by_prefix.assert_called_once_with("tmp:", graph="A")


def test_protocol_declares_the_graph_keyword():
    import inspect

    from iris_vector_graph.store_protocol import GraphStore

    for name in ("delete_nodes", "delete_edges"):
        param = inspect.signature(getattr(GraphStore, name)).parameters["graph"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
