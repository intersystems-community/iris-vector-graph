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
def test_a_string_that_reads_as_another_kind_leaves_the_column_as_it_was(q):
    assert "JSON_ARRAYAGG" in sql_of(q)


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
