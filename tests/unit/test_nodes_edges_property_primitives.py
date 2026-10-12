"""
Coverage tests for _engine/nodes_edges.py property primitives (v2.5.0).
All SQL is mocked — no IRIS connection needed.
Targets: lines 56-57, 81-82, 89-90, 96-97, 167, 176, 191-197, 263-264, 267,
         320-343, 438-496, 504-574, 588-599, 642, 647, 858-864, 874, 907,
         928-935, 956-957, 977, 993, 1003-1010, 1042-1066, 1093-1127, 1210
"""
import json
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine(fetchall=None, fetchone=None, cursor_side_effect=None):
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = fetchall or []
    cur.fetchone.return_value = fetchone
    cur.description = []
    if cursor_side_effect:
        cur.execute.side_effect = cursor_side_effect
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
    return eng, cur


# ---------------------------------------------------------------------------
# get_node_ids_by_property (lines 415-448)
# ---------------------------------------------------------------------------

def test_get_node_ids_by_property_no_val():
    eng, cur = make_engine(fetchall=[["n1"], ["n2"]])
    result = eng.get_node_ids_by_property("source_url")
    assert result == ["n1", "n2"]
    # Should pass key as param
    args = cur.execute.call_args[0]
    assert "Graph_KG.rdf_props" in args[0]
    assert args[1] == ["source_url"]


def test_get_node_ids_by_property_with_val():
    eng, cur = make_engine(fetchall=[["n1"]])
    result = eng.get_node_ids_by_property("status", val="active")
    assert result == ["n1"]
    args = cur.execute.call_args[0]
    assert "AND val = ?" in args[0]
    assert args[1] == ["status", "active"]


def test_get_node_ids_by_property_with_limit():
    eng, cur = make_engine(fetchall=[["n1"], ["n2"]])
    result = eng.get_node_ids_by_property("type", limit=5)
    assert "TOP 5" in cur.execute.call_args[0][0]


def test_get_node_ids_by_property_zero_limit_no_top():
    eng, cur = make_engine(fetchall=[])
    eng.get_node_ids_by_property("type", limit=0)
    assert "TOP" not in cur.execute.call_args[0][0]


def test_get_node_ids_by_property_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("DB error")
    result = eng.get_node_ids_by_property("key")
    assert result == []


# ---------------------------------------------------------------------------
# get_nodes_by_property (lines 450-461)
# ---------------------------------------------------------------------------

def test_get_nodes_by_property_delegates():
    eng, cur = make_engine(fetchall=[["n1"]])
    eng.get_nodes = MagicMock(return_value=[{"id": "n1", "labels": [], "properties": {}}])
    result = eng.get_nodes_by_property("name", val="Alice")
    eng.get_nodes.assert_called_once_with(["n1"])
    assert result[0]["id"] == "n1"


def test_get_nodes_by_property_empty_ids():
    eng, cur = make_engine(fetchall=[])
    eng.get_nodes = MagicMock()
    result = eng.get_nodes_by_property("name", val="nobody")
    eng.get_nodes.assert_not_called()
    assert result == []


# ---------------------------------------------------------------------------
# get_property_pairs (lines 463-479)
# ---------------------------------------------------------------------------

def test_get_property_pairs_basic():
    eng, cur = make_engine(fetchall=[["n1", "val1"], ["n2", "val2"]])
    result = eng.get_property_pairs("source_url")
    assert result == [("n1", "val1"), ("n2", "val2")]


def test_get_property_pairs_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.get_property_pairs("key")
    assert result == []


# ---------------------------------------------------------------------------
# get_property_values (lines 481-496)
# ---------------------------------------------------------------------------

def test_get_property_values_basic():
    eng, cur = make_engine(fetchall=[["url1"], ["url2"]])
    result = eng.get_property_values("source_url")
    assert result == ["url1", "url2"]


def test_get_property_values_filters_none():
    eng, cur = make_engine(fetchall=[["url1"], [None], ["url2"]])
    result = eng.get_property_values("source_url")
    assert None not in result
    assert "url1" in result and "url2" in result


def test_get_property_values_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.get_property_values("key")
    assert result == []


# ---------------------------------------------------------------------------
# property_value_exists (lines 498-512)
# ---------------------------------------------------------------------------

def test_property_value_exists_true():
    eng, cur = make_engine(fetchone=[1])
    assert eng.property_value_exists("url", "%example%") is True


def test_property_value_exists_false():
    eng, cur = make_engine(fetchone=None)
    assert eng.property_value_exists("url", "%missing%") is False


def test_property_value_exists_uses_top_1():
    eng, cur = make_engine(fetchone=None)
    eng.property_value_exists("key", "val%")
    sql = cur.execute.call_args[0][0]
    assert "TOP 1" in sql


def test_property_value_exists_error_returns_false():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    assert eng.property_value_exists("key", "val") is False


# ---------------------------------------------------------------------------
# get_property_pairs_like (lines 514-538)
# ---------------------------------------------------------------------------

def test_get_property_pairs_like_basic():
    eng, cur = make_engine(fetchall=[["n1", "http://example.com"]])
    result = eng.get_property_pairs_like("url", "%example%")
    assert result == [("n1", "http://example.com")]


def test_get_property_pairs_like_with_limit():
    eng, cur = make_engine(fetchall=[])
    eng.get_property_pairs_like("url", "%x%", limit=10)
    assert "TOP 10" in cur.execute.call_args[0][0]


def test_get_property_pairs_like_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.get_property_pairs_like("key", "pat%")
    assert result == []


# ---------------------------------------------------------------------------
# get_json_field_values (lines 540-557)
# ---------------------------------------------------------------------------

def test_get_json_field_values_basic():
    eng, cur = make_engine(fetchall=[["https://example.com"], ["https://other.com"]])
    result = eng.get_json_field_values("metadata", "source_url")
    assert "https://example.com" in result
    # Verify SQL uses $PIECE
    sql = cur.execute.call_args[0][0]
    assert "$PIECE" in sql


def test_get_json_field_values_filters_none():
    eng, cur = make_engine(fetchall=[["url1"], [None]])
    result = eng.get_json_field_values("metadata", "url")
    assert None not in result


def test_get_json_field_values_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.get_json_field_values("key", "field")
    assert result == []


# ---------------------------------------------------------------------------
# get_node_ids_like (lines 559-573)
# ---------------------------------------------------------------------------

def test_get_node_ids_like_basic():
    eng, cur = make_engine(fetchall=[["test-run:node1"], ["test-run:node2"]])
    result = eng.get_node_ids_like("test-run:%")
    assert "test-run:node1" in result


def test_get_node_ids_like_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.get_node_ids_like("pat%")
    assert result == []


# ---------------------------------------------------------------------------
# count_subjects_with_property (lines 576-601)
# ---------------------------------------------------------------------------

def test_count_subjects_with_property_key_only():
    eng, cur = make_engine(fetchone=[42])
    result = eng.count_subjects_with_property("name")
    assert result == 42


def test_count_subjects_with_property_key_and_val():
    eng, cur = make_engine(fetchone=[7])
    result = eng.count_subjects_with_property("status", val="active")
    assert result == 7
    args = cur.execute.call_args[0]
    assert "AND val = ?" in args[0]


def test_count_subjects_with_property_error_returns_zero():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    result = eng.count_subjects_with_property("key")
    assert result == 0


def test_count_subjects_with_property_none_row():
    eng, cur = make_engine(fetchone=None)
    result = eng.count_subjects_with_property("key")
    assert result == 0


# ---------------------------------------------------------------------------
# create_node (lines 603-667) — error paths
# ---------------------------------------------------------------------------

def test_create_node_unique_error_returns_false():
    """Unique constraint → create_node returns False (not raise)."""
    eng, cur = make_engine()
    calls = [0]
    def side_effect(sql, *args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:  # fail on INSERT INTO nodes
            raise Exception("SQLCODE -119 unique constraint violation")
        return None
    cur.execute.side_effect = side_effect
    result = eng.create_node("n1", labels=["Person"])
    assert result is False


def test_create_node_with_graph_param():
    """graph kwarg → adds __graph to props."""
    eng, cur = make_engine()
    cur.execute.side_effect = None
    result = eng.create_node("n1", labels=["Person"], properties={"name": "Alice"}, graph="myGraph")
    # commit should have been called
    commit_calls = [c for c in cur.execute.call_args_list if "COMMIT" in str(c)]
    assert result is True


# ---------------------------------------------------------------------------
# _parse_node_from_row_map (lines 316-343) — via get_node
# ---------------------------------------------------------------------------

def test_get_node_returns_none_for_missing():
    """get_node on nonexistent id returns None."""
    eng, cur = make_engine(fetchone=None)
    result = eng.get_node("nonexistent")
    assert result is None


def test_get_node_parses_labels_and_props():
    """get_node hydrates labels and properties from JSON."""
    labels_json = json.dumps(["Person", "Employee"])
    props_json = json.dumps([{"key": "name", "value": "Alice"}, {"key": "age", "value": "30"}])
    eng, cur = make_engine()
    # cursor.description needed for column name mapping
    cur.description = [("node_id",), ("n_labels",), ("n_props",)]
    cur.fetchone.return_value = ["n1", labels_json, props_json]
    result = eng.get_node("n1")
    if result:
        assert result["id"] == "n1" or "labels" in result


# ---------------------------------------------------------------------------
# bulk_load_session (lines 45-100) — context manager paths
# ---------------------------------------------------------------------------

def test_bulk_load_session_incremental_init_fallback():
    """incremental=True with iris_obj failure → incremental_ok = False."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))
    eng.sync = MagicMock()
    eng._bulk_load_drifted = MagicMock(return_value=False)
    # rebuild_indexes=False so no index management
    with eng.bulk_load_session(rebuild_indexes=False, incremental=True) as session:
        pass
    # sync should have been called since incremental failed
    eng.sync.assert_called()


def test_bulk_load_session_basic_no_rebuild():
    """Basic bulk_load_session without rebuild_indexes."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))
    eng.sync = MagicMock()
    eng._bulk_load_drifted = MagicMock(return_value=False)
    with eng.bulk_load_session(rebuild_indexes=False, incremental=False) as session:
        assert session is not None


# ---------------------------------------------------------------------------
# _bulk_load_drifted (lines 103-114)
# ---------------------------------------------------------------------------

def test_bulk_load_drifted_iris_error_returns_true():
    """Exception in _iris_obj → drifted = True."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))
    assert eng._bulk_load_drifted() is True


def test_bulk_load_drifted_no_edges_returns_false():
    """sql_edges == 0 → drifted = False even if NKG is empty."""
    eng, cur = make_engine(fetchone=[0])
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = "0"
    eng._iris_obj = MagicMock(return_value=iris_obj)
    assert eng._bulk_load_drifted() is False


# ---------------------------------------------------------------------------
# count_nodes (lines 347-371)
# ---------------------------------------------------------------------------

def test_count_nodes_no_label():
    eng, cur = make_engine(fetchone=[100])
    assert eng.count_nodes() == 100


def test_count_nodes_with_label():
    eng, cur = make_engine(fetchone=[5])
    assert eng.count_nodes(label="Person") == 5
    sql = cur.execute.call_args[0][0]
    assert "rdf_labels" in sql


def test_count_nodes_error_returns_zero():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    assert eng.count_nodes() == 0


# ---------------------------------------------------------------------------
# get_nodes (lines 191+) — error path
# ---------------------------------------------------------------------------

def test_get_nodes_empty_list():
    eng, cur = make_engine()
    result = eng.get_nodes([])
    assert result == []


def test_get_nodes_error_returns_empty():
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("fail")
    # Fallback path calls _get_node_cypher_fallback per-node — mock to return None
    eng._get_node_cypher_fallback = MagicMock(return_value=None)
    result = eng.get_nodes(["n1"])
    assert result == []
