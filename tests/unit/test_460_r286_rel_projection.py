"""4.6.0 R286 (the relationship projection items), pinned against the parse and the translation (no container).

The answers are proven live in tests/e2e/test_460_r286_rel_projection_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _ret(q: str):
    return parse_query(q).return_clause.items[0].expression


def _sql(q: str) -> str:
    return translate_to_sql(parse_query(q), {}).sql


def test_a_key_of_properties_of_a_variable_is_its_property():
    assert _ret("MATCH ()-[r]->() RETURN properties(r).s") == ast.PropertyReference(variable="r", property_name="s")
    assert _ret("MATCH (n) RETURN properties(n).id") == ast.PropertyReference(variable="n", property_name="id")


def test_other_calls_keep_their_key_access():
    e = _ret("MATCH (n) RETURN properties({a: 1}).a")
    assert isinstance(e, ast.FunctionCall) and e.function_name == "__prop__"
    e = _ret("MATCH (n) RETURN startNode(r).name")
    assert isinstance(e, ast.FunctionCall) and e.function_name == "__prop__"


def test_a_relationship_s_projection_reads_its_qualifiers():
    sql = _sql("MATCH ()-[r:T]->() RETURN r {.s, .u}")
    assert "node_id" not in sql.split("\nFROM", 1)[0]
    assert "CY_REL_PROP(e" in sql and "'$.s'" in sql and "'$.u'" in sql
    assert "'\"s\":'||" in sql and "'\"u\":'||" in sql


def test_a_relationship_through_with_reads_the_stage_column():
    sql = _sql("MATCH ()-[r:T]->() WITH r RETURN r {.s}")
    tail = sql.rsplit(")\nSELECT", 1)[1]
    assert "(Stage1.r, '$.s')" in tail and "rdf_props" not in tail


def test_a_node_s_projection_is_unchanged():
    assert "rdf_props" in _sql("MATCH (n) RETURN n {.name}")
