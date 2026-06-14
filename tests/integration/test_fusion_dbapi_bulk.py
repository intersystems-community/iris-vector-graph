"""
Integration tests for fusion.py, dbapi_utils.py, bulk_loader.py, arno_bridge.py
and nodes_edges.py uncovered paths.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.fusion import RRFFusion, HybridSearchFusion
from iris_vector_graph.stores.arno_bridge import (
    arno_available,
    clear_probe_cache,
    remap_kernel_ids,
    arno_call,
    build_kg_adjacency_json,
    ArnoError,
)


@pytest.fixture
def base_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"fb_{i}", labels=["FB"], properties={"v": i})
    for i in range(4):
        eng.create_edge(f"fb_{i}", "FB_REL", f"fb_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# fusion.py — RRFFusion
# ---------------------------------------------------------------------------

class TestRRFFusion:

    def test_fuse_empty_lists(self):
        result = RRFFusion.fuse_results([])
        assert result == []

    def test_fuse_single_list(self):
        result_list = [("a", 0.9), ("b", 0.7), ("c", 0.5)]
        result = RRFFusion.fuse_results([result_list])
        ids = [r[0] for r in result]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids

    def test_fuse_two_lists(self):
        list1 = [("a", 0.9), ("b", 0.7)]
        list2 = [("b", 0.8), ("c", 0.6)]
        result = RRFFusion.fuse_results([list1, list2])
        ids = [r[0] for r in result]
        assert "b" in ids

    def test_fuse_custom_c_constant(self):
        result_list = [("a", 0.9), ("b", 0.7), ("c", 0.5), ("d", 0.3)]
        result = RRFFusion.fuse_results([result_list], c=10)
        assert len(result) == 4

    def test_weighted_fusion_basic(self):
        list1 = [("a", 0.9), ("b", 0.7)]
        list2 = [("b", 0.8), ("c", 0.6)]
        result = RRFFusion.weighted_fusion([list1, list2], [0.6, 0.4])
        ids = [r[0] for r in result]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids

    def test_weighted_fusion_mismatched_lengths_raises(self):
        list1 = [("a", 0.9)]
        with pytest.raises(ValueError, match="must match"):
            RRFFusion.weighted_fusion([list1], [0.5, 0.5])

    def test_weighted_fusion_zero_weight_sum(self):
        list1 = [("a", 0.9)]
        result = RRFFusion.weighted_fusion([list1], [0.0])
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# fusion.py — HybridSearchFusion (L79, L144-195, L280-289)
# ---------------------------------------------------------------------------

class TestHybridSearchFusion:

    def test_multi_modal_search_no_query(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with pytest.raises(ValueError, match="must be provided"):
            fusion.multi_modal_search()

    def test_multi_modal_search_text_only(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.8), ("fb_1", 0.6)]):
            result = fusion.multi_modal_search(query_text="test query")
        assert isinstance(result, list)

    def test_multi_modal_search_vector_only(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_KNN_VEC", return_value=[("fb_0", 0.9)]):
            result = fusion.multi_modal_search(query_vector="[0.1, 0.2, 0.3, 0.4]")
        assert isinstance(result, list)

    def test_multi_modal_search_weighted_fusion_default_weights(self, base_eng):
        # weighted fusion with default weights (no explicit weights provided)
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.8)]):
            with patch.object(base_eng, "kg_NEIGHBORHOOD_EXPANSION",
                              side_effect=Exception("skip graph expansion")):
                result = fusion.multi_modal_search(
                    query_text="test", fusion_method="weighted"
                )
        assert isinstance(result, list)

    def test_multi_modal_search_unknown_fusion_raises(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.8)]):
            with pytest.raises(ValueError, match="Unknown fusion method"):
                fusion.multi_modal_search(query_text="test", fusion_method="bad_method")

    def test_multi_modal_search_text_fails(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", side_effect=Exception("kg_TXT unavail")):
            result = fusion.multi_modal_search(query_text="test")
        assert result == []

    def test_multi_modal_search_with_graph_expansion(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.8), ("fb_1", 0.6)]):
            with patch.object(base_eng, "kg_NEIGHBORHOOD_EXPANSION",
                              return_value=[{"target": "fb_2", "confidence": 800}]):
                result = fusion.multi_modal_search(query_text="test")
        assert isinstance(result, list)

    def test_adaptive_search_short_query(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.5)]):
            result = fusion.adaptive_search("test")
        assert isinstance(result, list)

    def test_adaptive_search_relationship_query(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(base_eng, "kg_TXT", return_value=[]):
            result = fusion.adaptive_search("related to the concept of nodes")
        assert isinstance(result, list)

    def test_adaptive_search_multi_modal_fails_then_fallback(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(fusion, "multi_modal_search", side_effect=Exception("search fail")):
            with patch.object(base_eng, "kg_TXT", return_value=[("fb_0", 0.5)]):
                result = fusion.adaptive_search("what is test")
        assert isinstance(result, list)

    def test_adaptive_search_all_fail(self, base_eng):
        fusion = HybridSearchFusion(base_eng)
        with patch.object(fusion, "multi_modal_search", side_effect=Exception("fail")):
            with patch.object(base_eng, "kg_TXT", side_effect=Exception("fail")):
                result = fusion.adaptive_search("what is test")
        assert result == []


# ---------------------------------------------------------------------------
# arno_bridge.py — remap_idx_results (L430-447)
# ---------------------------------------------------------------------------

class TestRemapKernelIds:

    def test_remap_basic(self):
        idx_to_node = ["node_a", "node_b", "node_c"]
        result_json = json.dumps([{"id": "0", "score": 0.9}, {"id": "2", "score": 0.5}])
        out = remap_kernel_ids(result_json, idx_to_node)
        assert out[0]["id"] == "node_a"
        assert out[1]["id"] == "node_c"

    def test_remap_empty_json(self):
        out = remap_kernel_ids("", [])
        assert out == []

    def test_remap_out_of_bounds_idx(self):
        idx_to_node = ["node_a"]
        result_json = json.dumps([{"id": "99", "score": 0.5}])
        out = remap_kernel_ids(result_json, idx_to_node)
        assert out[0]["id"] == "99"  # index untranslated

    def test_remap_non_dict_entries_skipped(self):
        idx_to_node = ["node_a"]
        result_json = json.dumps(["not_a_dict", {"id": "0", "score": 0.9}])
        out = remap_kernel_ids(result_json, idx_to_node)
        assert len(out) == 1
        assert out[0]["id"] == "node_a"

    def test_remap_string_id_kept(self):
        idx_to_node = ["node_a"]
        result_json = json.dumps([{"id": "node_xyz", "score": 0.7}])
        out = remap_kernel_ids(result_json, idx_to_node)
        assert out[0]["id"] == "node_xyz"


# ---------------------------------------------------------------------------
# arno_bridge.py — arno_available (L275-307) probing
# ---------------------------------------------------------------------------

class TestArnoAvailable:

    def test_arno_unavailable_without_library(self, iris_connection):
        clear_probe_cache()
        result = arno_available(iris_connection)
        assert result is False

    def test_arno_available_cached_false(self, iris_connection):
        from iris_vector_graph.stores.arno_bridge import _probe_cache, _conn_key
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": False}
        result = arno_available(iris_connection)
        assert result is False

    def test_arno_call_raises_when_unavailable(self, iris_connection):
        clear_probe_cache()
        with pytest.raises(ArnoError, match="libarno_callout not available"):
            arno_call(iris_connection, "kg_triangle_count_global")

    def test_arno_call_raises_unknown_fn(self, iris_connection):
        from iris_vector_graph.stores.arno_bridge import _probe_cache, _conn_key
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True, "lib_path": "/fake/libarno.so"}
        with pytest.raises(ArnoError, match="No SQL wrapper"):
            arno_call(iris_connection, "nonexistent_function")


# ---------------------------------------------------------------------------
# arno_bridge.py — clear_probe_cache
# ---------------------------------------------------------------------------

class TestClearProbeCache:

    def test_clear_probe_cache(self, iris_connection):
        from iris_vector_graph.stores.arno_bridge import _probe_cache, _conn_key
        key = _conn_key(iris_connection)
        _probe_cache[key] = {"available": True}
        clear_probe_cache()
        assert len(_probe_cache) == 0


# ---------------------------------------------------------------------------
# _engine/nodes_edges.py — nodes_exist fallback (L1031-1041)
# ---------------------------------------------------------------------------

class TestNodesExistFallback:

    def test_nodes_exist_basic(self, base_eng):
        result = base_eng.nodes_exist(["fb_0", "fb_1", "fb_99_nonexistent"])
        assert "fb_0" in result
        assert "fb_1" in result
        assert "fb_99_nonexistent" not in result

    def test_nodes_exist_empty(self, base_eng):
        result = base_eng.nodes_exist([])
        assert result == set()

    def test_nodes_exist_in_fails_falls_back(self, base_eng):
        # Mock the cursor to fail on IN query and succeed on individual queries
        original_cursor = base_eng.conn.cursor
        call_count = [0]

        def mock_cursor():
            mc = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # First cursor call — IN query fails
                mc.execute.side_effect = Exception("IN not supported")
            else:
                # Individual count queries work
                mc.execute.return_value = None
                mc.fetchone.return_value = (1,)
            mc.close = MagicMock()
            return mc

        with patch.object(base_eng.conn, "cursor", side_effect=mock_cursor):
            result = base_eng.nodes_exist(["fb_0"])
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# _engine/nodes_edges.py — store_edge duplicate handling (L1005-1008)
# ---------------------------------------------------------------------------

class TestStoreEdgeDuplicate:

    def test_store_edge_duplicate_ignored(self, base_eng):
        # Create the same edge twice — second should be silent duplicate
        result1 = base_eng.store_edge("fb_0", "DUP_REL", "fb_1")
        result2 = base_eng.store_edge("fb_0", "DUP_REL", "fb_1")
        assert result1 is True
        assert result2 is True

    def test_store_edge_non_duplicate_raises(self, base_eng):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("some other database error -999")
        mock_cursor.close = MagicMock()
        with patch.object(base_eng.conn, "cursor", return_value=mock_cursor):
            with pytest.raises(Exception, match="some other database error"):
                base_eng.store_edge("fb_0", "ERR_REL", "fb_1")


# ---------------------------------------------------------------------------
# _engine/nodes_edges.py — bulk_ingest_nodes SQL path (L600-622)
# ---------------------------------------------------------------------------

class TestBulkCreateNodes:

    def test_bulk_create_nodes_basic(self, base_eng):
        nodes = [
            {"id": "bulk_a", "labels": ["BK"], "properties": {"x": 1}},
            {"id": "bulk_b", "labels": ["BK"], "properties": {"x": 2}},
        ]
        result = base_eng.bulk_create_nodes(nodes)
        assert isinstance(result, list)

    def test_bulk_create_nodes_empty(self, base_eng):
        result = base_eng.bulk_create_nodes([])
        assert result == []

    def test_bulk_create_nodes_objectscript_fails_sql_fallback(self, base_eng):
        nodes = [
            {"id": "bulk_c", "labels": ["BK"], "properties": {"x": 3}},
        ]
        with patch.object(base_eng.capabilities.__class__, "objectscript_deployed",
                          new_callable=lambda: property(lambda self: True)):
            with patch.object(base_eng, "_iris_obj", side_effect=Exception("no iris obj")):
                result = base_eng.bulk_create_nodes(nodes)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _engine/nodes_edges.py — bulk_ingest_edges SQL path (L786-802)
# ---------------------------------------------------------------------------

class TestBulkIngestEdges:

    def test_bulk_ingest_edges_basic(self, base_eng):
        edges = [
            {"s": "fb_0", "p": "BULK_E", "o": "fb_2"},
            {"s": "fb_1", "p": "BULK_E", "o": "fb_3"},
        ]
        result = base_eng.bulk_ingest_edges(edges)
        assert isinstance(result, int)

    def test_bulk_ingest_edges_empty(self, base_eng):
        result = base_eng.bulk_ingest_edges([])
        assert result == 0

    def test_bulk_ingest_edges_objectscript_fails_sql_fallback(self, base_eng):
        edges = [{"s": "fb_0", "p": "BK_E2", "o": "fb_2"}]
        with patch.object(base_eng.capabilities.__class__, "objectscript_deployed",
                          new_callable=lambda: property(lambda self: True)):
            with patch.object(base_eng, "_iris_obj", side_effect=Exception("no iris")):
                result = base_eng.bulk_ingest_edges(edges)
        assert isinstance(result, int)
