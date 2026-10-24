"""
Extended coverage for _engine/query.py.
Targets lines 475-542, 552-879, 976-1048, 1185, 1201-1294.
No IRIS connection — all DB calls mocked.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine(rows=None, fetchone=None):
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.result import IVGResult

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = fetchone
    cur.description = []
    conn.cursor.return_value = cur
    conn.commit.return_value = None
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._native_vec_available = False
    eng._embedding_function_available = False
    eng._arno_available = False
    eng._arno_capabilities = {}
    eng.embedding_dimension = 128
    eng._nkg_dirty = False

    # _store shim used by QueryMixin helpers
    store = MagicMock()
    store.conn = conn
    store._schema_prefix = "Graph_KG"
    eng._store = store

    return eng, cur, store


# ---------------------------------------------------------------------------
# _fetch_props_json (lines 1194-1222)
# ---------------------------------------------------------------------------

def test_fetch_props_json_empty_list():
    eng, cur, store = make_engine()
    result = eng._fetch_props_json([])
    assert result == {}


def test_fetch_props_json_basic():
    eng, cur, store = make_engine()
    cur.fetchall.return_value = [
        ("n1", "name", "Alice"),
        ("n1", "age", "30"),
        ("n2", "name", "Bob"),
    ]
    store.conn.cursor.return_value = cur
    result = eng._fetch_props_json(["n1", "n2"])
    # n1 and n2 should be present
    assert "n1" in result
    assert "n2" in result
    # Values should be JSON-serialized prop lists
    props_n1 = json.loads(result["n1"])
    keys = [p["key"] for p in props_n1]
    assert "name" in keys
    assert "age" in keys


def test_fetch_props_json_error_returns_none_values():
    eng, cur, store = make_engine()
    store.conn.cursor.side_effect = Exception("no cursor")
    result = eng._fetch_props_json(["n1"])
    # On error: returns dict with None values
    assert result == {"n1": None}


def test_fetch_props_json_nodes_with_no_props():
    eng, cur, store = make_engine()
    cur.fetchall.return_value = []
    store.conn.cursor.return_value = cur
    result = eng._fetch_props_json(["n1", "n2"])
    # Present but None
    assert result["n1"] is None
    assert result["n2"] is None


# ---------------------------------------------------------------------------
# _bfs_with_paths (lines 1224-1294)
# ---------------------------------------------------------------------------

def test_bfs_with_paths_empty_graph():
    """No edges → empty result."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    cur.fetchall.return_value = []
    result = eng._bfs_with_paths("n1", [], 2, "out")
    assert result == []


def test_bfs_with_paths_cursor_error_returns_empty():
    """Cursor exception → empty list."""
    eng, cur, store = make_engine()
    store.conn.cursor.side_effect = Exception("no cursor")
    result = eng._bfs_with_paths("n1", [], 2, "out")
    assert result == []


def test_bfs_with_paths_one_hop_out():
    """Single outbound hop: n1 → n2."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
    result = eng._bfs_with_paths("n1", [], 1, "out")
    assert len(result) == 1
    nodes, edges = result[0]
    assert nodes == ["n1", "n2"]
    assert edges == ["KNOWS"]


def test_bfs_with_paths_two_hops():
    """n1→n2→n3 with 2 hops."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    hop1 = [("n1", "n2", "KNOWS")]
    hop2 = [("n2", "n3", "LIKES")]
    call_count = [0]
    def fetchall_side(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return hop1
        return hop2
    cur.fetchall.side_effect = fetchall_side
    result = eng._bfs_with_paths("n1", [], 2, "out")
    # Should find both n1→n2 and n1→n2→n3
    assert len(result) >= 1
    all_targets = [r[0][-1] for r in result]
    assert "n2" in all_targets


def test_bfs_with_paths_no_cycle():
    """No cycle: n1→n2→n1 should not follow back to n1."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    # hop1 returns n1→n2; hop2 returns n2→n1 (would create a cycle)
    hop1 = [("n1", "n2", "KNOWS")]
    hop2 = [("n2", "n1", "KNOWS")]
    call_count = [0]
    def fetchall_side(*a, **kw):
        call_count[0] += 1
        return hop1 if call_count[0] == 1 else hop2
    cur.fetchall.side_effect = fetchall_side
    result = eng._bfs_with_paths("n1", [], 2, "out")
    # n1 should not appear as a target node (cycle prevention)
    for nodes, edges in result:
        assert nodes[-1] != "n1", "Cycle detected — should be prevented"


def test_bfs_with_paths_inbound():
    """Inbound direction reverses s/o_id order."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    cur.fetchall.return_value = [("n2", "n1", "KNOWS")]  # o_id=n2, s=n1 → n2←n1
    result = eng._bfs_with_paths("n2", [], 1, "in")
    # Should find the path n2←n1
    assert len(result) == 1


def test_bfs_with_paths_both_directions():
    """Both direction queries outbound AND inbound."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    out_rows = [("n1", "n2", "KNOWS")]
    in_rows = [("n3", "n1", "LIKES")]  # n3 → n1 inbound
    calls = [0]
    def fetchall_side(*a, **kw):
        calls[0] += 1
        return out_rows if calls[0] % 2 == 1 else in_rows
    cur.fetchall.side_effect = fetchall_side
    result = eng._bfs_with_paths("n1", [], 1, "both")
    assert len(result) >= 1


def test_bfs_with_paths_with_predicates():
    """Predicates are passed as params to constrain edge type."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    cur.fetchall.return_value = [("n1", "n2", "KNOWS")]
    result = eng._bfs_with_paths("n1", ["KNOWS"], 1, "out")
    # Verify the predicate was part of the executed SQL params
    executed_params = cur.execute.call_args[0][1]
    assert "KNOWS" in executed_params


def test_bfs_with_paths_edge_query_error_breaks_loop():
    """Edge query failure stops BFS early."""
    eng, cur, store = make_engine()
    store.conn.cursor.return_value = cur
    cur.execute.side_effect = Exception("DB error")
    result = eng._bfs_with_paths("n1", [], 3, "out")
    assert result == []


# ---------------------------------------------------------------------------
# execute_aql (lines 167-175)
# ---------------------------------------------------------------------------

def test_execute_aql_basic():
    """execute_aql delegates to execute_cypher via AQL translation."""
    eng, cur, store = make_engine()
    eng.execute_cypher = MagicMock(return_value=MagicMock(columns=[], rows=[]))
    # AQL GRAPH traversal syntax: FOR v IN 1..1 OUTBOUND 'n1' GRAPH 'g' RETURN v
    result = eng.execute_aql("FOR v IN 1..1 OUTBOUND 'n1' GRAPH 'g' RETURN v")
    # execute_cypher should have been called
    eng.execute_cypher.assert_called_once()


# ---------------------------------------------------------------------------
# _execute_var_length_labeled (lines 880-968) — call via execute_cypher
# (Covered indirectly; direct unit tests for helpers above)
# ---------------------------------------------------------------------------

def test_route_var_length_nkg_dirty_raises():
    """_route_var_length raises IndexNotSyncedError when _nkg_dirty."""
    from iris_vector_graph.errors import IndexNotSyncedError
    eng, cur, store = make_engine()
    eng._nkg_dirty = True

    from iris_vector_graph.cypher.translator import SQLQuery, QueryMetadata
    sql_query = SQLQuery(
        sql="SELECT 1",
        parameters=[[]],
        query_metadata=QueryMetadata(var_length_paths=[{"direction": "out", "max_hops": 2}]),
    )
    with pytest.raises(IndexNotSyncedError):
        eng._route_var_length(sql_query, {})


# ---------------------------------------------------------------------------
# _execute_approx_count_distinct (lines 204, 365-366)
# via execute_cypher with approx_count_distinct
# ---------------------------------------------------------------------------

def test_execute_cypher_approx_count_distinct():
    """approx_count_distinct rewrite takes an internal path."""
    eng, cur, store = make_engine(rows=[[42]])
    cur.description = [("approxCount",)]
    store.conn.cursor.return_value = cur

    # _execute_approx_count_distinct needs _store.conn for the query
    # and needs the engine to have _store.execute_bfs
    store.execute_bfs = MagicMock(return_value=MagicMock(rows=[["n1"], ["n2"]], error=None))
    from iris_vector_graph.result import IVGResult

    exact = IVGResult(columns=["approxCount"], rows=[[2]])
    with patch.object(eng, "_approx_counted_exactly", return_value=exact) as counted:
        result = eng.execute_cypher(
            "MATCH (n) RETURN approx_count_distinct(n) AS approxCount"
        )
    # no hop: the HLL route has nothing to count, so it is counted exactly
    assert counted.called and result.rows == [[2]]


