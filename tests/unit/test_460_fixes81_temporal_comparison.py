"""4.6.0 fixes81 (walkthrough round 3, WRONG): comparisons of temporal values answer as openCypher does.

Before, `duration('P1D') < duration('P2D')` answered true and `duration('PT1H') <= duration('P1D')` false: SQL compared
the durations' text. openCypher (and docs/migration/v4.6.0.md): a duration orders with nothing, so `<`, `<=`, `>`, `>=`
with a duration on either side are null; `=` and `<>` compare its components (months, days, seconds), so
`duration('PT1H') = duration('PT60M')` is true and `duration('P1D') = duration('PT24H')` false. Values of two temporal
types (or a temporal and anything else) are not equal and do not order. Live: tests/e2e/test_460_r308_crosstype_e2e.py
(the temporal kinds of the comparison family) and the TCK's comparison and return-orderby areas.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def sql_of(q, params=None):
    s = translate_to_sql(parse_query(q), params or {})
    return s.sql if isinstance(s.sql, str) else "\n".join(s.sql)


def final_select(sql):
    return [line for line in sql.splitlines() if line.startswith("SELECT ")][-1]


@pytest.mark.parametrize("q, params", [
    ("RETURN duration('P1D') < duration('P2D') AS c", {}),
    ("RETURN duration('PT1H') <= duration('P1D') AS c", {}),
    ("RETURN duration('P1D') >= duration('P1D') AS c", {}),
    ("WITH duration('P1D') AS x, duration('P2D') AS y RETURN x < y AS c", {}),
    ("RETURN duration($x) > duration($y) AS c", {"x": "P1D", "y": "P2D"}),
    ("MATCH (n) RETURN n.d < duration('P1D') AS c", {}),
    ("RETURN date('2020-01-01') < datetime('2020-01-02T00:00Z') AS c", {}),
    ("RETURN duration('P1D') < 1 AS c", {}),
])
def test_an_ordering_with_a_duration_or_across_temporal_types_is_null(q, params):
    sel = final_select(sql_of(q, params))
    assert re.search(r"\bNULL AS c\b", sel), sel


@pytest.mark.parametrize("q, value", [
    ("RETURN duration('PT1H') = duration('PT60M') AS c", "1"),
    ("RETURN duration('P1D') = duration('PT24H') AS c", "0"),
    ("RETURN duration('P1D') <> duration('P2D') AS c", "1"),
    ("RETURN date('2020-01-01') = localdatetime('2020-01-01T00:00') AS c", "0"),
    ("RETURN duration('P1D') = 'P1D' AS c", "0"),
    ("RETURN date('2020-01-01') < date('2020-01-02') AS c", "1"),
])
def test_two_values_known_before_the_query_runs_answer_then(q, value):
    sel = final_select(sql_of(q))
    assert re.search(r"\b%s AS c\b" % value, sel), sel


@pytest.mark.parametrize("q", [
    "WITH duration('P1D') AS x, duration('PT24H') AS y RETURN x = y AS c",
    "MATCH (n) RETURN n.d = duration('P1D') AS c",
    "UNWIND [duration('P1D'), duration('P2D')] AS x RETURN x = duration('P1D') AS c",
])
def test_a_duration_equality_from_a_row_compares_components(q):
    assert "CY_JCMP(" in sql_of(q)
