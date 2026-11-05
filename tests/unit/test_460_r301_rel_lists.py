"""4.6.0 R301, offline: the SQL a variable-length relationship list, a returned relationship and a list-valued
variable-length property map translate to. The live answers, worked by hand, are in
tests/e2e/test_460_r301_rel_lists_e2e.py.

On the release head (6f9e732f) `rs` of `-[rs*1..2]->` (expanded in SQL, `vlpN.r`: `{"type", "props"}` objects) was
read as a list of anything: `x.w` read `$.w` of each object, `properties(x)` read x as a node id, `size(rs)` was the
JSON text's LENGTH; `RETURN r` spelled the stored qualifiers as they are (`{"w": "7"}`); and `[r*1..2 {l: [1]}]` went
to the engine's labeled route.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

FROM_A = "MATCH (a:V {name: 'a'})-[rs:R*1..2]->(b) "


def _sql(q, params=None):
    s = translate_to_sql(parse_query(q), params or {}).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("q", [FROM_A + "RETURN [x IN rs | x.w]", FROM_A + "UNWIND rs AS x RETURN x.w"])
def test_an_element_s_property_is_read_from_its_props(q):
    sql = _sql(q)
    assert "CY_VLP_PATHS(" in sql and "'$.props'), '$.w')" in sql, sql


def test_an_unwound_relationship_s_properties_are_its_props_typed():
    sql = _sql(FROM_A + "UNWIND rs AS x RETURN properties(x)")
    assert "CY_RETYPE_MAP(Graph_KG.JSON_VALUE(" in sql and "'$.props')" in sql, sql
    assert "CY_PROPS_MAP" not in sql, sql


def test_the_size_of_a_relationship_list_counts_its_elements():
    sql = _sql(FROM_A + "RETURN size(rs)")
    assert "JSON_ARRAYLENGTH(vlp" in sql and "LENGTH(vlp" not in sql.replace("ARRAYLENGTH(vlp", ""), sql


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a)-[r:R]->(b) RETURN r",
        "MATCH (a)-[r:R]->(b) RETURN [r] AS l",
        "MATCH (a)-[r:R]->(b) WITH r RETURN r",
    ],
)
def test_a_returned_relationship_types_its_stored_properties(q):
    sql = _sql(q)
    assert "CY_RETYPE_MAP(" in sql, sql
    assert "COALESCE(e" not in sql or "COALESCE(Graph_KG.CY_RETYPE_MAP(" in sql, sql


def test_a_list_valued_property_map_is_checked_in_sql():
    sql = _sql("MATCH (a:V {name: 'x'})-[r:R*1..2 {l: [1, 'a', true]}]->(b) RETURN a.name, b.name")
    assert "CY_VLP_PATHS(" in sql, sql
    assert "PATH '$.props.l'" in sql and """<> '[1,"a",true]'""" in sql, sql


@pytest.mark.parametrize("value", ["[1.5]", "[[1]]", "['a\"b']"])
def test_a_list_with_more_than_one_spelling_keeps_the_engine_route(value):
    sql = _sql(f"MATCH (a:V {{name: 'x'}})-[r:R*1..2 {{l: {value}}}]->(b) RETURN b.name")
    assert "CY_VLP_PATHS(" not in sql, sql
