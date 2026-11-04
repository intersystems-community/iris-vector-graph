"""4.6.0 R255: a literal list (or a variable bound to one) whose only element is a list is iterated from that
element's JSON text, not by JSON_TABLE, which iterates the inner list (fixes29). The answers are pinned live in
tests/e2e/test_460_r255_single_nested_list_e2e.py, on 2026.1 and 2026.3 with the native planner on and off."""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(cypher: str):
    return translate_to_sql(parse_query(cypher))


def test_unwind_of_one_list_is_one_row_of_its_text():
    q = _q("UNWIND [[1, 2, 3]] AS list RETURN list")
    assert "JSON_TABLE(" not in q.sql, q.sql
    assert "[1,2,3]" in q.parameters[0], q.parameters


def test_unwind_of_a_variable_bound_to_one_list():
    q = _q("WITH [[1, 2, 3]] AS ll UNWIND ll AS list RETURN size(list) AS s")
    assert "COLUMNS (list" not in q.sql, q.sql


def test_one_list_of_maps_is_its_json_text():
    q = _q("UNWIND [[{name: 'a'}]] AS l RETURN size(l) AS s")
    assert '[{"name":"a"}]' in q.parameters[0], q.parameters


def test_a_comprehension_over_one_list_reads_one_element():
    sql = _q("RETURN [x IN [[1, 2]] | size(x)] AS v").sql
    assert "'[1,2]' AS x" in sql, sql


@pytest.mark.parametrize(
    "cypher, form",
    [("UNWIND [[1, 2], [3]] AS l RETURN l", "JSON_TABLE("), ("UNWIND [1, 2] AS l RETURN l", " UNION ALL ")],
)
def test_other_lists_are_unchanged(cypher, form):
    assert form in _q(cypher).sql
