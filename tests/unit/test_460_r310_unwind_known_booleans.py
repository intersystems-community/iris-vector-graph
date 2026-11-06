"""4.6.0 R310 follow-up (fixes66): an UNWIND of a list whose elements are known before the query runs (a parameter,
or a WITH bound to a list literal) reads them from those values when one is a boolean or ''.

JSON_TABLE hands a JSON boolean on as the text 1 / 0 (not told from the string '1': measured over INTEGER, NUMERIC,
DOUBLE, VARBINARY columns and the SQL/JSON path filters, which IRIS's JSON_TABLE does not support) and '' as null, so
`UNWIND $l AS b RETURN b` with l = [true, false, ''] answered '1', '0', null, and so did `WITH [true, false, ''] AS l
UNWIND l AS b`. A literal UNWIND (`UNWIND [true, ''] AS b`) answered true and '': it reads its values as UNION ALL
rows, with a flag beside a boolean among other values (R273). These lists now take that route, up to 900 elements
(IRIS refuses 1000 literals in a statement). Pinned live in tests/e2e/test_460_r310_unwind_known_booleans_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params).sql


def test_a_parameter_list_holding_a_boolean_is_read_from_its_values():
    sql = _sql("UNWIND $l AS b RETURN b", {"l": [True, False, ""]})
    assert "JSON_TABLE" not in sql and "UNION ALL" in sql, sql


def test_a_with_bound_literal_list_holding_a_boolean_is_read_from_its_values():
    sql = _sql("WITH [true, false, ''] AS l UNWIND l AS b RETURN b")
    assert "JSON_TABLE" not in sql and "UNION ALL" in sql, sql


def test_a_parameter_list_holding_an_empty_string_among_numbers_is_read_from_its_values():
    sql = _sql("WITH $l AS l UNWIND l AS b RETURN b", {"l": [1, "", 2]})
    assert "JSON_TABLE" not in sql, sql


def test_a_list_of_numbers_keeps_json_table():
    assert "JSON_TABLE" in _sql("UNWIND $l AS b RETURN b", {"l": [1, 2, 3]})


def test_a_long_list_keeps_json_table():
    assert "JSON_TABLE" in _sql("UNWIND $l AS b RETURN b", {"l": [True] + list(range(950))})
