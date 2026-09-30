"""`delete_node` and `bulk_delete_nodes` go through the store's graph-scoped delete.

4.1.0 ran its own per-table DELETEs: `kg_NodeEmbeddings` only (a named graph's
vectors live in `kg_emb_<hash>`, whose FK then failed the node-row delete), one
autocommit per statement (so the failure left the node half-deleted), and
`except Exception: return False`. The store's `delete_nodes(ids, graph=)` is one
`Graph.KG.Eraser.EraseNodeIds` transaction per graph and covers every vector
table. These tests pin the delegation; `tests/integration/test_delete_node_named_graph.py`
pins the rows actually left.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.nodes_edges import NodesEdgesMixin
from iris_vector_graph.result import IVGResult


class FakeStore:
    def __init__(self, present=None, fail_for=(), reifiers=None):
        # {graph: set(ids)}
        self.present = {g: set(ids) for g, ids in (present or {}).items()}
        # {node id: [reifier ids of its edges]}
        self.reifiers = dict(reifiers or {})
        self.fail_for = set(fail_for)
        self.calls = []

    def delete_nodes(self, node_ids, *, graph=None):
        self.calls.append((graph, list(node_ids)))
        if graph in self.fail_for:
            raise RuntimeError(f"ERROR #5002: erase failed in {graph!r}")
        held = self.present.get(graph, set())
        gone = [n for n in node_ids if n in held]
        held.difference_update(gone)
        return IVGResult(columns=["deleted"], rows=[[len(gone)]])


def _engine(store):
    eng = NodesEdgesMixin.__new__(NodesEdgesMixin)
    eng._store = store
    eng._nkg_dirty = False
    eng._ledger_guard = None
    eng.conn = MagicMock()
    cursor = eng.conn.cursor.return_value

    def execute(sql, params=()):
        ids = set(params)
        if "rdf_reifications" in sql:
            cursor._rows = [(r,) for n in params[1:] for r in store.reifiers.get(n, [])]
            return
        cursor._rows = [(g, n) for g, held in store.present.items() for n in held & ids]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: cursor._rows
    eng._t = lambda name: f"Graph_KG.{name}"
    return eng


class TestDeleteNode:
    def test_a_named_graph_is_passed_to_the_store(self):
        store = FakeStore({"g1": {"n"}})
        assert _engine(store).delete_node("n", graph="g1") is True
        assert store.calls == [("g1", ["n"])]

    def test_none_is_the_default_graph(self):
        store = FakeStore({"": {"n"}, "g1": {"n"}})
        assert _engine(store).delete_node("n", graph=None) is True
        assert store.calls == [("", ["n"])]
        assert store.present["g1"] == {"n"}

    def test_no_graph_argument_reaches_every_graph_holding_the_node(self):
        store = FakeStore({"": {"n"}, "g1": {"n"}, "g2": {"other"}})
        assert _engine(store).delete_node("n") is True
        assert sorted(g for g, _ in store.calls) == ["", "g1"]

    def test_a_missing_node_is_false(self):
        store = FakeStore({"g1": {"other"}})
        assert _engine(store).delete_node("n", graph="g1") is False

    def test_a_failure_raises_instead_of_returning_false(self):
        store = FakeStore({"g1": {"n"}}, fail_for={"g1"})
        with pytest.raises(RuntimeError, match="erase failed"):
            _engine(store).delete_node("n", graph="g1")

    def test_an_invalid_graph_name_fails_before_any_delete(self):
        store = FakeStore()
        with pytest.raises(ValueError):
            _engine(store).delete_node("n", graph="bad\x01name")
        assert store.calls == []


class TestReifiersGoWithTheirEdges:
    """A reifier names one edge (`pk_reifications`), so it is dead once the edge is.

    4.1.0's `delete_node` deleted the reifier nodes of the node's edges; the Eraser
    removes only the `rdf_reifications` rows, which left each reifier as an orphan
    node with its `Reification` label (`test_reification_e2e` T017a).
    """

    def test_delete_node_deletes_the_reifiers_of_its_edges(self):
        store = FakeStore({"": {"n", "reif:1"}}, reifiers={"n": ["reif:1"]})
        assert _engine(store).delete_node("n") is True
        assert store.present[""] == set()

    def test_the_node_goes_first_and_the_reifiers_after(self):
        store = FakeStore({"": {"n", "reif:1"}}, reifiers={"n": ["reif:1"]})
        _engine(store).delete_node("n", graph=None)
        assert store.calls == [("", ["n"]), ("", ["reif:1"])]

    def test_reifiers_do_not_count_as_deleted_nodes(self):
        store = FakeStore({"": {"a", "reif:1"}}, reifiers={"a": ["reif:1"]})
        result = _engine(store).bulk_delete_nodes(["a"])
        assert (result.deleted, result.failed) == (1, 0)
        assert store.present[""] == set()

    def test_a_reifier_in_another_graph_is_deleted_there(self):
        store = FakeStore({"g1": {"n"}, "": {"reif:1"}}, reifiers={"n": ["reif:1"]})
        _engine(store).delete_node("n", graph="g1")
        assert store.present == {"g1": set(), "": set()}

    def test_no_reifiers_means_no_extra_call(self):
        store = FakeStore({"g1": {"n"}})
        _engine(store).delete_node("n", graph="g1")
        assert store.calls == [("g1", ["n"])]


class TestBulkDeleteNodes:
    def test_ids_are_deleted_in_every_graph_holding_them(self):
        store = FakeStore({"": {"a"}, "g1": {"b", "c"}})
        result = _engine(store).bulk_delete_nodes(["a", "b", "c", "absent"])
        assert (result.deleted, result.failed) == (3, 0)
        assert store.present == {"": set(), "g1": set()}

    def test_a_graph_argument_scopes_the_delete(self):
        store = FakeStore({"": {"a"}, "g1": {"a"}})
        result = _engine(store).bulk_delete_nodes(["a"], graph="g1")
        assert (result.deleted, result.failed) == (1, 0)
        assert store.present[""] == {"a"}

    def test_batches_respect_batch_size(self):
        store = FakeStore({"g1": {"a", "b", "c"}})
        _engine(store).bulk_delete_nodes(["a", "b", "c"], batch_size=2, graph="g1")
        assert [ids for _, ids in store.calls] == [["a", "b"], ["c"]]

    def test_a_failed_batch_is_counted_and_the_rest_still_run(self):
        # Each store call is one transaction, so a failed batch deleted nothing.
        store = FakeStore({"g1": {"a"}, "g2": {"b"}}, fail_for={"g1"})
        result = _engine(store).bulk_delete_nodes(["a", "b"])
        assert (result.deleted, result.failed) == (1, 1)
        assert store.present["g1"] == {"a"}

    def test_empty_input_touches_nothing(self):
        store = FakeStore()
        result = _engine(store).bulk_delete_nodes([])
        assert (result.deleted, result.failed) == (0, 0)
        assert store.calls == []
