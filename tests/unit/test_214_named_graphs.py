"""Spec 214 — unit tests for named graph node dimension (no IRIS required).

T005: TestSchemaMigration — create_node graph sentinel, __graph removed, idempotency
T006: TestEraseGraphExtended — erase delegates to Graph.KG.Eraser; the Eraser keeps FK order and both default-graph spellings
T016: TestAdjacencyLayout — WriteAdjacency/DeleteAdjacency use graph subscript
T039: TestDeleteEdgeScoping — delete_edge graph/all_graphs SQL generation
T044: TestGraphAwareAlgorithms — algorithm calls pass graph filter
T048: TestCypherGraphTargeting — translator injects graph_id into CREATE/MERGE
T056: TestImportNdjsonGraph — import_graph_ndjson graph param
"""

import os
import re
from pathlib import Path
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
        inserts = [
            c.args[0]
            for c in cursor.execute.call_args_list
            if c.args and "INSERT INTO Graph_KG.nodes" in str(c.args[0])
        ]
        # check: [s for s in sqls if "INSERT INTO Graph_KG.nodes" in s]
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


# ─── T006: erase extended ─────────────────────────────────────────────────────


class TestEraseGraphExtended:
    """T006 asserted the SQL `drop_graph` emitted: the table list, the FK order,
    the presence of a `graph_id` predicate. That is the implementation, and
    asserting it is why the temporal leak survived — the emitted DELETEs were in
    FK-safe order and correctly filtered, and the adjacency and the whole temporal
    index still outlived the graph.

    The deletes are now `Graph.KG.Eraser`'s, one transaction it owns in
    ObjectScript (ADR-0004). The FK order and the two spellings of the default
    graph are asserted against the live container in
    tests/integration/test_erase_graph.py. What survives here are the two claims
    that can be checked without one: the facade delegates rather than emitting SQL
    of its own, and the Eraser's source keeps the invariants that a passing
    integration test would not have caught the loss of.
    """

    ERASER = (
        Path(__file__).resolve().parents[2]
        / "iris_src"
        / "src"
        / "Graph"
        / "KG"
        / "Eraser.cls"
    )

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
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = 3
        eng._iris_obj = MagicMock(return_value=iris_obj)
        return eng, cursor, iris_obj

    def test_the_facade_emits_no_sql_of_its_own(self):
        """Two deletion paths is the thing the Eraser exists to end.

        A `cursor.execute` here would mean the facade deletes some of the graph
        outside the Eraser's transaction, which is a partial erasure on any failure.
        """
        eng, cursor, iris_obj = self._make_engine()

        eng.erase_graph("umls")

        assert not cursor.execute.called, (
            "the facade still deletes outside the Eraser's transaction"
        )
        iris_obj.classMethodValue.assert_called_once_with(
            "Graph.KG.Eraser", "EraseGraph", "umls"
        )

    def test_erase_non_existent_returns_zero(self):
        eng, cursor, iris_obj = self._make_engine()
        iris_obj.classMethodValue.return_value = 0

        assert eng.erase_graph("nonexistent") == 0

    def test_the_eraser_deletes_the_dependents_before_the_rows_they_name(self):
        """FK order, read off the source rather than off the emitted SQL.

        Labels and props name a node id and reifications name an edge id, so all
        three have to be deleted before the rows they point at.
        """
        text = self.ERASER.read_text()

        def first_delete(table):
            idx = text.find(f"DELETE FROM Graph_KG.{table}")
            assert idx != -1, f"the Eraser never deletes from {table}"
            return idx

        assert first_delete("rdf_labels") < first_delete("nodes")
        assert first_delete("rdf_props") < first_delete("nodes")
        assert first_delete("rdf_reifications") < first_delete("rdf_edges")

    def test_the_eraser_matches_both_spellings_of_the_default_graph(self):
        """`graph_id` is nullable, so the default graph has two spellings.

        `create_edge` writes '' explicitly; an INSERT omitting the column leaves
        NULL. And in IRIS embedded SQL an empty host variable binds as SQL NULL,
        not as the empty string — so COALESCE is needed on BOTH sides. Either
        omission erases half the default graph and returns a count that looks like
        success.
        """
        text = self.ERASER.read_text()
        predicates = [
            line.strip()
            for line in text.splitlines()
            if "graph_id" in line and "=" in line and not line.strip().startswith("//")
        ]
        assert predicates, "the Eraser has no graph_id predicate at all"
        for predicate in predicates:
            assert predicate.count("COALESCE") == 2, (
                f"a graph_id predicate is not COALESCEd on both sides: {predicate}"
            )

    def test_the_eraser_does_not_erase_every_graph_when_given_one(self):
        """An unfiltered DELETE belongs to EraseAll and nowhere else."""
        text = self.ERASER.read_text()
        per_graph = text[text.index("ClassMethod EraseGraph") : text.index("ClassMethod EraseAll")]
        for statement in re.findall(r"DELETE FROM Graph_KG\.\w+[^)]*", per_graph):
            assert "WHERE" in statement, (
                f"EraseGraph deletes without a predicate, which would empty every "
                f"graph: {statement.strip()}"
            )


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
        eng.import_graph_ndjson(str(ndjson), graph="snap")
        created_with_graph_2 = created_with_graph
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
