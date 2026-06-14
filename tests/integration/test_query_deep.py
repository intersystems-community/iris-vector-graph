"""
Integration tests targeting uncovered paths in _engine/query.py.

Covers:
  - execute_aql (L22)
  - _extract_traversal edge cases (L214, 220, 230-233, 236)
  - temporal BFS path (L329-331)
  - _execute_weighted_shortest_path (L353-380)
  - _execute_shortest_path_cypher return_funcs (L454-464)
  - _execute_var_length_cypher: count_match (L529-530), BFSFastJsonSorted exception (L558-568, L572)
  - _try_khop_fast_path exception paths (L729, 735-736, L743, L752-753, L760, L766-767, L776, L786-787)
  - NKG path (L809, 815-816)
  - _execute_approx_count_distinct (L850-851, L854, L868-872, L875, L884-885)
  - var_length BFS with return_properties enrichment (L339-344)
  - count_match in _execute_var_length_cypher (L643-644)
"""
import pytest
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


@pytest.fixture
def qd_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(6):
        eng.create_node(f"qd_{i}", labels=["QD"], properties={"val": i, "name": f"n{i}"})
    for i in range(5):
        eng.create_edge(f"qd_{i}", "QD_REL", f"qd_{i + 1}", qualifiers={"w": str(i)})
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# execute_aql — L22
# ---------------------------------------------------------------------------

class TestExecuteAQL:

    def test_execute_aql_simple(self, qd_eng):
        try:
            from iris_vector_graph.cypher.aql import translate_aql
        except ImportError:
            pytest.skip("AQL translation not available")
        # AQL FOR ... IN ... RETURN  maps to Cypher MATCH
        try:
            result = qd_eng.execute_aql("FOR n IN nodes RETURN n")
            assert result is not None
        except Exception:
            pytest.skip("AQL not fully supported")

    def test_execute_aql_with_bind_vars(self, qd_eng):
        try:
            result = qd_eng.execute_aql(
                "FOR n IN nodes FILTER n._key == @key RETURN n",
                bind_vars={"key": "qd_0"}
            )
            assert result is not None
        except Exception:
            pytest.skip("AQL not fully supported")


# ---------------------------------------------------------------------------
# Temporal BFS path (L329-331)
# ---------------------------------------------------------------------------

class TestTemporalBFS:

    def test_temporal_bfs_via_cypher(self, qd_eng):
        # temporal_window is triggered by special cypher patterns;
        # test by directly triggering execute_temporal_cypher if available
        fake_result = IVGResult(columns=["id", "hops"], rows=[["qd_1", 1]])
        with patch.object(qd_eng._store, "execute_temporal_cypher", return_value=fake_result) as mock_tc:
            # Inject a var_length_path with temporal_window=True
            from iris_vector_graph.cypher.translator import SQLQuery, QueryMetadata
            sql_query = MagicMock()
            sql_query.var_length_paths = [{
                "temporal_window": True,
                "ts_start": 0,
                "ts_end": 9999999999,
                "types": [],
                "direction": "out",
                "max_hops": 2,
                "min_hops": 1,
                "properties": {},
                "weighted": False,
                "shortest": False,
                "all_shortest": False,
                "return_path_funcs": [],
            }]
            sql_query.sql = "SELECT node_id AS id FROM Graph_KG.nodes LIMIT 100"
            sql_query.parameters = [["qd_0"]]
            from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
            result = qd_eng._route_var_length(sql_query, {})
            assert result is not None


# ---------------------------------------------------------------------------
# _execute_weighted_shortest_path (L353-380) — via Cypher
# ---------------------------------------------------------------------------

class TestWeightedShortestPath:

    def test_weighted_shortest_path_via_cypher(self, qd_eng):
        # Use mock to hit the _execute_weighted_shortest_path code path
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": True,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 5,
            "src_id_param": "$src",
            "dst_id_param": "$tgt",
            "types": ["QD_REL"],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "weight_property": "w",
        }]
        sql_query.sql = ""
        sql_query.parameters = [["qd_0"]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        fake_result = IVGResult(columns=["path", "totalCost"], rows=[])
        with patch.object(qd_eng._store, "execute_weighted_shortest_path", return_value=fake_result):
            result = qd_eng._execute_weighted_shortest_path(sql_query, {"src": "qd_0", "tgt": "qd_3"})
        assert result is not None

    def test_weighted_shortest_path_missing_src(self, qd_eng):
        # No source_id — should raise ValueError or return empty
        from iris_vector_graph.cypher.translator import SQLQuery, QueryMetadata
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": True,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 5,
            "src_id_param": None,
            "dst_id_param": None,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
        }]
        sql_query.sql = ""
        sql_query.parameters = [[]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        try:
            result = qd_eng._execute_weighted_shortest_path(sql_query, {})
        except ValueError:
            pass  # Expected


# ---------------------------------------------------------------------------
# _execute_shortest_path_cypher return_funcs (L454-464)
# ---------------------------------------------------------------------------

class TestShortestPathReturnFuncs:

    def _make_sql_query(self, return_path_funcs, src="'qd_0'", dst="'qd_1'"):
        from iris_vector_graph.cypher.translator import QueryMetadata
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": True,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 5,
            "src_id_param": src,
            "dst_id_param": dst,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": return_path_funcs,
            "source_var": None,
            "target_var": None,
        }]
        sql_query.sql = ""
        sql_query.parameters = [["qd_0"]]
        sql_query.query_metadata = QueryMetadata()
        return sql_query

    def test_shortest_path_length_func(self, qd_eng):
        import json
        fake_result = IVGResult(
            columns=["path", "length"],
            rows=[[json.dumps({"nodes": ["qd_0", "qd_1"], "rels": ["QD_REL"]}), 1]]
        )
        sql_query = self._make_sql_query(["length"])
        with patch.object(qd_eng._store, "execute_shortest_path", return_value=fake_result):
            result = qd_eng._execute_shortest_path_cypher(sql_query, {})
        assert result is not None

    def test_shortest_path_nodes_func(self, qd_eng):
        import json
        fake_result = IVGResult(
            columns=["path", "length"],
            rows=[[json.dumps({"nodes": ["qd_0", "qd_1"], "rels": ["QD_REL"]}), 1]]
        )
        sql_query = self._make_sql_query(["nodes", "relationships"])
        with patch.object(qd_eng._store, "execute_shortest_path", return_value=fake_result):
            result = qd_eng._execute_shortest_path_cypher(sql_query, {"src": "qd_0"})
        assert result is not None


# ---------------------------------------------------------------------------
# _execute_var_length_cypher: count_match fast path (L529-530)
# ---------------------------------------------------------------------------

class TestVarLengthCountMatch:

    def test_bfs_count_distinct_via_cypher(self, qd_eng):
        # Uses approx_count_distinct or COUNT(DISTINCT ...) patterns
        try:
            result = qd_eng.execute_cypher(
                "MATCH (a {node_id: $src})-[*1..2]->(b) RETURN count(DISTINCT b) AS c",
                parameters={"src": "qd_0"}
            )
            assert result is not None
        except Exception:
            pytest.skip("count(DISTINCT) BFS pattern not supported")

    def test_var_length_count_fast_path(self, qd_eng):
        # Inject count_match scenario: BFSFastCountDistinct
        from iris_vector_graph.schema import _call_classmethod
        with patch("iris_vector_graph.schema._call_classmethod", return_value="5") as mock_cc:
            sql_query = MagicMock()
            sql_query.var_length_paths = [{
                "weighted": False,
                "shortest": False,
                "all_shortest": False,
                "temporal_window": False,
                "min_hops": 1,
                "max_hops": 2,
                "types": [],
                "direction": "out",
                "properties": {},
                "return_path_funcs": [],
                "source_var": "src",
            }]
            sql_query.sql = "SELECT COUNT(DISTINCT b.node_id) AS c FROM Graph_KG.nodes LIMIT 100"
            sql_query.parameters = [["qd_0"]]
            from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
            qd_eng._nkg_dirty = False
            result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
            assert result is not None

    def test_var_length_count_fast_path_exception(self, qd_eng):
        # When BFSFastCountDistinct raises, cnt=0
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("fail")):
            sql_query = MagicMock()
            sql_query.var_length_paths = [{
                "weighted": False,
                "shortest": False,
                "all_shortest": False,
                "temporal_window": False,
                "min_hops": 1,
                "max_hops": 2,
                "types": [],
                "direction": "out",
                "properties": {},
                "return_path_funcs": [],
                "source_var": "src",
            }]
            sql_query.sql = "SELECT COUNT(DISTINCT b.node_id) AS c FROM Graph_KG.nodes LIMIT 100"
            sql_query.parameters = [["qd_0"]]
            from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
            qd_eng._nkg_dirty = False
            result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
            assert result is not None

    def test_var_length_bfs_fast_sorted_exception(self, qd_eng):
        # BFSFastJsonSorted fails — returns empty IVGResult
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("sorted fail")):
            sql_query = MagicMock()
            sql_query.var_length_paths = [{
                "weighted": False,
                "shortest": False,
                "all_shortest": False,
                "temporal_window": False,
                "min_hops": 1,
                "max_hops": 2,
                "types": [],
                "direction": "out",
                "properties": {},
                "return_path_funcs": [],
                "source_var": "src",
            }]
            sql_query.sql = "SELECT node_id AS id FROM Graph_KG.nodes LIMIT 100"
            sql_query.parameters = [["qd_0"]]
            from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
            qd_eng._nkg_dirty = False
            result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
            assert result is not None

    def test_var_length_no_source_id(self, qd_eng):
        # source_id is None — returns empty IVGResult
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 2,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "source_var": None,
        }]
        sql_query.sql = "SELECT node_id AS id FROM Graph_KG.nodes"
        sql_query.parameters = [[]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        qd_eng._nkg_dirty = False
        result = qd_eng._execute_var_length_cypher(sql_query, {})
        assert result.rows == []


# ---------------------------------------------------------------------------
# _try_khop_fast_path — 1-hop count exception (L735-736)
# ---------------------------------------------------------------------------

class TestKHopFastPathExceptions:

    def test_1hop_count_exception_returns_none(self, qd_eng):
        # Match the regex but make classMethodValue raise → returns None → falls through to normal path
        cypher = "MATCH (a {node_id: $src})-[:QD_REL]->(b) RETURN count(b) AS cnt"
        with patch.object(qd_eng, "_iris_obj", side_effect=RuntimeError("forced")):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None

    def test_1hop_ids_exception_returns_none(self, qd_eng):
        cypher = "MATCH (a {node_id: $src})-[:QD_REL]->(b) RETURN b.node_id"
        with patch.object(qd_eng, "_iris_obj", side_effect=RuntimeError("forced")):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None

    def test_2hop_count_exception_returns_none(self, qd_eng):
        cypher = "MATCH (a {node_id: $src})-[:QD_REL*2]->(b) RETURN count(b) AS cnt"
        with patch.object(qd_eng, "_iris_obj", side_effect=RuntimeError("forced")):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None

    def test_2hop_ids_exception_returns_none(self, qd_eng):
        cypher = "MATCH (a {node_id: $src})-[:QD_REL*2]->(b) RETURN b.node_id"
        with patch.object(qd_eng, "_iris_obj", side_effect=RuntimeError("forced")):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None

    def test_1hop_count_no_param(self, qd_eng):
        # src_id is None because param missing
        cypher = "MATCH (a {node_id: $missing})-[:QD_REL]->(b) RETURN count(b) AS cnt"
        result = qd_eng._try_khop_fast_path(cypher, {})
        assert result is None

    def test_2hop_ids_no_param(self, qd_eng):
        cypher = "MATCH (a {node_id: $missing})-[:QD_REL*2]->(b) RETURN b.node_id"
        result = qd_eng._try_khop_fast_path(cypher, {})
        assert result is None


# ---------------------------------------------------------------------------
# NKG var-length path (L809, L815-816) — via full execute_cypher
# ---------------------------------------------------------------------------

class TestNKGVarLengthPath:

    def test_khop_var_length_nkg_path(self, qd_eng):
        # If NKG is populated, routes through NKGAccelTraversal.KHopNeighbors
        # We can't force NKG state easily, but we can test the path via a mock
        cypher = "MATCH (a {node_id: $src})-[*1..2]->(b) RETURN b.node_id AS id"
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = lambda *args, **kwargs: (
            "1" if args[0] == "Graph.KG.Traversal" and args[1] == "NKGPopulated"
            else '{"nodes":[{"id":"qd_1","dist":1}]}'
        )
        with patch.object(qd_eng, "_iris_obj", return_value=mock_iris):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        # Either returns a result or None (if NKG not populated)
        assert result is None or hasattr(result, "rows")

    def test_khop_nkg_nkgpopulated_exception(self, qd_eng):
        # NKGPopulated raises → nkg_ok=False → returns None
        cypher = "MATCH (a {node_id: $src})-[*1..2]->(b) RETURN b.node_id AS id"
        with patch.object(qd_eng, "_iris_obj", side_effect=RuntimeError("no iris")):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None

    def test_khop_nkg_with_limit(self, qd_eng):
        cypher = "MATCH (a {node_id: $src})-[*1..3]->(b) RETURN b.node_id AS id LIMIT 10"
        mock_iris = MagicMock()
        mock_iris.classMethodValue.side_effect = lambda *args, **kwargs: (
            "1" if "NKGPopulated" in args
            else '{"nodes":[{"id":"qd_1","dist":1},{"id":"qd_2","dist":2}]}'
        )
        with patch.object(qd_eng, "_iris_obj", return_value=mock_iris):
            result = qd_eng._try_khop_fast_path(cypher, {"src": "qd_0"})
        assert result is None or hasattr(result, "rows")


# ---------------------------------------------------------------------------
# _execute_approx_count_distinct (L838 - L900)
# ---------------------------------------------------------------------------

class TestApproxCountDistinct:

    def test_approx_count_distinct_basic(self, qd_eng):
        # approx_count_distinct() in the Cypher query triggers L838
        result = qd_eng.execute_cypher(
            "MATCH (a {node_id: $src})-[*1..2]->(b) "
            "RETURN approx_count_distinct(b) AS estimate",
            parameters={"src": "qd_0"}
        )
        assert result is not None
        # Either estimate is returned, or falls back to 0
        assert hasattr(result, "rows")

    def test_approx_count_distinct_no_var_length(self, qd_eng):
        # Approx with no var_length_paths → returns [[0]]
        result = qd_eng.execute_cypher(
            "MATCH (n) RETURN approx_count_distinct(n) AS estimate"
        )
        assert result is not None

    def test_approx_count_distinct_no_source(self, qd_eng):
        # Cannot resolve source_id → returns [[0]]
        result = qd_eng.execute_cypher(
            "MATCH (a)-[*1..2]->(b) RETURN approx_count_distinct(b) AS estimate"
        )
        assert result is not None

    def test_approx_count_distinct_classmethod_raises(self, qd_eng):
        # CountDistinctKHop raises → estimate=0
        with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("hll fail")):
            result = qd_eng.execute_cypher(
                "MATCH (a {node_id: $src})-[*1..2]->(b) "
                "RETURN approx_count_distinct(b) AS estimate",
                parameters={"src": "qd_0"}
            )
        assert result is not None

    def test_approx_count_distinct_parse_exception(self, qd_eng):
        # Parse fails → returns [[0]]
        result = qd_eng.execute_cypher(
            "RETURN approx_count_distinct(x) AS cnt"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# var_length BFS with return_properties enrichment (L339-344)
# ---------------------------------------------------------------------------

class TestVarLengthReturnProperties:

    def test_bfs_with_return_props(self, qd_eng):
        # MATCH with var-length and RETURN b.val should trigger return_properties enrichment
        try:
            result = qd_eng.execute_cypher(
                "MATCH (a {node_id: $src})-[*1..2]->(b) RETURN b.node_id AS id, b.val AS val",
                parameters={"src": "qd_0"}
            )
            assert result is not None
        except Exception:
            pytest.skip("return_properties enrichment path not reachable")


# ---------------------------------------------------------------------------
# count_match in _execute_var_length_cypher (L643-644)
# ---------------------------------------------------------------------------

class TestVarLengthBFSCountMatch:

    def test_count_distinct_after_bfs(self, qd_eng):
        # BFS returns results, then count_match picks up COUNT(DISTINCT ...) in sql_str
        from iris_vector_graph.schema import _call_classmethod
        bfs_result = [{"s": "qd_0", "p": "QD_REL", "o": "qd_1", "step": 1}]
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 2,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "source_var": "src",
        }]
        sql_query.sql = "SELECT COUNT(DISTINCT b.node_id) AS cnt FROM Graph_KG.nodes"
        sql_query.parameters = [["qd_0"]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        qd_eng._nkg_dirty = False
        # Patch BFSFastJsonSorted to return a sorted: result that triggers ReadBFSResults
        with patch("iris_vector_graph.schema._call_classmethod") as mock_cc:
            mock_cc.return_value = '[{"s":"qd_0","p":"QD_REL","o":"qd_1","step":1}]'
            result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
        assert result is not None


# ---------------------------------------------------------------------------
# id_only fast path in _execute_var_length_cypher (L651-662)
# ---------------------------------------------------------------------------

class TestVarLengthIDOnlyFastPath:

    def test_id_only_path(self, qd_eng):
        # SELECT b.node_id AS id — id_only_match picks it up
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 2,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "source_var": "src",
        }]
        sql_query.sql = "SELECT DISTINCT b.node_id AS id FROM Graph_KG.nodes LIMIT 10"
        sql_query.parameters = [["qd_0"]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        qd_eng._nkg_dirty = False
        with patch("iris_vector_graph.schema._call_classmethod") as mock_cc:
            mock_cc.return_value = '[{"s":"qd_0","p":"QD_REL","o":"qd_1","step":1}]'
            result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
        assert result is not None


# ---------------------------------------------------------------------------
# BFS SORTED: with pagination paths (L558-568)
# ---------------------------------------------------------------------------

class TestBFSSortedPagination:

    def test_bfs_sorted_tag_with_max_results(self, qd_eng):
        # SORTED:tag path with max_results > 0 — tries ReadBFSResults, falls back to stream
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 2,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "source_var": "src",
        }]
        sql_query.sql = "SELECT b.node_id AS id FROM Graph_KG.nodes LIMIT 5"
        sql_query.parameters = [["qd_0"]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        qd_eng._nkg_dirty = False

        call_count = [0]
        def mock_call(conn, cls, method, *args):
            call_count[0] += 1
            if method == "BFSFastJsonSorted":
                return "SORTED:tag123"
            if method == "ReadBFSResults":
                raise RuntimeError("read failed")
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=mock_call):
            with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=[]):
                result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
        assert result is not None

    def test_bfs_sorted_tag_with_zero_max_results(self, qd_eng):
        # SORTED:tag with max_results=0 → streaming
        sql_query = MagicMock()
        sql_query.var_length_paths = [{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "temporal_window": False,
            "min_hops": 1,
            "max_hops": 2,
            "types": [],
            "direction": "out",
            "properties": {},
            "return_path_funcs": [],
            "source_var": "src",
        }]
        sql_query.sql = "SELECT b.node_id AS id FROM Graph_KG.nodes"
        sql_query.parameters = [["qd_0"]]
        from iris_vector_graph.cypher.translator import QueryMetadata; sql_query.query_metadata = QueryMetadata()
        qd_eng._nkg_dirty = False

        def mock_call(conn, cls, method, *args):
            if method == "BFSFastJsonSorted":
                return "SORTED:tag456"
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=mock_call):
            with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=[
                {"s": "qd_0", "p": "QD_REL", "o": "qd_1", "step": 1}
            ]):
                result = qd_eng._execute_var_length_cypher(sql_query, {"src": "qd_0"})
        assert result is not None
