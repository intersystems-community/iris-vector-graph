"""Spec-221 US-3: the batch path means what the single path means.

`upsert=True` reached `InsertEdge` as `mode="update"` after spec-221, but `BulkInsert`
was never in scope — the same flag still skipped there, which is the behaviour the
spec was filed against. And `bulk_create_edges_temporal` took a `mode` parameter it
never passed on: in the interface, reaching nothing.

These pin the Python half of the thread — engine → store → classmethod arguments —
without a container. The ObjectScript half is the live gate in
`tests/integration/test_221_upsert_weight_e2e.py`, because what mode *means* is a
property of the globals, not of the call.
"""

import json
from unittest.mock import MagicMock

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

# _call_classmethod(class, method, graphId, batchJSON, upsert, mode)
_ARG_UPSERT = 4
_ARG_MODE = 5


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = MagicMock()
    e._schema_prefix = "Graph_KG"
    e._in_bulk_load = False
    store = MagicMock()
    result = MagicMock()
    result.rows = [[1]]
    store.bulk_write_temporal_edges.return_value = result
    e._store = store
    return e, store


def _store(classmethod_return="1"):
    s = IRISGraphStore.__new__(IRISGraphStore)
    s._call_classmethod = MagicMock(return_value=classmethod_return)
    return s


_EDGES = [{"s": "a", "p": "P", "o": "b", "ts": 1000, "w": 0.9}]
_STORE_EDGES = [
    {"source": "a", "predicate": "P", "target": "b", "timestamp": 1000, "weight": 0.9}
]


class TestModeReachesTheStore:
    def test_an_explicit_mode_is_passed_on(self):
        engine, store = _engine()
        engine.bulk_create_edges_temporal(_EDGES, mode="update")
        assert store.bulk_write_temporal_edges.call_args.kwargs.get("mode") == "update"

    def test_skip_is_passed_on_too(self):
        """The old batch behaviour stays available, spelled as what it is."""
        engine, store = _engine()
        engine.bulk_create_edges_temporal(_EDGES, mode="skip")
        assert store.bulk_write_temporal_edges.call_args.kwargs.get("mode") == "skip"

    def test_no_mode_asks_for_nothing(self):
        engine, store = _engine()
        engine.bulk_create_edges_temporal(_EDGES)
        assert store.bulk_write_temporal_edges.call_args.kwargs.get("mode", "") == ""

    def test_upsert_still_reaches_the_store(self):
        engine, store = _engine()
        engine.bulk_create_edges_temporal(_EDGES, upsert=True)
        assert store.bulk_write_temporal_edges.call_args.kwargs.get("upsert") is True


class TestModeReachesBulkInsert:
    def test_mode_is_the_sixth_argument(self):
        store = _store()
        store.bulk_write_temporal_edges(_STORE_EDGES, mode="update")
        assert store._call_classmethod.call_args[0][_ARG_MODE] == "update"

    def test_no_mode_sends_an_empty_string_so_upsert_decides(self):
        store = _store()
        store.bulk_write_temporal_edges(_STORE_EDGES, upsert=True)
        args = store._call_classmethod.call_args[0]
        assert args[_ARG_MODE] == ""
        assert args[_ARG_UPSERT] == "1"

    def test_the_batch_itself_is_unchanged_by_a_mode(self):
        """Mode is per call, not per edge: it must not leak into the items."""
        store = _store()
        store.bulk_write_temporal_edges(_STORE_EDGES, mode="update")
        items = json.loads(store._call_classmethod.call_args[0][3])
        assert items == [{"s": "a", "p": "P", "o": "b", "ts": 1000, "w": 0.9}]


class TestTheFallbackPathCarriesModeToo:
    """When BulkInsert raises, the store re-writes edge by edge.

    A fallback that dropped the mode would turn an update into an insert on exactly
    the path nobody watches, and report the same count either way.
    """

    def test_write_temporal_edge_gets_the_mode(self):
        store = _store()
        store._call_classmethod = MagicMock(side_effect=RuntimeError("no bulk"))
        store.write_temporal_edge = MagicMock(return_value=MagicMock(error=None))

        store.bulk_write_temporal_edges(_STORE_EDGES, mode="update")

        call = store.write_temporal_edge.call_args
        assert call.kwargs.get("mode") == "update"

    def test_the_graph_goes_with_it(self):
        store = _store()
        store._call_classmethod = MagicMock(side_effect=RuntimeError("no bulk"))
        store.write_temporal_edge = MagicMock(return_value=MagicMock(error=None))

        store.bulk_write_temporal_edges(_STORE_EDGES, graph="acme", mode="update")

        assert store.write_temporal_edge.call_args.kwargs.get("graph") == "acme"
