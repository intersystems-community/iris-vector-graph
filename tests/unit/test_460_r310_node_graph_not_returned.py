"""4.6.0 R310 follow-up (fixes66): a node value answers its id, labels and properties only.

A collected node's JSON carries its graph (`"_graph"`, last) so a later property read finds the node's own graph's
row (test_460_r310_collected_node_two_graphs_e2e). That key is the engine's: every row execute_cypher answers (and so
Bolt and the REST API, which read those rows) drops it, at whatever depth of JSON text it sits.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from iris_vector_graph._engine.query import _finish_sql_result, _strip_node_graph

NODE = '{"_id":"a","_labels":["L"],"_props":["{\\"key\\":\\"k\\",\\"value\\":\\"v\\"}"]}'


def _with_graph(node: str, g: str) -> str:
    return node[:-1] + ',"_graph":"' + g + '"}'


def test_a_node_value():
    assert _strip_node_graph(_with_graph(NODE, "g1")) == NODE
    assert _strip_node_graph(_with_graph(NODE, "")) == NODE


def test_a_list_of_node_values_and_one_inside_another_value():
    listed = json.dumps([_with_graph(NODE, "g1"), _with_graph(NODE, "")], separators=(",", ":"))
    assert _strip_node_graph(listed) == json.dumps([NODE, NODE], separators=(",", ":"))
    nested = json.dumps([listed], separators=(",", ":"))
    assert _strip_node_graph(nested) == json.dumps([json.dumps([NODE, NODE], separators=(",", ":"))], separators=(",", ":"))


def test_other_text_is_left_alone():
    for s in ('{"_graph":"g"}', 'a "_graph" b', '[1,2]', NODE):
        assert _strip_node_graph(s) == s


def test_finished_rows_carry_only_id_labels_and_props():
    result = SimpleNamespace(columns=["c", "m", "n"], rows=[[json.dumps([_with_graph(NODE, "g")]), _with_graph(NODE, "g"), 7]],
                             error=None, bolt_column_types=None)
    sq = SimpleNamespace(bolt_column_types=None, column_name_map={}, typed_value_columns=[], bool_text_columns=[],
                         bool_expr_columns=[], prop_text_expr_columns=[], return_arity=3)
    out = _finish_sql_result(result, sq)
    c, m, n = out.rows[0]
    assert json.loads(json.loads(c)[0]).keys() == {"_id", "_labels", "_props"}
    assert json.loads(m).keys() == {"_id", "_labels", "_props"} and n == 7
