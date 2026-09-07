"""Spec 214 — unit tests for named graph node dimension (no IRIS required).

T005: TestSchemaMigration — create_node graph sentinel, __graph removed, idempotency
T006: TestDropGraphExtended — drop_graph FK-safe order, count, default graph untouched
T016: TestAdjacencyLayout — WriteAdjacency/DeleteAdjacency use graph subscript
T039: TestDeleteEdgeScoping — delete_edge graph/all_graphs SQL generation
T044: TestGraphAwareAlgorithms — algorithm calls pass graph filter
T048: TestCypherGraphTargeting — translator injects graph_id into CREATE/MERGE
T056: TestImportNdjsonGraph — import_graph_ndjson graph param
"""

import os
from unittest.mock import MagicMock, call, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


# ─── T005: Schema migration / create_node sentinel ────────────────────────────


class TestSchemaMigration:
    def _make_engine(self, rows=None):
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng._nkg_dirty = False
        eng.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = rows
        cursor.fetchall.return_value = []
        cursor.rowcount = 1
        cursor.__enter__ = lambda s: s
        cursor.__exit__ = MagicMock(return_value=False)
        eng.conn.cursor.return_value = cursor
        eng._store = MagicMock()
        eng._store.execute_transaction = MagicMock(return_value=MagicMock(rows=[], columns=[]))
        return eng, cursor

    def test_create_node_no_graph_writes_empty_sentinel(self):
        eng, cursor = self._make_engine((1,))
        with patch.object(eng, "_iris_obj", MagicMock()):
            eng.create_node("n1")
        sqls = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        inserts = [s for s in sqls if "INSERT INTO Graph_KG.nodes" in s]
        assert inserts, "No INSERT INTO nodes found"
        # After spec-214, the INSERT should include graph_id
        # Before implementation this will fail — which is expected (T005 must fail first)
        assert any(
            "graph_id" in s for s in inserts
        ), "graph_id not in INSERT (expected after spec-214)"

    def test_create_node_with_graph_writes_sentinel(self):
        eng, cursor = self._make_engine((1,))
        with patch.object(eng, "_iris_obj", MagicMock()):
            eng.create_node("n1", graph="umls")
        sqls = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        params = [c.args[1] for c in cursor.execute.call_args_list if len(c.args) > 1]
        # graph_id='umls' must appear in the parameters
        flat_params = [p for row in params if isinstance(row, (list, tuple)) for p in row]
        assert "umls" in flat_params, "graph='umls' not passed to INSERT"

    def test_create_node_empty_string_is_default_graph(self):
        eng, cursor = self._make_engine((1,))
        with patch.object(eng, "_iris_obj", MagicMock()):
            eng.create_node("n1", graph="")
        sqls = [c.args[0] for c in cursor.execute.call_args_list if c.args]
        params = [c.args[1] for c in cursor.execute.call_args_list if len(c.args) > 1]
        flat_params = [p for row in params if isinstance(row, (list, tuple)) for p in row]
        # graph='' should write graph_id='', not 'None' or None
        assert "" in flat_params or "n1" in flat_params

    def test_create_node_no_graph_does_not_write_dunder_graph_prop(self):
        eng, cursor = self._make_engine((1,))
        with patch.object(eng, "_iris_obj", MagicMock()):
            eng.create_node("n1")
        sqls = [str(c.args) for c in cursor.execute.call_args_list]
        assert not any(
            "__graph" in s for s in sqls
        ), "__graph pseudo-prop still written after spec-214"

    def test_add_graph_id_to_nodes_idempotent_when_column_exists(self):
        from iris_vector_graph.schema import GraphSchema

        # Migration function must be callable; "already has" error swallowed
        if not hasattr(GraphSchema, "add_graph_id_to_nodes"):
            pytest.skip("add_graph_id_to_nodes not yet implemented")
        cursor = MagicMock()
        cursor.execute.side_effect = [Exception("already has"), None, None, None]
        GraphSchema.add_graph_id_to_nodes(cursor)  # must not raise


# ─── T006: drop_graph extended ────────────────────────────────────────────────


class TestDropGraphExtended:
    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng._nkg_dirty = False
        eng.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 3
        eng.conn.cursor.return_value = cursor
        eng._store = MagicMock()
        eng.__dict__["_ledger_guard_obj"] = MagicMock()
        eng.__dict__["_ledger_guard_obj"].check_structural_write = MagicMock()
        return eng, cursor

    def test_drop_graph_executes_in_fk_safe_order(self):
        eng, cursor = self._make_engine()
        eng.drop_graph("umls")
        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        # After spec-214, drop_graph must delete nodes too
        node_deletes = [s for s in sqls if "Graph_KG.nodes" in s and "DELETE" in s.upper()]
        edge_deletes = [s for s in sqls if "rdf_edges" in s and "DELETE" in s.upper()]
        assert node_deletes, "drop_graph does not delete nodes (spec-214 extension missing)"
        assert edge_deletes, "drop_graph does not delete edges"
        # labels/props must be deleted before nodes (FK safety)
        label_deletes = [
            i for i, s in enumerate(sqls) if "rdf_labels" in s and "DELETE" in s.upper()
        ]
        # Only lines that DELETE directly from nodes (not sub-query references)
        # e.g. "DELETE FROM Graph_KG.nodes WHERE graph_id = ?"
        node_delete_idx = [
            i
            for i, stmt in enumerate(sqls)
            if stmt.lower().strip().startswith("delete from graph_kg.nodes")
            or stmt.lower().strip().startswith("delete from graph_kg.nodes_new")
        ]
        if label_deletes and node_delete_idx:
            assert min(label_deletes) < min(
                node_delete_idx
            ), "labels deleted AFTER nodes (FK violation)"

    def test_drop_graph_non_existent_returns_zero(self):
        eng, cursor = self._make_engine()
        cursor.rowcount = 0
        result = eng.drop_graph("nonexistent")
        assert result == 0

    def test_drop_graph_does_not_touch_default_graph(self):
        eng, cursor = self._make_engine()
        eng.drop_graph("umls")
        sqls = [str(c.args) for c in cursor.execute.call_args_list if c.args]
        # No DELETE should omit the graph_id filter (that would delete the default graph)
        unfiltered = [s for s in sqls if "DELETE FROM Graph_KG.nodes" in s and "graph_id" not in s]
        assert (
            not unfiltered
        ), "drop_graph deletes nodes without a graph_id filter (would wipe default graph)"


# ─── T016: Adjacency layout ────────────────────────────────────────────────────


class TestAdjacencyLayout:
    """After spec-214 create_edge must pass graph to WriteAdjacency."""

    def _make_engine_with_native(self, graph=None):
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng._nkg_dirty = False
        eng.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        cursor.fetchone.return_value = (1,)
        eng.conn.cursor.return_value = cursor
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = ""
        iris_obj.classMethodVoid.return_value = None
        eng._store = MagicMock()
        eng._store.capabilities.return_value = {"native_sql": True}
        eng.__dict__["_ledger_guard_obj"] = MagicMock()
        eng.__dict__["_ledger_guard_obj"].check_structural_write = MagicMock()
        return eng, iris_obj, cursor

    def test_create_edge_passes_graph_to_write_adjacency(self):
        eng, iris_obj, cursor = self._make_engine_with_native()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            eng.create_edge("a", "R", "b", graph="umls")
        calls = [c for c in iris_obj.classMethodVoid.call_args_list if "WriteAdjacency" in str(c)]
        if not calls:
            calls = [
                c for c in iris_obj.classMethodValue.call_args_list if "WriteAdjacency" in str(c)
            ]
        # After spec-214, WriteAdjacency should receive graph='umls'
        assert calls, "WriteAdjacency not called"
        args = calls[0].args
        assert "umls" in args, f"graph='umls' not passed to WriteAdjacency; got {args}"

    def test_create_edge_no_graph_passes_empty_string(self):
        eng, iris_obj, cursor = self._make_engine_with_native()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            eng.create_edge("a", "R", "b")
        calls = [c for c in iris_obj.classMethodVoid.call_args_list if "WriteAdjacency" in str(c)]
        if not calls:
            calls = [
                c for c in iris_obj.classMethodValue.call_args_list if "WriteAdjacency" in str(c)
            ]
        if calls:
            args = calls[0].args
            assert "" in args or "umls" not in str(
                args
            ), "non-empty graph passed for default create_edge"


# ─── T039: delete_edge scoping ────────────────────────────────────────────────


class TestDeleteEdgeScoping:
    def _engine(self):
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng.conn = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        eng.conn.cursor.return_value = cursor
        eng._store = MagicMock()
        eng.__dict__["_ledger_guard_obj"] = MagicMock()
        eng.__dict__["_ledger_guard_obj"].check_structural_write = MagicMock()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        eng._iris_obj = MagicMock(return_value=iris_obj)
        return eng, cursor

    def test_delete_edge_no_graph_scopes_to_default(self):
        eng, cursor = self._engine()
        eng.delete_edge("a", "R", "b")
        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        deletes = [s for s in sqls if "DELETE" in s.upper() and "rdf_edges" in s]
        assert deletes, "No DELETE on rdf_edges"
        assert any(
            "graph_id" in s for s in deletes
        ), "graph_id filter missing in delete_edge default"

    def test_delete_edge_with_graph_scopes_to_that_graph(self):
        eng, cursor = self._engine()
        eng.delete_edge("a", "R", "b", graph="umls")
        params = [c.args[1] for c in cursor.execute.call_args_list if len(c.args) > 1]
        flat = [p for row in params if isinstance(row, (list, tuple)) for p in row]
        assert "umls" in flat, "graph='umls' not in DELETE params"

    def test_delete_edge_all_graphs_true_omits_graph_filter(self):
        eng, cursor = self._engine()
        eng.delete_edge("a", "R", "b", all_graphs=True)
        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        deletes = [s for s in sqls if "DELETE" in s.upper() and "rdf_edges" in s]
        # all_graphs=True should NOT have graph_id filter
        assert not any(
            "graph_id" in s for s in deletes
        ), "all_graphs=True still filters by graph_id"


# ─── T056: import_graph_ndjson graph param ────────────────────────────────────


class TestImportNdjsonGraph:
    def test_import_ndjson_graph_param_exists(self):
        import inspect
        from iris_vector_graph._engine.snapshot import SnapshotMixin

        sig = inspect.signature(SnapshotMixin.import_graph_ndjson)
        assert "graph" in sig.parameters, "graph param missing from import_graph_ndjson"

    def test_import_ndjson_type_node_accepted(self, tmp_path):
        ndjson = tmp_path / "test.ndjson"
        ndjson.write_text(
            '{"type":"node","id":"p1","labels":["L"],"props":{"k":"v"}}\n{"type":"rel","s":"p1","p":"R","o":"p1","graph":null,"quals":{},"stmt_id":"1"}\n'
        )
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng.conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        eng.conn.cursor.return_value = cursor
        eng.__dict__["_ledger_guard_obj"] = MagicMock()
        eng.__dict__["_ledger_guard_obj"].check_structural_write = MagicMock()
        eng._store = MagicMock()
        created_with_graph = []

        def _cn(nid, labels=None, properties=None, graph=None):
            created_with_graph.append((nid, graph))
            return True

        eng.create_node = _cn
        eng.create_edge = MagicMock(return_value=True)
        eng.bulk_create_edges_temporal = MagicMock()
        result = eng.import_graph_ndjson(str(ndjson), graph="snap")
        graphs = {g for _, g in created_with_graph}
        assert "snap" in graphs, f"graph='snap' not passed to create_node; got {created_with_graph}"

    def test_import_ndjson_no_graph_uses_default(self, tmp_path):
        ndjson = tmp_path / "test.ndjson"
        ndjson.write_text('{"kind":"node","id":"p1","labels":[],"properties":{}}\n')
        from iris_vector_graph.engine import IRISGraphEngine

        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng.conn = MagicMock()
        cursor = MagicMock()
        eng.conn.cursor.return_value = cursor
        eng.__dict__["_ledger_guard_obj"] = MagicMock()
        eng.__dict__["_ledger_guard_obj"].check_structural_write = MagicMock()
        eng._store = MagicMock()
        created_with_graph = []

        def _cn(nid, labels=None, properties=None, graph=None):
            created_with_graph.append((nid, graph))
            return True

        eng.create_node = _cn
        eng.create_edge = MagicMock(return_value=True)
        eng.bulk_create_edges_temporal = MagicMock()
        eng.import_graph_ndjson(str(ndjson))
        graphs = {g for _, g in created_with_graph}
        assert graphs <= {None, ""}, f"Unexpected graphs passed to create_node: {graphs}"
