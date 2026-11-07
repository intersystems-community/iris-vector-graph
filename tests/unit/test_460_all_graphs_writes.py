"""4.6.0: a Cypher write with no USE GRAPH runs once per graph its MATCH bound, on that graph's ids (the executor's
half; the live behaviour is tests/e2e/test_460_all_graphs_writes_e2e.py).

The translator captures the written nodes with their graphs (`__capture_ids__ <key>`, two columns) and writes each
statement reading them with `graph_id = __GRAPH_<key>__` tests and, for an INSERT's row, the value `__GRAPHV_<key>__`.
The executor runs such a statement once per captured graph, with that graph's ids and that graph's name in both.
"""

from __future__ import annotations

from iris_vector_graph.stores.iris_sql_store import _capture_pairs, _capture_rows, _expand_captured


def _captured(rows):
    captured, graphs = {}, {}
    _capture_rows("w0", rows, captured, graphs)
    return captured, graphs


def test_an_insert_runs_once_per_graph_carrying_that_graph():
    captured, graphs = _captured([("a", ""), ("a", "g1"), ("b", "g1")])
    stmt = (
        "INSERT INTO rdf_props (graph_id, s, k) SELECT __GRAPHV_w0__, node_id, ? FROM nodes "
        "WHERE graph_id = __GRAPH_w0__ AND node_id IN (__IDS_w0__)"
    )
    assert _expand_captured(stmt, captured, graphs) == [
        "INSERT INTO rdf_props (graph_id, s, k) SELECT '', node_id, ? FROM nodes "
        "WHERE graph_id = '' AND node_id IN ('a')",
        "INSERT INTO rdf_props (graph_id, s, k) SELECT 'g1', node_id, ? FROM nodes "
        "WHERE graph_id = 'g1' AND node_id IN ('a', 'b')",
    ]


def test_a_graph_name_is_quoted_as_a_literal():
    captured, graphs = _captured([("a", "it's")])
    out = _expand_captured("SELECT __GRAPHV_w0__ WHERE graph_id = __GRAPH_w0__", captured, graphs)
    assert out == ["SELECT 'it''s' WHERE graph_id = 'it''s'"]


def test_nothing_captured_matches_nothing():
    captured, graphs = _captured([])
    out = _expand_captured("SELECT __GRAPHV_w0__ FROM t WHERE graph_id = __GRAPH_w0__ AND s IN (__IDS_w0__)", captured, graphs)
    assert out == ["SELECT NULL FROM t WHERE graph_id = NULL AND s IN (NULL)"]


def test_pairs_with_a_graph_are_read_per_graph():
    """`SET n.p = r.w` pairs each node with its row's edge: the same node id in two graphs pairs with each graph's own
    edge, where the first pair read used to answer for both."""
    captured, graphs = {}, {}
    _capture_pairs("v1", [("a", 10, ""), ("a", 20, "g1")], captured, graphs, with_graph=True)
    stmt = "UPDATE t SET x = __PAIR_v1__[s] WHERE s IN (__IDS_v1__) AND graph_id = __GRAPH_v1__"
    assert _expand_captured(stmt, captured, graphs) == [
        "UPDATE t SET x = (CASE s WHEN 'a' THEN CAST(10 AS BIGINT) END) WHERE s IN ('a') AND graph_id = ''",
        "UPDATE t SET x = (CASE s WHEN 'a' THEN CAST(20 AS BIGINT) END) WHERE s IN ('a') AND graph_id = 'g1'",
    ]


def test_pairs_without_a_graph_keep_the_first_pair_and_leave_the_graph_groups():
    """A two-column pairs select carries no graph: it does not touch a key's graph groups (an empty entry there would
    run a per-graph statement with `graph_id = NULL`, matching nothing)."""
    captured, graphs = {}, {"v1": {"g1": ["a"]}}
    _capture_pairs("v1", [("a", "b1"), ("a", "b9")], captured, graphs, with_graph=False)
    assert captured["v1#pairs"] == {"a": "b1"} and graphs["v1"] == {"g1": ["a"]}


def test_pairs_with_a_graph_and_no_row_capture_no_graph():
    """A three-column pairs select that read no row: the key holds no graph, so a statement scoped to it matches
    nothing (stale groups would write the ids of an earlier read)."""
    captured, graphs = {}, {"v1": {"g1": ["a"]}}
    _capture_pairs("v1", [], captured, graphs, with_graph=True)
    assert graphs["v1"] == {}
    out = _expand_captured("DELETE FROM t WHERE s IN (__IDS_v1__) AND graph_id = __GRAPH_v1__", captured, graphs)
    assert out == ["DELETE FROM t WHERE s IN (NULL) AND graph_id = NULL"]


def test_json_table_text_spells_the_empty_string_as_nul():
    from iris_vector_graph.stores.iris_sql_store import json_table_text

    assert [json_table_text(v) for v in ("", None, "\x00", "a", 0, False)] == ["\x00", None, "\x00", "a", 0, False]
