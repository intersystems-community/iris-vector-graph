"""4.6.0 R311 follow-up: a variable-length read the engine's BFS routes answer returns Python values too (fixes72).

`_execute_var_length_labeled_path_funcs` and `_execute_var_length_labeled` build their own rows, with `nodes(p)`,
`relationships(p)`, `RETURN p` and a hop's relationship variable as JSON text (json.dumps), and never pass them through
`_finish_sql_result`, so where they answered, those columns came back as text while the SQL route answers lists and
dicts. Since R308 (3ff6a049) no shape measured reaches them for those columns (a named path and a hop's relationship
variable expand in SQL; shortestPath has its own route), so the decode is applied where `_execute_parsed_sql` returns
the routes' answer: whatever route answers a translated statement, its list, map and path columns are Python values.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.result import IVGResult

Q = "MATCH p = (a {node_id: 'x'})-[:k*1..2]->(b) RETURN nodes(p) AS ns, relationships(p) AS rs, p, length(p) AS l"


def _engine():
    from iris_vector_graph._engine.query import QueryMixin

    e = QueryMixin.__new__(QueryMixin)
    e._store = SimpleNamespace()
    e._store_capabilities = {"native_sql": True}
    e._schema_prefix = "Graph_KG"
    e._fetch_first_unsafe = False
    e.get_table_mapping = lambda *a, **k: None
    e.get_rel_mapping = lambda *a, **k: None
    return e


def test_the_bfs_routes_text_columns_are_answered_as_python_values():
    rels = [{"type": "k", "props": {"w": 1}}]
    text_row = [json.dumps(["x", "y"]), json.dumps(rels), json.dumps({"nodes": ["x", "y"], "rels": ["k"]}), 1]
    routed = IVGResult(columns=["ns", "rs", "p", "l"], rows=[text_row])
    e = _engine()
    # the statement handed to the routes, as before R308 (a named path from an id-bound start)
    with patch("iris_vector_graph.cypher.translator._vlp_bfs_answers", return_value=True), \
            patch.object(type(e), "_route_var_length", return_value=routed) as route:
        r = e._execute_parsed_sql(parse_query(Q), {})
    assert route.called, "the statement went to the BFS routes"
    assert r.rows == [[["x", "y"], rels, {"nodes": ["x", "y"], "rels": ["k"]}, 1]]


def test_a_routes_answer_already_typed_is_left_as_it_is():
    typed = IVGResult(columns=["ns", "rs", "p", "l"], rows=[[["x"], [], {"nodes": ["x"], "rels": []}, 0]])
    e = _engine()
    with patch("iris_vector_graph.cypher.translator._vlp_bfs_answers", return_value=True), \
            patch.object(type(e), "_route_var_length", return_value=typed):
        r = e._execute_parsed_sql(parse_query(Q), {})
    assert r.rows == [[["x"], [], {"nodes": ["x"], "rels": []}, 0]]
