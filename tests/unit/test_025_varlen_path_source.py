"""Spec 025 amendment (2026-10-07): a variable-length path with path functions starts from the
node the pattern names, answers every column, and says so when it cannot find its source.

Evidence: docs/research/length-path-empty-result-report.md. The live companion is
tests/integration/test_025_varlen_path_functions.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.query import extract_vlp_source_ids
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

# ------------------------------------------------------------------ T033: get_nodes chunks


def _store():
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store.conn.cursor.return_value.fetchall.return_value = []
    return store


def test_get_nodes_reads_a_thousand_ids_in_chunks_of_at_most_500():
    store = _store()
    ids = [f"n{i:05d}" for i in range(1200)]
    result = store.get_nodes(ids)
    executed = store.conn.cursor.return_value.execute.call_args_list
    sizes = [len(c.args[1]) for c in executed]
    assert sizes == [500, 500, 200]
    assert max(sizes) <= 500
    assert [r[0] for r in result.rows] == ids  # every id answered, in the order asked


def test_get_nodes_chunks_the_property_read_too():
    store = _store()
    store.get_nodes([f"n{i}" for i in range(1100)], properties=["id"])
    executed = store.conn.cursor.return_value.execute.call_args_list
    assert max(len(c.args[1]) for c in executed) <= 500
    assert len(executed) == 6  # 3 label chunks + 3 property chunks


def test_get_nodes_merges_labels_across_chunks():
    store = _store()
    ids = [f"n{i}" for i in range(600)]
    store.conn.cursor.return_value.fetchall.side_effect = [
        [("n0", "A")],
        [("n599", "B")],
    ]
    rows = {r[0]: json.loads(r[1]) for r in store.get_nodes(ids).rows}
    assert rows["n0"] == ["A"] and rows["n599"] == ["B"] and rows["n300"] == []


# ------------------------------------------------------------ T034: source extraction

SQL_BOUND = (
    "SELECT %EXACT(p3.val) AS dep_id, 1 AS d\nFROM Graph_KG.nodes n0\n"
    "JOIN Graph_KG.rdf_labels l1 ON l1.s = n0.node_id AND l1.label = ?\n"
    "JOIN Graph_KG.nodes n2 ON 1=1\n"
    "WHERE l1.s IS NOT NULL AND n0.node_id = CAST('prop23' AS VARCHAR(256))"
)


def _sq(sql=SQL_BOUND, params=("Proposition",)):
    sq = MagicMock()
    sq.sql = sql
    sq.parameters = [list(params)]
    return sq


def _labels_store(labels_of: dict):
    store = MagicMock()

    def get_node_labels(ids):
        return IVGResult(
            columns=["id", "labels"], rows=[[i, json.dumps(labels_of.get(i, []))] for i in ids]
        )

    store.get_node_labels.side_effect = get_node_labels
    return store


def test_an_id_binding_wins_over_the_label():
    store = _labels_store({"prop23": ["Proposition"]})
    ids = extract_vlp_source_ids(_sq(), ["Proposition"], "n0", "n2", store)
    assert ids == ["prop23"]
    store.query_nodes.assert_not_called()  # the label's whole population was never read


def test_the_label_narrows_an_id_binding():
    store = _labels_store({"prop23": ["Goal"]})
    assert extract_vlp_source_ids(_sq(), ["Proposition"], "n0", "n2", store) == []


def test_an_id_binding_without_a_label_needs_no_label_read():
    sq = _sq(sql="SELECT 1 FROM Graph_KG.nodes n0 WHERE n0.node_id = CAST('prop23' AS VARCHAR(256))", params=())
    store = _labels_store({})
    assert extract_vlp_source_ids(sq, [], "n0", "n2", store) == ["prop23"]


def test_a_label_only_source_is_every_node_with_the_label():
    sq = _sq(sql="SELECT 1 FROM Graph_KG.nodes n0 JOIN Graph_KG.rdf_labels l1 ON l1.s = n0.node_id")
    store = MagicMock()
    store.query_nodes.return_value = IVGResult(columns=["id"], rows=[["a"], ["b"]])
    assert sorted(extract_vlp_source_ids(sq, ["Proposition"], "n0", "n2", store)) == ["a", "b"]


def test_a_failed_label_lookup_is_an_error_not_an_empty_answer():
    sq = _sq(sql="SELECT 1 FROM Graph_KG.nodes n0 JOIN Graph_KG.rdf_labels l1 ON l1.s = n0.node_id")
    store = MagicMock()
    store.query_nodes.side_effect = RuntimeError("SQLCODE -1: too many parameters")
    with pytest.raises(RuntimeError, match="Proposition"):
        extract_vlp_source_ids(sq, ["Proposition"], "n0", "n2", store)


def test_a_failed_label_check_on_a_bound_id_is_an_error_too():
    store = MagicMock()
    store.get_node_labels.side_effect = RuntimeError("link down")
    with pytest.raises(RuntimeError):
        extract_vlp_source_ids(_sq(), ["Proposition"], "n0", "n2", store)


# ------------------------------------------------------------------ T035/T036: routing


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._nkg_dirty = False
    eng._arno_capabilities = {}
    eng._arno_available = False
    eng._store = MagicMock()
    return eng


def _vl(**over):
    base = {
        "types": ["depends_on"],
        "max_hops": 3,
        "min_hops": 1,
        "direction": "out",
        "properties": {},
        "return_path_funcs": ["length"],
        "weighted": False,
        "shortest": False,
        "all_shortest": False,
        "src_id_param": "prop23",
        "source_labels": ["Proposition"],
        "source_alias": "n0",
        "target_alias": "n2",
        "source_var": "p",
        "target_var": "dep",
        "path_named_var": "path",
    }
    base.update(over)
    return base


def _routed_sq(vl):
    sq = MagicMock()
    sq.var_length_paths = [vl]
    sq.sql = SQL_BOUND
    sq.parameters = [["Proposition", "id"]]
    sq.column_name_map = {}
    sq.graph_context = None
    return sq


def test_path_functions_with_a_resolved_unlabelled_source_pass_that_source_through():
    eng = _engine()
    sentinel = IVGResult(columns=["dep.id", "d"], rows=[["x", 1]])
    with patch.object(
        eng, "_execute_var_length_labeled_path_funcs", return_value=sentinel
    ) as labeled, patch.object(eng, "_execute_var_length_cypher") as legacy:
        out = eng._route_var_length(_routed_sq(_vl(source_labels=[])), {})
    assert out is sentinel
    legacy.assert_not_called()
    assert labeled.call_args.kwargs["source_ids"] == ["prop23"]


def test_path_functions_with_a_labelled_source_let_the_extractor_check_the_label_on_the_id():
    eng = _engine()
    sentinel = IVGResult(columns=["dep.id", "d"], rows=[])
    with patch.object(
        eng, "_execute_var_length_labeled_path_funcs", return_value=sentinel
    ) as labeled, patch.object(eng, "_execute_var_length_cypher") as legacy:
        eng._route_var_length(_routed_sq(_vl()), {})
    legacy.assert_not_called()
    assert labeled.call_args.kwargs["source_ids"] is None


def test_path_functions_with_no_bound_source_still_find_their_sources_by_label():
    eng = _engine()
    sentinel = IVGResult(columns=["dep_id", "d"], rows=[])
    with patch.object(
        eng, "_execute_var_length_labeled_path_funcs", return_value=sentinel
    ) as labeled:
        eng._route_var_length(_routed_sq(_vl(src_id_param=None)), {})
    assert labeled.call_args.kwargs.get("source_ids") is None


def test_the_legacy_executor_starts_from_the_routers_source_not_the_first_string_parameter():
    eng = _engine()
    sq = _routed_sq(_vl(return_path_funcs=[], min_hops=2))
    calls = []

    def fake(conn, cls, method, *args):
        calls.append((method, args))
        return "SORTED:0"

    with patch("iris_vector_graph.schema._call_classmethod", side_effect=fake):
        eng._execute_var_length_cypher(sq, {})
    bfs = [a for m, a in calls if m == "BFSFastJsonSorted"]
    assert bfs and bfs[0][0] == "prop23", bfs


# ------------------------------------------------- FR-010: one column per RETURN item


def _executor_inputs(query: str):
    """A real translated query, so the columns under test are the translator's own."""
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    parsed = parse_query(query)
    sq = translate_to_sql(parsed, {}, engine=None)
    sq.return_items = list(parsed.return_clause.items)
    return sq, sq.var_length_paths[0]


def _run_executor(query: str, bfs_rows, props):
    eng = _engine()
    sq, vl = _executor_inputs(query)
    eng._store.execute_bfs.return_value = IVGResult(columns=["id", "hops", "pred"], rows=bfs_rows)
    eng._store.query_nodes.return_value = IVGResult(columns=["id"], rows=[])
    cursor = MagicMock()
    cursor.fetchall.return_value = [(t, v) for t, v in props.items()]
    eng._store.conn.cursor.return_value = cursor
    return eng._execute_var_length_labeled_path_funcs(sq, {}, vl, source_ids=["prop23"])


Q = "MATCH path = (p:Proposition {id: 'prop23'})-[:depends_on*1..3]->(dep) "


def test_a_property_and_an_aliased_length_are_two_columns_in_return_order():
    out = _run_executor(Q + "RETURN dep.id, length(path) AS depth", [["a", 1], ["b", 2]], {"a": "a", "b": "b"})
    assert out.columns == ["dep.id", "depth"]
    assert out.rows == [["a", 1], ["b", 2]]


def test_length_alone_is_one_column():
    out = _run_executor(Q + "RETURN length(path) AS d", [["a", 1], ["b", 3]], {})
    assert out.columns == ["d"] and out.rows == [[1], [3]]


def test_a_property_before_and_after_the_path_function_keeps_the_order():
    out = _run_executor(Q + "RETURN length(path) AS n, dep.id AS who", [["a", 2]], {"a": "a"})
    assert out.columns == ["n", "who"] and out.rows == [[2, "a"]]


def test_an_empty_traversal_keeps_the_columns():
    out = _run_executor(Q + "RETURN dep.id, length(path) AS depth", [], {})
    assert out.columns == ["dep.id", "depth"] and out.rows == []


def test_a_missing_property_is_null_not_a_dropped_row():
    out = _run_executor(Q + "RETURN dep.id, length(path) AS depth", [["a", 1], ["b", 1]], {"a": "a"})
    assert out.rows == [["a", 1], ["b", 1]] or out.rows == [["a", 1], [None, 1]]
    assert len(out.rows) == 2
