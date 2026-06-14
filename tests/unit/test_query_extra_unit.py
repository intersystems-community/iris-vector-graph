"""
Extra coverage for _engine/query.py miss lines not covered by existing tests.
All tests use mocked connections — no live IRIS required.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# Semicolon multi-part: exception in sub-execute is swallowed (lines 114-115)
# ---------------------------------------------------------------------------

class TestSemicolonMultiPartException:

    def test_sub_execute_exception_skipped(self):
        """Lines 114-115: sub execute_cypher raises → exception swallowed, result still returned."""
        eng, conn, cursor = _make_eng()

        # Use a real semicolon+CALL query so the branch triggers
        # Patch _execute_parsed to raise on the first call, succeed on the second
        call_count = [0]

        def fake_parsed(cypher, *args, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first sub fails")
            return IVGResult(columns=["x"], rows=[["ok"]])

        with patch.object(eng, "_execute_parsed", side_effect=fake_parsed):
            result = eng.execute_cypher(
                "CALL apoc.something(); CALL apoc.other()", parameters={}
            )

        assert isinstance(result, IVGResult)
        # The failing part was skipped, second returned
        assert result.columns in [["x"], ["result"]]


# ---------------------------------------------------------------------------
# _execute_weighted_shortest_path (lines 353-380)
# ---------------------------------------------------------------------------

class TestExecuteWeightedShortestPath:

    def _make_sq(self, src="$src", dst="$dst"):
        sq = MagicMock()
        sq.var_length_paths = [{
            "src_id_param": src, "dst_id_param": dst,
            "weight_property": "weight", "max_hops": 5,
            "weighted": True, "types": [],
        }]
        sq.query_metadata = {}
        return sq

    def test_both_ids_resolved_calls_store(self):
        """Lines 353-380: source + target resolved → store.execute_weighted_shortest_path."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_weighted_shortest_path.return_value = {"nodes": ["n1", "n2"]}
        eng._store = store

        sq = self._make_sq(src="$src", dst="$dst")
        result = eng._execute_weighted_shortest_path(sq, {"src": "n1", "dst": "n2"})
        store.execute_weighted_shortest_path.assert_called_with("n1", "n2", "weight", 5)

    def test_source_none_raises_value_error(self):
        """Lines 375-380: src resolves None → ValueError."""
        eng, conn, cursor = _make_eng()
        sq = self._make_sq(src=None, dst="$dst")
        with pytest.raises(ValueError):
            eng._execute_weighted_shortest_path(sq, {"dst": "n2"})

    def test_quoted_string_param_stripped(self):
        """Line 363: src_id_param quoted string like 'n1' → strips quotes."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_weighted_shortest_path.return_value = {"nodes": []}
        eng._store = store

        sq = self._make_sq(src="'n1'", dst="'n2'")
        result = eng._execute_weighted_shortest_path(sq, {})
        store.execute_weighted_shortest_path.assert_called_with("n1", "n2", "weight", 5)


# ---------------------------------------------------------------------------
# _execute_shortest_path_cypher: parameter resolve paths (lines 396-428)
# ---------------------------------------------------------------------------

class TestExecuteShortestPathCypherPaths:

    def _make_sq(self, src="$src", dst="$dst", all_shortest=False):
        sq = MagicMock()
        sq.var_length_paths = [{
            "src_id_param": src, "dst_id_param": dst,
            "types": [], "max_hops": 5, "direction": "both",
            "all_shortest": all_shortest, "return_path_funcs": [],
            "source_var": "src", "target_var": "dst",
        }]
        sq.query_metadata = {}
        return sq

    def test_all_shortest_path(self):
        """Line 396: all_shortest=True → find_all=1."""
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod", return_value='[]'):
            sq = self._make_sq(all_shortest=True)
            result = eng._execute_shortest_path_cypher(sq, {"src": "n1", "dst": "n2"})
        assert isinstance(result, IVGResult)

    def test_source_resolved_from_source_var(self):
        """Line 408: source_id resolved from source_var in parameters."""
        eng, conn, cursor = _make_eng()
        sq = MagicMock()
        sq.var_length_paths = [{
            "src_id_param": None, "dst_id_param": "$dst",
            "types": [], "max_hops": 5, "direction": "both",
            "all_shortest": False, "return_path_funcs": [],
            "source_var": "src", "target_var": "dst",
        }]
        sq.query_metadata = {}
        with patch("iris_vector_graph.schema._call_classmethod", return_value='[]'):
            result = eng._execute_shortest_path_cypher(sq, {"src": "n1", "dst": "n2"})
        assert isinstance(result, IVGResult)

    def test_source_from_first_param_value(self):
        """Line 417: source_id from first parameters value (no source_var match)."""
        eng, conn, cursor = _make_eng()
        sq = MagicMock()
        sq.var_length_paths = [{
            "src_id_param": None, "dst_id_param": "$dst",
            "types": [], "max_hops": 5, "direction": "both",
            "all_shortest": False, "return_path_funcs": [],
            "source_var": "missing_var", "target_var": "dst",
        }]
        sq.query_metadata = {}
        with patch("iris_vector_graph.schema._call_classmethod", return_value='[]'):
            result = eng._execute_shortest_path_cypher(sq, {"dst": "n2", "other": "n1"})
        assert isinstance(result, IVGResult)

    def test_source_none_raises(self):
        """Lines 426-428: source_id None → ValueError."""
        eng, conn, cursor = _make_eng()
        sq = MagicMock()
        sq.var_length_paths = [{
            "src_id_param": None, "dst_id_param": None,
            "types": [], "max_hops": 5, "direction": "both",
            "all_shortest": False, "return_path_funcs": [],
            "source_var": None, "target_var": None,
        }]
        sq.query_metadata = {}
        with pytest.raises(ValueError):
            eng._execute_shortest_path_cypher(sq, {})


# ---------------------------------------------------------------------------
# _execute_var_length_cypher: source_id from source_var (lines 499-503)
# ---------------------------------------------------------------------------

class TestExecuteVarLengthSource:

    def test_source_id_from_source_var(self):
        """Lines 499-503: source_id from source_var in parameters."""
        eng, conn, cursor = _make_eng()
        eng._arno_capabilities = {}

        sq = MagicMock()
        sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 2, "direction": "out",
            "min_hops": 1, "properties": None, "return_path_funcs": [],
            "weighted": False, "shortest": False, "all_shortest": False,
            "source_var": "src", "is_count": False,
        }]
        sq.parameters = []
        sq.sql = "SELECT DISTINCT b.node_id AS node_id"
        sq.query_metadata = {}

        with patch("iris_vector_graph.schema._call_classmethod", return_value="[]"):
            result = eng._execute_var_length_cypher(sq, {"src": "n1"})
        assert isinstance(result, IVGResult)


# ---------------------------------------------------------------------------
# BFS SORTED paths (lines 558-568, 590-596)
# ---------------------------------------------------------------------------

class TestBfsSortedPaths:

    def _make_sq(self, source_id="n1", sql="SELECT DISTINCT b.node_id AS node_id LIMIT 10"):
        sq = MagicMock()
        sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 3, "direction": "out",
            "min_hops": 1, "properties": None, "return_path_funcs": [],
            "weighted": False, "shortest": False, "all_shortest": False,
            "source_var": None, "is_count": False,
        }]
        sq.parameters = [[source_id]]
        sq.sql = sql
        sq.query_metadata = {}
        return sq

    def test_arno_bfs_sorted_tag_with_max_results(self):
        """Lines 558-568: SORTED: tag + max_results > 0 → ReadBFSResults."""
        eng, conn, cursor = _make_eng()
        eng._arno_capabilities = {"arno": True, "bfs_json": True}
        sq = self._make_sq()

        call_results = {}
        def cm(conn_arg, cls, method, *args):
            call_results[method] = call_results.get(method, 0) + 1
            if method == "BFSJson":
                return "SORTED:abc123"
            if method == "ReadBFSResults":
                return json.dumps([{"o": "n2", "s": 1}])
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=cm):
            with patch.object(eng, "_detect_arno", return_value=True):
                result = eng._execute_var_length_cypher(sq, {})
        assert isinstance(result, IVGResult)

    def test_bfs_fast_json_sorted_tag_with_max_results(self):
        """Lines 590-596: BFSFastJsonSorted SORTED: tag + max_results → ReadBFSResults."""
        eng, conn, cursor = _make_eng()
        eng._arno_capabilities = {}
        sq = self._make_sq()

        def cm(conn_arg, cls, method, *args):
            if method == "BFSFastJsonSorted":
                return "SORTED:xyz789"
            if method == "ReadBFSResults":
                return json.dumps([{"o": "n2", "s": 1}])
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=cm):
            result = eng._execute_var_length_cypher(sq, {})
        assert isinstance(result, IVGResult)

    def test_bfs_fast_json_sorted_plain_results(self):
        """Line 572: Arno fails → fallback to BFSFastJsonSorted with plain JSON."""
        eng, conn, cursor = _make_eng()
        eng._arno_capabilities = {}
        sq = self._make_sq(sql="SELECT DISTINCT b.node_id AS node_id")

        results_json = json.dumps([{"o": "n2", "s": 1}])

        def cm(conn_arg, cls, method, *args):
            if method == "BFSFastJsonSorted":
                return results_json
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=cm):
            result = eng._execute_var_length_cypher(sq, {})
        assert isinstance(result, IVGResult)


# ---------------------------------------------------------------------------
# rel_props_filter + count_match (lines 618, 643-644)
# ---------------------------------------------------------------------------

class TestVarLengthFiltersAndCount:

    def test_rel_props_filter_applied(self):
        """Line 618: rel_props_filter → _filter_edges_by_properties called via Arno plain JSON."""
        eng, conn, cursor = _make_eng()
        # Use Arno path so plain JSON populates bfs_results (reaches line 618)
        eng._arno_capabilities = {"arno": True, "bfs": True, "rust_callout": True}

        sq = MagicMock()
        sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 2, "direction": "out",
            "min_hops": 1, "properties": {"weight": 0.5},
            "return_path_funcs": [],
            "weighted": False, "shortest": False, "all_shortest": False,
            "source_var": "src", "is_count": False,
        }]
        sq.parameters = []
        sq.sql = "SELECT DISTINCT b.node_id AS node_id"
        sq.query_metadata = {}

        bfs_results = [{"o": "n2", "s": 1, "weight": 0.9}]

        with patch("iris_vector_graph.schema._call_classmethod", return_value="[]"):
            with patch.object(eng, "_detect_arno", return_value=True):
                with patch.object(eng, "_arno_call", return_value=json.dumps(bfs_results)):
                    with patch.object(eng, "_filter_edges_by_properties", return_value=bfs_results) as mock_filter:
                        result = eng._execute_var_length_cypher(sq, {"src": "n1"})

        mock_filter.assert_called()
        assert isinstance(result, IVGResult)

    def test_count_match_early_path_returns_count(self):
        """Line 521-536: COUNT(DISTINCT) early path → BFSFastCountDistinct → returns immediately."""
        eng, conn, cursor = _make_eng()
        eng._arno_capabilities = {}

        sq = MagicMock()
        sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 2, "direction": "out",
            "min_hops": 1, "properties": None, "return_path_funcs": [],
            "weighted": False, "shortest": False, "all_shortest": False,
            "source_var": "src", "is_count": False,
        }]
        sq.parameters = []
        sq.sql = "SELECT COUNT(DISTINCT b.node_id) AS cnt FROM ..."
        sq.query_metadata = {}

        def cm(conn_arg, cls, method, *args):
            if method == "BFSFastCountDistinct":
                return "7"
            return "[]"

        with patch("iris_vector_graph.schema._call_classmethod", side_effect=cm):
            result = eng._execute_var_length_cypher(sq, {"src": "n1"})

        assert isinstance(result, IVGResult)
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 7


# ---------------------------------------------------------------------------
# _try_khop_fast_path: src_id=None branches (lines 743, 766-767, 776)
# ---------------------------------------------------------------------------

class TestKhopSrcNone:

    def test_1hop_ids_src_none_returns_none(self):
        """Line 743: 1-hop IDs match but src_id=None → return None."""
        eng, conn, cursor = _make_eng()
        q = "MATCH (n {node_id: $missing})-[:TREATS]->(m) RETURN m.node_id"
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "hop1\nhop2"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})  # 'missing' not in params
        assert result is None

    def test_2hop_count_src_none_returns_none(self):
        """Lines 766-767: 2-hop count match but src_id=None → return None."""
        eng, conn, cursor = _make_eng()
        q = "MATCH (n {node_id: $missing})-[:TREATS*2]->(m) RETURN count(m) AS cnt"
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "5"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})
        assert result is None

    def test_2hop_ids_src_none_returns_none(self):
        """Line 776: 2-hop IDs match but src_id=None → return None."""
        eng, conn, cursor = _make_eng()
        q = "MATCH (n {node_id: $missing})-[:TREATS*2]->(m) RETURN m.node_id"
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "hop1\nhop2"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {})
        assert result is None


# ---------------------------------------------------------------------------
# NKG accel: exception + limit path (lines 815-816, 832)
# ---------------------------------------------------------------------------

class TestNkgAccelPaths:

    def test_nkg_check_exception_returns_none(self):
        """Lines 815-816: NKGPopulated check raises → nkg_ok=False → None."""
        eng, conn, cursor = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..2]->(m) RETURN m.node_id"

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("NKG check failed")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "n1"})
        assert result is None

    def test_nkg_with_limit_applied(self):
        """Line 832: NKG results limited by limit."""
        eng, conn, cursor = _make_eng()
        q = "MATCH (n {node_id: $src})-[*1..2]->(m) RETURN m.node_id LIMIT 1"

        nkg_json = json.dumps({"nodes": [
            {"id": "seed", "dist": 0},
            {"id": "hop1", "dist": 1},
            {"id": "hop2", "dist": 2},
        ]})

        def cmv(cls, method, *args):
            if method == "NKGPopulated":
                return "1"
            if method == "KHopNeighbors":
                return nkg_json
            return "0"

        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = cmv
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng._try_khop_fast_path(q, {"src": "seed"})

        assert result is not None
        assert len(result.rows) == 1


# ---------------------------------------------------------------------------
# _execute_approx_count_distinct (lines 850-875)
# ---------------------------------------------------------------------------

class TestExecuteApproxCountDistinct:

    def _make_match(self, col_name="approx_cnt"):
        import re
        m = re.search(r"approx_count_distinct\((\w+)\)", f"approx_count_distinct({col_name})")
        # Wrap to provide group(2) which the method uses for col_name
        class FakeMatch:
            def group(self, n):
                return {"1": "approx_count_distinct", "2": col_name}[str(n)]
        return FakeMatch()

    def test_no_var_length_paths_returns_zero(self):
        """Lines 850-851: translate gives no var_length_paths → return 0."""
        eng, conn, cursor = _make_eng()

        mock_sq = MagicMock()
        mock_sq.var_length_paths = []

        with patch("iris_vector_graph._engine.query.parse_query"):
            with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=mock_sq):
                result = eng._execute_approx_count_distinct(
                    "MATCH (n)-[*1..2]->(m) RETURN count(m)", {}, self._make_match("cnt")
                )
        assert result.rows[0][0] == 0

    def test_parse_error_returns_zero(self):
        """Line 854: parse_query raises → return 0."""
        eng, conn, cursor = _make_eng()

        with patch("iris_vector_graph._engine.query.parse_query", side_effect=Exception("parse fail")):
            result = eng._execute_approx_count_distinct(
                "INVALID CYPHER", {}, self._make_match("cnt")
            )
        assert result.rows[0][0] == 0

    def test_source_from_source_var(self):
        """Lines 868-872: source resolved from source_var in parameters."""
        eng, conn, cursor = _make_eng()

        mock_sq = MagicMock()
        mock_sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 2, "direction": "both",
            "source_var": "src",
        }]
        mock_sq.parameters = []

        with patch("iris_vector_graph._engine.query.parse_query"):
            with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=mock_sq):
                with patch("iris_vector_graph.schema._call_classmethod", return_value="42"):
                    result = eng._execute_approx_count_distinct(
                        "MATCH (n)-[*1..2]->(m) RETURN count(m)", {"src": "n1"},
                        self._make_match("cnt")
                    )
        assert isinstance(result, IVGResult)

    def test_source_none_returns_zero(self):
        """Line 875: source_id None → return 0."""
        eng, conn, cursor = _make_eng()

        mock_sq = MagicMock()
        mock_sq.var_length_paths = [{
            "types": ["TREATS"], "max_hops": 2, "direction": "both",
            "source_var": None,
        }]
        mock_sq.parameters = []

        with patch("iris_vector_graph._engine.query.parse_query"):
            with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=mock_sq):
                result = eng._execute_approx_count_distinct(
                    "MATCH (n)-[*1..2]->(m) RETURN count(m)", {}, self._make_match("cnt")
                )
        assert result.rows[0][0] == 0
