"""4.6.0 R157: `properties(n)` passes n's graph to `CY_PROPS_MAP`, which reads only that graph's rows.

`CY_PROPS_MAP(nid, tbl)` read `rdf_props WHERE s = ?`: two graphs holding a node of one id got one merged map. It now
takes the graph as a third argument, which the translator fills as it scopes the `labels(n)` and `properties` sub-SELECTs:
the `USE GRAPH` literal, or in an all-graphs read the node row's own `graph_id`. NULL (a node the translation cannot
tie to a row, as after a WITH in an all-graphs read) reads every graph, as before.

The answers on a live graph are in `tests/e2e/test_460_properties_map_graph_e2e.py`.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _sql(query):
    sql = translate_to_sql(parse_query(query), {}).sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def test_use_graph_passes_the_graph():
    sql = _sql("USE GRAPH 'g1' MATCH (n:L) RETURN properties(n) AS p")
    assert "CY_PROPS_MAP(n0.node_id, 'rdf_props', 'g1')" in sql.replace("Graph_KG.", ""), sql


def test_the_default_graph_is_passed_as_the_empty_string():
    sql = _sql("USE GRAPH '' MATCH (n:L) RETURN properties(n) AS p")
    assert "CY_PROPS_MAP(n0.node_id, 'rdf_props', '')" in sql.replace("Graph_KG.", ""), sql


def test_an_all_graphs_read_passes_each_rows_graph():
    sql = _sql("MATCH (n:L) RETURN n.name AS nm, properties(n) AS p")
    assert "CY_PROPS_MAP(n0.node_id, 'rdf_props', n0.graph_id)" in sql.replace("Graph_KG.", ""), sql


def test_the_function_filters_on_its_graph_argument():
    ddl = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if ".CY_PROPS_MAP(" in s)
    assert "CY_PROPS_MAP(nid VARCHAR(256), tbl VARCHAR(256), gid VARCHAR(256))" in ddl, ddl
    assert "graph_id" in ddl, ddl
