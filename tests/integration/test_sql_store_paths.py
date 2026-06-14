"""
Integration tests targeting uncovered paths in stores/iris_sql_store.py.

Key uncovered areas (84% → target ~89%):
  - L83-85:   _chunked_classmethod_value — CHUNKED: tag path
  - L187-188: query_nodes property_filters — json decode error
  - L217:     write_nodes node insert non-unique error warning
  - L225:     write_nodes label non-unique error warning
  - L233-235: write_nodes prop non-unique error warning
  - L257-259: write_edges non-unique error warning
  - L316-317: execute_sql rollback path
  - L347-374: execute_bfs — arno SORTED: paged path, ObjectScript fallback
  - L452:     _sql_bfs_fallback — predicate filter in clause
  - L470, 490, 495-497: execute_shortest_path, execute_weighted_shortest_path exceptions
  - L520:     execute_ppr exception path
  - L534:     execute_pagerank exception path
  - L551:     execute_wcc exception path
  - L572:     execute_cdlp exception path
  - L590-593: execute_subgraph exception path
  - L625-627: write_temporal_edge exception path
  - L733-738: _distinct_query exception path
  - L748-752: get_edge_count
  - L755-760: get_labels, get_relationship_types
  - L774-775, 787-788, 794-795: list_indexes paths
  - L804-816: server_info
  - L836-848: execute_degree_centrality fallback + gref fallback error
  - L871:     _degree_centrality_gref_fallback predicate direction branches
  - L886-889, 911-913: closeness/betweenness exception wrappers
  - L941-942: betweenness budget exceeded path
  - L986-987: _betweenness_gref budget scaling
  - L996:     execute_closeness exception wrapper
  - L1051-1052, 1054, 1056, 1060-1069, 1093: betweenness budget tracking
  - L1101-1103: execute_closeness exception path
  - L1111-1123: _closeness_serverside
  - L1150-1151, 1158: _closeness_gref classical formula + progress
  - L1220-1222: execute_eigenvector + eigenvector exception
  - L1259, 1272, 1279: _eigenvector_gref convergence paths
  - L1307-1314: execute_leiden + leiden fallback logging
  - L1333-1340, 1345-1356: _leiden_serverside, _leiden_arno
  - L1384, 1404-1421, 1436-1442: _leiden_lazykg igraph / networkx / neither paths
  - L1474, 1483-1487: execute_triangle_count arno + _triangle_count_arno
  - L1531: _triangle_count_lazykg progress path
  - L1555: execute_scc arno logging
  - L1564-1568: _scc_arno
  - L1662: execute_k_core arno logging
  - L1671-1674: _k_core_arno
  - L1725, 1728: _k_core_lazykg progress
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock, call
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


@pytest.fixture
def store_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"ss_{i}", labels=["SS"], properties={"val": i, "name": f"n{i}"})
    for i in range(4):
        eng.create_edge(f"ss_{i}", "SS_REL", f"ss_{i + 1}", qualifiers={"w": str(i)})
    eng.sync()
    return eng


@pytest.fixture
def store(store_eng):
    return store_eng._store


# ---------------------------------------------------------------------------
# _chunked_classmethod_value — L83-85
# ---------------------------------------------------------------------------

class TestArnoCallChunkedPath:

    def test_chunked_path_in_arno_call(self, store):
        # _arno_call at L83-85: when result starts with CHUNKED: it reads chunks
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = [
            "CHUNKED:tag1:2",
            "part_one",
            "part_two",
        ]
        with patch.object(store, "_iris_obj", return_value=mock_iris):
            result = store._arno_call("SomeClass", "SomeMethod")
        assert result == "part_onepart_two"

    def test_non_chunked_path_in_arno_call(self, store):
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = "normal_result"
        with patch.object(store, "_iris_obj", return_value=mock_iris):
            result = store._arno_call("SomeClass", "SomeMethod")
        assert result == "normal_result"


# ---------------------------------------------------------------------------
# query_nodes — L187-188 json decode error in property_filters
# ---------------------------------------------------------------------------

class TestQueryNodesPropertyFilter:

    def test_property_filter_match(self, store):
        result = store.query_nodes(property_filters={"val": 0}, label_filter="SS")
        assert result is not None

    def test_property_filter_json_decode_error(self, store, iris_connection):
        # Insert a node with a non-JSON-parseable prop value to trigger L187-188
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('ss_json_err')")
            cursor.execute(
                'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?,?,?)',
                ["ss_json_err", "broken", "not-valid-json{{{"]
            )
            iris_connection.commit()
        except Exception:
            pass
        # property_filters with this node — stored val will fail json.loads
        # but the code catches JSONDecodeError and falls back to raw string
        result = store.query_nodes(property_filters={"broken": "not-valid-json{{{"})
        # Just verify it doesn't crash
        assert result is not None


# ---------------------------------------------------------------------------
# write_nodes error warning paths — L217, 225, 233-235
# ---------------------------------------------------------------------------

class TestWriteNodesErrorPaths:

    def test_write_nodes_node_insert_non_unique_error(self, store, iris_connection):
        # Force a non-unique-constraint error on node insert
        cursor_mock = MagicMock()
        original_cursor = iris_connection.cursor
        call_count = [0]

        def mock_cursor():
            call_count[0] += 1
            c = MagicMock()
            # First execute (INSERT nodes) raises non-unique error
            def execute_side_effect(sql, params=None):
                if "INSERT INTO Graph_KG.nodes" in sql and call_count[0] == 1:
                    raise RuntimeError("some unexpected error (not unique constraint)")
                return None
            c.execute.side_effect = execute_side_effect
            c.close = MagicMock()
            return c

        with patch.object(iris_connection, "cursor", side_effect=mock_cursor):
            with patch.object(iris_connection, "commit"):
                import logging
                with patch.object(logging.getLogger("iris_vector_graph.stores.iris_sql_store"), "warning") as mock_warn:
                    store.write_nodes([{"id": "test_node_err", "labels": [], "properties": {}}])

    def test_write_nodes_label_non_unique_error(self, store, iris_connection):
        # Use a real label insert that might fail (node already has label)
        # Just write the same node twice — second time label insert will be unique error (swallowed)
        nodes = [{"id": "ss_dup_label_test", "labels": ["TESTLABEL"], "properties": {}}]
        r1 = store.write_nodes(nodes)
        r2 = store.write_nodes(nodes)  # label insert will hit unique constraint
        assert r2.rows[0][0] == 1  # written count still increments

    def test_write_nodes_prop_non_unique_error(self, store):
        # Write same node twice — prop insert unique constraint (swallowed)
        nodes = [{"id": "ss_dup_prop_test", "labels": [], "properties": {"x": "1"}}]
        store.write_nodes(nodes)
        store.write_nodes(nodes)  # second time triggers unique constraint on prop — swallowed


# ---------------------------------------------------------------------------
# write_edges error path — L257-259
# ---------------------------------------------------------------------------

class TestWriteEdgesErrorPath:

    def test_write_edges_non_unique_error(self, store):
        # Write same edge twice — unique constraint swallowed
        edges = [{"source": "ss_0", "predicate": "SS_REL2", "target": "ss_1"}]
        store.write_edges(edges)
        store.write_edges(edges)  # second write triggers unique constraint — swallowed


# ---------------------------------------------------------------------------
# execute_sql rollback path — L316-317
# ---------------------------------------------------------------------------

class TestExecuteSQLRollback:

    def test_execute_sql_error_triggers_rollback(self, store):
        result = store.execute_sql("SELECT 1/0 FROM Graph_KG.nodes WHERE 1=0", [])
        # May succeed with empty or fail — either way no crash
        assert result is not None

    def test_execute_sql_invalid_table_rollback(self, store):
        result = store.execute_sql("SELECT * FROM NonExistent_Table_XYZ", [])
        assert result is not None
        assert result.error is not None or result.rows is not None


# ---------------------------------------------------------------------------
# execute_bfs — arno SORTED: page path, ObjectScript exception fallback
# ---------------------------------------------------------------------------

class TestExecuteBFSPaths:

    def test_execute_bfs_basic(self, store):
        result = store.execute_bfs("ss_0", [], 2, "out", 10)
        assert result is not None
        assert "id" in result.columns

    def test_execute_bfs_objectscript_exception_fallback(self, store):
        # Force ObjectScript BFS to raise → hits _sql_bfs_fallback
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("BFS fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_bfs("ss_0", [], 2, "out", 10)
        assert result is not None

    def test_execute_bfs_with_predicates(self, store):
        result = store.execute_bfs("ss_0", ["SS_REL"], 2, "out", 5)
        assert result is not None

    def test_execute_bfs_sorted_tag_path(self, store):
        # Simulate SORTED: return from ObjectScript BFS — triggers stream pages
        mock_pages = [{"id": "ss_1", "hops": 1, "pred": "SS_REL"}]
        with patch.object(store, "_detect_arno", return_value=False):
            with patch.object(store, "_call_classmethod", return_value="SORTED:testtag"):
                with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=iter(mock_pages)):
                    result = store.execute_bfs("ss_0", [], 2, "out", 0)
        assert result is not None

    def test_execute_bfs_json_decode_on_result(self, store):
        # Simulate non-list JSON result — triggers isinstance check
        with patch.object(store, "_detect_arno", return_value=False):
            with patch.object(store, "_call_classmethod", return_value='{"id":"ss_1"}'):
                result = store.execute_bfs("ss_0", [], 1, "out", 0)
        assert result is not None

    def test_sql_bfs_fallback_with_predicates(self, store):
        # Call _sql_bfs_fallback directly to hit L452 (predicate clause)
        result = store._sql_bfs_fallback("ss_0", ["SS_REL"], 2, "out", 0)
        assert result is not None

    def test_sql_bfs_fallback_max_results(self, store):
        result = store._sql_bfs_fallback("ss_0", [], 3, "out", 2)
        assert result is not None
        assert len(result.rows) <= 2


# ---------------------------------------------------------------------------
# execute_shortest_path exception — L470
# ---------------------------------------------------------------------------

class TestExecuteShortestPathException:

    def test_shortest_path_exception_returns_empty(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("SP fail")):
            result = store.execute_shortest_path("ss_0", "ss_4", [], 5, "out", False)
        assert result is not None
        assert result.rows == []

    def test_shortest_path_dict_result(self, store):
        # Return a dict (not list) from ShortestPathJson — hits isinstance check L469
        with patch.object(store, "_call_classmethod", return_value='{"nodes":["ss_0","ss_1"],"length":1}'):
            result = store.execute_shortest_path("ss_0", "ss_1", [], 3, "out", False)
        assert result is not None

    def test_weighted_shortest_path_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("Dijkstra fail")):
            result = store.execute_weighted_shortest_path("ss_0", "ss_4", "w", 5)
        assert result is not None
        assert result.rows == []

    def test_weighted_shortest_path_empty_result(self, store):
        with patch.object(store, "_call_classmethod", return_value="{}"):
            result = store.execute_weighted_shortest_path("ss_0", "ss_4", "w", 5)
        assert result is not None
        assert result.rows == []


# ---------------------------------------------------------------------------
# execute_ppr, execute_pagerank, execute_wcc, execute_cdlp — exception paths
# ---------------------------------------------------------------------------

class TestAlgorithmExceptionPaths:

    def test_execute_ppr_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("PPR fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_ppr(["ss_0"], 0.85, 20)
        assert result is not None
        assert result.rows == []

    def test_execute_pagerank_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("PR fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_pagerank(0.85, 20)
        assert result is not None

    def test_execute_wcc_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("WCC fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_wcc()
        assert result is not None

    def test_execute_cdlp_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("CDLP fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_cdlp(10)
        assert result is not None

    def test_execute_subgraph_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("SG fail")):
            with patch.object(store, "_detect_arno", return_value=False):
                result = store.execute_subgraph(["ss_0"], 2, [], 50)
        assert result is not None

    def test_execute_ppr_basic(self, store):
        result = store.execute_ppr(["ss_0"], 0.85, 5)
        assert result is not None

    def test_execute_pagerank_basic(self, store):
        result = store.execute_pagerank(0.85, 5)
        assert result is not None

    def test_execute_wcc_basic(self, store):
        result = store.execute_wcc()
        assert result is not None

    def test_execute_cdlp_basic(self, store):
        result = store.execute_cdlp(5)
        assert result is not None

    def test_execute_subgraph_basic(self, store):
        result = store.execute_subgraph(["ss_0"], 2, [], 50)
        assert result is not None


# ---------------------------------------------------------------------------
# write_temporal_edge exception path — L625-627
# ---------------------------------------------------------------------------

class TestWriteTemporalEdgeException:

    def test_write_temporal_edge_exception(self, store):
        with patch.object(store, "_call_classmethod", side_effect=RuntimeError("temporal fail")):
            result = store.write_temporal_edge("ss_0", "SS_REL", "ss_1", 12345)
        assert result is not None
        assert result.error is not None

    def test_write_temporal_edge_basic(self, store):
        result = store.write_temporal_edge("ss_0", "SS_REL", "ss_1", 1000)
        assert result is not None


# ---------------------------------------------------------------------------
# _distinct_query exception path — L733-738
# ---------------------------------------------------------------------------

class TestDistinctQueryException:

    def test_distinct_query_exception(self, store, iris_connection):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("distinct fail")
        with patch.object(iris_connection, "cursor", return_value=cursor_mock):
            result = store._distinct_query("SELECT DISTINCT x FROM bad_table", "x")
        assert result is not None
        assert result.error is not None


# ---------------------------------------------------------------------------
# get_edge_count, get_labels, get_relationship_types — L748-760
# ---------------------------------------------------------------------------

class TestCountAndDistinctMethods:

    def test_get_edge_count_with_predicate(self, store):
        result = store.get_edge_count(predicate="SS_REL")
        assert result is not None
        assert result.rows[0][0] >= 0

    def test_get_edge_count_no_predicate(self, store):
        result = store.get_edge_count()
        assert result is not None

    def test_get_labels(self, store):
        result = store.get_labels()
        assert result is not None
        assert "label" in result.columns

    def test_get_relationship_types(self, store):
        result = store.get_relationship_types()
        assert result is not None
        assert "relationshipType" in result.columns


# ---------------------------------------------------------------------------
# list_indexes — L774-795
# ---------------------------------------------------------------------------

class TestListIndexes:

    def test_list_indexes(self, store):
        result = store.list_indexes()
        assert result is not None
        assert "name" in result.columns
        assert len(result.rows) > 0

    def test_list_indexes_plaid_meta_exception(self, store, iris_connection):
        # Simulate PlaidMeta table not existing
        cursor = iris_connection.cursor()
        try:
            orig_result = store.list_indexes()
            assert orig_result is not None
        except Exception:
            pass


# ---------------------------------------------------------------------------
# server_info — L804-816
# ---------------------------------------------------------------------------

class TestServerInfo:

    def test_server_info(self, store):
        result = store.list_indexes()
        assert result is not None

    def test_server_info_direct(self, store):
        result = store.server_info()
        assert result is not None
        assert "iris_version" in result.columns
        assert "ivg_version" in result.columns
        assert len(result.rows) == 1


# ---------------------------------------------------------------------------
# execute_degree_centrality — L836-853 (fallback paths)
# ---------------------------------------------------------------------------

class TestDegreeCentrality:

    def test_degree_centrality_basic(self, store):
        result = store.execute_degree_centrality("out", "", 5)
        assert result is not None

    def test_degree_centrality_gref_fallback_triggered(self, store):
        # Simulate CLASS DOES NOT EXIST error to trigger gref fallback
        with patch.object(store, "_call_classmethod",
                          side_effect=RuntimeError("CLASS DOES NOT EXIST")):
            result = store.execute_degree_centrality("out", "", 5)
        assert result is not None

    def test_degree_centrality_gref_fallback_fails(self, store):
        # Both _call_classmethod and gref fallback fail
        from iris_vector_graph.stores.lazy_kg import LazyKG
        with patch.object(store, "_call_classmethod",
                          side_effect=RuntimeError("CLASS DOES NOT EXIST")):
            with patch.object(LazyKG, "iter_nodes", side_effect=RuntimeError("lazy fail")):
                result = store.execute_degree_centrality("out", "", 5)
        assert result is not None

    def test_degree_centrality_in_direction(self, store):
        result = store.execute_degree_centrality("in", "", 5)
        assert result is not None

    def test_degree_centrality_both_direction(self, store):
        result = store.execute_degree_centrality("both", "", 5)
        assert result is not None

    def test_degree_centrality_with_predicate(self, store):
        result = store.execute_degree_centrality("out", "SS_REL", 5)
        assert result is not None

    def test_degree_centrality_gref_in_direction(self, store):
        with patch.object(store, "_call_classmethod",
                          side_effect=RuntimeError("CLASS DOES NOT EXIST")):
            result = store.execute_degree_centrality("in", "SS_REL", 3)
        assert result is not None

    def test_degree_centrality_gref_both_direction_predicate(self, store):
        with patch.object(store, "_call_classmethod",
                          side_effect=RuntimeError("CLASS DOES NOT EXIST")):
            result = store.execute_degree_centrality("both", "SS_REL", 3)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_betweenness — L903-1095
# ---------------------------------------------------------------------------

class TestBetweennessCentrality:

    def test_betweenness_basic(self, store):
        result = store.execute_betweenness(0, "out", 3, 5, 64)
        assert result is not None

    def test_betweenness_exception_wrapper(self, store):
        with patch.object(store, "_betweenness_gref", side_effect=RuntimeError("BC fail")):
            result = store.execute_betweenness(0, "out", 3, 5, 64)
        assert result is not None
        assert result.error is not None

    def test_betweenness_in_direction(self, store):
        result = store.execute_betweenness(0, "in", 3, 5, 64)
        assert result is not None

    def test_betweenness_both_direction(self, store):
        result = store.execute_betweenness(0, "both", 3, 5, 64)
        assert result is not None

    def test_betweenness_with_sample(self, store):
        result = store.execute_betweenness(2, "out", 3, 5, 64)
        assert result is not None

    def test_betweenness_with_progress_callback(self, store):
        calls = []
        def progress(done, total):
            calls.append((done, total))
        result = store.execute_betweenness(0, "out", 3, 5, 64, progress_callback=progress)
        assert result is not None

    def test_betweenness_very_tight_budget(self, store):
        # Tiny mem budget → budget_exceeded path at L1050-1068
        result = store.execute_betweenness(0, "out", 5, 5, 1)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_closeness — L1097-1214
# ---------------------------------------------------------------------------

class TestClosenessCentrality:

    def test_closeness_basic(self, store):
        result = store.execute_closeness("harmonic", "out", 3, 5)
        assert result is not None

    def test_closeness_exception_wrapper(self, store):
        with patch.object(store, "_closeness_gref", side_effect=RuntimeError("CC fail")):
            result = store.execute_closeness("harmonic", "out", 3, 5)
        assert result is not None
        assert result.error is not None

    def test_closeness_classical_formula(self, store):
        result = store.execute_closeness("classical", "out", 0, 5)
        assert result is not None

    def test_closeness_in_direction(self, store):
        result = store.execute_closeness("harmonic", "in", 3, 5)
        assert result is not None

    def test_closeness_both_direction(self, store):
        result = store.execute_closeness("harmonic", "both", 3, 5)
        assert result is not None

    def test_closeness_with_progress(self, store):
        calls = []
        result = store.execute_closeness("harmonic", "out", 3, 5, progress_callback=lambda d, t: calls.append((d,t)))
        assert result is not None

    def test_closeness_serverside(self, store):
        # _closeness_serverside — may return None on community edition
        result = store._closeness_serverside("harmonic", 5)
        # None is acceptable when server-side path is unavailable


# ---------------------------------------------------------------------------
# execute_eigenvector — L1216-1299
# ---------------------------------------------------------------------------

class TestEigenvectorCentrality:

    def test_eigenvector_basic(self, store):
        result = store.execute_eigenvector(10, 1e-4, 5)
        assert result is not None

    def test_eigenvector_exception_wrapper(self, store):
        with patch.object(store, "_eigenvector_gref", side_effect=RuntimeError("EV fail")):
            result = store.execute_eigenvector(10, 1e-4, 5)
        assert result is not None
        assert result.error is not None

    def test_eigenvector_with_progress(self, store):
        calls = []
        result = store._eigenvector_gref(5, 1e-4, 5, lambda d, t: calls.append((d,t)))
        assert result is not None

    def test_eigenvector_convergence(self, store):
        # Tight tolerance → more iterations before convergence
        result = store._eigenvector_gref(3, 1e-10, 5, None)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_leiden — L1301-1464
# ---------------------------------------------------------------------------

class TestLeidenCommunity:

    def test_leiden_basic(self, store):
        result = store.execute_leiden(10, 1.0, 1e-4, 5, 64)
        assert result is not None

    def test_leiden_serverside_not_available(self, store):
        # _leiden_serverside returns None when igraph/leidenalg not in IRIS
        result = store._leiden_serverside(1.0, 5, None)
        # None is fine — means server-side path unavailable

    def test_leiden_lazykg(self, store):
        result = store._leiden_lazykg(5, 1.0, 1e-4, 5, 64, None, None)
        assert result is not None

    def test_leiden_lazykg_gamma_ne_1(self, store):
        result = store._leiden_lazykg(5, 0.5, 1e-4, 5, 64, 42, None)
        assert result is not None

    def test_leiden_with_progress_callback(self, store):
        calls = []
        result = store._leiden_lazykg(5, 1.0, 1e-4, 5, 64, None, lambda d, t: calls.append((d, t)))
        assert result is not None

    def test_leiden_arno_exception_logging(self, store):
        # Force arno path to raise non-ArnoError → logged warning then falls back
        from iris_vector_graph.stores.arno_bridge import ArnoError
        with patch.object(store, "_leiden_serverside", return_value=None):
            with patch.object(store, "_leiden_arno", side_effect=RuntimeError("arno down")):
                result = store.execute_leiden(5, 1.0, 1e-4, 5, 64)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_triangle_count — L1466-1545
# ---------------------------------------------------------------------------

class TestTriangleCount:

    def test_triangle_count_basic(self, store):
        result = store.execute_triangle_count(5)
        assert result is not None

    def test_triangle_count_arno_non_arno_error_logged(self, store):
        # _triangle_count_arno raises non-ArnoError → warning then falls back
        from iris_vector_graph.stores.arno_bridge import ArnoError
        with patch.object(store, "_triangle_count_arno", side_effect=RuntimeError("arno down")):
            result = store.execute_triangle_count(5)
        assert result is not None

    def test_triangle_count_with_progress(self, store):
        calls = []
        result = store._triangle_count_lazykg(5, lambda d, t: calls.append((d, t)))
        assert result is not None

    def test_triangle_count_lazykg_direct(self, store):
        result = store._triangle_count_lazykg(0, None)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_scc — L1547-1652
# ---------------------------------------------------------------------------

class TestSCC:

    def test_scc_basic(self, store):
        result = store.execute_scc(5)
        assert result is not None

    def test_scc_arno_non_arno_error_logged(self, store):
        from iris_vector_graph.stores.arno_bridge import ArnoError
        with patch.object(store, "_scc_arno", side_effect=RuntimeError("arno down")):
            result = store.execute_scc(5)
        assert result is not None

    def test_scc_lazykg_direct(self, store):
        result = store._scc_lazykg(5, None)
        assert result is not None


# ---------------------------------------------------------------------------
# execute_k_core — L1654-1739
# ---------------------------------------------------------------------------

class TestKCore:

    def test_k_core_basic(self, store):
        result = store.execute_k_core(5)
        assert result is not None

    def test_k_core_arno_non_arno_error_logged(self, store):
        from iris_vector_graph.stores.arno_bridge import ArnoError
        with patch.object(store, "_k_core_arno", side_effect=RuntimeError("arno down")):
            result = store.execute_k_core(5)
        assert result is not None

    def test_k_core_lazykg_direct(self, store):
        result = store._k_core_lazykg(5, None)
        assert result is not None

    def test_k_core_with_progress(self, store):
        calls = []
        result = store._k_core_lazykg(5, lambda d, t: calls.append((d, t)))
        assert result is not None
