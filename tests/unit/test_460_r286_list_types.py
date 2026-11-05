"""4.6.0 R286 (the list-value items), pinned against the translation and the routine text (no container).

The answers are proven live in tests/e2e/test_460_r286_list_types_e2e.py.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _t(q: str):
    return translate_to_sql(parse_query(q), {})


def _last_select(sql: str) -> str:
    return sql.rsplit(")\nSELECT", 1)[1]


def test_a_with_carries_an_unwind_boolean_s_flag():
    t = _t("UNWIND [true, 1] AS v WITH v RETURN v")
    assert "u0.v__b AS v__b" in t.sql
    tail = _last_select(t.sql)
    assert "CASE WHEN Stage1.v__b = 1 THEN CHAR(1) || 'J' || CASE WHEN Stage1.v = 1 THEN 'true'" in tail
    assert "v" in t.typed_value_columns


def test_the_flag_follows_a_rename_and_a_second_with():
    sql = _t("UNWIND [true, 1] AS v WITH v AS w WITH w RETURN w").sql
    assert "u0.v__b AS w__b" in sql and "Stage1.w__b AS w__b" in sql
    assert "Stage2.w__b = 1" in _last_select(sql)


def test_a_grouping_with_groups_on_the_flag():
    sql = _t("UNWIND [true, 1, true] AS v WITH v, count(*) AS c RETURN v, c").sql
    assert re.search(r"GROUP BY [^)]*u0\.v__b", sql), sql


def test_a_row_list_element_carries_whether_it_is_a_boolean():
    t = _t("WITH ['true', true] AS l WITH l[0] AS s RETURN s")
    assert "Graph_KG.CY_LIST_VAL(Stage1.l, 0) AS s" in t.sql
    assert (
        "CASE WHEN Graph_KG.CY_LIST_ELEM(Stage1.l, 0) = CHAR(1) || 'Jtrue' OR Graph_KG.CY_LIST_ELEM(Stage1.l, 0) = "
        "CHAR(1) || 'Jfalse' THEN 1 ELSE 0 END AS s__b" in t.sql
    )
    tail = _last_select(t.sql)
    # the boolean is the stored text itself; anything else as CY_TYPED reads it, never as a stored 'true'
    assert "CASE WHEN Stage2.s__b = 1 THEN CHAR(1) || 'J' || Stage2.s ELSE Graph_KG.CY_TYPED(Stage2.s) END" in tail
    assert "s" not in t.prop_text_expr_columns


def test_collect_of_a_flagged_element_lists_the_boolean_or_the_value():
    sql = _t("WITH ['true', true] AS l WITH l[1] AS b RETURN collect(b)").sql
    assert "CASE WHEN Stage2.b__b = 1 THEN Stage2.b ELSE COALESCE(Graph_KG.CY_JSON_NUM(Stage2.b)" in sql


def test_a_value_with_no_flag_reads_as_before():
    # a literal list with no boolean and no string 'true' / 'false' needs no flag
    assert "__b" not in _t("WITH [1, 'a'] AS l WITH l[0] AS x RETURN x").sql
    # a collected list's kinds are not known statically: flagged
    assert "x__b" in _t("UNWIND [1, 2] AS v WITH collect(v) AS l WITH l[0] AS x RETURN x").sql
    sql = _t("UNWIND [1, 2] AS v WITH v RETURN v").sql
    assert "__b" not in sql


def test_cy_list_val_reads_the_point_from_the_json_text():
    stmt = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if "FUNCTION Graph_KG.CY_LIST_VAL(" in s)
    body = stmt.split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert 'Set x = a.%Get(i, , "json")' in body
    assert "'" not in body and not re.search(r":\w", body)
