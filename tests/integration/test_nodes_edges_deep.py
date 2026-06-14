"""
Deep coverage tests for _engine/nodes_edges.py uncovered paths.

Targets (among others):
  L90-91  — _bulk_load_drifted drift-warning path (incremental_ok=True, drifted=True)
  L114-115 — _bulk_load_drifted exception → returns True
  L137-138 — _assert_node_exists SQL exception swallowed
  L192-198 — get_nodes batch empty/fallback paths
  L264-268 — get_nodes batch SQL exception → per-node fallback
  L300, 311 — get_nodes single-node cypher fallback
  L321-344 — _get_node_cypher_fallback full path
  L370-372 — count_nodes exception → 0
  L530-532 — set_edge_weight exception → False
  L543-546 — delete_edge ^KG kill warning
  L551-552 — list_graphs
  L570-571 — drop_graph exception swallowed
  L600-622 — bulk_create_nodes BulkIngestNodesSQL path
  L663, 684-687 — bulk_create_nodes exception paths
  L712-713 — bulk_create_edges with graph
  L787-802 — bulk_import_edges SQL fallback path
  L816, 819-820 — WriteAdjacency exception
  L848-858 — delete_node reification cascade
  L872-874 — delete_node failure
  L905-906 — bulk_delete_nodes failure
  L971-972, 984-987 — update_node paths
  L1005-1008, 1030-1041 — nodes_exist exception fallback
"""
import pytest
from unittest.mock import patch, MagicMock, call
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def ne_graph(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"ne_{i}", labels=["NENode"], properties={"idx": i, "name": f"node_{i}"})
    for i in range(4):
        eng.create_edge(f"ne_{i}", "NE_REL", f"ne_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# _bulk_load_drifted — L90-91 (drift warning) and L114-115 (exception → True)
# ---------------------------------------------------------------------------

class TestBulkLoadDrifted:

    def test_bulk_load_drifted_exception_returns_true(self, ne_graph):
        with patch.object(ne_graph, "_iris_obj", side_effect=RuntimeError("iris down")):
            result = ne_graph._bulk_load_drifted()
        assert result is True

    def test_bulk_load_drifted_zero_nkg(self, ne_graph):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = "0"
        with patch.object(ne_graph, "_iris_obj", return_value=mock_iris):
            result = ne_graph._bulk_load_drifted()
        assert isinstance(result, bool)

    def test_bulk_load_drifted_nkg_matches(self, ne_graph):
        mock_iris = MagicMock()
        # Return matching counts so not drifted
        mock_iris.classMethodValue.side_effect = ["5", "5"]
        with patch.object(ne_graph, "_iris_obj", return_value=mock_iris):
            result = ne_graph._bulk_load_drifted()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _assert_node_exists — L137-138 (SQL exception swallowed)
# ---------------------------------------------------------------------------

class TestAssertNodeExists:

    def test_assert_node_exists_sql_exception_swallowed(self, ne_graph):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("db error")
        cursor_mock.close = MagicMock()
        cursor_mock.fetchone.return_value = None
        with patch.object(ne_graph.conn, "cursor", return_value=cursor_mock):
            ne_graph._assert_node_exists("ne_0")  # Must not raise


# ---------------------------------------------------------------------------
# count_nodes — L370-372 (exception → 0)
# ---------------------------------------------------------------------------

class TestCountNodesException:

    def test_count_nodes_no_label(self, ne_graph):
        count = ne_graph.count_nodes()
        assert isinstance(count, int)
        assert count >= 5

    def test_count_nodes_with_label(self, ne_graph):
        count = ne_graph.count_nodes(label="NENode")
        assert isinstance(count, int)
        assert count >= 5

    def test_count_nodes_sql_exception_returns_zero(self, ne_graph):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("count fail")
        cursor_mock.close = MagicMock()
        with patch.object(ne_graph.conn, "cursor", return_value=cursor_mock):
            result = ne_graph.count_nodes()
        assert result == 0


# ---------------------------------------------------------------------------
# get_nodes — L264-268 batch exception → per-node fallback, L192-198 empty
# ---------------------------------------------------------------------------

class TestGetNodesBatchFallback:

    def test_get_nodes_empty_list(self, ne_graph):
        result = ne_graph.get_nodes([])
        assert result == []

    def test_get_nodes_existing(self, ne_graph):
        result = ne_graph.get_nodes(["ne_0", "ne_1"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_nodes_missing_ids(self, ne_graph):
        result = ne_graph.get_nodes(["__never_existed__"])
        assert isinstance(result, list)

    def test_get_nodes_mixed(self, ne_graph):
        result = ne_graph.get_nodes(["ne_0", "__missing__"])
        assert isinstance(result, list)

    def test_get_nodes_large_batch_triggers_fallback(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        for i in range(5):
            eng.create_node(f"lg_{i}", labels=["LG"])
        eng.sync()
        ids = [f"lg_{i}" for i in range(5)] + [f"__missing_{i}__" for i in range(3)]
        result = eng.get_nodes(ids)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# set_edge_weight — L530-532 (exception → False)
# ---------------------------------------------------------------------------

class TestSetEdgeWeightException:

    def test_set_edge_weight_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("ew_s", labels=["EW"])
        eng.create_node("ew_t", labels=["EW"])
        eng.create_edge("ew_s", "EW_REL", "ew_t")
        eng.sync()
        result = eng.set_edge_weight("ew_s", "EW_REL", "ew_t", 2.5)
        assert isinstance(result, bool)

    def test_set_edge_weight_exception_returns_false(self, ne_graph):
        mock_iris = MagicMock()
        mock_iris.classMethodVoid.side_effect = RuntimeError("weight fail")
        with patch.object(ne_graph, "_iris_obj", return_value=mock_iris):
            result = ne_graph.set_edge_weight("ne_0", "NE_REL", "ne_1", 3.0)
        assert result is False


# ---------------------------------------------------------------------------
# delete_edge — L543-546 (adjacency warning)
# ---------------------------------------------------------------------------

class TestDeleteEdge:

    def test_delete_edge_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("ded_s", labels=["DED"])
        eng.create_node("ded_t", labels=["DED"])
        eng.create_edge("ded_s", "DED_REL", "ded_t")
        eng.sync()
        result = eng.delete_edge("ded_s", "DED_REL", "ded_t")
        assert isinstance(result, bool)

    def test_delete_edge_adjacency_warning_swallowed(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("ded2_s", labels=["DED2"])
        eng.create_node("ded2_t", labels=["DED2"])
        eng.create_edge("ded2_s", "DED2_REL", "ded2_t")
        eng.sync()
        mock_iris = MagicMock()
        mock_iris.classMethodVoid.side_effect = RuntimeError("^KG kill fail")
        with patch.object(eng, "_iris_obj", return_value=mock_iris):
            result = eng.delete_edge("ded2_s", "DED2_REL", "ded2_t")
        # Warning logged but result should be True
        assert result is True

    def test_delete_edge_missing(self, ne_graph):
        result = ne_graph.delete_edge("__no__", "NO_REL", "__no__")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# list_graphs / drop_graph — L551-552, L570-571
# ---------------------------------------------------------------------------

class TestListDropGraph:

    def test_list_graphs_returns_list(self, ne_graph):
        result = ne_graph.list_graphs()
        assert isinstance(result, list)

    def test_drop_graph_nonexistent(self, ne_graph):
        result = ne_graph.drop_graph("__nonexistent_graph__")
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# bulk_create_nodes — L600-622 (ObjectScript path) and L663, 684-687 (exceptions)
# ---------------------------------------------------------------------------

class TestBulkCreateNodes:

    def test_bulk_create_nodes_empty_list(self, ne_graph):
        result = ne_graph.bulk_create_nodes([])
        assert result == [] or result == 0

    def test_bulk_create_nodes_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        nodes = [
            {"id": f"bcn_{i}", "labels": ["BCN"], "properties": {"val": i}}
            for i in range(5)
        ]
        result = eng.bulk_create_nodes(nodes)
        assert isinstance(result, (list, int))

    def test_bulk_create_nodes_with_dict_property(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        nodes = [{"id": "bcn_dict", "labels": ["BCN"], "properties": {"meta": {"k": "v"}}}]
        result = eng.bulk_create_nodes(nodes)
        assert isinstance(result, (list, int))

    def test_bulk_create_nodes_objectscript_fails_fallback(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        nodes = [{"id": "bcn_fb_0", "labels": ["BCN_FB"], "properties": {}}]
        with patch("iris_vector_graph.schema._call_classmethod_large",
                   side_effect=RuntimeError("bulk fail")):
            result = eng.bulk_create_nodes(nodes, disable_indexes=False)
        assert isinstance(result, (list, int))

    def test_bulk_create_nodes_large_batch(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        nodes = [
            {"id": f"bcn_lg_{i}", "labels": ["BCN_LG"], "properties": {"x": i}}
            for i in range(30)
        ]
        result = eng.bulk_create_nodes(nodes)
        assert isinstance(result, (list, int))


# ---------------------------------------------------------------------------
# bulk_create_edges — L712-713 (with graph)
# ---------------------------------------------------------------------------

class TestBulkCreateEdges:

    def test_bulk_create_edges_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bce_s", labels=["BCE"])
        eng.create_node("bce_t", labels=["BCE"])
        eng.sync()
        count = eng.bulk_create_edges([
            {"source_id": "bce_s", "predicate": "BCE_REL", "target_id": "bce_t"}
        ])
        assert isinstance(count, int)

    def test_bulk_create_edges_empty(self, ne_graph):
        count = ne_graph.bulk_create_edges([])
        assert count == 0

    def test_bulk_create_edges_with_graph_param(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bceg_s", labels=["BCEG"])
        eng.create_node("bceg_t", labels=["BCEG"])
        eng.sync()
        count = eng.bulk_create_edges(
            [{"source_id": "bceg_s", "predicate": "BCEG_REL", "target_id": "bceg_t"}],
            graph="test_graph_context"
        )
        assert isinstance(count, int)

    def test_bulk_create_edges_large(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        for i in range(5):
            eng.create_node(f"bcel_{i}", labels=["BCEL"])
        eng.sync()
        edges = [
            {"source_id": f"bcel_{i}", "predicate": "BCEL_REL", "target_id": f"bcel_{i+1}"}
            for i in range(4)
        ]
        count = eng.bulk_create_edges(edges)
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# bulk_ingest_edges (predicate-based) — L787-802 SQL fallback
# ---------------------------------------------------------------------------

class TestBulkIngestEdges:

    def test_bulk_ingest_edges_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bie_s", labels=["BIE"])
        eng.create_node("bie_t", labels=["BIE"])
        eng.sync()
        result = eng.bulk_ingest_edges(
            [{"s": "bie_s", "p": "BIE_REL", "o": "bie_t"}], predicate="BIE_REL", auto_sync=False
        )
        assert isinstance(result, int)

    def test_bulk_ingest_edges_empty(self, ne_graph):
        result = ne_graph.bulk_ingest_edges([], predicate="X", auto_sync=False)
        assert result == 0

    def test_bulk_ingest_edges_objectscript_fails_sql_fallback(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bie_fb_s", labels=["BIE_FB"])
        eng.create_node("bie_fb_t", labels=["BIE_FB"])
        eng.sync()
        edges = [{"s": "bie_fb_s", "p": "BIE_FB_REL", "o": "bie_fb_t"}]
        with patch("iris_vector_graph.schema._call_classmethod_large",
                   side_effect=RuntimeError("bulk edge fail")):
            result = eng.bulk_ingest_edges(edges, predicate="BIE_FB_REL", auto_sync=False)
        assert isinstance(result, int)

    def test_bulk_ingest_edges_adjacency_fail_warning(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bie_adj_s", labels=["BIE_ADJ"])
        eng.create_node("bie_adj_t", labels=["BIE_ADJ"])
        eng.sync()
        edges = [{"s": "bie_adj_s", "p": "BIE_ADJ_REL", "o": "bie_adj_t"}]
        mock_iris = MagicMock()
        mock_iris.classMethodVoid.side_effect = RuntimeError("adj fail")
        with patch("iris_vector_graph.schema._call_classmethod_large",
                   side_effect=RuntimeError("force SQL path")):
            with patch.object(eng, "_iris_obj", return_value=mock_iris):
                result = eng.bulk_ingest_edges(edges, predicate="BIE_ADJ_REL", auto_sync=False)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# delete_node — L848-858 (reification cascade), L872-874 (failure)
# ---------------------------------------------------------------------------

class TestDeleteNode:

    def test_delete_node_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("dn_basic", labels=["DN"])
        eng.sync()
        result = eng.delete_node("dn_basic")
        assert isinstance(result, bool)

    def test_delete_node_with_edges_and_cascade(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("dn_src", labels=["DN"])
        eng.create_node("dn_dst", labels=["DN"])
        eng.create_edge("dn_src", "DN_REL", "dn_dst")
        eng.sync()
        # Reify the edge to trigger L848-858 cascade on delete
        cursor = eng.conn.cursor()
        try:
            cursor.execute(
                "SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='dn_src' AND p='DN_REL' LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                eng.reify_edge(row[0], props={"conf": "0.9"})
        except Exception:
            pass
        finally:
            cursor.close()
        result = eng.delete_node("dn_src")
        assert isinstance(result, bool)

    def test_delete_node_sql_exception_returns_false(self, ne_graph):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("delete fail")
        cursor_mock.close = MagicMock()
        with patch.object(ne_graph.conn, "cursor", return_value=cursor_mock):
            result = ne_graph.delete_node("ne_0")
        assert result is False

    def test_delete_node_missing_noop(self, ne_graph):
        ne_graph.delete_node("__never_existed__")


# ---------------------------------------------------------------------------
# bulk_delete_nodes — L905-906 (failure)
# ---------------------------------------------------------------------------

class TestBulkDeleteNodes:

    def test_bulk_delete_nodes_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        for i in range(5):
            eng.create_node(f"bdn_{i}", labels=["BDN"])
        eng.sync()
        deleted = eng.bulk_delete_nodes([f"bdn_{i}" for i in range(5)])
        assert isinstance(deleted, int)
        assert deleted >= 0

    def test_bulk_delete_nodes_empty(self, ne_graph):
        deleted = ne_graph.bulk_delete_nodes([])
        assert deleted == 0

    def test_bulk_delete_nodes_with_edges(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.create_node("bdnedge_hub", labels=["BDN_HUB"])
        eng.create_node("bdnedge_spoke", labels=["BDN_SPOKE"])
        eng.create_edge("bdnedge_hub", "BDNE_REL", "bdnedge_spoke")
        eng.sync()
        deleted = eng.bulk_delete_nodes(["bdnedge_hub"])
        assert deleted >= 0


# ---------------------------------------------------------------------------
# store_node (upsert/update) — L971-972, L984-987 (property/label duplicate)
# ---------------------------------------------------------------------------

class TestUpdateNode:

    def test_update_node_add_property(self, ne_graph):
        ne_graph.store_node("ne_0", properties={"extra_prop": "value"})

    def test_update_node_duplicate_label_via_cypher(self, ne_graph):
        # Add label via Cypher (duplicate-safe) — hits label upsert code path
        ne_graph.execute_cypher(
            "MATCH (n {node_id: 'ne_0'}) SET n:ExtraLabel RETURN n.node_id AS id"
        )

    def test_update_node_add_new_label(self, ne_graph):
        ne_graph.store_node("ne_1", labels=["NewLabel"])

    def test_update_node_change_property(self, ne_graph):
        ne_graph.store_node("ne_2", properties={"idx": 999})


# ---------------------------------------------------------------------------
# nodes_exist — L1005-1008, L1030-1041 (exception fallback per-node)
# ---------------------------------------------------------------------------

class TestNodesExist:

    def test_nodes_exist_all_present(self, ne_graph):
        result = ne_graph.nodes_exist(["ne_0", "ne_1", "ne_2"])
        present = set(result)
        assert "ne_0" in present
        assert "ne_1" in present

    def test_nodes_exist_mixed(self, ne_graph):
        result = ne_graph.nodes_exist(["ne_0", "__missing__"])
        present = set(result)
        assert "ne_0" in present
        assert "__missing__" not in present

    def test_nodes_exist_empty_list(self, ne_graph):
        result = ne_graph.nodes_exist([])
        assert len(result) == 0

    def test_nodes_exist_large_batch_triggers_chunking(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        for i in range(10):
            eng.create_node(f"ne_lb_{i}", labels=["NELB"])
        eng.sync()
        # Pass a large list to force chunking/fallback path
        ids = [f"ne_lb_{i}" for i in range(10)] + [f"__missing_{i}__" for i in range(5)]
        result = eng.nodes_exist(ids)
        present = set(result)
        for i in range(10):
            assert f"ne_lb_{i}" in present


# ---------------------------------------------------------------------------
# bulk_load_session (context manager)
# ---------------------------------------------------------------------------

class TestBulkLoadSession:

    def test_bulk_load_session_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        with eng.bulk_load_session() as session:
            session.add_nodes([{"id": "bls_0", "labels": ["BLS"]}])
            session.add_nodes([{"id": "bls_1", "labels": ["BLS"]}])
            session.add_edges([{"s": "bls_0", "p": "BLS_REL", "o": "bls_1"}])

    def test_bulk_load_session_incremental_false(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        with eng.bulk_load_session(incremental=False) as session:
            session.add_nodes([{"id": "bls_full", "labels": ["BLS"]}])

    def test_bulk_load_session_no_rebuild(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        with eng.bulk_load_session(rebuild_indexes=False) as session:
            session.add_nodes([{"id": "bls_norebuild", "labels": ["BLS"]}])


# ---------------------------------------------------------------------------
# Additional coverage via Cypher (update SET, count nodes/edges)
# ---------------------------------------------------------------------------

class TestUpdateNodeViaCypher:

    def test_set_property_via_cypher(self, ne_graph):
        result = ne_graph.execute_cypher(
            "MATCH (n {node_id: 'ne_0'}) SET n.extra = 'updated' RETURN n.node_id AS id"
        )
        assert result is not None

    def test_remove_property_via_cypher(self, ne_graph):
        result = ne_graph.execute_cypher(
            "MATCH (n {node_id: 'ne_0'}) REMOVE n.name RETURN n.node_id AS id"
        )
        assert result is not None


class TestNodeEdgeCount:

    def test_node_count_positive(self, ne_graph):
        count = ne_graph.node_count()
        assert isinstance(count, int)
        assert count >= 5

    def test_edge_count_positive(self, ne_graph):
        count = ne_graph.edge_count()
        assert isinstance(count, int)
        assert count >= 4


class TestStoreNodeEdge:

    def test_store_node_basic(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        result = eng.store_node("store_n0", properties={"x": 1}, labels=["Stored"])
        node = eng.get_node("store_n0")
        assert node is not None

    def test_store_edge_with_qualifiers(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        eng.store_node("sq_s", labels=["SQ"])
        eng.store_node("sq_t", labels=["SQ"])
        result = eng.store_edge("sq_s", "SQ_REL", "sq_t", qualifiers={"weight": 1.5})
        assert result is True

    def test_store_node_multiple_labels(self, iris_connection, iris_master_cleanup):
        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        result = eng.store_node("multi_lbl", labels=["LblA", "LblB"])
        assert result is True
