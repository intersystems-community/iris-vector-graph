"""
Unit tests targeting remaining uncovered lines in:
  - iris_vector_graph/_engine/query.py
  - iris_vector_graph/_engine/snapshot.py
  - iris_vector_graph/engine.py

Uses a minimal mock engine to avoid IRIS connections.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph._engine.query import (
    _split_top_level_and,
    _handle_gds_shim,
    _build_path_func_columns,
    GDS_SHIM_MAP,
)


# ---------------------------------------------------------------------------
# Shared engine factory
# ---------------------------------------------------------------------------

def make_engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    cur.description = []
    conn.cursor.return_value = cur
    conn.commit.return_value = None
    conn.rollback.return_value = None
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._native_vec_available = False
    eng._embedding_function_available = False
    eng._arno_available = False
    eng._arno_capabilities = {}
    eng.embedding_dimension = 128
    eng._nkg_dirty = False
    eng._store_capabilities = {"native_sql": True}
    store = MagicMock()
    store.capabilities.return_value = {"native_sql": True}
    store.execute_sql.return_value = IVGResult(columns=[], rows=[])
    store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
    store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
    store.get_nodes.return_value = IVGResult(columns=["id", "labels"], rows=[])
    store.conn = conn
    store._schema_prefix = "Graph_KG"
    eng._store = store
    # Stub methods that touch IRIS
    eng._reconnect_if_stale = MagicMock()
    eng._try_system_procedure = MagicMock(return_value=None)
    eng._try_khop_fast_path = MagicMock(return_value=None)
    eng._detect_arno = MagicMock(return_value=False)
    eng._iris_obj = MagicMock(return_value=MagicMock())
    eng._handle_show_command = MagicMock(return_value=IVGResult(columns=[], rows=[]))
    eng._build_index_registry = MagicMock(return_value={})
    eng._index_registry = {}
    eng._pending_index_config = {}
    eng._fetch_first_unsafe_cached = False
    eng._connection_params = None
    eng._native_conn = None
    eng._table_mapping_cache = None
    eng._rel_mapping_cache = None
    eng._nkg_dirty = False
    eng.vector_dtype = "DOUBLE"
    eng._t = lambda name: f"Graph_KG.{name}"
    return eng, conn, cur


# ---------------------------------------------------------------------------
# _split_top_level_and  (lines 14-50)
# ---------------------------------------------------------------------------

class TestSplitTopLevelAnd:

    def test_single_condition(self):
        result = _split_top_level_and("a = 1")
        assert result == ["a = 1"]

    def test_two_top_level_conditions(self):
        result = _split_top_level_and("a = 1 AND b = 2")
        assert len(result) == 2
        assert "a = 1" in result
        assert "b = 2" in result

    def test_three_conditions(self):
        result = _split_top_level_and("a = 1 AND b = 2 AND c = 3")
        assert len(result) == 3

    def test_nested_paren_not_split(self):
        """AND inside parens must NOT be treated as a top-level separator."""
        result = _split_top_level_and("a = 1 AND (b = 2 AND c = 3)")
        assert len(result) == 2

    def test_exists_subquery_preserved(self):
        result = _split_top_level_and("EXISTS (SELECT 1 WHERE x = 1 AND y = 2) AND z = 3")
        assert len(result) == 2

    def test_empty_string_returns_list_with_empty(self):
        result = _split_top_level_and("")
        assert result == [""]

    def test_leading_and_skipped(self):
        result = _split_top_level_and("AND a = 1")
        # The leading AND at i==0 is skipped; result should contain "a = 1"
        assert len(result) >= 1

    def test_and_at_end(self):
        # ' AND' at end of string (i+4 >= len(text))
        result = _split_top_level_and("a = 1 AND")
        assert "a = 1" in result


# ---------------------------------------------------------------------------
# _handle_gds_shim  (lines 68-98)
# ---------------------------------------------------------------------------

class TestHandleGdsShim:

    def _make_proc(self, name, args=None):
        proc = MagicMock()
        proc.procedure_name = name
        proc.arguments = args or []
        proc.yield_items = []
        return proc

    def test_non_gds_returns_none(self):
        proc = self._make_proc("ivg.ppr")
        result = _handle_gds_shim(proc)
        assert result is None

    def test_unknown_gds_returns_error_result(self):
        proc = self._make_proc("gds.unknown.procedure")
        result = _handle_gds_shim(proc)
        assert isinstance(result, IVGResult)
        assert result.error is not None
        assert "not shimmed" in result.error

    def test_known_gds_returns_tuple(self):
        proc = self._make_proc("gds.pagerank.stream")
        result = _handle_gds_shim(proc)
        assert isinstance(result, tuple)
        shimmed, _ = result
        assert shimmed.procedure_name == "ivg.ppr"

    def test_dijkstra_shimmed(self):
        proc = self._make_proc("gds.shortestpath.dijkstra.stream")
        result = _handle_gds_shim(proc)
        assert isinstance(result, tuple)
        shimmed, _ = result
        assert shimmed.procedure_name == "ivg.shortestPath.weighted"

    def test_all_known_procedures_shimmed(self):
        for gds_name in GDS_SHIM_MAP:
            proc = self._make_proc(gds_name)
            result = _handle_gds_shim(proc)
            assert isinstance(result, tuple), f"Expected tuple for {gds_name}"

    def test_case_insensitive_matching(self):
        proc = self._make_proc("GDS.PageRank.Stream")
        result = _handle_gds_shim(proc)
        assert isinstance(result, tuple)

    def test_shimmed_proc_has_yield_items(self):
        proc = self._make_proc("gds.louvain.stream")
        proc.yield_items = ["communityId", "score"]
        result = _handle_gds_shim(proc)
        shimmed, _ = result
        assert shimmed.yield_items == ["communityId", "score"]


# ---------------------------------------------------------------------------
# _build_path_func_columns  (lines 101-164)
# ---------------------------------------------------------------------------

class TestBuildPathFuncColumns:

    def test_no_col_map_path_func(self):
        cols = _build_path_func_columns(["path"], "a", "b", {})
        assert "p" in cols

    def test_no_col_map_nodes_and_length(self):
        cols = _build_path_func_columns(["nodes", "length"], "src", "tgt", {})
        assert "src" in cols
        assert "tgt" in cols
        assert "l" in cols

    def test_no_col_map_relationships(self):
        cols = _build_path_func_columns(["relationships"], "a", "b", {})
        assert "relationships(p)" in cols

    def test_no_col_map_nodes_only(self):
        cols = _build_path_func_columns(["nodes"], "a", "b", {})
        assert "nodes(p)" in cols

    def test_no_col_map_empty_funcs(self):
        cols = _build_path_func_columns([], "a", "b", {})
        # Falls back to [source_var, target_var]
        assert cols == ["a", "b"]

    def test_with_col_map_source_target(self):
        col_map = {"a": "a", "b": "b"}
        cols = _build_path_func_columns([], "a", "b", col_map)
        assert "a" in cols
        assert "b" in cols

    def test_col_map_with_length_func(self):
        col_map = {"a": "a", "b": "b", "l": "length(p)"}
        cols = _build_path_func_columns(["length"], "a", "b", col_map)
        assert "l" in cols

    def test_col_map_remaining_added(self):
        # extra col_map entries not matched by funcs or vars
        col_map = {"a": "a", "extra": "some_other"}
        cols = _build_path_func_columns([], "a", "b", col_map)
        assert "extra" in cols

    def test_path_named_var_used(self):
        cols = _build_path_func_columns(["path"], "a", "b", {}, path_named_var="mypath")
        assert "mypath" in cols

    def test_col_map_func_expr_no_rename(self):
        # col_map where alias == cypher_expr for func(p)
        col_map = {"length(p)": "length(p)"}
        cols = _build_path_func_columns(["length"], "a", "b", col_map)
        assert "length(p)" in cols


# ---------------------------------------------------------------------------
# execute_aql  (line 175 - delegates to execute_cypher)
# ---------------------------------------------------------------------------

class TestExecuteAql:

    def test_delegates_to_execute_cypher(self):
        eng, conn, cur = make_engine()
        with patch("iris_vector_graph.cypher.aql.translate_aql",
                   return_value=("MATCH (n) RETURN n", {})):
            with patch.object(eng, "execute_cypher",
                              return_value=IVGResult(columns=["id"], rows=[["n1"]])) as mock_ec:
                result = eng.execute_aql("FOR v IN 1..2 OUTBOUND @start g RETURN v", bind_vars={"start": "n1"})
        mock_ec.assert_called_once()
        assert result.columns == ["id"]

    def test_no_bind_vars(self):
        eng, conn, cur = make_engine()
        with patch("iris_vector_graph.cypher.aql.translate_aql",
                   return_value=("MATCH (n) RETURN n", {})):
            with patch.object(eng, "execute_cypher",
                              return_value=IVGResult(columns=[], rows=[])) as mock_ec:
                eng.execute_aql("FOR v IN 1..2 OUTBOUND @start g RETURN v")
        mock_ec.assert_called_once()


# ---------------------------------------------------------------------------
# execute_cypher — SHOW command  (line 278)
# ---------------------------------------------------------------------------

class TestExecuteCypherShow:

    def test_show_command_dispatched(self):
        eng, conn, cur = make_engine()
        result = eng.execute_cypher("SHOW DATABASES")
        eng._handle_show_command.assert_called_once()


# ---------------------------------------------------------------------------
# _execute_traversal  (lines 421-435) — list-based raw result
# ---------------------------------------------------------------------------

class TestExecuteTraversal:

    def _make_sql_query(self):
        sq = MagicMock()
        sq.query_metadata = {}
        return sq

    def test_list_raw_returns_ids(self):
        eng, conn, cur = make_engine()
        raw = [{"node_id": "n2", "hops": 1}, {"node_id": "n3", "hops": 2}]
        eng._store.execute_bfs.return_value = raw

        traversal = {
            "source_id": "n1", "predicates": ["TREATS"],
            "direction": "out", "is_count": False, "return_col": "id",
        }
        result = eng._execute_traversal(traversal, self._make_sql_query(), None, {})
        assert result.columns == ["id"]
        assert ["n2"] in result.rows

    def test_list_raw_count_mode(self):
        eng, conn, cur = make_engine()
        raw = [{"node_id": "n2"}, {"node_id": "n3"}]
        eng._store.execute_bfs.return_value = raw

        traversal = {
            "source_id": "n1", "predicates": [],
            "direction": "out", "is_count": True, "return_col": "cnt",
        }
        result = eng._execute_traversal(traversal, self._make_sql_query(), None, {})
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 2

    def test_ivg_result_with_error(self):
        eng, conn, cur = make_engine()
        err_result = IVGResult(columns=["id", "hops"], rows=[], error="fail")
        eng._store.execute_bfs.return_value = err_result

        traversal = {
            "source_id": "n1", "predicates": [],
            "direction": "out", "is_count": False, "return_col": "id",
        }
        result = eng._execute_traversal(traversal, self._make_sql_query(), None, {})
        assert result.rows == []

    def test_ivg_result_no_error_ids(self):
        eng, conn, cur = make_engine()
        ok_result = IVGResult(columns=["id", "hops"], rows=[["n2", 1]])
        eng._store.execute_bfs.return_value = ok_result

        traversal = {
            "source_id": "n1", "predicates": [],
            "direction": "out", "is_count": False, "return_col": "id",
        }
        result = eng._execute_traversal(traversal, self._make_sql_query(), None, {})
        assert result.rows == [["n2"]]


# ---------------------------------------------------------------------------
# _route_var_length — weighted path dispatch (line 442)
# ---------------------------------------------------------------------------

class TestRouteVarLengthWeighted:

    def _make_vl_sql(self, **overrides):
        vl = dict(
            weighted=False, shortest=False, all_shortest=False,
            min_hops=1, properties={}, temporal_window=False,
            direction="out", types=[], max_hops=5,
            src_id_param="n1", src_id_param_prefix=None,
            source_labels=[], source_var=None,
            return_path_funcs=[],
        )
        vl.update(overrides)
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM Graph_KG.nodes"
        sq.parameters = [["n1"]]
        sq.query_metadata = {}
        return sq

    def test_weighted_dispatched(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql(weighted=True)
        with patch.object(eng, "_execute_weighted_shortest_path",
                          return_value=IVGResult(columns=[], rows=[])) as mock_w:
            eng._route_var_length(sq, {})
        mock_w.assert_called_once()

    def test_src_id_param_dollar_resolved(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql(src_id_param="$mynode", source_labels=[])
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, {"mynode": "realid"})
        eng._store.execute_bfs.assert_called()

    def test_src_var_in_parameters_resolved(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = dict(
            weighted=False, shortest=False, all_shortest=False,
            min_hops=1, properties={}, temporal_window=False,
            direction="out", types=[], max_hops=5,
            src_id_param=None, source_labels=[],
            source_var="n", return_path_funcs=[],
        )
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM nodes"
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, {"n": "node1"})
        assert isinstance(result, IVGResult)

    def test_source_id_none_returns_empty(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = dict(
            weighted=False, shortest=False, all_shortest=False,
            min_hops=1, properties={}, temporal_window=False,
            direction="out", types=[], max_hops=5,
            src_id_param=None, source_labels=[],
            source_var=None, return_path_funcs=[],
        )
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = [[]]
        sq.query_metadata = {}
        result = eng._route_var_length(sq, {})
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_min_hops_gt1_dispatches_cypher(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql(min_hops=2)
        with patch.object(eng, "_execute_var_length_cypher",
                          return_value=IVGResult(columns=[], rows=[])) as mock_vlc:
            eng._route_var_length(sq, {})
        mock_vlc.assert_called_once()

    def test_return_path_funcs_dispatches_the_path_function_executor(self):
        # Spec 025 amendment 2026-10-07: path functions are implemented only in the
        # path-function executor; the legacy executor dropped their columns.
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql(return_path_funcs=["length"])
        with patch.object(eng, "_execute_var_length_labeled_path_funcs",
                          return_value=IVGResult(columns=[], rows=[])) as mock_pf, \
             patch.object(eng, "_execute_var_length_cypher") as mock_vlc:
            eng._route_var_length(sq, {})
        mock_pf.assert_called_once()
        mock_vlc.assert_not_called()

    def test_labeled_source_dispatches_labeled(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = dict(
            weighted=False, shortest=False, all_shortest=False,
            min_hops=1, properties={}, temporal_window=False,
            direction="out", types=[], max_hops=5,
            src_id_param=None, source_labels=["Drug"],
            source_var=None, return_path_funcs=[],
        )
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = [[]]
        sq.query_metadata = {}
        with patch.object(eng, "_execute_var_length_labeled",
                          return_value=IVGResult(columns=[], rows=[])) as mock_labeled:
            eng._route_var_length(sq, {})
        mock_labeled.assert_called_once()

    def test_labeled_with_path_funcs_dispatches_labeled_path_funcs(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = dict(
            weighted=False, shortest=False, all_shortest=False,
            min_hops=1, properties={}, temporal_window=False,
            direction="out", types=[], max_hops=5,
            src_id_param=None, source_labels=[],
            source_var=None, return_path_funcs=["length"],
        )
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = [[]]
        sq.query_metadata = {}
        with patch.object(eng, "_execute_var_length_labeled_path_funcs",
                          return_value=IVGResult(columns=[], rows=[])) as mock_lpf:
            eng._route_var_length(sq, {})
        mock_lpf.assert_called_once()

    def test_temporal_window_dispatched(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql(temporal_window=True, ts_start=100, ts_end=200)
        eng._store.execute_temporal_cypher.return_value = IVGResult(
            columns=["id"], rows=[["n2"]]
        )
        result = eng._route_var_length(sq, {})
        eng._store.execute_temporal_cypher.assert_called_once()

    def test_count_match_path_returns_count(self):
        # the BFS-rows fallback: VarLength.EndsCount (the count route's first choice) unavailable, explicitly
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        eng._iris_obj = MagicMock(side_effect=RuntimeError("EndsCount unavailable"))
        sq = self._make_vl_sql()
        sq.sql = "SELECT COUNT(DISTINCT n.id) AS cnt FROM Graph_KG.nodes"
        sq.parameters = [["n1"]]
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["n2", 1], ["n3", 2]]
        )
        result = eng._route_var_length(sq, {})
        assert result.columns == ["cnt"]
        assert result.rows[0][0] == 2

    def test_return_properties_enrichment(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql()
        sq.sql = "SELECT id FROM Graph_KG.nodes LIMIT 10"
        metadata = MagicMock()
        metadata.return_properties = ["name"]
        sq.query_metadata = metadata
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["n2", 1]]
        )
        eng._store.get_nodes.return_value = IVGResult(
            columns=["id", "labels", "name"], rows=[["n2", "[]", "BRCA1"]]
        )
        result = eng._route_var_length(sq, {})
        assert "name" in result.columns

    def test_fetch_first_sql_limit_extracted(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql()
        sq.sql = "SELECT id FROM Graph_KG.nodes FETCH FIRST 5 ROWS ONLY"
        sq.parameters = [["n1"]]
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, {})
        assert isinstance(result, IVGResult)

    def test_top_n_sql_limit_extracted(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_vl_sql()
        sq.sql = "SELECT TOP 3 id FROM Graph_KG.nodes"
        sq.parameters = [["n1"]]
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, {})
        assert isinstance(result, IVGResult)


# ---------------------------------------------------------------------------
# _execute_weighted_shortest_path  (lines 1359-1389)
# ---------------------------------------------------------------------------

class TestExecuteWeightedShortestPath:

    def _make_sq(self, src="n1", dst="n2", weight="weight", max_hops=10):
        vl = {
            "weighted": True,
            "src_id_param": src,
            "dst_id_param": dst,
            "weight_property": weight,
            "max_hops": max_hops,
            "types": [],
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = []
        sq.query_metadata = {}
        return sq

    def test_basic_call(self):
        eng, conn, cur = make_engine()
        eng._store.execute_weighted_shortest_path.return_value = IVGResult(
            columns=["path", "cost"], rows=[["...", 1.5]]
        )
        sq = self._make_sq()
        result = eng._execute_weighted_shortest_path(sq, {})
        eng._store.execute_weighted_shortest_path.assert_called_once_with(
            "n1", "n2", "weight", 10
        )

    def test_quoted_src(self):
        eng, conn, cur = make_engine()
        eng._store.execute_weighted_shortest_path.return_value = IVGResult(
            columns=["path", "cost"], rows=[]
        )
        sq = self._make_sq(src="'node_a'", dst="'node_b'")
        result = eng._execute_weighted_shortest_path(sq, {})
        eng._store.execute_weighted_shortest_path.assert_called_once_with(
            "node_a", "node_b", "weight", 10
        )

    def test_dollar_param_resolved(self):
        eng, conn, cur = make_engine()
        eng._store.execute_weighted_shortest_path.return_value = IVGResult(
            columns=["path", "cost"], rows=[]
        )
        sq = self._make_sq(src="$from", dst="$to")
        result = eng._execute_weighted_shortest_path(sq, {"from": "n1", "to": "n2"})
        eng._store.execute_weighted_shortest_path.assert_called_once_with(
            "n1", "n2", "weight", 10
        )

    def test_missing_src_raises(self):
        eng, conn, cur = make_engine()
        sq = self._make_sq(src=None, dst="n2")
        with pytest.raises(ValueError, match="requires both from and to"):
            eng._execute_weighted_shortest_path(sq, {})

    def test_missing_dst_raises(self):
        eng, conn, cur = make_engine()
        sq = self._make_sq(src="n1", dst=None)
        with pytest.raises(ValueError, match="requires both from and to"):
            eng._execute_weighted_shortest_path(sq, {})

    def test_dollar_param_missing_raises(self):
        eng, conn, cur = make_engine()
        sq = self._make_sq(src="$from", dst="$to")
        with pytest.raises(ValueError, match="requires both from and to"):
            eng._execute_weighted_shortest_path(sq, {})


# ---------------------------------------------------------------------------
# _execute_shortest_path_cypher — param resolution paths (lines 1390-1483)
# ---------------------------------------------------------------------------

class TestExecuteShortestPathCypher:

    def _make_sq(self, src="n1", dst="n2", types=None, max_hops=5,
                 direction="both", all_shortest=False, return_path_funcs=None,
                 src_var=None, dst_var=None):
        vl = {
            "weighted": False,
            "shortest": True,
            "all_shortest": all_shortest,
            "min_hops": 1,
            "properties": {},
            "src_id_param": src,
            "dst_id_param": dst,
            "types": types or [],
            "max_hops": max_hops,
            "direction": direction,
            "return_path_funcs": return_path_funcs or [],
            "source_var": src_var,
            "target_var": dst_var,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT ..."
        sq.parameters = []
        sq.query_metadata = {}
        return sq

    def test_src_from_source_var_parameter(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        sq = self._make_sq(src=None, dst="n2", src_var="a")
        result = eng._execute_shortest_path_cypher(sq, {"a": "node_a"})
        eng._store.execute_shortest_path.assert_called_once()

    def test_src_from_first_string_param(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        sq = self._make_sq(src=None, dst="n2")
        result = eng._execute_shortest_path_cypher(sq, {"x": "first_str"})
        eng._store.execute_shortest_path.assert_called_once()

    def test_dst_from_target_var_parameter(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        sq = self._make_sq(src="n1", dst=None, dst_var="b")
        result = eng._execute_shortest_path_cypher(sq, {"b": "node_b"})
        eng._store.execute_shortest_path.assert_called_once()

    def test_dst_from_second_string_param(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        sq = self._make_sq(src="n1", dst=None)
        result = eng._execute_shortest_path_cypher(sq, {"x": "first", "y": "second"})
        eng._store.execute_shortest_path.assert_called_once()

    def test_src_from_sql_params(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        vl = {
            "weighted": False, "shortest": True, "all_shortest": False,
            "min_hops": 1, "properties": {},
            "src_id_param": None, "dst_id_param": None,
            "types": [], "max_hops": 5, "direction": "both",
            "return_path_funcs": [], "source_var": None, "target_var": None,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = [["node_a", "node_b"]]
        sq.query_metadata = {}
        result = eng._execute_shortest_path_cypher(sq, None)
        eng._store.execute_shortest_path.assert_called_once()

    def test_return_path_funcs_nodes(self):
        eng, conn, cur = make_engine()
        path_json = json.dumps({"nodes": ["n1", "n2"], "rels": []})
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 1]]
        )
        sq = self._make_sq(return_path_funcs=["nodes"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "nodes" in result.columns

    def test_return_path_funcs_relationships(self):
        eng, conn, cur = make_engine()
        path_json = json.dumps({"nodes": ["n1", "n2"], "rels": ["TREATS"]})
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 1]]
        )
        sq = self._make_sq(return_path_funcs=["relationships"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "relationships" in result.columns

    def test_return_path_funcs_length_and_nodes(self):
        eng, conn, cur = make_engine()
        path_json = json.dumps({"nodes": ["n1", "n2"], "rels": []})
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 2]]
        )
        sq = self._make_sq(return_path_funcs=["length", "nodes"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "length" in result.columns
        assert "nodes" in result.columns

    def test_no_return_funcs_returns_raw(self):
        eng, conn, cur = make_engine()
        raw = IVGResult(columns=["path", "length"], rows=[["...", 2]])
        eng._store.execute_shortest_path.return_value = raw
        sq = self._make_sq(return_path_funcs=[])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert result is raw

    def test_dollar_src_resolved_from_params(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[]
        )
        sq = self._make_sq(src="$from", dst="$to")
        result = eng._execute_shortest_path_cypher(sq, {"from": "n1", "to": "n2"})
        eng._store.execute_shortest_path.assert_called_once()

    def test_null_path_json_returns_empty_nodes(self):
        eng, conn, cur = make_engine()
        eng._store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[None, 1]]
        )
        sq = self._make_sq(return_path_funcs=["nodes"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "nodes" in result.columns
        assert result.rows[0][0] == []


# ---------------------------------------------------------------------------
# _execute_var_length_cypher — source_var resolution and count paths
# ---------------------------------------------------------------------------

class TestExecuteVarLengthCypherExtra:

    def _make_sq(self, src_var=None, direction="out", types=None, max_hops=3,
                 min_hops=1, sql="", params=None, count_col=None):
        vl = {
            "types": types or [],
            "max_hops": max_hops,
            "min_hops": min_hops,
            "properties": {},
            "direction": direction,
            "source_var": src_var,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        if count_col:
            sq.sql = f"SELECT COUNT(DISTINCT n.id) AS {count_col} FROM nodes"
        else:
            sq.sql = sql
        sq.parameters = [params] if params else [[]]
        sq.query_metadata = {}
        return sq

    def test_source_from_source_var(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_sq(src_var="n", params=[])
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       side_effect=Exception("no iris")):
                result = eng._execute_var_length_cypher(sq, {"n": "mynode"})
        # Should fail gracefully and return empty
        assert isinstance(result, IVGResult)

    def test_source_from_first_iter_value(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_sq(src_var=None, params=[])
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       side_effect=Exception("no iris")):
                result = eng._execute_var_length_cypher(sq, {"x": "anynode"})
        assert isinstance(result, IVGResult)

    def test_count_path_returns_count(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_sq(count_col="reach", params=["n1"])
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       return_value="99"):
                result = eng._execute_var_length_cypher(sq, {})
        assert result.columns == ["reach"]
        assert result.rows[0][0] == 99

    def test_count_exception_fallback(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        sq = self._make_sq(count_col="reach", params=["n1"])
        with patch.object(eng, "_detect_arno", return_value=False):
            # First call (count) raises, we get 0
            with patch("iris_vector_graph.schema._call_classmethod",
                       side_effect=Exception("db error")):
                result = eng._execute_var_length_cypher(sq, {})
        assert result.columns == ["reach"]
        assert result.rows[0][0] == 0


# ---------------------------------------------------------------------------
# _execute_var_length_labeled — basic paths
# ---------------------------------------------------------------------------

class TestExecuteVarLengthLabeled:

    def _make_sq(self, col_map=None, sql="", params=None):
        sq = MagicMock()
        sq.sql = sql
        sq.column_name_map = col_map or {}
        sq.parameters = [params] if params else [[]]
        sq.query_metadata = {}
        return sq

    def test_empty_source_ids_count_returns_zero(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 5, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": False, "source_var": None,
        }
        sq = self._make_sq(col_map={"count": "count(c)"}, sql="SELECT COUNT(c) FROM nodes")
        sq.var_length_paths = [vl]
        # query_nodes returns no rows -> no source IDs
        eng._store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
        result = eng._execute_var_length_labeled(sq, {}, vl)
        assert result.rows == [[0]]

    def test_empty_source_ids_non_count_returns_empty(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 5, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": False, "source_var": None,
        }
        sq = self._make_sq()
        sq.var_length_paths = [vl]
        eng._store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
        result = eng._execute_var_length_labeled(sq, {}, vl)
        assert result.rows == []

    def test_with_source_ids_bfs_runs(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 2, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": False, "source_var": None,
        }
        sq = self._make_sq()
        sq.var_length_paths = [vl]
        eng._store.query_nodes.return_value = IVGResult(
            columns=["id"], rows=[["drug1"]]
        )
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["target1", 1]]
        )
        result = eng._execute_var_length_labeled(sq, {}, vl)
        assert isinstance(result, IVGResult)

    def test_min_hops_zero_includes_self(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 0, "max_hops": 2, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": False, "source_var": None,
        }
        sq = self._make_sq()
        sq.var_length_paths = [vl]
        eng._store.query_nodes.return_value = IVGResult(
            columns=["id"], rows=[["drug1"]]
        )
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[]
        )
        result = eng._execute_var_length_labeled(sq, {}, vl)
        # drug1 should be included as a 0-hop self-node
        assert any(row[0] == "drug1" for row in result.rows) or result.rows == [["drug1"]]

    def test_optional_empty_targets_returns_null_row(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 2, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": True, "source_var": None,
        }
        sq = self._make_sq()
        sq.var_length_paths = [vl]
        eng._store.query_nodes.return_value = IVGResult(
            columns=["id"], rows=[["drug1"]]
        )
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[]
        )
        result = eng._execute_var_length_labeled(sq, {}, vl)
        assert result.rows == [[None]]

    def test_count_with_target_ids(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 2, "direction": "out",
            "target_var": "c", "source_alias": "", "target_alias": "",
            "rel_var": None, "optional": False, "source_var": None,
        }
        sq = self._make_sq(col_map={"cnt": "count(c)"}, sql="SELECT COUNT(c) FROM nodes")
        sq.var_length_paths = [vl]
        eng._store.query_nodes.return_value = IVGResult(
            columns=["id"], rows=[["drug1"]]
        )
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["target1", 1]]
        )
        result = eng._execute_var_length_labeled(sq, {}, vl)
        # With count SQL pattern, returns count
        assert result.rows[0][0] >= 0


# ---------------------------------------------------------------------------
# _execute_var_length_labeled_path_funcs — basic paths
# ---------------------------------------------------------------------------

class TestExecuteVarLengthLabeledPathFuncs:

    def test_empty_source_ids_returns_empty(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 3, "direction": "out",
            "source_var": "a", "target_var": "b",
            "source_alias": "", "target_alias": "",
            "return_path_funcs": ["length"],
            "path_named_var": None,
        }
        sq = MagicMock()
        sq.sql = ""
        sq.column_name_map = {}
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
        result = eng._execute_var_length_labeled_path_funcs(sq, {}, vl)
        assert result.rows == []

    def test_with_source_bfs_runs(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": [], "types": [],
            "min_hops": 1, "max_hops": 2, "direction": "out",
            "source_var": "a", "target_var": "b",
            "source_alias": "", "target_alias": "",
            "return_path_funcs": ["length"],
            "path_named_var": None,
        }
        sq = MagicMock()
        sq.sql = ""
        sq.column_name_map = {"l": "length(p)"}
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.query_nodes.return_value = IVGResult(
            columns=["id"], rows=[["drug1"]]
        )
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["target1", 1]]
        )
        # Mock node data fetch cursor
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_cur.close.return_value = None
        eng._store.conn.cursor.return_value = mock_cur
        result = eng._execute_var_length_labeled_path_funcs(sq, {}, vl)
        assert isinstance(result, IVGResult)

    def test_target_label_filtering(self):
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "source_labels": ["Drug"],
            "target_labels": ["Disease"],
            "types": [],
            "min_hops": 1, "max_hops": 2, "direction": "out",
            "source_var": "a", "target_var": "b",
            "source_alias": "", "target_alias": "",
            "return_path_funcs": ["length"],
            "path_named_var": None,
        }
        sq = MagicMock()
        sq.sql = ""
        sq.column_name_map = {}
        sq.parameters = [[]]
        sq.query_metadata = {}

        call_count = [0]
        def query_nodes_side(label_filter=None, **kwargs):
            if label_filter == "Drug":
                return IVGResult(columns=["id"], rows=[["drug1"]])
            elif label_filter == "Disease":
                return IVGResult(columns=["id"], rows=[["disease1"]])
            return IVGResult(columns=["id"], rows=[])

        eng._store.query_nodes.side_effect = query_nodes_side
        eng._store.execute_bfs.return_value = IVGResult(
            columns=["id", "hops"], rows=[["other_node", 1]]
        )
        mock_cur2 = MagicMock()
        mock_cur2.fetchall.return_value = []
        mock_cur2.close.return_value = None
        eng._store.conn.cursor.return_value = mock_cur2
        result = eng._execute_var_length_labeled_path_funcs(sq, {}, vl)
        # other_node not in Disease labels, so no matching path_triples
        assert result.rows == []


# ---------------------------------------------------------------------------
# _fetch_props_json  (lines 1194-1222)
# ---------------------------------------------------------------------------

class TestFetchPropsJson:

    def test_empty_ids_returns_empty(self):
        eng, conn, cur = make_engine()
        result = eng._fetch_props_json([])
        assert result == {}

    def test_single_node_with_props(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("n1", "name", "Alice"), ("n1", "age", "30")]
        conn.cursor.return_value = mock_cur
        eng._store.conn = conn
        result = eng._fetch_props_json(["n1"])
        assert "n1" in result
        assert result["n1"] is not None

    def test_exception_returns_nones(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = Exception("db error")
        conn.cursor.return_value = mock_cur
        eng._store.conn = conn
        result = eng._fetch_props_json(["n1", "n2"])
        assert "n1" in result
        assert "n2" in result

    def test_chunking_over_499(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        conn.cursor.return_value = mock_cur
        eng._store.conn = conn
        ids = [f"n{i}" for i in range(600)]
        result = eng._fetch_props_json(ids)
        # Should have called execute twice (two chunks)
        assert mock_cur.execute.call_count == 2


# ---------------------------------------------------------------------------
# _bfs_with_paths  (lines 1224-1294)
# ---------------------------------------------------------------------------

class TestBfsWithPaths:

    def test_basic_outbound(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("n1", "n2", "TREATS")]
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", [], 1, "out")
        assert isinstance(result, list)
        assert len(result) == 1
        nodes, edges = result[0]
        assert nodes == ["n1", "n2"]
        assert edges == ["TREATS"]

    def test_inbound(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("n2", "n1", "TREATS")]
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", [], 1, "in")
        assert isinstance(result, list)

    def test_both_directions(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        # Both directions: out returns [], in returns []
        mock_cur.fetchall.return_value = []
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", [], 1, "both")
        assert isinstance(result, list)

    def test_no_cycles(self):
        eng, conn, cur = make_engine()
        # Edge n1->n2 and n2->n1 — cycle should be avoided
        mock_cur = MagicMock()
        # First hop: n1 -> n2
        # Second hop: n2 -> n1 (should be filtered)
        call_count = [0]
        def fetchall_side():
            call_count[0] += 1
            if call_count[0] == 1:
                return [("n1", "n2", "REL")]
            return [("n2", "n1", "REL")]

        mock_cur.fetchall.side_effect = fetchall_side
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", [], 2, "out")
        # Path n1->n2 should exist, but n1->n2->n1 should NOT (cycle)
        for nodes, edges in result:
            assert nodes.count("n1") == 1

    def test_cursor_exception_returns_empty(self):
        eng, conn, cur = make_engine()
        conn.cursor.side_effect = Exception("no cursor")
        result = eng._bfs_with_paths("n1", [], 2, "out")
        assert result == []

    def test_edge_query_exception_breaks(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = Exception("sql error")
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", [], 2, "out")
        assert result == []

    def test_with_predicates(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
        conn.cursor.return_value = mock_cur
        eng._store._schema_prefix = "Graph_KG"
        eng._store.conn = conn
        result = eng._bfs_with_paths("n1", ["KNOWS"], 1, "out")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _filter_nodes_by_post_where  (lines 1296-1357)
# ---------------------------------------------------------------------------

class TestFilterNodesByPostWhere:

    def test_empty_target_ids_returns_empty(self):
        eng, conn, cur = make_engine()
        result = eng._filter_nodes_by_post_where([], "n", ["n.age > 10"], [], "")
        assert result == []

    def test_empty_conditions_returns_all(self):
        eng, conn, cur = make_engine()
        result = eng._filter_nodes_by_post_where(["n1", "n2"], "n", [], [], "")
        assert result == ["n1", "n2"]

    def test_no_cartesian_match_returns_all(self):
        eng, conn, cur = make_engine()
        sql = "SELECT * FROM nodes n"  # No Cartesian JOIN pattern
        result = eng._filter_nodes_by_post_where(
            ["n1", "n2"], "tgt", ["tgt.name = 'X'"], [], sql
        )
        assert result == ["n1", "n2"]

    def test_filters_using_post_where(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [("n1",)]  # Only n1 passes
        conn.cursor.return_value = mock_cur
        eng._store.conn = conn
        sql = (
            "SELECT a.node_id, b.node_id\n"
            "FROM nodes a\n"
            "JOIN nodes b ON 1=1\n"
            "WHERE a.node_id = ?"
        )
        result = eng._filter_nodes_by_post_where(
            ["n1", "n2"], "b", ["b.name = ?"], ["Alice"], sql
        )
        assert "n1" in result

    def test_exception_returns_all(self):
        eng, conn, cur = make_engine()
        conn.cursor.side_effect = Exception("db error")
        sql = (
            "SELECT a.node_id\nFROM nodes a\n"
            "JOIN nodes b ON 1=1\n"
            "WHERE a.x = 1"
        )
        result = eng._filter_nodes_by_post_where(
            ["n1", "n2"], "b", ["b.name = ?"], ["X"], sql
        )
        assert set(result) == {"n1", "n2"}

    def test_chunk_query_failure_includes_chunk(self):
        eng, conn, cur = make_engine()
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = Exception("sql fail")
        conn.cursor.return_value = mock_cur
        eng._store.conn = conn
        sql = (
            "SELECT a.node_id\nFROM nodes a\n"
            "JOIN nodes b ON 1=1\n"
            "WHERE a.x = 1"
        )
        result = eng._filter_nodes_by_post_where(
            ["n1", "n2"], "b", ["b.name = ?"], ["X"], sql
        )
        # On failure, chunk is added to filtered
        assert set(result) == {"n1", "n2"}


# ---------------------------------------------------------------------------
# _execute_approx_count_distinct  (lines 1853-1919)
# ---------------------------------------------------------------------------

class TestExecuteApproxCountDistinct:

    def _make_match(self, col_name="approx_count"):
        import re
        m = re.search(
            r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
            f"MATCH (n)-[*1..3]->(m) RETURN approx_count_distinct(m) AS {col_name}",
            re.IGNORECASE,
        )
        return m

    def test_no_var_length_paths_counts_exactly(self):
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        exact = IVGResult(columns=["est"], rows=[[4]])
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse, patch.object(
            eng, "_approx_exact_reason", return_value=None
        ), patch.object(eng, "execute_cypher", return_value=exact) as run:
            with patch("iris_vector_graph._engine.query.translate_to_sql") as mock_translate:
                mock_parse.return_value = MagicMock()
                sq = MagicMock()
                sq.var_length_paths = []
                mock_translate.return_value = sq
                result = eng._execute_approx_count_distinct(
                    "MATCH (n)-[*]->(m) RETURN approx_count_distinct(m) AS est", {}, m
                )
        assert run.call_args.args[0] == "MATCH (n)-[*]->(m) RETURN count(DISTINCT m) AS est"
        assert result.rows == [[4]]

    def test_parse_exception_goes_to_the_exact_path(self):
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        failed = IVGResult(columns=[], rows=[], error="parse fail")
        with patch("iris_vector_graph._engine.query.parse_query", side_effect=Exception("parse fail")), patch.object(
            eng, "execute_cypher", return_value=failed
        ):
            result = eng._execute_approx_count_distinct(
                "MATCH (n)-[*]->(m) RETURN approx_count_distinct(m) AS est", {}, m
            )
        assert result.error == "parse fail"

    def test_no_source_id_returns_zero(self):
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse, patch.object(
            eng, "_approx_exact_reason", return_value=None
        ):
            with patch("iris_vector_graph._engine.query.translate_to_sql") as mock_translate:
                mock_parse.return_value = MagicMock()
                vl = {"types": [], "max_hops": 3, "direction": "both", "source_var": None}
                sq = MagicMock()
                sq.var_length_paths = [vl]
                sq.parameters = [[]]  # No string params
                mock_translate.return_value = sq
                result = eng._execute_approx_count_distinct(
                    "MATCH (n)-[*]->(m) RETURN approx_count_distinct(m) AS est", {}, m
                )
        assert result.columns == ["est"]
        assert result.rows == [[0]]

    def test_call_classmethod_success(self):
        """_call_classmethod succeeds — estimate should be returned."""
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        # Pass n="n1" so source_var 'n' resolves to "n1" in the approx function
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value='{"estimate": 500, "registers": 256, "std_error": 0.02}'):
            result = eng._execute_approx_count_distinct(
                "MATCH (n {node_id: $n})-[*1..3]->(m) RETURN approx_count_distinct(m) AS est",
                {"n": "n1"}, m
            )
        assert result.columns == ["est"]
        assert result.rows[0][0] == 500

    def test_call_classmethod_exception_counts_exactly(self):
        """_call_classmethod raises: the count is made exactly (count(DISTINCT ...)), not reported as 0."""
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        exact = IVGResult(columns=["est"], rows=[[7]])
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=Exception("IRIS error")), \
             patch.object(eng, "execute_cypher", return_value=exact) as run:
            result = eng._execute_approx_count_distinct(
                "MATCH (n {node_id: $n})-[*1..3]->(m) RETURN approx_count_distinct(m) AS est",
                {"n": "n1"}, m
            )
        assert run.call_args.args[0] == "MATCH (n {node_id: $n})-[*1..3]->(m) RETURN count(DISTINCT m) AS est"
        assert result.rows[0][0] == 7
        assert any("counted exactly" in w for w in result.metadata.warnings)

    def test_source_var_in_parameters(self):
        """source_var present in parameters → used as source_id."""
        eng, conn, cur = make_engine()
        m = self._make_match("est")
        # The query uses $n which resolves to source_var='n' in the vl dict
        with patch("iris_vector_graph.schema._call_classmethod",
                   return_value='{"estimate": 42, "registers": 128, "std_error": 0.05}'):
            result = eng._execute_approx_count_distinct(
                "MATCH (n {node_id: $n})-[*1..3]->(m) RETURN approx_count_distinct(m) AS est",
                {"n": "node1"}, m
            )
        assert result.rows[0][0] == 42


# ---------------------------------------------------------------------------
# _execute_parsed — column_name_map and bolt_column_types paths
# ---------------------------------------------------------------------------

class TestExecuteParsedColumnMapping:

    def test_column_name_map_applied(self):
        eng, conn, cur = make_engine()
        store = MagicMock()
        result_from_store = IVGResult(columns=["raw_col"], rows=[["val"]])
        store.execute_sql.return_value = result_from_store
        store.capabilities.return_value = {"native_sql": True}
        eng._store = store
        eng._store_capabilities = {"native_sql": True}

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "SELECT raw_col FROM nodes"
        sql_query.parameters = []
        sql_query.query_metadata = {}
        sql_query.bolt_column_types = {}
        sql_query.column_name_map = {"raw_col": "friendly_name"}

        parsed = MagicMock()
        parsed.procedure_call = None

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            eng._execute_parsed(parsed, {})

        # The column mapping should have been applied
        assert result_from_store.columns == ["friendly_name"]

    def test_bolt_column_types_applied(self):
        eng, conn, cur = make_engine()
        store = MagicMock()
        result_from_store = IVGResult(columns=["id"], rows=[["n1"]])
        result_from_store.bolt_column_types = None
        store.execute_sql.return_value = result_from_store
        store.capabilities.return_value = {"native_sql": True}
        eng._store = store
        eng._store_capabilities = {"native_sql": True}

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "SELECT id FROM nodes"
        sql_query.parameters = []
        sql_query.query_metadata = {}
        sql_query.bolt_column_types = {"id": "NODE"}
        sql_query.column_name_map = {}

        parsed = MagicMock()
        parsed.procedure_call = None

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            eng._execute_parsed(parsed, {})

        assert result_from_store.bolt_column_types == {"id": "NODE"}

    def test_transactional_column_name_map_applied(self):
        eng, conn, cur = make_engine()
        store = MagicMock()
        tx_result = IVGResult(columns=["raw"], rows=[])
        store.execute_transaction.return_value = tx_result
        store.capabilities.return_value = {"native_sql": True}
        eng._store = store
        eng._store_capabilities = {"native_sql": True}

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = True
        sql_query.sql = "INSERT INTO ..."
        sql_query.parameters = [["x"]]
        sql_query.query_metadata = {}
        sql_query.column_name_map = {"raw": "nice"}

        parsed = MagicMock()
        parsed.procedure_call = None

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            eng._execute_parsed(parsed, {})

        assert tx_result.columns == ["nice"]


# ---------------------------------------------------------------------------
# _extract_traversal — additional branches
# ---------------------------------------------------------------------------

class TestExtractTraversalAdditional:

    def test_src_id_property_with_name_attr(self):
        """Branch: hasattr(v, 'name') → src_id = parameters.get(v.name)."""
        from iris_vector_graph.cypher.ast import Direction
        eng, conn, cur = make_engine()

        param_ref = MagicMock()
        param_ref.name = "nodeId"

        node = MagicMock()
        node.labels = []
        node.properties = {"id": param_ref}

        dst_node = MagicMock()
        dst_node.labels = []
        dst_node.properties = {}

        rel = MagicMock()
        rel.types = ["TREATS"]
        rel.variable_length = None
        rel.direction = Direction.INCOMING

        pattern = MagicMock()
        pattern.nodes = [node, dst_node]
        pattern.relationships = [rel]

        clause = MagicMock()
        clause.patterns = [pattern]

        qp = MagicMock()
        qp.clauses = [clause]

        ret_item = MagicMock()
        ret_item.alias = "target"
        del ret_item.expression.function_name
        ret_item.expression.property_name = "id"

        return_clause = MagicMock()
        return_clause.items = [ret_item]

        parsed = MagicMock()
        parsed.query_parts = [qp]
        parsed.return_clause = return_clause
        parsed.limit = None

        result = eng._extract_traversal(parsed, {"nodeId": "real_node"})
        assert result is not None
        assert result["source_id"] == "real_node"
        assert result["direction"] == "in"

    def test_src_id_non_string_property(self):
        """Branch: not str → src_id = str(v)."""
        from iris_vector_graph.cypher.ast import Direction
        eng, conn, cur = make_engine()

        node = MagicMock()
        node.labels = []
        node.properties = {"id": 42}  # int, not str and no .name

        dst_node = MagicMock()
        dst_node.labels = []
        dst_node.properties = {}

        rel = MagicMock()
        rel.types = []
        rel.variable_length = None
        rel.direction = Direction.OUTGOING

        pattern = MagicMock()
        pattern.nodes = [node, dst_node]
        pattern.relationships = [rel]

        clause = MagicMock()
        clause.patterns = [pattern]

        qp = MagicMock()
        qp.clauses = [clause]

        ret_item = MagicMock()
        ret_item.alias = None
        del ret_item.expression.function_name
        ret_item.expression.property_name = "id"

        return_clause = MagicMock()
        return_clause.items = [ret_item]

        parsed = MagicMock()
        parsed.query_parts = [qp]
        parsed.return_clause = return_clause
        parsed.limit = None

        result = eng._extract_traversal(parsed, {})
        assert result is not None
        assert result["source_id"] == "42"

    def test_src_id_plain_string_not_dollar(self):
        """Branch: isinstance(v, str) and not v.startswith('$') → src_id = v."""
        from iris_vector_graph.cypher.ast import Direction
        eng, conn, cur = make_engine()

        node = MagicMock()
        node.labels = []
        node.properties = {"id": "literal_id"}

        dst_node = MagicMock()
        dst_node.labels = []
        dst_node.properties = {}

        rel = MagicMock()
        rel.types = ["KNOWS"]
        rel.variable_length = None
        rel.direction = Direction.OUTGOING

        pattern = MagicMock()
        pattern.nodes = [node, dst_node]
        pattern.relationships = [rel]

        clause = MagicMock()
        clause.patterns = [pattern]

        qp = MagicMock()
        qp.clauses = [clause]

        ret_item = MagicMock()
        ret_item.alias = "node_id"
        del ret_item.expression.function_name
        ret_item.expression.property_name = "id"

        return_clause = MagicMock()
        return_clause.items = [ret_item]

        parsed = MagicMock()
        parsed.query_parts = [qp]
        parsed.return_clause = return_clause
        parsed.limit = None

        result = eng._extract_traversal(parsed, {})
        assert result is not None
        assert result["source_id"] == "literal_id"


# ---------------------------------------------------------------------------
# snapshot.py — load_networkx progress_callback paths
# ---------------------------------------------------------------------------

class TestLoadNetworkxProgressCallback:

    def _make_networkx_graph(self, node_count=1, edge_count=0):
        G = MagicMock()
        nodes_data = [(str(i), {"type": "Gene", "name": f"n{i}"}) for i in range(node_count)]
        edges_data = [(str(i), str(i + 1), {"predicate": "TREATS"}) for i in range(edge_count)]
        G.nodes.return_value = nodes_data
        G.edges.return_value = edges_data
        G.number_of_nodes.return_value = node_count
        G.number_of_edges.return_value = edge_count
        return G

    def test_progress_callback_called_after_nodes(self):
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = self._make_networkx_graph(node_count=1)
        G.edges.return_value = []

        cb = MagicMock()
        eng.load_networkx(G, progress_callback=cb, auto_sync=False)
        cb.assert_called()

    def test_auto_sync_true_calls_sync(self):
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = self._make_networkx_graph(node_count=1)
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=True)
        eng.sync.assert_called_once()

    def test_auto_sync_false_no_sync(self):
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=False)  # all skipped
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = self._make_networkx_graph(node_count=1)
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=False)
        eng.sync.assert_not_called()

    def test_deprecated_auto_rebuild_kg(self):
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = self._make_networkx_graph(node_count=1)
        G.edges.return_value = []

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng.load_networkx(G, auto_rebuild_kg=False)
        assert any("deprecated" in str(warning.message).lower() for warning in w)

    def test_skipped_nodes_counted(self):
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=False)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = self._make_networkx_graph(node_count=3)
        G.edges.return_value = []

        stats = eng.load_networkx(G, auto_sync=False)
        assert stats["skipped_nodes"] == 3
        assert stats["nodes"] == 0

    def test_namespace_label_fallback(self):
        """label_attr not in data but 'namespace' in data."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [("n1", {"namespace": "Gene"})]
        G.edges.return_value = []

        stats = eng.load_networkx(G, label_attr="type", auto_sync=False)
        # namespace fallback used
        create_call = eng.create_node.call_args
        assert "Gene" in create_call.kwargs.get("labels", [])

    def test_label_attr_list_value(self):
        """label_attr value is a list — use it directly."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [("n1", {"type": ["Gene", "Protein"]})]
        G.edges.return_value = []

        eng.load_networkx(G, label_attr="type", auto_sync=False)
        create_call = eng.create_node.call_args
        labels = create_call.kwargs.get("labels", [])
        assert "Gene" in labels and "Protein" in labels


# ---------------------------------------------------------------------------
# engine.py — _bfs_stream_pages  (lines 84-103)
# ---------------------------------------------------------------------------

class TestBfsStreamPages:

    def test_done_page_stops(self):
        from iris_vector_graph.engine import _bfs_stream_pages
        items = [{"o": "n1"}, {"o": "n2"}]
        page = {"items": items, "done": True}
        with patch("iris_vector_graph.engine._call_classmethod",
                   return_value=json.dumps(page)):
            result = list(_bfs_stream_pages(MagicMock(), "tag123"))
        assert result == items

    def test_sorted_prefix_stops(self):
        from iris_vector_graph.engine import _bfs_stream_pages
        with patch("iris_vector_graph.engine._call_classmethod",
                   return_value="SORTED:0"):
            result = list(_bfs_stream_pages(MagicMock(), "tag123"))
        assert result == []

    def test_paginated_result(self):
        from iris_vector_graph.engine import _bfs_stream_pages
        page1 = {"items": [{"o": "n1"}], "done": False, "next_step": 1, "next_o": "n1"}
        page2 = {"items": [{"o": "n2"}], "done": True}
        side_effects = [json.dumps(page1), json.dumps(page2)]
        with patch("iris_vector_graph.engine._call_classmethod",
                   side_effect=side_effects):
            result = list(_bfs_stream_pages(MagicMock(), "tag123"))
        assert len(result) == 2

    def test_next_step_minus1_stops(self):
        from iris_vector_graph.engine import _bfs_stream_pages
        page = {"items": [{"o": "n1"}], "done": False, "next_step": -1}
        with patch("iris_vector_graph.engine._call_classmethod",
                   return_value=json.dumps(page)):
            result = list(_bfs_stream_pages(MagicMock(), "tag123"))
        assert result == [{"o": "n1"}]


# ---------------------------------------------------------------------------
# engine.py — _detect_fetch_first_unsafe (lines 236-291)
# ---------------------------------------------------------------------------

class TestDetectFetchFirstUnsafe:

    def test_no_connection_params_uses_version_check_safe(self):
        eng, conn, cur = make_engine()
        eng._connection_params = None
        cur.fetchone.return_value = ("IRIS 2025.1.0 build 100",)
        result = eng._detect_fetch_first_unsafe()
        assert result is False

    def test_version_2026_returns_unsafe(self):
        eng, conn, cur = make_engine()
        eng._connection_params = None
        cur.fetchone.return_value = ("IRIS 2026.1.0 build 234U",)
        result = eng._detect_fetch_first_unsafe()
        assert result is True

    def test_version_check_exception_returns_false(self):
        eng, conn, cur = make_engine()
        eng._connection_params = None
        conn.cursor.side_effect = Exception("no cursor")
        result = eng._detect_fetch_first_unsafe()
        assert result is False

    def test_subprocess_probe_safe(self):
        eng, conn, cur = make_engine()
        eng._connection_params = {"hostname": "localhost", "port": 1972}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = eng._detect_fetch_first_unsafe()
        assert result is False

    def test_subprocess_probe_sigsegv(self):
        eng, conn, cur = make_engine()
        eng._connection_params = {"hostname": "localhost", "port": 1972}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=-11, stdout="", stderr="")
            result = eng._detect_fetch_first_unsafe()
        assert result is True

    def test_subprocess_timeout_marks_unsafe(self):
        eng, conn, cur = make_engine()
        eng._connection_params = {"hostname": "localhost", "port": 1972}
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            result = eng._detect_fetch_first_unsafe()
        assert result is True

    def test_subprocess_exception_falls_back_to_version(self):
        eng, conn, cur = make_engine()
        eng._connection_params = {"hostname": "localhost", "port": 1972}
        cur.fetchone.return_value = ("IRIS 2026.3.0 AI build 106",)
        with patch("subprocess.run", side_effect=Exception("subprocess error")):
            result = eng._detect_fetch_first_unsafe()
        assert result is True


# ---------------------------------------------------------------------------
# engine.py — _fetch_first_unsafe property  (lines 222-234)
# ---------------------------------------------------------------------------

class TestFetchFirstUnsafeProperty:

    def test_cached_value_used(self):
        eng, conn, cur = make_engine()
        eng._fetch_first_unsafe_cached = True
        assert eng._fetch_first_unsafe is True
        # _detect should not have been called
        eng._detect_fetch_first_unsafe = MagicMock()
        assert eng._fetch_first_unsafe is True
        eng._detect_fetch_first_unsafe.assert_not_called()

    def test_none_triggers_detection(self):
        eng, conn, cur = make_engine()
        eng._fetch_first_unsafe_cached = None
        with patch.object(eng, "_detect_fetch_first_unsafe", return_value=False) as mock_detect:
            result = eng._fetch_first_unsafe
        mock_detect.assert_called_once()
        assert result is False


# ---------------------------------------------------------------------------
# engine.py — khop2_count_fast / khop2_count_exact  (lines 1920-1931)
# ---------------------------------------------------------------------------

class TestKhop2Methods:

    def test_khop2_count_fast(self):
        eng, conn, cur = make_engine()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "42"
        eng._iris_obj.return_value = iris_obj
        result = eng.khop2_count_fast("n1", "TREATS")
        assert result == 42

    def test_khop2_count_exact(self):
        eng, conn, cur = make_engine()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "17"
        eng._iris_obj.return_value = iris_obj
        result = eng.khop2_count_exact("n1", "KNOWS")
        assert result == 17

    def test_khop2_count_fast_empty_predicate(self):
        eng, conn, cur = make_engine()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "0"
        eng._iris_obj.return_value = iris_obj
        result = eng.khop2_count_fast("n1")
        assert result == 0


# ---------------------------------------------------------------------------
# Additional _execute_parsed native_sql label_filter path
# ---------------------------------------------------------------------------

class TestExecuteParsedNativeSqlFalse:

    def test_label_filter_extracted_from_clause(self):
        """native_sql=False, label on first node → label_filter passed to query_nodes."""
        eng, conn, cur = make_engine()
        eng._store_capabilities = {"native_sql": False}
        store = MagicMock()
        store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
        eng._store = store

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "..."
        sql_query.parameters = []
        sql_query.query_metadata = {}

        # Build parsed object with patterns[0].nodes[0].labels[0] = "Gene"
        node = MagicMock()
        node.labels = ["Gene"]

        pattern = MagicMock()
        pattern.nodes = [node]

        clause = MagicMock()
        clause.patterns = [pattern]

        qp = MagicMock()
        qp.clauses = [clause]

        ret_item = MagicMock()
        ret_item.expression.property_name = "name"

        return_clause = MagicMock()
        return_clause.items = [ret_item]

        parsed = MagicMock()
        parsed.procedure_call = None
        parsed.query_parts = [qp]
        parsed.return_clause = return_clause
        parsed.limit = "10"

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            with patch.object(eng, "_extract_traversal", return_value=None):
                eng._execute_parsed(parsed, {})

        store.query_nodes.assert_called_once_with(
            label_filter="Gene",
            property_filters=None,
            return_properties=["name"],
            limit=10,
        )

    def test_limit_parse_exception_fallback(self):
        """Exception in limit parsing falls back to limit=0."""
        eng, conn, cur = make_engine()
        eng._store_capabilities = {"native_sql": False}
        store = MagicMock()
        store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
        eng._store = store

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "..."
        sql_query.parameters = []
        sql_query.query_metadata = {}

        parsed = MagicMock()
        parsed.procedure_call = None
        parsed.query_parts = []
        parsed.return_clause = None
        parsed.limit = "not_a_number"  # will raise ValueError on int()

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            with patch.object(eng, "_extract_traversal", return_value=None):
                eng._execute_parsed(parsed, {})

        store.query_nodes.assert_called_once_with(
            label_filter=None,
            property_filters=None,
            return_properties=None,
            limit=0,
        )


# ---------------------------------------------------------------------------
# snapshot.py — load_networkx 10000-modulo progress paths (lines 60-64, 87-91)
# ---------------------------------------------------------------------------

class TestLoadNetworkxProgressModulo:

    def test_node_progress_at_10000_multiple(self):
        """Every 10000 nodes triggers logger.info and progress_callback."""
        eng, conn, cur = make_engine()
        # Rotate: True every time so added_nodes increments
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        node_count = 10000
        G = MagicMock()
        G.number_of_nodes.return_value = node_count
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [(str(i), {"type": "Gene"}) for i in range(node_count)]
        G.edges.return_value = []

        cb = MagicMock()
        with patch("iris_vector_graph._engine.snapshot.logger") as mock_log:
            eng.load_networkx(G, progress_callback=cb, auto_sync=False)

        # At n_done=10000, logger.info and callback are called
        # Check logger.info was called with "Nodes:" (line 60-62)
        info_calls = [str(c) for c in mock_log.info.call_args_list]
        assert any("Nodes:" in c for c in info_calls)
        # progress_callback called at least 2 times (at n_done=10000 and at end)
        assert cb.call_count >= 2

    def test_edge_progress_at_10000_multiple(self):
        """Every 10000 edges triggers logger.info and progress_callback."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        edge_count = 10000
        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = edge_count
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [
            (str(i), str(i + 1), {"predicate": "KNOWS"}) for i in range(edge_count)
        ]

        cb = MagicMock()
        with patch("iris_vector_graph._engine.snapshot.logger") as mock_log:
            eng.load_networkx(G, progress_callback=cb, auto_sync=False)

        info_calls = [str(c) for c in mock_log.info.call_args_list]
        assert any("Edges:" in c for c in info_calls)

    def test_skipped_edge_branch(self):
        """create_edge returns False → skipped_edges incremented (line 83-84)."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 2
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [
            ("n0", "n1", {"predicate": "TREATS"}),
            ("n1", "n2", {"label": "CAUSES"}),
        ]

        stats = eng.load_networkx(G, auto_sync=False)
        assert stats["skipped_edges"] == 2
        assert stats["edges"] == 0

    def test_long_prop_truncated(self):
        """Property values > 60000 chars are truncated (line 51-52)."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        long_value = "x" * 70000
        G.nodes.return_value = [("n0", {"type": "Gene", "desc": long_value})]
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_node.call_args.kwargs
        props = call_kw.get("properties", {})
        # desc should be truncated to 60000
        assert "desc" in props
        assert len(props["desc"]) == 60000

    def test_none_prop_value_skipped(self):
        """Property with None value is skipped (line 48)."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [("n0", {"type": "Gene", "desc": None, "name": "test"})]
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_node.call_args.kwargs
        props = call_kw.get("properties", {})
        assert "desc" not in props
        assert "name" in props

    def test_edge_key_used_as_predicate(self):
        """Edge 'key' attribute used as predicate when 'predicate' and 'label' absent."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 1
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [("n0", "n1", {"key": "ASSOCIATED_WITH"})]

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_edge.call_args.kwargs
        assert call_kw.get("predicate") == "ASSOCIATED_WITH"

    def test_edge_is_a_default_predicate(self):
        """Edge with no predicate/label/key defaults to 'is_a'."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 1
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [("n0", "n1", {})]

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_edge.call_args.kwargs
        assert call_kw.get("predicate") == "is_a"

    def test_edge_qualifiers_passed(self):
        """Edge extra attributes are passed as qualifiers."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 1
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [("n0", "n1", {"predicate": "TREATS", "confidence": 0.9})]

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_edge.call_args.kwargs
        assert call_kw.get("qualifiers") == {"confidence": 0.9}

    def test_edge_empty_qualifiers_passes_none(self):
        """Edge with only predicate key → qualifiers=None."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=True)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 1
        G.nodes.return_value = [("n0", {})]
        G.edges.return_value = [("n0", "n1", {"predicate": "TREATS"})]

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_edge.call_args.kwargs
        assert call_kw.get("qualifiers") is None

    def test_int_node_id_converted_to_str(self):
        """Integer node_id is converted to str for create_node."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [(12345, {"type": "Gene"})]
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_node.call_args.kwargs
        assert call_kw.get("node_id") == "12345"

    def test_non_str_prop_value_converted(self):
        """Non-string property values converted via str()."""
        eng, conn, cur = make_engine()
        eng.create_node = MagicMock(return_value=True)
        eng.create_edge = MagicMock(return_value=False)
        eng.sync = MagicMock()

        G = MagicMock()
        G.number_of_nodes.return_value = 1
        G.number_of_edges.return_value = 0
        G.nodes.return_value = [("n0", {"type": "Gene", "count": 42})]
        G.edges.return_value = []

        eng.load_networkx(G, auto_sync=False)
        call_kw = eng.create_node.call_args.kwargs
        props = call_kw.get("properties", {})
        assert props.get("count") == "42"


# ---------------------------------------------------------------------------
# snapshot.py — import_rdf raises ImportError when rdflib not available
# ---------------------------------------------------------------------------

class TestImportRdfNoRdflib:

    def test_raises_import_error_when_no_rdflib(self):
        eng, conn, cur = make_engine()
        with patch.dict("sys.modules", {"rdflib": None}):
            with pytest.raises(ImportError, match="rdflib"):
                eng.import_rdf("/fake/path.ttl")


# ---------------------------------------------------------------------------
# Additional _route_var_length: source_id fallback from iter(parameters)
# ---------------------------------------------------------------------------

class TestRouteVarLengthFallbacks:

    def test_source_id_from_first_param_value(self):
        """With src_id_param set (dollar form) and source_id still None,
        fallback uses next(iter(parameters.values()))."""
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        # Use a dollar param that's NOT in the parameters dict
        # So source_id stays None after $-resolution, then uses fallback
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 5,
            "src_id_param": "$notfound", "source_labels": [],
            "source_var": None, "return_path_funcs": [],
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM nodes"
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        # source_id resolved from parameters.values() → "anyval"
        result = eng._route_var_length(sq, {"x": "anyval"})
        assert isinstance(result, IVGResult)
        eng._store.execute_bfs.assert_called()

    def test_src_id_param_non_dollar_used_directly(self):
        """src_id_param that doesn't start with $ used directly as source_id."""
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 5,
            "src_id_param": "literal_node_id", "source_labels": [],
            "source_var": None, "return_path_funcs": [],
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM nodes"
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, {})
        assert isinstance(result, IVGResult)
        # Should have called execute_bfs with the literal id
        call_args = eng._store.execute_bfs.call_args
        assert call_args[0][0] == "literal_node_id"

    def test_src_id_param_dollar_missing_in_params_falls_through(self):
        """$param not in parameters → tries fallback source_var resolution."""
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 5,
            "src_id_param": "$missing", "source_labels": [],
            "source_var": "n", "return_path_funcs": [],
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM nodes"
        sq.parameters = [[]]
        sq.query_metadata = {}
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        # source_var 'n' in parameters
        result = eng._route_var_length(sq, {"n": "resolved_node"})
        assert isinstance(result, IVGResult)
        eng._store.execute_bfs.assert_called()


# ---------------------------------------------------------------------------
# Additional engine.py helpers
# ---------------------------------------------------------------------------

class TestEngineHelpers:

    def test_t_method(self):
        """_t() qualifies table name with schema prefix."""
        eng, conn, cur = make_engine()
        eng._schema_prefix = "Graph_KG"
        # The _t method was overridden in make_engine; test the real one
        from iris_vector_graph.cypher.translator import _table, set_schema_prefix
        set_schema_prefix("Graph_KG")
        result = _table("nodes")
        assert "Graph_KG" in result
        assert "nodes" in result

    def test_get_sentence_transformers_cached(self):
        """_get_sentence_transformers() returns the same object on repeated calls."""
        import iris_vector_graph.engine as engine_mod
        # Reset the global
        orig = engine_mod._sentence_transformers
        engine_mod._sentence_transformers = None
        try:
            with patch.dict("sys.modules", {"sentence_transformers": MagicMock()}):
                import sys
                mock_st = sys.modules["sentence_transformers"]
                result1 = engine_mod._get_sentence_transformers()
                result2 = engine_mod._get_sentence_transformers()
                assert result1 is result2
        finally:
            engine_mod._sentence_transformers = orig

    def test_is_sentence_transformer_false_no_st(self):
        """_is_sentence_transformer returns False when import fails."""
        import iris_vector_graph.engine as engine_mod
        orig = engine_mod._sentence_transformers
        engine_mod._sentence_transformers = None
        try:
            with patch.dict("sys.modules", {"sentence_transformers": None}):
                result = engine_mod._is_sentence_transformer(object())
                assert result is False
        finally:
            engine_mod._sentence_transformers = orig

    def test_khop2_validation_empty_id_raises(self):
        """KHop2Input validation raises on empty node_id."""
        from iris_vector_graph._validate import KHop2Input
        with pytest.raises(Exception):
            KHop2Input(node_id="")

    def test_route_var_length_source_id_fallback_from_params_list(self):
        """source_id extracted from SQL parameters list when src_id_param is set
        (dollar form, not found in parameters) but source_id still None."""
        eng, conn, cur = make_engine()
        eng._nkg_dirty = False
        # src_id_param is "$notpresent" so src_id_param is not None but source_id stays None
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 5,
            "src_id_param": "$notpresent", "source_labels": [],
            "source_var": None, "return_path_funcs": [],
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM nodes"
        # params has a string that doesn't start with Graph_KG — this is the fallback
        sq.parameters = [["node_from_params", "Graph_KG.nodes"]]
        sq.query_metadata = {}
        eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[])
        result = eng._route_var_length(sq, None)  # no parameters dict
        assert isinstance(result, IVGResult)
        eng._store.execute_bfs.assert_called()
        call_args = eng._store.execute_bfs.call_args
        assert call_args[0][0] == "node_from_params"
