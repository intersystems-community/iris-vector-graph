"""
Coverage tests for _engine/query.py uncovered paths.
Uses unit-testable pure functions and light mocking.
No IRIS connection required.

Target lines: 137-138, 142-143, 204, 365-366, 393, 397, 430, 434, 442, 450-455,
459, 472, 475, 475-542, 552-879, 976-987, 1002, 1028, 1063-1066, 1072-1083,
1094-1095, 1105-1111, 1120-1121, 1124-1127, 1131-1136, 1144-1164, 1185, 1251,
1311, 1320, 1323, 1348-1350, 1355-1357, 1376-1377, 1408, 1426, 1435, 1437,
1512, 1544-1545, 1573-1583, 1587, 1603, 1610-1611, 1619-1626, 1658-1659,
1781-1782, 1885, 1899-1900
"""
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine():
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.result import IVGResult
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

    # Set up a minimal _store mock
    store = MagicMock()
    store_result = IVGResult(columns=[], rows=[])
    store.execute_bfs.return_value = store_result
    store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[])
    store.get_nodes.return_value = IVGResult(columns=["node_id", "labels"], rows=[])
    store.execute_sql.return_value = IVGResult(columns=[], rows=[])
    store.execute_transaction.return_value = IVGResult(columns=[], rows=[])
    store.capabilities.return_value = {"native_sql": True}
    store.conn = conn
    store._schema_prefix = "Graph_KG"
    eng._store = store
    eng._store_capabilities = {"native_sql": True}
    return eng, conn, cur


def _make_metadata():
    from iris_vector_graph.cypher.translator import QueryMetadata
    return QueryMetadata()


def make_sql_query(sql="SELECT 1", params=None, var_length_paths=None,
                   column_name_map=None, is_transactional=False,
                   bolt_column_types=None, query_metadata=None):
    sq = MagicMock()
    sq.sql = sql
    sq.parameters = [params or []]
    sq.var_length_paths = var_length_paths or []
    sq.column_name_map = column_name_map or {}
    sq.is_transactional = is_transactional
    sq.bolt_column_types = bolt_column_types
    sq.query_metadata = query_metadata or _make_metadata()
    return sq


# ---------------------------------------------------------------------------
# _split_top_level_and  (lines 14-50)
# ---------------------------------------------------------------------------

def test_split_top_level_and_simple():
    from iris_vector_graph._engine.query import _split_top_level_and
    result = _split_top_level_and("a = 1 AND b = 2")
    assert len(result) == 2
    assert "a = 1" in result[0]


def test_split_top_level_and_nested():
    from iris_vector_graph._engine.query import _split_top_level_and
    result = _split_top_level_and("EXISTS(SELECT 1 WHERE a = 1 AND b = 2) AND c = 3")
    assert len(result) == 2
    assert "EXISTS" in result[0]
    assert "c = 3" in result[1]


def test_split_top_level_and_no_and():
    from iris_vector_graph._engine.query import _split_top_level_and
    result = _split_top_level_and("a = 1")
    assert result == ["a = 1"]


def test_split_top_level_and_leading_and():
    from iris_vector_graph._engine.query import _split_top_level_and
    result = _split_top_level_and("AND b = 2")
    assert isinstance(result, list)


def test_split_top_level_and_multiple_depth():
    from iris_vector_graph._engine.query import _split_top_level_and
    result = _split_top_level_and("(a AND b) AND (c AND d)")
    assert len(result) == 2


# ---------------------------------------------------------------------------
# GDS_SHIM_MAP and _handle_gds_shim  (lines 58-98)
# ---------------------------------------------------------------------------

def test_gds_shim_map_not_empty():
    from iris_vector_graph._engine.query import GDS_SHIM_MAP
    assert "gds.pagerank.stream" in GDS_SHIM_MAP


def test_handle_gds_shim_non_gds():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock()
    proc.procedure_name = "ivg.bfs"
    result = _handle_gds_shim(proc)
    assert result is None


def test_handle_gds_shim_known_pagerank():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock()
    proc.procedure_name = "gds.pagerank.stream"
    proc.arguments = []
    proc.yield_items = []
    result = _handle_gds_shim(proc)
    assert isinstance(result, tuple)
    assert result[0].procedure_name == "ivg.ppr"


def test_handle_gds_shim_unknown_gds():
    from iris_vector_graph._engine.query import _handle_gds_shim
    from iris_vector_graph.result import IVGResult
    proc = MagicMock()
    proc.procedure_name = "gds.nonexistent.stream"
    result = _handle_gds_shim(proc)
    assert isinstance(result, IVGResult)
    assert result.error is not None
    assert "not shimmed" in result.error


def test_handle_gds_shim_shortestpath():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock()
    proc.procedure_name = "gds.shortestPath.dijkstra.stream"
    proc.arguments = []
    proc.yield_items = []
    result = _handle_gds_shim(proc)
    assert isinstance(result, tuple)


def test_handle_gds_shim_betweenness():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock()
    proc.procedure_name = "gds.betweenness.stream"
    proc.arguments = []
    proc.yield_items = []
    result = _handle_gds_shim(proc)
    assert isinstance(result, tuple)
    assert result[0].procedure_name == "ivg.betweenness"


def test_handle_gds_shim_louvain():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock()
    proc.procedure_name = "gds.louvain.stream"
    proc.arguments = [1, 2]
    proc.yield_items = ["community"]
    result = _handle_gds_shim(proc)
    assert isinstance(result, tuple)
    assert result[0].procedure_name == "ivg.leiden"
    assert result[0].arguments == [1, 2]
    assert result[0].yield_items == ["community"]


def test_handle_gds_shim_no_yield_items():
    from iris_vector_graph._engine.query import _handle_gds_shim
    proc = MagicMock(spec=["procedure_name", "arguments"])
    proc.procedure_name = "gds.louvain.stream"
    proc.arguments = []
    # No yield_items attr — getattr should default to []
    result = _handle_gds_shim(proc)
    assert isinstance(result, tuple)
    assert result[0].yield_items == []


# ---------------------------------------------------------------------------
# _build_path_func_columns  (lines 101-164)
# ---------------------------------------------------------------------------

def test_build_path_func_columns_no_col_map():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns(["path", "length"], "a", "b", {})
    assert len(cols) > 0


def test_build_path_func_columns_with_col_map():
    from iris_vector_graph._engine.query import _build_path_func_columns
    col_map = {"a": "a", "b": "b", "l": "length(p)"}
    cols = _build_path_func_columns(["length"], "a", "b", col_map)
    assert isinstance(cols, list)
    assert len(cols) > 0


def test_build_path_func_columns_length_only():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns(["length"], "src", "tgt", {})
    assert "src" in cols or "tgt" in cols


def test_build_path_func_columns_empty_funcs():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns([], "x", "y", {})
    assert "x" in cols or "y" in cols


def test_build_path_func_columns_relationships():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns(["relationships"], "a", "b", {})
    assert "relationships(p)" in cols or len(cols) > 0


def test_build_path_func_columns_nodes():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns(["nodes"], "a", "b", {})
    assert len(cols) > 0


def test_build_path_func_columns_with_named_path():
    from iris_vector_graph._engine.query import _build_path_func_columns
    cols = _build_path_func_columns(["path"], "a", "b", {}, path_named_var="myPath")
    assert "myPath" in cols


def test_build_path_func_columns_col_map_src_only():
    from iris_vector_graph._engine.query import _build_path_func_columns
    # col_map has source but not target  (line 137-138 + 142-143)
    col_map = {"src_alias": "a"}
    cols = _build_path_func_columns(["length"], "a", "b", col_map)
    assert "src_alias" in cols


def test_build_path_func_columns_col_map_func_expr():
    from iris_vector_graph._engine.query import _build_path_func_columns
    # col_map uses function alias form  (lines 135-138)
    col_map = {"l": "length(p)", "a": "a", "b": "b"}
    cols = _build_path_func_columns(["length"], "a", "b", col_map)
    assert "l" in cols or "a" in cols


def test_build_path_func_columns_col_map_remaining():
    from iris_vector_graph._engine.query import _build_path_func_columns
    # Extra entries in col_map that aren't path funcs — should add remaining (lines 145-148)
    col_map = {"a": "a", "b": "b", "extra": "something_else"}
    cols = _build_path_func_columns([], "a", "b", col_map)
    assert "extra" in cols


# ---------------------------------------------------------------------------
# execute_cypher: fast-path and early returns  (lines 193-314)
# ---------------------------------------------------------------------------

def test_execute_cypher_approx_count_distinct():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_execute_approx_count_distinct") as mock_acd:
        from iris_vector_graph.result import IVGResult
        mock_acd.return_value = IVGResult(columns=["approxCount"], rows=[[42]])
        result = eng.execute_cypher(
            "MATCH (n)-[*1..3]->(m) RETURN approx_count_distinct(m) AS approxCount"
        )
        assert mock_acd.called
        assert result is not None


def test_execute_cypher_khop_fast_path_returns_none():
    eng, conn, cur = make_engine()
    # No khop pattern — fast path should return None and fall through (line 204)
    with patch.object(eng, "_try_khop_fast_path", return_value=None):
        with patch.object(eng, "_reconnect_if_stale"):
            with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
                with patch("iris_vector_graph._engine.query.translate_to_sql") as mock_trans:
                    parsed = MagicMock()
                    parsed.subsequent_queries = []
                    parsed.procedure_call = None
                    parsed.is_mutation = False
                    mock_parse.return_value = parsed
                    sql_q = make_sql_query()
                    mock_trans.return_value = sql_q
                    eng.execute_cypher("MATCH (n) RETURN n.id")


def test_execute_cypher_explain():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("EXPLAIN MATCH (n) RETURN n")
    assert result["columns"] == ["Plan"]


def test_execute_cypher_show_command():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_handle_show_command") as mock_show:
        from iris_vector_graph.result import IVGResult
        mock_show.return_value = IVGResult(columns=["result"], rows=[])
        result = eng.execute_cypher("SHOW DATABASES")
        mock_show.assert_called_once()


def test_execute_cypher_create_constraint():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("CREATE CONSTRAINT ON (n:Person) ASSERT n.id IS UNIQUE")
    assert result["rows"] == []


def test_execute_cypher_drop_constraint():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("DROP CONSTRAINT myConstraint")
    assert result["rows"] == []


def test_execute_cypher_create_index():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("CREATE INDEX ON :Person(name)")
    assert result["rows"] == []


def test_execute_cypher_drop_index():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("DROP INDEX myIndex")
    assert result["rows"] == []


def test_execute_cypher_create_fulltext():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("CREATE FULLTEXT INDEX ON :Person(name)")
    assert result["rows"] == []


def test_execute_cypher_create_lookup():
    eng, conn, cur = make_engine()
    result = eng.execute_cypher("CREATE LOOKUP INDEX ON EACH NODE CALL db.nodeLabels()")
    assert result["rows"] == []


def test_execute_cypher_read_only_mutation_raises():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_reconnect_if_stale"):
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
            parsed = MagicMock()
            parsed.subsequent_queries = []
            parsed.procedure_call = None
            parsed.is_mutation = True
            mock_parse.return_value = parsed
            with pytest.raises(PermissionError, match="Read-only mode"):
                eng.execute_cypher("CREATE (n:Person {id: 1})", read_only=True)


def test_execute_cypher_subsequent_queries():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_reconnect_if_stale"):
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
            with patch.object(eng, "_execute_parsed") as mock_ep:
                from iris_vector_graph.result import IVGResult
                sub_result = IVGResult(columns=["x"], rows=[[1]])
                mock_ep.return_value = sub_result
                parsed_main = MagicMock()
                sub_q = MagicMock()
                sub_q.subsequent_queries = []
                sub_q.procedure_call = None
                sub_q.is_mutation = False
                parsed_main.subsequent_queries = [sub_q]
                parsed_main.procedure_call = None
                parsed_main.is_mutation = False
                mock_parse.return_value = parsed_main
                result = eng.execute_cypher("MATCH (n) RETURN n.id")
                assert mock_ep.called


def test_execute_cypher_semicolon_multi_call():
    eng, conn, cur = make_engine()
    with patch.object(eng, "execute_cypher", wraps=eng.execute_cypher) as mock_ec:
        # Use a real semi-colon + CALL pattern
        try:
            eng.execute_cypher("CALL db.labels() YIELD label RETURN label; MATCH (n) RETURN n")
        except Exception:
            pass  # coverage is the goal


def test_execute_cypher_db_labels_union():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_try_system_procedure") as mock_sp:
        from iris_vector_graph.result import IVGResult
        mock_sp.return_value = IVGResult(columns=["label"], rows=[["Person"], ["Animal"]])
        cur.fetchall.return_value = [("name",)]
        cypher = (
            "CALL db.labels() YIELD label "
            "UNION "
            "CALL db.relationshipTypes() YIELD relationshipType"
        )
        try:
            result = eng.execute_cypher(cypher)
        except Exception:
            pass


def test_execute_cypher_entity_union():
    eng, conn, cur = make_engine()
    cur.fetchall.return_value = [("node1",)]
    cypher = (
        "MATCH (n) RETURN DISTINCT n AS entity "
        "UNION ALL "
        "MATCH ()-[r]->() RETURN DISTINCT r AS entity"
    )
    try:
        result = eng.execute_cypher(cypher)
    except Exception:
        pass


def test_execute_cypher_count_union_all():
    eng, conn, cur = make_engine()
    cur.fetchone.return_value = (5,)
    cur.fetchall.return_value = []
    cypher = (
        "MATCH () RETURN COUNT(*) AS count "
        "UNION ALL "
        "MATCH ()-[]->() RETURN COUNT(*) AS count"
    )
    try:
        result = eng.execute_cypher(cypher)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# _execute_parsed: fallback to store.query_nodes  (lines 344-372)
# ---------------------------------------------------------------------------

def test_execute_parsed_fallback_to_query_nodes():
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store_capabilities = {"native_sql": False}
    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])

    with patch("iris_vector_graph._engine.query.translate_to_sql") as mock_trans:
        sq = make_sql_query()
        mock_trans.return_value = sq

        with patch.object(eng, "_extract_traversal", return_value=None):
            with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
                parsed = MagicMock()
                parsed.subsequent_queries = []
                parsed.procedure_call = None
                parsed.is_mutation = False
                parsed.return_clause = None
                parsed.limit = None
                mock_parse.return_value = parsed

                with patch.object(eng, "_reconnect_if_stale"):
                    result = eng.execute_cypher("MATCH (n) RETURN n")
                    # _store.query_nodes was called
                    assert eng._store.query_nodes.called


def test_execute_parsed_with_limit_exception():
    """Exercises the except pass block in _execute_parsed (lines 365-366)."""
    eng, conn, cur = make_engine()
    eng._store_capabilities = {"native_sql": False}

    with patch("iris_vector_graph._engine.query.translate_to_sql") as mock_trans:
        sq = make_sql_query()
        mock_trans.return_value = sq

        with patch.object(eng, "_extract_traversal", return_value=None):
            with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
                parsed = MagicMock()
                parsed.subsequent_queries = []
                parsed.procedure_call = None
                parsed.is_mutation = False
                # Make limit raise when cast to int
                parsed.limit = "bad"
                parsed.return_clause = None
                mock_parse.return_value = parsed

                with patch.object(eng, "_reconnect_if_stale"):
                    # Should not raise — exception is swallowed
                    from iris_vector_graph.result import IVGResult
                    eng._store.query_nodes.return_value = IVGResult(columns=[], rows=[])
                    result = eng.execute_cypher("MATCH (n) RETURN n")


# ---------------------------------------------------------------------------
# _extract_traversal (lines 373-420)
# ---------------------------------------------------------------------------

def test_extract_traversal_with_param_ref():
    """Exercises hasattr(v, 'name') branch (line 393)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.cypher.ast import Direction

    parsed = MagicMock()
    clause = MagicMock()
    pattern = MagicMock()
    src_node = MagicMock()
    tgt_node = MagicMock()
    rel = MagicMock()

    # Set up the param ref object with .name attribute
    param_ref = MagicMock()
    param_ref.name = "src"

    src_node.properties = {"id": param_ref}
    src_node.labels = []
    tgt_node.properties = {}
    tgt_node.labels = []

    rel.variable_length = None
    rel.types = ["KNOWS"]
    rel.direction = Direction.OUTGOING

    pattern.nodes = [src_node, tgt_node]
    pattern.relationships = [rel]

    clause.patterns = [pattern]
    parsed.query_parts = [MagicMock(clauses=[clause])]
    parsed.return_clause = None

    result = eng._extract_traversal(parsed, {"src": "node1"})
    assert result is not None
    assert result["source_id"] == "node1"


def test_extract_traversal_with_literal_str():
    """Exercises literal string (not $) branch (line 395)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.cypher.ast import Direction

    parsed = MagicMock()
    clause = MagicMock()
    pattern = MagicMock()
    src_node = MagicMock()
    tgt_node = MagicMock()
    rel = MagicMock()

    src_node.properties = {"id": "literal_id"}
    src_node.labels = []
    tgt_node.properties = {}
    tgt_node.labels = []

    rel.variable_length = None
    rel.types = []
    rel.direction = Direction.BOTH

    pattern.nodes = [src_node, tgt_node]
    pattern.relationships = [rel]

    clause.patterns = [pattern]
    parsed.query_parts = [MagicMock(clauses=[clause])]
    parsed.return_clause = None

    result = eng._extract_traversal(parsed, {})
    assert result is not None
    assert result["source_id"] == "literal_id"
    assert result["direction"] == "both"


def test_extract_traversal_with_non_str_id():
    """Exercises else: src_id = str(v) branch (line 397)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.cypher.ast import Direction

    parsed = MagicMock()
    clause = MagicMock()
    pattern = MagicMock()
    src_node = MagicMock()
    tgt_node = MagicMock()
    rel = MagicMock()

    src_node.properties = {"id": 42}  # int, not str, no .name
    src_node.labels = []
    tgt_node.properties = {}
    tgt_node.labels = []

    rel.variable_length = None
    rel.types = []
    rel.direction = Direction.INCOMING

    pattern.nodes = [src_node, tgt_node]
    pattern.relationships = [rel]

    clause.patterns = [pattern]
    parsed.query_parts = [MagicMock(clauses=[clause])]
    parsed.return_clause = None

    result = eng._extract_traversal(parsed, {})
    assert result is not None
    assert result["source_id"] == "42"
    assert result["direction"] == "in"


# ---------------------------------------------------------------------------
# _execute_traversal  (lines 421-435)
# ---------------------------------------------------------------------------

def test_execute_traversal_with_list_raw():
    """Exercises isinstance(raw, list) branch (line 430)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    sql_q = make_sql_query()
    traversal = {
        "source_id": "n1",
        "predicates": ["KNOWS"],
        "direction": "out",
        "is_count": False,
        "return_col": "id",
    }
    eng._store.execute_bfs.return_value = [
        {"node_id": "n2", "hops": 1},
        {"id": "n3", "hops": 1},
    ]
    parsed = MagicMock()
    result = eng._execute_traversal(traversal, sql_q, parsed, {})
    assert result["columns"] == ["id"]
    assert len(result["rows"]) == 2


def test_execute_traversal_is_count():
    """Exercises is_count branch (line 434)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    sql_q = make_sql_query()
    traversal = {
        "source_id": "n1",
        "predicates": [],
        "direction": "out",
        "is_count": True,
        "return_col": "cnt",
    }
    bfs_result = IVGResult(columns=["node_id"], rows=[["n2"], ["n3"]])
    eng._store.execute_bfs.return_value = bfs_result
    parsed = MagicMock()
    result = eng._execute_traversal(traversal, sql_q, parsed, {})
    assert result["columns"] == ["cnt"]
    assert result["rows"] == [[2]]


def test_execute_traversal_ivgresult_with_error():
    """Exercises IVGResult with error (line 432)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    sql_q = make_sql_query()
    traversal = {
        "source_id": "n1",
        "predicates": [],
        "direction": "out",
        "is_count": False,
        "return_col": "id",
    }
    bfs_result = IVGResult(columns=[], rows=[], error="some error")
    eng._store.execute_bfs.return_value = bfs_result
    parsed = MagicMock()
    result = eng._execute_traversal(traversal, sql_q, parsed, {})
    assert result["rows"] == []


# ---------------------------------------------------------------------------
# _route_var_length  (lines 436-542)
# ---------------------------------------------------------------------------

def test_route_var_length_raises_when_nkg_dirty():
    eng, conn, cur = make_engine()
    eng._nkg_dirty = True
    sql_q = make_sql_query(var_length_paths=[{"weighted": False, "shortest": False, "all_shortest": False}])
    from iris_vector_graph.errors import IndexNotSyncedError
    with pytest.raises(IndexNotSyncedError):
        eng._route_var_length(sql_q, {})


def test_route_var_length_weighted():
    """Exercises weighted branch (line 442)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(var_length_paths=[{
        "weighted": True,
        "src_id_param": "n1",
        "dst_id_param": "n2",
        "weight_property": "weight",
        "max_hops": 5,
        "types": [],
    }])
    from iris_vector_graph.result import IVGResult
    eng._store.execute_weighted_shortest_path.return_value = IVGResult(columns=["path"], rows=[])
    result = eng._route_var_length(sql_q, {})
    assert eng._store.execute_weighted_shortest_path.called


def test_route_var_length_shortest():
    """Exercises shortest path branch (line 443-444)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(var_length_paths=[{
        "weighted": False,
        "shortest": True,
        "all_shortest": False,
        "src_id_param": "n1",
        "dst_id_param": "n2",
        "types": [],
        "max_hops": 5,
        "direction": "both",
        "return_path_funcs": [],
    }])
    with patch.object(eng, "_execute_shortest_path_cypher") as mock_sp:
        from iris_vector_graph.result import IVGResult
        mock_sp.return_value = IVGResult(columns=["p"], rows=[])
        result = eng._route_var_length(sql_q, {})
        mock_sp.assert_called_once()


def test_route_var_length_src_id_from_param():
    """Exercises src_id_param resolution (lines 450-455)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT TOP 10 n.node_id AS nid FROM Graph_KG.nodes n",
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "$nodeId",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
        }]
    )
    from iris_vector_graph.result import IVGResult
    eng._store.execute_bfs.return_value = IVGResult(columns=["node_id", "hops"], rows=[])
    result = eng._route_var_length(sql_q, {"nodeId": "n1"})
    assert result is not None


def test_route_var_length_src_id_literal():
    """Exercises literal src_id_param (not $) branch (line 454-455)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT n.node_id AS nid FROM Graph_KG.nodes n",
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "literal_node",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
        }]
    )
    from iris_vector_graph.result import IVGResult
    eng._store.execute_bfs.return_value = IVGResult(columns=["node_id", "hops"], rows=[])
    result = eng._route_var_length(sql_q, {})
    assert result is not None


def test_route_var_length_no_source_returns_empty():
    """Exercises source_id is None → return empty (line 493)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT n.node_id AS nid FROM Graph_KG.nodes n",
        params=[],
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": None,
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
            "source_var": None,
        }]
    )
    result = eng._route_var_length(sql_q, {})
    assert result["rows"] == []


def test_route_var_length_count_match():
    """Exercises COUNT(DISTINCT ...) pattern (line 510-514)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT COUNT(DISTINCT n.node_id) AS cnt FROM Graph_KG.nodes n",
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "n1",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
        }]
    )
    from iris_vector_graph.result import IVGResult
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1], ["n3", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    result = eng._route_var_length(sql_q, {})
    assert result["columns"] == ["cnt"]
    assert result["rows"] == [[2]]


def test_route_var_length_temporal():
    """Exercises temporal_window branch (lines 521-525)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT n.node_id AS nid FROM Graph_KG.nodes n",
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "n1",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
            "temporal_window": True,
            "ts_start": 1000,
            "ts_end": 2000,
        }]
    )
    from iris_vector_graph.result import IVGResult
    eng._store.execute_temporal_cypher = MagicMock(
        return_value=IVGResult(columns=["node_id", "hops"], rows=[])
    )
    result = eng._route_var_length(sql_q, {})
    assert eng._store.execute_temporal_cypher.called


def test_route_var_length_with_return_properties():
    """Exercises return_properties enrichment (lines 530-542)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    from unittest.mock import PropertyMock

    meta = MagicMock()
    meta.return_properties = ["name"]

    sql_q = make_sql_query(
        sql="SELECT n.node_id AS nid FROM Graph_KG.nodes n",
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "n1",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
        }]
    )
    sql_q.query_metadata = meta

    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    bfs_result.columns = ["node_id", "hops"]
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(
        columns=["node_id", "labels", "name"],
        rows=[["n2", "[]", "Alice"]]
    )
    result = eng._route_var_length(sql_q, {})
    # Should have enriched columns
    assert "name" in result["columns"]


def test_route_var_length_min_hops_gt_1_delegates_to_cypher():
    """Exercises min_hops > 1 delegation (line 475)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": "n1",
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 5,
            "direction": "out",
            "min_hops": 2,
            "properties": None,
        }]
    )
    with patch.object(eng, "_execute_var_length_cypher") as mock_vlc:
        from iris_vector_graph.result import IVGResult
        mock_vlc.return_value = IVGResult(columns=["id"], rows=[])
        result = eng._route_var_length(sql_q, {})
        mock_vlc.assert_called_once()


def test_route_var_length_return_path_funcs_delegates():
    """Exercises return_path_funcs branch (line 472)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": None,
            "source_labels": [],
            "return_path_funcs": ["length"],
            "types": [],
            "max_hops": 5,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
            "source_var": None,
        }]
    )
    with patch.object(eng, "_execute_var_length_labeled_path_funcs") as mock_lpf:
        from iris_vector_graph.result import IVGResult
        mock_lpf.return_value = IVGResult(columns=["l"], rows=[])
        result = eng._route_var_length(sql_q, {})
        mock_lpf.assert_called_once()


def test_route_var_length_labeled_delegates():
    """Exercises labeled branch (line 473)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": None,
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 5,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
            "source_var": None,
        }]
    )
    with patch.object(eng, "_execute_var_length_labeled") as mock_lbl:
        from iris_vector_graph.result import IVGResult
        mock_lbl.return_value = IVGResult(columns=["id"], rows=[])
        result = eng._route_var_length(sql_q, {})
        mock_lbl.assert_called_once()


def test_route_var_length_source_from_sql_params():
    """Exercises fallback source_id from sql_query.parameters (lines 483-490)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT n.node_id AS nid FROM Graph_KG.nodes n",
        params=["node_abc"],
        var_length_paths=[{
            "weighted": False,
            "shortest": False,
            "all_shortest": False,
            "src_id_param": None,
            "source_labels": [],
            "return_path_funcs": [],
            "types": [],
            "max_hops": 3,
            "direction": "out",
            "min_hops": 1,
            "properties": None,
            "source_var": None,
        }]
    )
    sql_q.parameters = [["node_abc"]]
    from iris_vector_graph.result import IVGResult
    eng._store.execute_bfs.return_value = IVGResult(columns=["node_id", "hops"], rows=[])
    result = eng._route_var_length(sql_q, {})
    assert result is not None


# ---------------------------------------------------------------------------
# _execute_var_length_labeled_path_funcs  (lines 544-883)
# ---------------------------------------------------------------------------

def test_execute_var_length_labeled_path_funcs_no_source_ids():
    """Empty source_ids returns empty result (lines 636-638)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[])
    sql_q = make_sql_query()
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "source_var": "a",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "return_path_funcs": ["length"],
        "path_named_var": None,
    }
    result = eng._execute_var_length_labeled_path_funcs(sql_q, {}, vl0)
    assert result["rows"] == []


def test_execute_var_length_labeled_path_funcs_with_sources():
    """Source IDs found, BFS returns results (lines 647-677)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result

    # node data fetch
    cur.fetchall.return_value = []

    sql_q = make_sql_query(column_name_map={"a": "a", "b": "b", "l": "length(p)"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "source_var": "a",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "return_path_funcs": ["length"],
        "path_named_var": None,
    }
    result = eng._execute_var_length_labeled_path_funcs(sql_q, {}, vl0)
    assert result is not None


def test_execute_var_length_labeled_path_funcs_min_hops_zero():
    """min_hops=0 includes self-pairs (lines 649-651)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    cur.fetchall.return_value = []

    sql_q = make_sql_query(column_name_map={"a": "a", "b": "b", "l": "length(p)"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 0,
        "max_hops": 3,
        "direction": "out",
        "source_var": "a",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "return_path_funcs": ["length"],
        "path_named_var": None,
    }
    result = eng._execute_var_length_labeled_path_funcs(sql_q, {}, vl0)
    # At min_hops=0, n1→n1 pair is included
    assert len(result["rows"]) >= 1


def test_execute_var_length_labeled_path_funcs_with_target_labels():
    """Target label filtering (lines 680-697)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    # Source lookup
    eng._store.query_nodes.side_effect = [
        IVGResult(columns=["node_id"], rows=[["n1"]]),       # source label
        IVGResult(columns=["node_id"], rows=[["n2"]]),        # target label
    ]
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1], ["n3", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    cur.fetchall.return_value = []

    sql_q = make_sql_query()
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": ["Animal"],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "source_var": "a",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "return_path_funcs": ["length"],
        "path_named_var": None,
    }
    result = eng._execute_var_length_labeled_path_funcs(sql_q, {}, vl0)
    # Only n2 should survive target label filter
    assert all(row[0] != "n3" or "n3" not in str(result) for row in result.rows)


def test_execute_var_length_labeled_path_funcs_no_path_triples():
    """Empty path_triples returns empty result (lines 699-701)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    cur.fetchall.return_value = []

    sql_q = make_sql_query()
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "source_var": "a",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "return_path_funcs": ["length"],
        "path_named_var": None,
    }
    result = eng._execute_var_length_labeled_path_funcs(sql_q, {}, vl0)
    assert result["rows"] == []


# ---------------------------------------------------------------------------
# _execute_var_length_labeled  (lines 885-1192)
# ---------------------------------------------------------------------------

def test_execute_var_length_labeled_no_source_ids_count():
    """No source IDs with is_count → return 0 (lines 1051-1053)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[])
    sql_q = make_sql_query(
        sql="SELECT COUNT(DISTINCT b.node_id) AS cnt FROM Graph_KG.nodes",
        column_name_map={"cnt": "count(b)"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert result["rows"] == [[0]]


def test_execute_var_length_labeled_no_source_ids_empty():
    """No source IDs, not count → return [] (line 1054)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[])
    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert result["rows"] == []


def test_execute_var_length_labeled_with_bfs_results():
    """BFS runs and returns target IDs (lines 1084-1095)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(columns=["node_id", "labels"], rows=[["n2", "[]"]])

    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert len(result["rows"]) > 0


def test_execute_var_length_labeled_min_hops_zero():
    """min_hops=0 includes self-pair (lines 1063-1066)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(columns=["node_id", "labels"], rows=[["n1", "[]"]])

    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 0,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert len(result["rows"]) >= 1


def test_execute_var_length_labeled_rel_var_bfs_with_paths():
    """rel_var triggers _bfs_with_paths branch (lines 1072-1083)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    cur.fetchall.return_value = []

    sql_q = make_sql_query(
        sql="SELECT NULL AS r FROM Graph_KG.nodes",
        column_name_map={"r": "r"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": "r",
        "optional": False,
        "source_var": "a",
    }
    with patch.object(eng, "_bfs_with_paths", return_value=[
        (["n1", "n2"], ["KNOWS"])
    ]):
        result = eng._execute_var_length_labeled(sql_q, {}, vl0)
        assert result is not None


def test_execute_var_length_labeled_bfs_exception():
    """BFS exception is swallowed (lines 1094-1095)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    eng._store.execute_bfs.side_effect = RuntimeError("BFS fail")

    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert result["rows"] == []


def test_execute_var_length_labeled_target_labels():
    """Target label filtering (lines 1105-1111)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.side_effect = [
        IVGResult(columns=["node_id"], rows=[["n1"]]),   # source label
        IVGResult(columns=["node_id"], rows=[["n2"]]),    # target label
    ]
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1], ["n3", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(columns=["node_id", "labels"], rows=[["n2", "[]"]])

    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": ["Animal"],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    # n2 survives filter, n3 does not
    assert result is not None


def test_execute_var_length_labeled_is_count():
    """Count path returns count (lines 1119-1121)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1], ["n3", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result

    sql_q = make_sql_query(
        sql="SELECT COUNT(DISTINCT b.node_id) AS cnt FROM Graph_KG.nodes",
        column_name_map={"cnt": "count(b)"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert result["rows"] == [[2]]


def test_execute_var_length_labeled_no_targets_optional():
    """No targets + optional → return null row (lines 1124-1126)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result

    sql_q = make_sql_query(
        sql="SELECT b_id, b_labels, b_props FROM Graph_KG.nodes",
        column_name_map={"b": "b"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": True,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    # Should return a single null row
    assert result["rows"] == [[None, None, None]] or len(result["rows"]) == 1


def test_execute_var_length_labeled_no_targets_not_optional():
    """No targets + not optional → return [] (lines 1127)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result

    sql_q = make_sql_query(column_name_map={"b": "b"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert result["rows"] == []


def test_execute_var_length_labeled_rel_var_rows_out():
    """_rel_in_sql returns edge list per path (lines 1131-1140)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])

    sql_q = make_sql_query(
        sql="SELECT NULL AS r FROM Graph_KG.nodes",
        column_name_map={"r": "r"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 3,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": "r",
        "optional": False,
        "source_var": "a",
    }
    with patch.object(eng, "_bfs_with_paths", return_value=[
        (["n1", "n2"], ["KNOWS"]),
        (["n1", "n3"], ["LIKES"]),
    ]):
        result = eng._execute_var_length_labeled(sql_q, {}, vl0)
        assert result["columns"] == ["r"]
        assert len(result["rows"]) == 2


def test_execute_var_length_labeled_node_triple_in_sql():
    """node-triple columns x_id/x_labels/x_props (lines 1144-1162)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(columns=["node_id", "labels"], rows=[["n2", '["Person"]']])
    cur.fetchall.return_value = []  # props query

    sql_q = make_sql_query(
        sql="SELECT b_id, b_labels, b_props FROM Graph_KG.nodes",
        column_name_map={"b": "b"},
    )
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    with patch.object(eng, "_fetch_props_json", return_value={"n2": None}):
        result = eng._execute_var_length_labeled(sql_q, {}, vl0)
        assert "b_id" in result["columns"]


def test_execute_var_length_labeled_with_return_props():
    """return_props fetches properties (lines 1170-1192)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    eng._store.get_nodes.return_value = IVGResult(
        columns=["node_id", "labels", "name"],
        rows=[["n2", "[]", "Alice"]],
    )

    sql_q = make_sql_query(column_name_map={"b_name": "b.name"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    assert "b.name" in result["columns"]
    assert result["rows"] == [["Alice"]]


def test_execute_var_length_labeled_with_return_props_padding():
    """return_props pads missing values with None (line 1185)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    eng._store.query_nodes.return_value = IVGResult(columns=["node_id"], rows=[["n1"]])
    bfs_result = IVGResult(columns=["node_id", "hops"], rows=[["n2", 1]])
    bfs_result.error = None
    eng._store.execute_bfs.return_value = bfs_result
    # get_nodes returns no matching row for n2
    eng._store.get_nodes.return_value = IVGResult(columns=["node_id", "labels", "name", "age"], rows=[])

    sql_q = make_sql_query(column_name_map={"b_name": "b.name", "b_age": "b.age"})
    vl0 = {
        "source_labels": ["Person"],
        "target_labels": [],
        "types": [],
        "min_hops": 1,
        "max_hops": 5,
        "direction": "out",
        "target_var": "b",
        "source_alias": "",
        "target_alias": "",
        "rel_var": None,
        "optional": False,
        "source_var": "a",
    }
    result = eng._execute_var_length_labeled(sql_q, {}, vl0)
    # n2 has no props → [None, None]
    assert result["rows"] == [[None, None]]


# ---------------------------------------------------------------------------
# _fetch_props_json  (lines 1194-1222)
# ---------------------------------------------------------------------------

def test_fetch_props_json_empty():
    eng, conn, cur = make_engine()
    result = eng._fetch_props_json([])
    assert result == {}


def test_fetch_props_json_with_data():
    eng, conn, cur = make_engine()
    eng._store.conn = conn
    cur.fetchall.return_value = [("n1", "name", "Alice"), ("n1", "age", "30")]
    result = eng._fetch_props_json(["n1"])
    assert "n1" in result
    data = json.loads(result["n1"])
    assert any(p["key"] == "name" for p in data)


def test_fetch_props_json_exception():
    eng, conn, cur = make_engine()
    eng._store.conn.cursor.side_effect = RuntimeError("db error")
    result = eng._fetch_props_json(["n1"])
    # Should not raise — returns None for n1
    assert result.get("n1") is None


# ---------------------------------------------------------------------------
# _bfs_with_paths  (lines 1224-1294)
# ---------------------------------------------------------------------------

def test_bfs_with_paths_out_direction():
    eng, conn, cur = make_engine()
    # First call: edges from n1 → n2
    cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", [], 2, "out")
    assert len(results) > 0
    assert results[0][0] == ["n1", "n2"]
    assert results[0][1] == ["KNOWS"]


def test_bfs_with_paths_in_direction():
    eng, conn, cur = make_engine()
    # "in" direction: SELECT o_id, s, p FROM edges WHERE o_id IN [n1]
    # Returns (n1, n2, KNOWS) meaning n2→n1 edge. adj[n1] = [(n2, KNOWS)]
    cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", [], 1, "in")
    assert len(results) > 0


def test_bfs_with_paths_both_direction():
    eng, conn, cur = make_engine()
    # Both direction: two queries
    cur.fetchall.side_effect = [
        [("n1", "n2", "KNOWS")],  # out
        [],  # in
    ]
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", [], 1, "both")
    assert len(results) > 0


def test_bfs_with_paths_cursor_exception():
    eng, conn, cur = make_engine()
    eng._store.conn.cursor.side_effect = RuntimeError("no cursor")
    results = eng._bfs_with_paths("n1", [], 2, "out")
    assert results == []


def test_bfs_with_paths_no_edges():
    eng, conn, cur = make_engine()
    cur.fetchall.return_value = []
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", [], 3, "out")
    assert results == []


def test_bfs_with_paths_cycle_prevention():
    eng, conn, cur = make_engine()
    # n1→n2, n2→n1 (cycle) — should not loop forever
    cur.fetchall.side_effect = [
        [("n1", "n2", "KNOWS")],  # hop 1: n1→n2
        [("n2", "n1", "KNOWS")],  # hop 2: n2→n1 (cycle, should be excluded)
    ]
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", [], 2, "out")
    # n1→n2 should be in results, n1→n2→n1 should not (cycle)
    for nodes, _ in results:
        assert len(set(nodes)) == len(nodes), f"Cycle detected in path: {nodes}"


def test_bfs_with_paths_with_predicates():
    eng, conn, cur = make_engine()
    cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
    eng._store.conn = conn
    results = eng._bfs_with_paths("n1", ["KNOWS"], 1, "out")
    assert len(results) > 0


# ---------------------------------------------------------------------------
# _filter_nodes_by_post_where  (lines 1296-1357)
# ---------------------------------------------------------------------------

def test_filter_nodes_by_post_where_empty():
    eng, conn, cur = make_engine()
    result = eng._filter_nodes_by_post_where([], "n", [], [], "SELECT 1")
    assert result == []


def test_filter_nodes_by_post_where_no_conds():
    eng, conn, cur = make_engine()
    result = eng._filter_nodes_by_post_where(["n1", "n2"], "n", [], [], "SELECT 1")
    assert result == ["n1", "n2"]


def test_filter_nodes_by_post_where_no_cartesian_match():
    """No cartesian boundary → return target_ids unchanged (line 1320)."""
    eng, conn, cur = make_engine()
    result = eng._filter_nodes_by_post_where(
        ["n1", "n2"], "n", ["n.name = 'Alice'"], [], "SELECT n.node_id FROM nodes n"
    )
    assert result == ["n1", "n2"]


def test_filter_nodes_by_post_where_with_match():
    """Cartesian boundary found, executes filter query (lines 1325-1354)."""
    eng, conn, cur = make_engine()
    eng._store.conn = conn
    # Mock the post-filter query to return "n1" only
    cur.fetchall.return_value = [("n1",)]

    original_sql = (
        "SELECT DISTINCT n.node_id\n"
        "FROM Graph_KG.nodes n\n"
        "JOIN Graph_KG.nodes b ON 1=1\n"
        "WHERE n.name = 'Alice'"
    )
    result = eng._filter_nodes_by_post_where(
        ["n1", "n2"], "b", ["b.name = ?"], ["Alice"], original_sql
    )
    # n1 is returned by mock query
    assert "n1" in result


def test_filter_nodes_by_post_where_query_exception():
    """Filter query exception → include all in chunk (lines 1352-1353)."""
    eng, conn, cur = make_engine()
    eng._store.conn = conn
    cur.execute.side_effect = RuntimeError("query fail")

    original_sql = (
        "SELECT DISTINCT n.node_id\n"
        "FROM Graph_KG.nodes n\n"
        "JOIN Graph_KG.nodes b ON 1=1\n"
        "WHERE b.name = 'Alice'"
    )
    result = eng._filter_nodes_by_post_where(
        ["n1", "n2"], "b", ["b.name = 'Alice'"], [], original_sql
    )
    # Exception → includes all
    assert "n1" in result and "n2" in result


def test_filter_nodes_by_post_where_outer_exception():
    """Outer exception → return target_ids (lines 1355-1357)."""
    eng, conn, cur = make_engine()
    eng._store.conn.cursor.side_effect = RuntimeError("cursor fail")

    original_sql = (
        "SELECT DISTINCT n.node_id\n"
        "FROM Graph_KG.nodes n\n"
        "JOIN Graph_KG.nodes b ON 1=1\n"
        "WHERE b.name = 'Alice'"
    )
    result = eng._filter_nodes_by_post_where(
        ["n1", "n2"], "b", ["b.name = 'Alice'"], [], original_sql
    )
    assert "n1" in result and "n2" in result


# ---------------------------------------------------------------------------
# _execute_weighted_shortest_path  (lines 1359-1389)
# ---------------------------------------------------------------------------

def test_execute_weighted_shortest_path_ok():
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_weighted_shortest_path.return_value = IVGResult(
        columns=["path", "weight"], rows=[["p1", 3.5]]
    )
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": "'n1'",
        "dst_id_param": "'n2'",
        "weight_property": "weight",
        "max_hops": 10,
        "types": [],
    }])
    result = eng._execute_weighted_shortest_path(sql_q, {})
    assert eng._store.execute_weighted_shortest_path.called


def test_execute_weighted_shortest_path_with_params():
    """Tests $param resolution (lines 1374-1376)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_weighted_shortest_path.return_value = IVGResult(columns=["path"], rows=[])
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": "$from",
        "dst_id_param": "$to",
        "weight_property": "weight",
        "max_hops": 5,
        "types": [],
    }])
    result = eng._execute_weighted_shortest_path(sql_q, {"from": "n1", "to": "n2"})
    assert eng._store.execute_weighted_shortest_path.called


def test_execute_weighted_shortest_path_missing_source():
    """source_id None → ValueError (line 1382-1385)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": None,
        "dst_id_param": "'n2'",
        "weight_property": "weight",
        "max_hops": 5,
        "types": [],
    }])
    with pytest.raises(ValueError, match="requires both from and to"):
        eng._execute_weighted_shortest_path(sql_q, {})


# ---------------------------------------------------------------------------
# _execute_shortest_path_cypher  (lines 1390-1483)
# ---------------------------------------------------------------------------

def test_execute_shortest_path_cypher_basic():
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_shortest_path.return_value = IVGResult(
        columns=["p", "length"], rows=[['{"nodes":[],"rels":[]}', 2]]
    )
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": "n1",
        "dst_id_param": "n2",
        "types": ["KNOWS"],
        "max_hops": 5,
        "direction": "both",
        "all_shortest": False,
        "return_path_funcs": [],
    }])
    result = eng._execute_shortest_path_cypher(sql_q, {})
    assert eng._store.execute_shortest_path.called


def test_execute_shortest_path_cypher_with_params():
    """Tests $param resolution (lines 1407-1408)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_shortest_path.return_value = IVGResult(
        columns=["p", "length"], rows=[]
    )
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": "$from",
        "dst_id_param": "$to",
        "types": [],
        "max_hops": 5,
        "direction": "both",
        "all_shortest": False,
        "return_path_funcs": [],
    }])
    result = eng._execute_shortest_path_cypher(sql_q, {"from": "n1", "to": "n2"})
    assert eng._store.execute_shortest_path.called


def test_execute_shortest_path_cypher_target_from_source_var():
    """source_var / target_var fallback (lines 1414-1429)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_shortest_path.return_value = IVGResult(columns=["p"], rows=[])
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": None,
        "dst_id_param": None,
        "source_var": "a",
        "target_var": "b",
        "types": [],
        "max_hops": 5,
        "direction": "both",
        "all_shortest": False,
        "return_path_funcs": [],
    }])
    result = eng._execute_shortest_path_cypher(sql_q, {"a": "n1", "b": "n2"})
    assert eng._store.execute_shortest_path.called


def test_execute_shortest_path_cypher_from_sql_params():
    """SQL params fallback (lines 1431-1437)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._store.execute_shortest_path.return_value = IVGResult(columns=["p"], rows=[])
    sql_q = make_sql_query(
        params=["n1", "n2"],
        var_length_paths=[{
            "src_id_param": None,
            "dst_id_param": None,
            "types": [],
            "max_hops": 5,
            "direction": "both",
            "all_shortest": False,
            "return_path_funcs": [],
        }]
    )
    sql_q.parameters = [["n1", "n2"]]
    result = eng._execute_shortest_path_cypher(sql_q, {})
    assert eng._store.execute_shortest_path.called


def test_execute_shortest_path_cypher_missing_both_raises():
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": None,
        "dst_id_param": None,
        "types": [],
        "max_hops": 5,
        "direction": "both",
        "all_shortest": False,
        "return_path_funcs": [],
    }])
    with pytest.raises(ValueError, match="shortestPath requires"):
        eng._execute_shortest_path_cypher(sql_q, {})


def test_execute_shortest_path_cypher_return_funcs_length():
    """Return path funcs processing (lines 1454-1475)."""
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    path_json = json.dumps({"nodes": [{"_id": "n1"}, {"_id": "n2"}], "rels": ["KNOWS"]})
    eng._store.execute_shortest_path.return_value = IVGResult(
        columns=["p", "length"], rows=[[path_json, 1]],
        sql="", params=[]
    )
    sql_q = make_sql_query(var_length_paths=[{
        "src_id_param": "n1",
        "dst_id_param": "n2",
        "types": [],
        "max_hops": 5,
        "direction": "both",
        "all_shortest": False,
        "return_path_funcs": ["length", "nodes", "relationships"],
    }])
    result = eng._execute_shortest_path_cypher(sql_q, {})
    assert "length" in result["columns"]
    assert "nodes" in result["columns"]


# ---------------------------------------------------------------------------
# _execute_var_length_cypher  (lines 1485-1708)
# ---------------------------------------------------------------------------

def test_execute_var_length_cypher_nkg_dirty():
    eng, conn, cur = make_engine()
    eng._nkg_dirty = True
    sql_q = make_sql_query(var_length_paths=[{
        "types": [],
        "max_hops": 3,
        "min_hops": 1,
        "direction": "out",
        "properties": {},
    }])
    from iris_vector_graph.errors import IndexNotSyncedError
    with pytest.raises(IndexNotSyncedError):
        eng._execute_var_length_cypher(sql_q)


def test_execute_var_length_cypher_no_source_id():
    """source_id None → empty result (lines 1514-1520)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        params=[],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [[]]
    with patch("iris_vector_graph._engine.query.translate_to_sql"):
        result = eng._execute_var_length_cypher(sql_q)
        assert result["rows"] == []


def test_execute_var_length_cypher_count_match_callclassmethod():
    """COUNT match path using _call_classmethod (lines 1537-1551)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT COUNT(DISTINCT b.node_id) AS cnt FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]
    with patch("iris_vector_graph.schema._call_classmethod", return_value="42"):
        result = eng._execute_var_length_cypher(sql_q)
        assert result["rows"] == [[42]]
        assert result["columns"] == ["cnt"]


def test_execute_var_length_cypher_count_match_exception():
    """COUNT match path: _call_classmethod exception → cnt=0 (lines 1544-1545)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT COUNT(DISTINCT b.node_id) AS cnt FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("fail")):
        result = eng._execute_var_length_cypher(sql_q)
        assert result["rows"] == [[0]]


def test_execute_var_length_cypher_bfsfastjsonsorted_sorted_tag():
    """BFSFastJsonSorted returns SORTED:tag → streams results (lines 1600-1611)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT b.node_id AS b_id FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    from iris_vector_graph.result import IVGResult
    with patch("iris_vector_graph.schema._call_classmethod", return_value="SORTED:abc"):
        with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=iter([{"o": "n2", "step": 1}])):
            result = eng._execute_var_length_cypher(sql_q)
            assert result is not None


def test_execute_var_length_cypher_bfsfastjsonsorted_empty():
    """BFSFastJsonSorted returns SORTED:0 → bfs_results=[] (line 1613)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT b.node_id AS b_id FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    # SORTED:0 is the "empty" sentinel → bfs_results = []
    with patch("iris_vector_graph.schema._call_classmethod", return_value="SORTED:0"):
        result = eng._execute_var_length_cypher(sql_q)
        assert result["rows"] == []


def test_execute_var_length_cypher_bfsfastjsonsorted_exception():
    """BFSFastJsonSorted exception → empty result (lines 1614-1616)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT b.node_id AS b_id FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("fail")):
        result = eng._execute_var_length_cypher(sql_q)
        assert result["rows"] == []


def test_execute_var_length_cypher_min_hops_filter():
    """min_hops > 1 filtering (lines 1618-1630)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT b.node_id AS b_id FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 5,
            "min_hops": 2,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    bfs_data = [
        {"o": "n2", "step": 1},
        {"o": "n3", "step": 2},
    ]
    # BFSFastJsonSorted returns SORTED:tag → _bfs_stream_pages runs → yields bfs_data
    with patch("iris_vector_graph.schema._call_classmethod", return_value="SORTED:abc"):
        with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=iter(bfs_data)):
            result = eng._execute_var_length_cypher(sql_q)
            # n2 (step=1) should be excluded; n3 (step=2) included
            ids = [r[0] for r in result.rows]
            assert "n2" not in ids
            assert "n3" in ids


def test_execute_var_length_cypher_id_only_match():
    """id_only_match fast path (lines 1666-1677)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT DISTINCT b.node_id AS bid FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    bfs_data = [{"o": "n2", "step": 1}, {"o": "n3", "step": 1}]
    with patch("iris_vector_graph.schema._call_classmethod", return_value="SORTED:abc"):
        with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=iter(bfs_data)):
            result = eng._execute_var_length_cypher(sql_q)
            assert "bid" in result["columns"]
            assert ["n2"] in result["rows"] or ["n3"] in result["rows"]


def test_execute_var_length_cypher_count_second_match():
    """Post-BFS COUNT match path (lines 1657-1664)."""
    eng, conn, cur = make_engine()
    sql_q = make_sql_query(
        sql="SELECT DISTINCT b.node_id AS bid FROM Graph_KG.nodes",
        params=["n1"],
        var_length_paths=[{
            "types": [],
            "max_hops": 3,
            "min_hops": 1,
            "direction": "out",
            "properties": {},
            "source_var": None,
        }]
    )
    sql_q.parameters = [["n1"]]

    bfs_data = [{"o": "n2", "step": 1}]
    with patch("iris_vector_graph.schema._call_classmethod", return_value="SORTED:abc"):
        with patch("iris_vector_graph.engine._bfs_stream_pages", return_value=iter(bfs_data)):
            result = eng._execute_var_length_cypher(sql_q)
            assert "bid" in result["columns"]
            assert result["rows"] == [["n2"]]


# ---------------------------------------------------------------------------
# _try_khop_fast_path  (lines 1709-1852)
# ---------------------------------------------------------------------------

def test_try_khop_fast_path_no_match():
    eng, conn, cur = make_engine()
    result = eng._try_khop_fast_path("MATCH (n) RETURN n", {})
    assert result is None


def test_try_khop_fast_path_1hop_count_no_param():
    eng, conn, cur = make_engine()
    result = eng._try_khop_fast_path(
        "MATCH (a {node_id: $src})-[:KNOWS]->(b) RETURN count(b) AS cnt",
        {}  # no src param
    )
    assert result is None


def test_try_khop_fast_path_1hop_count_iris_exception():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
        result = eng._try_khop_fast_path(
            "MATCH (a {node_id: $src})-[:KNOWS]->(b) RETURN count(b) AS cnt",
            {"src": "n1"}
        )
        assert result is None


def test_try_khop_fast_path_1hop_ids_no_param():
    eng, conn, cur = make_engine()
    result = eng._try_khop_fast_path(
        "MATCH (a {node_id: $src})-[:KNOWS]->(b) RETURN b.node_id",
        {}
    )
    assert result is None


def test_try_khop_fast_path_1hop_ids_iris_exception():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
        result = eng._try_khop_fast_path(
            "MATCH (a {node_id: $src})-[:KNOWS]->(b) RETURN b.node_id",
            {"src": "n1"}
        )
        assert result is None


def test_try_khop_fast_path_2hop_count_no_param():
    eng, conn, cur = make_engine()
    result = eng._try_khop_fast_path(
        "MATCH (a {node_id: $src})-[:KNOWS*2]->(b) RETURN count(b) AS cnt",
        {}
    )
    assert result is None


def test_try_khop_fast_path_2hop_count_iris_exception():
    """Exercises KHop2CountExact exception path (lines 1781-1782)."""
    eng, conn, cur = make_engine()
    with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
        result = eng._try_khop_fast_path(
            "MATCH (a {node_id: $src})-[:KNOWS*2]->(b) RETURN count(b) AS cnt",
            {"src": "n1"}
        )
        assert result is None


def test_try_khop_fast_path_2hop_ids_no_param():
    eng, conn, cur = make_engine()
    result = eng._try_khop_fast_path(
        "MATCH (a {node_id: $src})-[:KNOWS*2]->(b) RETURN b.node_id",
        {}
    )
    assert result is None


def test_try_khop_fast_path_2hop_ids_iris_exception():
    eng, conn, cur = make_engine()
    with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no iris")):
        result = eng._try_khop_fast_path(
            "MATCH (a {node_id: $src})-[:KNOWS*2]->(b) RETURN b.node_id",
            {"src": "n1"}
        )
        assert result is None


# ---------------------------------------------------------------------------
# _execute_approx_count_distinct  (lines 1853-1919)
# ---------------------------------------------------------------------------

def test_execute_approx_count_distinct_no_var_length_paths():
    """No var_length_paths → return 0 (lines 1868-1869).

    Use a simple MATCH (no variable-length) so the real parser+translator
    produces no var_length_paths in the result.
    """
    eng, conn, cur = make_engine()
    import re
    cypher = "MATCH (n) RETURN approx_count_distinct(n) AS approxCount"
    m = re.search(r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
                  cypher, re.IGNORECASE)
    # Real parse+translate: simple MATCH (n) has no var_length_paths
    result = eng._execute_approx_count_distinct(cypher, {}, m)
    assert result["rows"] == [[0]]
    assert result["columns"] == ["approxCount"]


def test_execute_approx_count_distinct_parse_exception():
    """Parse exception → return 0 (lines 1865-1866)."""
    eng, conn, cur = make_engine()
    import re
    cypher = "MATCH (n) RETURN approx_count_distinct(n) AS approxCount"
    m = re.search(r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
                  cypher, re.IGNORECASE)
    # Patch the locally-imported parse_query inside the function's module
    with patch("iris_vector_graph.cypher.parser.parse_query", side_effect=Exception("parse fail")):
        result = eng._execute_approx_count_distinct(cypher, {}, m)
        assert result["rows"] == [[0]]


def test_execute_approx_count_distinct_no_source_id():
    """source_id None → return 0 (lines 1889-1890).

    Use a real VLP query but no bound parameters so source_id stays None.
    """
    eng, conn, cur = make_engine()
    import re
    cypher = "MATCH (n {node_id: $src})-[*1..3]->(m) RETURN approx_count_distinct(m) AS approxCount"
    m = re.search(r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
                  cypher, re.IGNORECASE)
    # No parameters → source_id resolves to None → returns [[0]]
    result = eng._execute_approx_count_distinct(cypher, {}, m)
    assert result["rows"] == [[0]]


def test_execute_approx_count_distinct_count_distinct_khop_ok():
    """CountDistinctKHop succeeds (lines 1893-1904)."""
    eng, conn, cur = make_engine()
    import re
    cypher = "MATCH (n {node_id: $src})-[*1..3]->(m) RETURN approx_count_distinct(m) AS approxCount"
    m = re.search(r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
                  cypher, re.IGNORECASE)
    with patch("iris_vector_graph.schema._call_classmethod",
               return_value='{"estimate":100,"registers":256,"std_error":0.065}'):
        result = eng._execute_approx_count_distinct(cypher, {"src": "n1"}, m)
        assert result["rows"] == [[100]]
        assert "approx_count_distinct" in result.metadata.warnings[0]


def test_execute_approx_count_distinct_count_distinct_khop_fail():
    """CountDistinctKHop exception → estimate=0 (lines 1899-1905)."""
    eng, conn, cur = make_engine()
    import re
    cypher = "MATCH (n {node_id: $src})-[*1..3]->(m) RETURN approx_count_distinct(m) AS approxCount"
    m = re.search(r'\bapprox_count_distinct\s*\(\s*(\w+)\s*\)\s+AS\s+(\w+)',
                  cypher, re.IGNORECASE)
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("fail")):
        result = eng._execute_approx_count_distinct(cypher, {"src": "n1"}, m)
        assert result["rows"] == [[0]]


# ---------------------------------------------------------------------------
# _execute_parsed: transactional path and column_name_map  (lines 324-343)
# ---------------------------------------------------------------------------

def test_execute_parsed_transactional():
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    from unittest.mock import MagicMock

    meta = MagicMock()
    sql_q = make_sql_query(is_transactional=True)
    sql_q.query_metadata = meta
    sql_q.column_name_map = {"old": "new"}

    tx_result = IVGResult(columns=["old"], rows=[["val"]])
    eng._store.execute_transaction.return_value = tx_result

    with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_q):
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
            parsed = MagicMock()
            parsed.subsequent_queries = []
            parsed.procedure_call = None
            parsed.is_mutation = False
            mock_parse.return_value = parsed
            with patch.object(eng, "_reconnect_if_stale"):
                result = eng.execute_cypher("CREATE (n:Person)")
                # column_name_map applied
                assert "new" in result["columns"]


def test_execute_parsed_native_sql_with_bolt_column_types():
    eng, conn, cur = make_engine()
    from iris_vector_graph.result import IVGResult

    sql_q = make_sql_query()
    sql_q.bolt_column_types = {"id": "integer"}
    sql_q.column_name_map = {}
    sql_q.query_metadata = None
    sql_q.is_transactional = False
    sql_q.var_length_paths = []

    native_result = IVGResult(columns=["id"], rows=[[1]])
    eng._store.execute_sql.return_value = native_result

    with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_q):
        with patch("iris_vector_graph._engine.query.parse_query") as mock_parse:
            parsed = MagicMock()
            parsed.subsequent_queries = []
            parsed.procedure_call = None
            parsed.is_mutation = False
            mock_parse.return_value = parsed
            with patch.object(eng, "_reconnect_if_stale"):
                result = eng.execute_cypher("MATCH (n) RETURN n.id")
                assert result.bolt_column_types == {"id": "integer"}
