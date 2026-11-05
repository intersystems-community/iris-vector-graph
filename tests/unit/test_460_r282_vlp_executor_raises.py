"""4.6.0 R282: the var-length path executors raise instead of answering fewer rows, and read in the path's graph.

`_execute_var_length_labeled_path_funcs`, `_bfs_with_paths` and `_execute_var_length_labeled` caught every SQL error,
logged it at debug and answered truncated or missing paths, nodes with no labels or properties, and relationships with
none. Graph scope is checked live in tests/e2e/test_460_r282_vlp_executor_graph_scope_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

QUERY = Path(__file__).resolve().parents[2] / "iris_vector_graph" / "_engine" / "query.py"


def _body(name: str) -> str:
    src = QUERY.read_text(encoding="utf-8")
    start = src.index(f"    def {name}(")
    end = src.index("\n    def ", start + 10)
    return src[start:end]


@pytest.mark.parametrize("name", ["_execute_var_length_labeled_path_funcs", "_bfs_with_paths", "_vlp_walks",
                                  "_vlp_labeled", "_vlp_node_data", "_vlp_qualifiers", "_project_path_items"])
def test_no_handler_swallows(name):
    assert not re.search(r"except( Exception\b|\s*:)", _body(name)), name  # a narrow ValueError for int() stays


def test_the_labeled_route_does_not_swallow_its_walks():
    body = _body("_execute_var_length_labeled")
    walk = body[body.index("for src_id in source_ids:", body.index("Step 2")):body.index("Filter by min_hops")]
    assert not re.search(r"except( Exception\b|\s*:)", walk)


def test_shortest_path_json_no_longer_rebuilds_relationships():
    """It reads no graph and could pick another path between the same ends; the path walked is kept instead."""
    assert "\"ShortestPathJson\"" not in _body("_execute_var_length_labeled_path_funcs")


def _engine(cursor):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._schema_prefix = "Graph_KG"
    store = MagicMock()
    store.conn.cursor.return_value = cursor
    store._schema_prefix = "Graph_KG"
    eng._store = store
    return eng


def test_a_failed_edge_query_raises_not_a_shorter_walk():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [[("n1", "n2", "KNOWS")]]
    cursor.execute.side_effect = [None, RuntimeError("<-99> not privileged")]
    with pytest.raises(RuntimeError, match="-99"):
        _engine(cursor)._bfs_with_paths("n1", [], 3, "out", graph="")


def test_a_failed_bfs_fallback_raises():
    from iris_vector_graph.result import IVGResult

    eng = _engine(MagicMock())

    class _Store:  # a store written before execute_vl_ends existed
        def execute_bfs(self, *a, **k):
            return IVGResult(columns=[], rows=[], error="walk failed")

    eng._store = _Store()
    with pytest.raises(RuntimeError, match="walk failed"):
        eng._vl_ends("n1", [], "out", 1, 2, "")


def test_relationship_qualifiers_are_read_in_the_paths_graph_and_direction():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, ('{"w": 9}',)]
    eng = _engine(cursor)
    assert eng._vlp_qualifiers("a1", "knows", "b1", "both", "g282") == '{"w": 9}'
    (sql1, args1), (sql2, args2) = [c.args for c in cursor.execute.call_args_list]
    assert "COALESCE(graph_id, '') = COALESCE(?, '')" in sql1
    assert args1 == ["a1", "knows", "b1", "g282"] and args2 == ["b1", "knows", "a1", "g282"]


def test_the_node_fetch_reads_labels_and_properties_in_the_nodes_graph():
    cursor = MagicMock()
    cursor.fetchall.return_value = [("b1", '["P"]', "[]")]
    eng = _engine(cursor)
    out = eng._vlp_node_data({("g282", "b1")})
    assert out == {("g282", "b1"): {"_id": "b1", "_labels": '["P"]', "_props": "[]"}}
    sql, args = cursor.execute.call_args.args
    assert args == ["g282", "b1"]
    assert "AND COALESCE(graph_id, '') = COALESCE(n.graph_id, ''))" in sql  # labels and properties, each subquery
    assert sql.count("COALESCE(n.graph_id, '')") == 3
