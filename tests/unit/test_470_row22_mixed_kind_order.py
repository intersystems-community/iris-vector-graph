"""4.7.0 row 22, pinned against the translation (no container): an ordering comparison of an UNWIND row of a mixed-kind
list literal with a value of a known kind is null on a row of another kind.

`UNWIND [date('2020-01-01'), 'b'] AS e RETURN e < 'c'` answered true for the date (its ISO text ordered as a string),
and `UNWIND [1, 'b'] AS e RETURN e < 'c'` true for 1; openCypher answers null for values of two kinds. The answers are
proven live in tests/e2e/test_470_unwound_values_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q: str) -> str:
    s = translate_to_sql(parse_query(q), {}).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("q, tag", [
    ("UNWIND [1, 'b'] AS e RETURN e < 'c' AS r", "s"),
    ("UNWIND [1, 'b', null] AS e RETURN e < 2 AS r", "n"),
    ("UNWIND [date('2020-01-01'), 'b'] AS e RETURN e < 'c' AS r", "s"),
    ("UNWIND [date('2020-01-01'), 'b'] AS e RETURN e >= date('2020-01-15') AS r", "date"),
])
def test_an_ordering_comparison_reads_the_rows_kind(q, tag):
    assert f"CASE WHEN u0.e__t <> '{tag}' THEN NULL WHEN" in _sql(q)


def test_the_date_is_a_row_of_its_own_kind():
    sql = _sql("UNWIND [date('2020-01-01'), 'b'] AS e RETURN e < 'c' AS r")
    assert "'date' AS e__t" in sql and "'s' AS e__t" in sql


@pytest.mark.parametrize("q", [
    "UNWIND [1, 2] AS e RETURN e < 3 AS r",
    "UNWIND ['a', 'b'] AS e RETURN e < 'c' AS r",
    "UNWIND [1, null] AS e RETURN e < 3 AS r",
])
def test_a_list_of_one_kind_carries_no_kind(q):
    assert "__t" not in _sql(q)


def test_equality_is_not_guarded():
    """= and <> of two kinds already answer false and true; only ordering is null."""
    assert "e__t <>" not in _sql("UNWIND [1, 'b'] AS e RETURN e = 'b' AS r")


def test_a_run_time_list_reads_the_kind_cy_list_kinds_gives():
    sql = _sql("UNWIND [1, 'b'] AS e WITH collect(e) AS l UNWIND l AS x RETURN x < 'c' AS r")
    assert "CASE WHEN u1.x__k <> 's' THEN NULL WHEN" in sql


def test_a_run_time_list_against_a_temporal_is_not_guarded():
    """A list built at run time holds a date as its text (kind s): a guard would answer null for two dates."""
    sql = _sql("UNWIND [date('2020-01-01'), date('2020-02-01')] AS e WITH collect(e) AS l UNWIND l AS x "
               "RETURN x < date('2020-01-15') AS r")
    assert "x__k <>" not in sql


def test_a_literal_holding_a_nested_list_goes_through_cy_list_kinds():
    sql = _sql("UNWIND ['1', true, [2]] AS e RETURN e")
    assert "Graph_KG.CY_LIST_KINDS(" in sql and "e__j" in sql


def test_a_literal_of_scalars_does_not():
    assert "CY_LIST_KINDS" not in _sql("UNWIND ['1', 2, 'x'] AS e RETURN e")


def test_cy_list_kinds_names_every_kind():
    from iris_vector_graph.schema import GraphSchema

    body = next(s for s in GraphSchema.get_procedures_sql_list("Graph_KG") if "CY_LIST_KINDS(" in s.split("RETURNS")[0])
    assert ('k = $Select(t = "boolean": "b", t = "number": "n", t = "string": "s", t = "array": "l", '
            't = "object": "m", 1: "z")') in body
