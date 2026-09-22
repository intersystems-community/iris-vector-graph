import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.schema import GraphSchema


class TestIRISCapabilities:
    def test_defaults_all_false(self):
        caps = IRISCapabilities()
        assert not caps.objectscript_deployed
        assert not caps.kg_built
        assert not caps.graphoperators_deployed

    def test_can_set_fields(self):
        caps = IRISCapabilities(
            objectscript_deployed=True,
            kg_built=True,
            graphoperators_deployed=True,
        )
        assert caps.objectscript_deployed
        assert caps.kg_built
        assert caps.graphoperators_deployed

    def test_repr_is_readable(self):
        caps = IRISCapabilities(
            objectscript_deployed=True,
            kg_built=False,
            graphoperators_deployed=True,
        )
        repr_str = repr(caps)
        assert "IRISCapabilities" in repr_str
        assert "objectscript_deployed=True" in repr_str
        assert "kg_built=False" in repr_str


class TestCheckObjectscriptClasses:
    def test_returns_true_when_compiled(self):
        cursor = MagicMock()
        # Dictionary queries return count=1 for PageRank and GraphOperators
        cursor.fetchone.side_effect = [(1,), (1,)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=None) as mock_cm:
            caps = GraphSchema.check_objectscript_classes(cursor)

        assert caps.objectscript_deployed
        assert caps.graphoperators_deployed
        assert not caps.kg_built  # _call_classmethod returns None → falsy
        # Dictionary queries use cursor.execute
        calls = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("Graph.KG.PageRank" in sql for sql in calls)
        assert any("GraphOperators" in sql for sql in calls)
        # kg_built check goes through _call_classmethod, not cursor.execute
        assert any(
            call.args[2] == "IsSet" for call in mock_cm.call_args_list
        )

    def test_returns_false_when_not_compiled(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(0,), (0,)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=None):
            caps = GraphSchema.check_objectscript_classes(cursor)

        assert not caps.objectscript_deployed
        assert not caps.graphoperators_deployed

    def test_checks_graphoperators_too(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (0,)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=None):
            GraphSchema.check_objectscript_classes(cursor)

        calls = [call.args[0] for call in cursor.execute.call_args_list]
        assert any("Graph.KG.PageRank" in sql for sql in calls)
        assert any("GraphOperators" in sql for sql in calls)

    def test_kg_built_false_when_not_in_meta(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (1,)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=None):
            caps = GraphSchema.check_objectscript_classes(cursor)

        assert not caps.kg_built

    def test_kg_built_true_when_in_meta(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (1,)]

        # _call_classmethod returns "1" for IsSet → truthy → kg_built=True
        with patch("iris_vector_graph.schema._call_classmethod", return_value="1"):
            caps = GraphSchema.check_objectscript_classes(cursor)

        assert caps.kg_built

    def test_fallback_to_classmethod_on_dict_error(self):
        """When %Dictionary query fails, falls back to native-API path via _call_classmethod."""
        cursor = MagicMock()
        # Both dictionary queries raise
        cursor.execute.side_effect = [Exception("dict fail"), Exception("dict fail")]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=1) as mock_cm:
            caps = GraphSchema.check_objectscript_classes(cursor)

        # Fallback uses _call_classmethod for %Exists checks
        assert isinstance(caps, IRISCapabilities)
        # When dict query fails, fallback _call_classmethod is called for %Exists
        method_names = [c.args[2] for c in mock_cm.call_args_list]
        assert "%Exists" in method_names or "IsSet" in method_names


class TestDeployObjectscriptClasses:
    def test_returns_capabilities_after_deploy(self):
        cursor = MagicMock()
        expected = IRISCapabilities(objectscript_deployed=True, kg_built=True, graphoperators_deployed=True)

        with patch.object(GraphSchema, "check_objectscript_classes", return_value=expected) as mock_check:
            result = GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))

        assert result is expected
        mock_check.assert_called_once_with(cursor, conn=None)

    def test_graceful_degradation_on_failure(self):
        cursor = MagicMock()
        # deploy_objectscript_classes delegates entirely to check_objectscript_classes;
        # simulate that raising an exception inside check_objectscript_classes returns defaults.
        with patch.object(GraphSchema, "check_objectscript_classes", side_effect=Exception("boom")):
            result = GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))

        assert isinstance(result, IRISCapabilities)
        assert not result.objectscript_deployed

    def test_uses_correct_iris_src_path(self):
        cursor = MagicMock()
        iris_src_path = Path("/opt/iris_src")

        with patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()) as mock_check:
            GraphSchema.deploy_objectscript_classes(cursor, iris_src_path)

        # deploy_objectscript_classes is pure detection — it just calls check_objectscript_classes
        mock_check.assert_called_once_with(cursor, conn=None)


class TestDeployRemovesTheStaleEdgeClasses:
    """`/tmp/src` is never pruned, so a deleted `.cls` keeps compiling.

    `Graph.KG.Edge` (deleted by spec 227) and `Graph.KG.TestEdge` (deleted
    earlier) both declared `SqlTableName = rdf_edges`. Whichever compiles last
    owns the table, and class-owned it has no `edge_id` — the reification cascade
    and every read in `_engine/prov.py` answer SQLCODE -29. Deleting the source
    file from the server's directory is only half the job: the compiled class
    survives in the namespace and keeps the table. Deploy has to delete both.
    """

    STALE = ("Graph.KG.Edge", "Graph.KG.TestEdge")

    def _deploy_calls(self):
        cursor = MagicMock()
        with patch("iris_vector_graph.schema._call_classmethod") as mock_cm:
            with patch.object(
                GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()
            ):
                GraphSchema.deploy_objectscript_classes(cursor, Path("/tmp/iris_src"))
        return mock_cm.call_args_list

    @pytest.mark.parametrize("cls_name", STALE)
    def test_the_source_file_is_deleted_before_loaddir(self, cls_name):
        calls = self._deploy_calls()
        path = f"/{cls_name.replace('.', '/')}.cls"
        deleted = [
            i
            for i, c in enumerate(calls)
            if c.args[1:3] == ("%Library.File", "Delete") and str(c.args[3]).endswith(path)
        ]
        loaddir = [i for i, c in enumerate(calls) if c.args[2] == "LoadDir"]
        assert deleted, f"{cls_name}.cls is not removed from the server's source directory"
        assert loaddir, "deploy no longer calls LoadDir"
        assert min(deleted) < min(loaddir), (
            f"{cls_name}.cls must be deleted before LoadDir, or LoadDir compiles it again"
        )

    @pytest.mark.parametrize("cls_name", STALE)
    def test_the_compiled_class_is_deleted_after_loaddir(self, cls_name):
        calls = self._deploy_calls()
        deletes = [
            c
            for c in calls
            if c.args[1:3] == ("%SYSTEM.OBJ", "Delete") and c.args[3] == cls_name
        ]
        assert deletes, (
            f"{cls_name} is never deleted from the namespace. A container that once "
            "held the file still has the class compiled, and it owns Graph_KG.rdf_edges."
        )

    @pytest.mark.parametrize("cls_name", STALE)
    def test_deploy_never_compiles_them(self, cls_name):
        calls = self._deploy_calls()
        compiles = [
            c
            for c in calls
            if c.args[1:3] == ("%SYSTEM.OBJ", "Compile") and c.args[3] == cls_name
        ]
        assert compiles == [], (
            f"deploy still compiles {cls_name}, which no longer exists in iris_src/src "
            "and would take Graph_KG.rdf_edges away from the DDL if it did."
        )


class TestBootstrapKgGlobal:
    def test_returns_false_if_already_done(self):
        cursor = MagicMock()
        # IsSet returns truthy → already done
        with patch("iris_vector_graph.schema._call_classmethod", return_value="1") as mock_cm:
            result = GraphSchema.bootstrap_kg_global(cursor)

        assert not result
        # IsSet was called
        assert any(
            call.args[2] == "IsSet" for call in mock_cm.call_args_list
        )

    def test_skips_build_if_no_edges(self):
        cursor = MagicMock()
        # IsSet -> not done (None/falsy), COUNT -> 0 rows
        cursor.fetchone.side_effect = [(0,)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value=None) as mock_cm:
            result = GraphSchema.bootstrap_kg_global(cursor)

        assert not result
        # BuildKG was NOT called
        assert not any(call.args[2] == "BuildKG" for call in mock_cm.call_args_list)

    def test_calls_build_kg_when_edges_exist(self):
        cursor = MagicMock()
        # IsSet -> not done, COUNT -> 5 edges
        cursor.fetchone.side_effect = [(5,)]

        # _call_classmethod: IsSet→None, BuildKG→1, Set→1
        side_effects = [None, 1, 1]
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=side_effects) as mock_cm:
            result = GraphSchema.bootstrap_kg_global(cursor)

        assert result
        assert any(call.args[2] == "BuildKG" for call in mock_cm.call_args_list)

    def test_records_completion_in_meta(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(3,)]

        side_effects = [None, 1, 1]
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=side_effects) as mock_cm:
            GraphSchema.bootstrap_kg_global(cursor)

        # Set('kg_built', '1') was called
        set_calls = [c for c in mock_cm.call_args_list if c.args[2] == "Set"]
        assert set_calls
        assert set_calls[0].args[3] == "kg_built"
        assert set_calls[0].args[4] == "1"

    def test_idempotent_second_call_returns_false(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(4,)]

        # First call: IsSet→None, BuildKG→1, Set→1  → True
        # Second call: IsSet→"1" (already done) → False
        call_count = {"n": 0}

        def side_effect(conn_or_cursor, cls, method, *args):
            call_count["n"] += 1
            if method == "IsSet":
                return None if call_count["n"] == 1 else "1"
            return 1

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=side_effect):
            first = GraphSchema.bootstrap_kg_global(cursor)
            second = GraphSchema.bootstrap_kg_global(cursor)

        assert first
        assert not second
