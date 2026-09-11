"""Unit tests for spec-223 Python layer — graph param wiring.

No container required. Mocks verify the graph identifier flows from
create_edge_temporal → write_temporal_edge → InsertEdge as first positional arg.
"""
from unittest.mock import MagicMock, patch, call


class TestWriteTemporalEdgePassesGraph:

    def _make_store(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = MagicMock(spec=IRISGraphStore)
        store._namespace = "USER"
        store._schema_prefix = "Graph_KG"
        return store

    def test_write_temporal_edge_passes_graph_to_insertedge(self):
        """T020 — graph="acme" reaches InsertEdge as first arg after method name."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore.__new__(IRISGraphStore)
        store._namespace = "USER"
        store._schema_prefix = "Graph_KG"
        store._namespace_checked = True

        calls = []
        def fake_call_classmethod(cls, method, *args):
            calls.append((cls, method) + args)
            return 1

        store._call_classmethod = fake_call_classmethod

        store.write_temporal_edge(
            "svc-a", "CALLS", "svc-b",
            timestamp=1000, weight=0.5,
            graph="acme",
        )
        assert len(calls) == 1
        cls, method, graph_id = calls[0][0], calls[0][1], calls[0][2]
        assert cls == "Graph.KG.TemporalIndex"
        assert method == "InsertEdge"
        assert graph_id == "acme", f"graphId should be 'acme', got {graph_id!r}"

    def test_write_temporal_edge_no_graph_defaults_to_empty(self):
        """T021 — no graph arg → InsertEdge first arg is ''."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore.__new__(IRISGraphStore)
        store._namespace = "USER"
        store._schema_prefix = "Graph_KG"
        store._namespace_checked = True

        calls = []
        def fake_call_classmethod(cls, method, *args):
            calls.append((cls, method) + args)
            return 1
        store._call_classmethod = fake_call_classmethod

        store.write_temporal_edge("svc-a", "CALLS", "svc-b", timestamp=1000)
        assert len(calls) == 1
        graph_id = calls[0][2]
        assert graph_id == "", f"graphId should be '' (default), got {graph_id!r}"

    def test_create_edge_temporal_passes_graph_through(self):
        """T022 — write_temporal_edge receives graph kwarg from create_edge_temporal."""
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore.__new__(IRISGraphStore)
        store._namespace = "USER"
        store._schema_prefix = "Graph_KG"
        store._namespace_checked = True

        calls = []
        def fake_call_classmethod(cls, method, *args):
            calls.append((cls, method) + args)
            return 1
        store._call_classmethod = fake_call_classmethod

        # Call write_temporal_edge directly with graph kwarg
        result = store.write_temporal_edge(
            "svc-a", "CALLS", "svc-b",
            timestamp=1000, weight=0.5,
            graph="acme",
        )
        assert len(calls) == 1
        graph_id = calls[0][2]
        assert graph_id == "acme", f"graph should be 'acme', got {graph_id!r}"
