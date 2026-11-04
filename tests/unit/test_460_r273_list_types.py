"""4.6.0 R273 (the UNWIND, collect() and list-element cases), pinned against the translation (no container).

The answers are proven live in tests/e2e/test_460_r273_list_types_e2e.py.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _t(q: str, params=None):
    return translate_to_sql(parse_query(q), dict(params or {}))


def test_an_unwind_list_mixing_a_boolean_carries_a_flag_column():
    t = _t("UNWIND [1, true, 'a'] AS v RETURN v")
    assert "SELECT ? AS v, 0 AS v__b UNION ALL SELECT ? AS v, 1 AS v__b UNION ALL SELECT ? AS v, 0 AS v__b" in t.sql
    assert "CASE WHEN u0.v__b = 1 THEN CHAR(1) || 'J' || CASE WHEN u0.v = 1 THEN 'true' ELSE 'false' END" in t.sql
    assert "ELSE Graph_KG.CY_TYPED(u0.v) END AS v" in t.sql
    assert "v" in t.typed_value_columns


def test_a_list_of_one_kind_needs_no_flag():
    for q in ("UNWIND [true, false] AS v RETURN v", "UNWIND [1, 2] AS v RETURN v", "UNWIND [true, null] AS v RETURN v"):
        assert "__b" not in _t(q).sql, q


def test_collect_of_a_flagged_variable_lists_the_boolean():
    sql = _t("UNWIND [1, true] AS v WITH collect(v) AS vs RETURN last(vs)").sql
    assert "LIST(NULLIF(CASE WHEN u0.v__b = 1 THEN CASE WHEN u0.v = 1 THEN 'true' ELSE 'false' END" in sql


def test_the_flag_is_not_read_after_a_with():
    """A later stage has no flag column: the variable reads as it did."""
    sql = _t("UNWIND [1, true] AS v WITH v RETURN v").sql
    assert "v__b" not in sql.rsplit(")\nSELECT", 1)[1]


def test_an_unwind_of_a_scalar_parameter_is_statically_typed():
    t = _t("UNWIND $p AS v RETURN v", {"p": [1, 2]})
    assert "v" in t.int_expr_columns
    t = _t("UNWIND $f AS v RETURN v", {"f": [1.5, 2.0]})
    assert "v" in t.float_expr_columns
    t = _t("UNWIND $b AS v RETURN v", {"b": [True, False]})
    assert "v" in t.bool_expr_columns


def test_an_unwind_of_a_parameter_holding_the_empty_string_is_iterated_from_its_values():
    t = _t("UNWIND $s AS v RETURN v", {"s": ["", "a"]})
    assert "JSON_TABLE" not in t.sql and "UNION ALL" in t.sql
    assert "JSON_TABLE" in _t("UNWIND $s AS v RETURN v", {"s": ["b", "a"]}).sql


def test_collect_of_a_list_valued_column_lists_its_json():
    sql = _t("WITH [1, 2] AS l WITH collect(l) AS c RETURN c").sql
    assert "LIST(NULLIF(Stage1.l, 'null'))" in sql and "JSON_ARRAYAGG" not in sql


def test_an_unwind_of_a_collected_list_of_lists_is_not_flattened():
    sql = _t("WITH [1, 2] AS l WITH collect(l) AS c UNWIND c AS x RETURN x").sql
    assert "JSON_TABLE(Graph_KG.LIST_CONCAT(Stage2.c, '[null]')" in sql
    assert "WHERE u0.x IS NOT NULL" in sql


def test_a_with_column_of_a_row_list_element_reads_cy_list_val():
    t = _t("UNWIND [3, 4] AS v WITH collect(v) AS x WITH x[0] AS y, last(x) AS z RETURN y, z, y * 2")
    assert "Graph_KG.CY_LIST_VAL(Stage1.x, 0) AS y" in t.sql
    assert "Graph_KG.CY_LIST_VAL(Stage1.x, -1) AS z" in t.sql
    assert "CY_TYPED(Stage2.y) AS y" in t.sql
    assert "CY_ARITH(Stage2.y, '*', 2)" in t.sql


def test_a_return_column_of_a_row_list_element_keeps_cy_list_elem():
    sql = _t("UNWIND [3, 4] AS v WITH collect(v) AS x RETURN x[0], head(x)").sql
    assert "CY_LIST_ELEM(Stage1.x, 0)" in sql and "CY_LIST_VAL" not in sql


def test_arithmetic_over_a_row_list_element_is_typed():
    sql = _t("UNWIND [3, 4] AS v WITH collect(v) AS x RETURN x[0] + 1").sql
    assert "CY_ARITH(Graph_KG.JSON_ARRAYGET(Stage1.x, 0), '+', 1)" in sql


def test_size_of_a_parameter_is_its_value_s_size():
    t = _t("RETURN size($q), size($t)", {"q": [1, 2, 3], "t": "abcd"})
    assert "ELSE 3 END" in t.sql and "ELSE 4 END" in t.sql
    assert t.sql.count("?") == 2  # each parameter's marker stays, compared with NULL


def test_cy_list_val_body():
    stmt = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if "FUNCTION Graph_KG.CY_LIST_VAL(" in s)
    body = stmt.split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert "'" not in body and not re.search(r":\w", body)
    assert 'If t = "boolean" { Quit $Select(v: "true", 1: "false") }' in body
    assert 'If (v [ ".") || (v [ "E") { Quit $DOUBLE(v) } Quit +v' in body
    assert 'If v = "" { Quit $Char(0) }' in body
