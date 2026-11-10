"""4.6.0 R308 (cmptypes), offline: collect() of an UNWIND variable over a list known before the query runs lists
each element as its type.

Before, `UNWIND [false, true] AS e RETURN collect(e)` answered [0, 1] (IRIS binds a boolean as 1 or 0), and
`UNWIND [1, 'b', [1], {k: 1}] AS e RETURN collect(e)` listed the list and the map as strings ("[1]"): JSON_TABLE's
VARCHAR column holds a nested list's text, a number as an IRIS number and a boolean as '1' or '0'. The elements of a
literal or parameter list are known, so each row's text says which it is, unless a string element could be read as
another kind (the string '[2]' beside the list [2], '1' beside a boolean): then the column reads as before (docs/KNOWN_ISSUES.md), as
does a list built while the query runs. Live: tests/e2e/test_460_r308_crosstype_e2e.py (the collected form).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def sql_of(q, params=None):
    s = translate_to_sql(parse_query(q), params or {})
    return s.sql if isinstance(s.sql, str) else "\n".join(s.sql)


def test_booleans_are_listed_as_booleans():
    sql = sql_of("UNWIND [false, true] AS e RETURN collect(e) AS l")
    assert "THEN 'true'" in sql and "THEN 'false'" in sql and "JSON_ARRAYAGG" not in sql, sql


def test_a_nested_list_or_map_is_listed_as_itself():
    sql = sql_of("UNWIND [1, 'b', [1], {k: 1}, 2.5] AS e RETURN collect(e) AS l")
    assert "IN ('[', '{')" in sql and "CY_JSON_NUM(u0.e)" in sql and "JSON_ARRAYAGG" not in sql, sql


def test_booleans_beside_a_list_are_listed_as_booleans():
    sql = sql_of("UNWIND [true, [1]] AS e RETURN collect(e) AS l")
    assert "u0.e = '1' THEN 'true'" in sql, sql


@pytest.mark.parametrize("q", [
    "UNWIND [[2], '[2]'] AS e RETURN collect(e) AS l",
    "UNWIND [true, '1', [1]] AS e RETURN collect(e) AS l",
])
def test_a_string_that_reads_as_another_kind_is_told_apart_by_its_json(q):
    """4.7.0 row 22: such a literal (one holding a list or map) goes through CY_LIST_KINDS, whose JSON text tells the
    string '[2]' from the list [2] and '1' from true; before, the column read as it was (the string as the list)."""
    sql = sql_of(q)
    assert "Graph_KG.CY_LIST_KINDS(" in sql and "e__j" in sql, sql


def test_one_kind_of_scalar_is_unchanged():
    assert "JSON_ARRAYAGG(u0.e)" in sql_of("UNWIND [1, 2] AS e RETURN collect(e) AS l")


@pytest.mark.parametrize("q", [
    "UNWIND [1, ['a'], false] AS v RETURN v",
    "UNWIND ['[1]', ['a'], 1] AS v RETURN v",
    "UNWIND ['text', '7', '[1]', 1.5, ['list'], false] AS v RETURN v",
])
def test_a_returned_row_of_a_known_mixed_list_is_marked_json(q):
    """fixes64's cases (measured on release d7e6c340 and 4.5.x: false came back '0', the string '[1]' as [1])."""
    sql = sql_of(q)
    assert "CHAR(1) || 'J' ||" in sql, sql


def test_a_string_spelled_as_a_list_is_told_by_its_text():
    sql = sql_of("UNWIND ['[1]', ['a'], 1] AS v RETURN collect(v) AS l")
    assert "u0.v IN ('[1]') THEN" in sql, sql


def test_a_sort_key_reads_the_unmarked_row():
    """TCK ReturnOrderBy1 [9]/[10] after the merge with 460-fixes67: the ORDER BY's CY_SORT_KEY read the RETURN
    column's J-marked text, so the lists sorted as strings ([] came after ['a', 1])."""
    sql = sql_of("UNWIND [[], ['a'], ['a', 1], [1], [1, 'a'], [1, null], [null, 1], [null, 2]] AS lists "
                 "RETURN lists ORDER BY lists")
    order_by = sql[sql.rindex("ORDER BY"):]
    assert "CY_SORT_KEY(u0.lists, 'x')" in order_by and "CHAR(1)" not in order_by, order_by


@pytest.mark.parametrize("q", [
    "UNWIND [1, null] AS x RETURN x ORDER BY CASE WHEN x IS NULL THEN 0 ELSE x END",
    "UNWIND [[1], 'a', null] AS x RETURN x ORDER BY CASE WHEN x IS NULL THEN NULL ELSE 1 END",
])
def test_a_user_case_keeps_its_own_sort_key(q):
    """fixes64's check: only the RETURN column's own known-row form is unwrapped, never a user's CASE."""
    from iris_vector_graph.cypher.translator import _unwrap_typed

    sql = "CASE WHEN u0.x IS NULL THEN 0 ELSE u0.x END"
    assert _unwrap_typed(sql) == sql
    order_by = sql_of(q)
    order_by = order_by[order_by.rindex("ORDER BY"):]
    assert "CASE WHEN" in order_by, order_by
