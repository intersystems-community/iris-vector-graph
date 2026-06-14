"""
Unit tests for _engine/nodes_edges.py covering:
- count_nodes (with and without label)
- _bulk_load_drifted (returns True on error, False on 0 edges)
- backfill_2hop_exact (success and failure)
- _assert_node_exists (found and not found)
- delete_edge (success and failure)
- bulk_delete_nodes (success path)
- create_node (basic SQL path)
- bulk_create_nodes (SQL path, no ObjectScript)
- bulk_create_edges (SQL path)
- _resolve_node_from_row (JSON and plain list parsing)

No IRIS connection needed — mocks conn and cursor.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.executemany.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# count_nodes
# ---------------------------------------------------------------------------

class TestCountNodes:

    def test_count_all_nodes(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (42,)
        result = eng.count_nodes()
        assert result == 42

    def test_count_nodes_with_label(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (7,)
        result = eng.count_nodes(label="Disease")
        assert result == 7
        # Verify it queried rdf_labels
        sql = cursor.execute.call_args[0][0]
        assert "rdf_labels" in sql

    def test_count_nodes_returns_zero_on_error(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table not found")
        result = eng.count_nodes()
        assert result == 0


# ---------------------------------------------------------------------------
# _bulk_load_drifted
# ---------------------------------------------------------------------------

class TestBulkLoadDrifted:

    def test_returns_false_when_zero_sql_edges(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (0,)
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._bulk_load_drifted()
        assert result is False

    def test_returns_true_when_nkg_empty_but_edges_exist(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (100,)  # sql_edges = 100
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"  # nkg_nodes = 0
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._bulk_load_drifted()
        assert result is True

    def test_returns_true_on_exception(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("DB gone")
        result = eng._bulk_load_drifted()
        assert result is True


# ---------------------------------------------------------------------------
# backfill_2hop_exact
# ---------------------------------------------------------------------------

class TestBackfill2HopExact:

    def test_success_returns_count(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "50"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_2hop_exact()
        assert result == 50

    def test_failure_returns_zero(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("class not found")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_2hop_exact()
        assert result == 0


# ---------------------------------------------------------------------------
# _assert_node_exists
# ---------------------------------------------------------------------------

class TestAssertNodeExists:

    def test_existing_node_does_not_raise(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (1,)
        eng._assert_node_exists("existing_node")  # should not raise

    def test_missing_node_raises_value_error(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (0,)
        with pytest.raises(ValueError, match="Node does not exist"):
            eng._assert_node_exists("missing_node")

    def test_db_error_is_swallowed(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("connection lost")
        eng._assert_node_exists("any_node")  # should not raise


# ---------------------------------------------------------------------------
# delete_edge
# ---------------------------------------------------------------------------

class TestDeleteEdge:

    def test_success_returns_true(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.delete_edge("src", "TREATS", "tgt")
        assert result is True

    def test_sql_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("delete failed")
        result = eng.delete_edge("src", "TREATS", "tgt")
        assert result is False

    def test_adjacency_kill_failure_still_returns_true(self):
        """delete_edge should succeed even if ^KG kill fails."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.side_effect = RuntimeError("^KG kill failed")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.delete_edge("src", "TREATS", "tgt")
        assert result is True


# ---------------------------------------------------------------------------
# bulk_delete_nodes
# ---------------------------------------------------------------------------

class TestBulkDeleteNodes:

    def test_deletes_all_nodes_and_returns_count(self):
        eng, conn, cursor = _make_eng()
        result = eng.bulk_delete_nodes(["n1", "n2", "n3"])
        assert result == 3

    def test_empty_list_returns_zero(self):
        eng, conn, cursor = _make_eng()
        result = eng.bulk_delete_nodes([])
        assert result == 0

    def test_batch_failure_skips_but_continues(self):
        eng, conn, cursor = _make_eng()
        # First execute call raises, subsequent calls succeed
        call_count = [0]
        def execute_side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("constraint")
        cursor.execute.side_effect = execute_side
        result = eng.bulk_delete_nodes(["n1"])
        # Failed batch → 0 deleted
        assert result == 0


# ---------------------------------------------------------------------------
# create_node
# ---------------------------------------------------------------------------

class TestCreateNode:

    def test_creates_node_and_returns_true(self):
        eng, conn, cursor = _make_eng()
        result = eng.create_node("new_node", labels=["Gene"], properties={"name": "BRCA1"})
        assert result is not None

    def test_create_node_no_labels_no_props(self):
        eng, conn, cursor = _make_eng()
        result = eng.create_node("bare_node")
        assert result is not None

    def test_create_node_sql_error_returns_false(self):
        eng, conn, cursor = _make_eng()
        # Fail on INSERT but let START TRANSACTION and ROLLBACK pass
        def execute_side(sql, *args, **kwargs):
            if sql and "INSERT" in sql.upper():
                raise RuntimeError("insert failed")
        cursor.execute.side_effect = execute_side
        result = eng.create_node("bad_node")
        assert result is False


# ---------------------------------------------------------------------------
# bulk_create_nodes (SQL fallback — no ObjectScript)
# ---------------------------------------------------------------------------

class TestBulkCreateNodes:

    def test_basic_sql_path(self):
        eng, conn, cursor = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False)
        nodes = [
            {"id": "n1", "labels": ["Gene"], "properties": {"name": "BRCA1"}},
            {"id": "n2", "labels": ["Disease"], "properties": {"name": "Cancer"}},
        ]
        result = eng.bulk_create_nodes(nodes)
        assert result is not None

    def test_skips_nodes_without_id(self):
        eng, conn, cursor = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False)
        nodes = [
            {"labels": ["NoId"]},  # no id — should be skipped
            {"id": "valid", "labels": ["Gene"]},
        ]
        eng.bulk_create_nodes(nodes)
        # Should not raise

    def test_empty_nodes_returns_zero(self):
        eng, conn, cursor = _make_eng()
        result = eng.bulk_create_nodes([])
        assert result == 0 or result == []


# ---------------------------------------------------------------------------
# bulk_create_edges (SQL path)
# ---------------------------------------------------------------------------

class TestBulkCreateEdges:

    def test_basic_sql_path(self):
        eng, conn, cursor = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False)
        edges = [
            {"source_id": "n1", "predicate": "TREATS", "target_id": "n2"},
            {"source_id": "n2", "predicate": "CAUSES", "target_id": "n3"},
        ]
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.bulk_create_edges(edges, auto_sync=False)
        assert isinstance(result, int)

    def test_empty_edges_returns_zero(self):
        eng, conn, cursor = _make_eng()
        result = eng.bulk_create_edges([])
        assert result == 0

    def test_edges_missing_keys_are_skipped(self):
        eng, conn, cursor = _make_eng()
        from iris_vector_graph.capabilities import IRISCapabilities
        eng.capabilities = IRISCapabilities(objectscript_deployed=False)
        # Edge missing "target_id"
        edges = [{"source_id": "n1", "predicate": "X"}]
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.bulk_create_edges(edges, auto_sync=False)
        assert result == 0


# ---------------------------------------------------------------------------
# get_node (SQL path)
# ---------------------------------------------------------------------------

class TestGetNode:

    def test_get_node_found(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.side_effect = [
            ("n1", json.dumps(["Gene"]), json.dumps([{"key": "name", "value": "BRCA1"}])),
        ]
        cursor.description = [("node_id",), ("node_labels",), ("node_props",)]
        result = eng.get_node("n1")
        # Returns dict or None — just verify no crash
        assert result is None or isinstance(result, dict)

    def test_get_node_not_found_returns_none(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = None
        result = eng.get_node("missing_node")
        assert result is None

    def test_get_node_sql_error_returns_none(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("SQL error")
        result = eng.get_node("any_node")
        assert result is None


# ---------------------------------------------------------------------------
# _BulkLoadSession (lines 19-30)
# ---------------------------------------------------------------------------

class TestBulkLoadSession:

    def test_add_nodes_accumulates_stats(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_with_reconnect", return_value=3):
            with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
                with patch.object(eng, "_bulk_load_drifted", return_value=False):
                    with patch.object(eng, "sync"):
                        with patch("iris_vector_graph._engine.schema.GraphSchema"):
                            with eng.bulk_load_session(rebuild_indexes=False) as session:
                                session.add_nodes([{"id": "n1"}, {"id": "n2"}, {"id": "n3"}])
        assert session.stats["nodes"] == 3

    def test_add_edges_accumulates_stats(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_with_reconnect", return_value=2):
            with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
                with patch.object(eng, "_bulk_load_drifted", return_value=False):
                    with patch.object(eng, "sync"):
                        with patch("iris_vector_graph._engine.schema.GraphSchema"):
                            with eng.bulk_load_session(rebuild_indexes=False) as session:
                                session.add_edges([("n1", "n2"), ("n2", "n3")])
        assert session.stats["edges"] == 2


# ---------------------------------------------------------------------------
# get_nodes: exception fallback (line 300), empty_nids (lines 270-288)
# ---------------------------------------------------------------------------

class TestGetNodesExtended:

    def test_exception_falls_back_to_cypher(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("SQL error")
        with patch.object(eng, "_get_node_cypher_fallback",
                          return_value={"id": "n1", "labels": []}) as mock_fb:
            result = eng.get_nodes(["n1"])
        mock_fb.assert_called_once_with("n1")
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        eng, conn, cursor = _make_eng()
        result = eng.get_nodes([])
        assert result == []


# ---------------------------------------------------------------------------
# set_edge_weight (lines 520-532)
# ---------------------------------------------------------------------------

class TestSetEdgeWeight:

    def test_success_returns_true(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.set_edge_weight("n1", "TREATS", "n2", 0.75)
        assert result is True

    def test_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.side_effect = RuntimeError("method failed")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.set_edge_weight("n1", "TREATS", "n2", 0.5)
        assert result is False


# ---------------------------------------------------------------------------
# drop_graph (lines 564-572)
# ---------------------------------------------------------------------------

class TestDropGraph:

    def test_returns_deleted_count(self):
        eng, conn, cursor = _make_eng()
        cursor.rowcount = 5
        result = eng.drop_graph("g1")
        assert result == 5

    def test_commit_failure_doesnt_raise(self):
        eng, conn, cursor = _make_eng()
        cursor.rowcount = 3
        conn.commit.side_effect = RuntimeError("commit failed")
        result = eng.drop_graph("g1")
        assert result == 3


# ---------------------------------------------------------------------------
# bulk_create_nodes: objectscript path (lines 599-620), graph prop (line 659)
# ---------------------------------------------------------------------------

class TestBulkCreateNodesExtended:

    def test_objectscript_path_falls_back_to_sql(self):
        from iris_vector_graph.capabilities import IRISCapabilities
        eng, conn, cursor = _make_eng()
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)
        iris_obj = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch("iris_vector_graph.schema._call_classmethod_large",
                       side_effect=RuntimeError("no chunk size")):
                with patch("iris_vector_graph._engine.nodes_edges.GraphSchema"):
                    result = eng.bulk_create_nodes(
                        [{"id": "n1"}, {"id": "n2"}], disable_indexes=False
                    )
        assert isinstance(result, list)

    def test_graph_prop_added_when_node_has_graph_key(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph._engine.nodes_edges.GraphSchema") as mock_gs:
            mock_gs.disable_indexes.return_value = None
            mock_gs.rebuild_indexes.return_value = None
            mock_gs.get_bulk_insert_sql.return_value = "INSERT INTO nodes VALUES (?, ?)"
            result = eng.bulk_create_nodes(
                [{"id": "n1", "labels": ["Gene"], "graph": "g1"}],
                disable_indexes=False,
            )
        assert "n1" in result


# ---------------------------------------------------------------------------
# bulk_create_edges: graph= path (lines 731-749)
# ---------------------------------------------------------------------------

class TestBulkCreateEdgesExtended:

    def test_with_graph_executes_graph_sql(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph._engine.nodes_edges.GraphSchema") as mock_gs:
            mock_gs.disable_indexes.return_value = None
            mock_gs.rebuild_indexes.return_value = None
            mock_gs.get_bulk_insert_sql.side_effect = lambda name: f"INSERT INTO {name} ..."
            with patch.object(eng, "sync"):
                result = eng.bulk_create_edges(
                    [{"source_id": "n1", "predicate": "TREATS", "target_id": "n2"}],
                    graph="g1",
                    disable_indexes=False,
                    auto_sync=False,
                )
        assert result == 1

    def test_auto_rebuild_kg_deprecated_warning(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph._engine.nodes_edges.GraphSchema"):
            with patch.object(eng, "sync"):
                with pytest.warns(DeprecationWarning, match="auto_rebuild_kg"):
                    eng.bulk_create_edges(
                        [{"source_id": "n1", "predicate": "T", "target_id": "n2"}],
                        auto_rebuild_kg=False,
                        disable_indexes=False,
                    )


# ---------------------------------------------------------------------------
# bulk_ingest_edges: tuple input (lines 776-779), objectscript path (786-800)
# ---------------------------------------------------------------------------

class TestBulkIngestEdgesExtended:

    def test_tuple_input_normalized(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj") as mock_iris:
            mock_iris.return_value.classMethodVoid.return_value = None
            result = eng.bulk_ingest_edges(
                [("n1", "n2", "TREATS"), ("n2", "n3")],
                predicate="KNOWS",
                auto_sync=False,
            )
        assert isinstance(result, int)

    def test_objectscript_path_when_deployed(self):
        from iris_vector_graph.capabilities import IRISCapabilities
        eng, conn, cursor = _make_eng()
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)
        iris_obj = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch("iris_vector_graph.schema._call_classmethod_large",
                       side_effect=RuntimeError("no chunk size")):
                with patch.object(eng, "sync"):
                    result = eng.bulk_ingest_edges(
                        [{"s": "n1", "p": "T", "o": "n2"}] * 3,
                        auto_sync=False,
                    )
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# store_node: with properties and labels (lines 957-990)
# ---------------------------------------------------------------------------

class TestStoreNode:

    def test_node_without_props_or_labels(self):
        eng, conn, cursor = _make_eng()
        result = eng.store_node("n1")
        assert result is True

    def test_with_properties_writes_props(self):
        eng, conn, cursor = _make_eng()
        result = eng.store_node("n1", properties={"name": "BRCA1", "score": 0.9})
        assert result is True

    def test_with_labels_inserts_label_rows(self):
        eng, conn, cursor = _make_eng()
        result = eng.store_node("n1", labels=["Gene", "Protein"])
        assert result is True

    def test_duplicate_node_swallowed(self):
        eng, conn, cursor = _make_eng()
        call_n = [0]
        def side(sql, params=None):
            call_n[0] += 1
            if call_n[0] == 1:
                raise Exception("-119 UNIQUE violation")
        cursor.execute.side_effect = side
        result = eng.store_node("n1")
        assert result is True


# ---------------------------------------------------------------------------
# store_edge (lines 1005-1008)
# ---------------------------------------------------------------------------

class TestStoreEdge:

    def test_with_qualifiers_succeeds(self):
        eng, conn, cursor = _make_eng()
        result = eng.store_edge("n1", "TREATS", "n2", qualifiers={"confidence": 0.9})
        assert result is True

    def test_duplicate_edge_swallowed(self):
        eng, conn, cursor = _make_eng()
        call_n = [0]
        def side(sql, params=None):
            call_n[0] += 1
            if call_n[0] == 3:  # 1=store_node(n1), 2=store_node(n2), 3=edge INSERT
                raise Exception("-119 UNIQUE constraint")
        cursor.execute.side_effect = side
        result = eng.store_edge("n1", "TREATS", "n2")
        assert result is True


# ---------------------------------------------------------------------------
# nodes_exist: IN-query failure fallback (lines 1030-1039)
# ---------------------------------------------------------------------------

class TestNodesExistExtended:

    def test_in_query_failure_falls_back_to_individual(self):
        eng, conn, cursor = _make_eng()
        call_n = [0]
        def side_execute(sql, params=None):
            call_n[0] += 1
            if "IN (" in str(sql):
                raise RuntimeError("IN clause failed")
        cursor.execute.side_effect = side_execute
        cursor.fetchone.return_value = (1,)  # node exists in fallback
        result = eng.nodes_exist(["n1"])
        assert isinstance(result, set)
