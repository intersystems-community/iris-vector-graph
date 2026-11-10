"""4.6.0 R139: an equality between a relationship property and a number accepts every stored spelling of the number.

`rdf_edges.qualifiers` holds a number written by the API as a JSON number, which IVG's `JSON_VALUE` reads back in
ObjectScript's form (`.95`), and one written by a Cypher CREATE as the text `0.95`. A WITH column reads the property
through `CY_REL_PROP`, which spells the API's number `0.95`. R121 compared an inline `{c: 0.95}` with each spelling;
`WHERE r.c = $v`, `WHERE r.c = 0.95` and a filter on `WITH r.c AS c` compared the text with one bound or cast number,
which IRIS compares as text, so `.95` (or `0.95`) missed.

The live answers are in `tests/e2e/test_460_rel_number_filters_e2e.py`; this pins the SQL for each shape.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.prop_values import number_spellings

SPELLED = "IN ('.95', '0.95')"


def _tr(query, params=None):
    return translate_to_sql(parse_query(query), dict(params or {}, a="a"))


def _sql(query, params=None):
    return _tr(query, params).sql


def _params(query, params=None):
    return [p for group in _tr(query, params).parameters for p in group]


def test_a_parameter_fraction_compares_every_spelling():
    q = "MATCH (a {node_id: $a})-[r:R]->(x) WHERE r.c = $v RETURN x.node_id"
    sql = _sql(q, {"v": 0.95})
    assert SPELLED in sql, sql
    assert 0.95 not in _params(q, {"v": 0.95})  # not bound: a bound double compares as text


def test_a_literal_fraction_compares_every_spelling():
    assert SPELLED in _sql("MATCH (a {node_id: $a})-[r:R]->(x) WHERE r.c = 0.95 RETURN x.node_id")


def test_the_number_on_the_left_too():
    assert SPELLED in _sql("MATCH (a {node_id: $a})-[r:R]->(x) WHERE $v = r.c RETURN x.node_id", {"v": 0.95})


def test_not_equal_is_not_in():
    sql = _sql("MATCH (a {node_id: $a})-[r:R]->(x) WHERE r.c <> $v RETURN x.node_id", {"v": 0.95})
    assert "NOT IN ('.95', '0.95')" in sql, sql


def test_a_whole_number_also_matches_its_float_spelling():
    # a Cypher CREATE of {c: 5.0} stores '5.0'; 5 = 5.0 in Cypher
    sql = _sql("MATCH (a {node_id: $a})-[r:R]->(x) WHERE r.c = 5 RETURN x.node_id")
    assert "IN ('5', '5.0')" in sql, sql


def test_a_with_column_filter_pushed_into_the_match():
    sql = _sql("MATCH (a {node_id: $a})-[r:R]->(x) WITH r.c AS c, x WHERE c = $v RETURN x.node_id", {"v": 0.95})
    assert SPELLED in sql, sql


def test_a_with_column_filter_in_a_later_stage():
    sql = _sql("MATCH (a {node_id: $a})-[r:R]->(x) WITH r.c AS c, x WITH c, x WHERE c = 0.95 RETURN x.node_id")
    assert f"Stage1.c {SPELLED}" in sql, sql


def test_a_with_column_filter_after_where_in_the_return_part():
    sql = _sql("MATCH (a {node_id: $a})-[r:R]->(x) WITH r.c AS c, x MATCH (x) WHERE c = $v RETURN x.node_id",
               {"v": 0.95})
    assert SPELLED in sql, sql


def test_a_string_filter_is_unchanged():
    q = "MATCH (a {node_id: $a})-[r:R]->(x) WHERE r.c = $v RETURN x.node_id"
    assert "IN (" not in _sql(q, {"v": ".95"}) and ".95" in _params(q, {"v": ".95"})


def test_a_node_property_filter_compares_the_same_spellings():
    # R168 gave node properties the same comparison (tests/unit/test_460_node_number_filters.py)
    sql = _sql("MATCH (n {node_id: $a}) WHERE n.c = $v RETURN n.node_id", {"v": 0.95})
    assert f"p1.val {SPELLED}" in sql, sql


def test_spellings_of_a_whole_number_float():
    assert number_spellings(5.0) == ["5", "5.0"]


def test_properties_of_a_node_retype_no_text():
    """`properties(n)` (CY_PROPS_MAP): `$IsValidNum` alone turned the string ".95" into 0.95; from 4.6.0 R121 text
    spelled as JSON spells a number still became one, so the string '30' was 30 (4.7.0 row 10). Now every value keeps
    the type it was stored with, as `n.p` reads it."""
    from iris_vector_graph.schema import GraphSchema

    ddl = next(s for s in GraphSchema.get_procedures_sql_list() if "CY_PROPS_MAP(nid" in s)
    assert "$Match(v" not in ddl
    assert "ElseIf $IsValidNum(v) {" not in ddl
