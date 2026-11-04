"""4.6.0 R227: an element of a list that comes from a row keeps its type (offline: the SQL and the decoding).

R193 kept the element's type for a constant list (`head([1, 2, 3])` folds to 1). A list from a row (a WITH column, a
collect()) was read by `JSON_ARRAYGET`, which answers the element's IRIS value as text: `WITH [1,'a',2.5,true] AS x
RETURN head(x), x[0], last(x)` answered '1', '1', '1' (true is 1 to IRIS). A RETURN column that is `head(L)`, `last(L)`
or `L[<integer>]` now reads `CY_LIST_ELEM(L, i)`, which answers a scalar element as its JSON text behind a mark, and
the engine decodes it with `json.loads` (prop_values.decode_typed_text, kind "J"). The answers live, under both driver
versions, are in tests/e2e/test_460_r227_row_list_types_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.prop_values import decode_typed_text
from iris_vector_graph.schema import GraphSchema


def _q(query: str):
    return translate_to_sql(parse_query(query), {})


@pytest.mark.parametrize("text,value", [
    ("\x01J1", 1),
    ('\x01J"a"', "a"),
    ("\x01J2.5", 2.5),
    ("\x01Jtrue", True),
    ("\x01Jfalse", False),
    ('\x01J"1"', "1"),
    ('\x01J"\\u0001x"', "\x01x"),
])
def test_a_marked_element_decodes_as_its_json_type(text, value):
    got = decode_typed_text(text)
    assert got == value and type(got) is type(value)


def test_an_unmarked_value_is_left_as_it_is():
    assert decode_typed_text('{"a":1}') == '{"a":1}'
    assert decode_typed_text("[1,2]") == "[1,2]"


def test_head_index_and_last_of_a_row_list_read_the_typed_element():
    r = _q("WITH [1,'a',2.5,true] AS x RETURN head(x), x[0], last(x), x[-2]")
    final = r.sql.rsplit("\n)\n", 1)[-1]
    elems = re.findall(r"CY_LIST_ELEM\(Stage1\.x, (-?\d+)\) AS (\w+)", final)
    assert [i for i, _ in elems] == ["0", "0", "-1", "-2"], final
    assert {a for _, a in elems} <= set(r.typed_value_columns), (r.typed_value_columns, final)
    assert "JSON_ARRAYGET" not in final, final


def test_a_collected_list_s_elements_read_typed():
    r = _q("UNWIND [1, 'a'] AS v WITH collect(v) AS vs RETURN head(vs), vs[1]")
    final = r.sql.rsplit("\n)\n", 1)[-1]
    assert len(re.findall(r"CY_LIST_ELEM\(Stage1\.vs, -?\d+\)", final)) == 2, final


def test_a_constant_list_still_folds():
    r = _q("RETURN head([1, 'a']), [1, 'a'][1], last([1, true])")
    assert "CY_LIST_ELEM" not in r.sql and "JSON_ARRAYGET" not in r.sql, r.sql


def test_an_element_inside_an_expression_keeps_its_reading():
    # only a RETURN column that is the element itself reads typed; a filter or an operand compares IRIS values
    r = _q("WITH [1, 2] AS x WHERE x[0] = 1 RETURN x[1] + 1 AS y")
    assert "CY_LIST_ELEM" not in r.sql, r.sql


def test_setup_creates_cy_list_elem():
    statements = [s for s in GraphSchema.get_procedures_sql_list() if "CY_LIST_ELEM(" in s]
    assert len(statements) == 1, statements
    body = statements[0]
    # the DBAPI DDL tokenizer breaks on these inside a LANGUAGE OBJECTSCRIPT body (iris_ddl_objectscript_apostrophe)
    assert "'" not in body and "?" not in body, body
