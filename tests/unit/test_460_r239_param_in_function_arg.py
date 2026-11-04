"""4.6.0 R239: a parameter inside a function's argument keeps its binding.

`MATCH (n) RETURN toString(n.f * $k)` failed "Incorrect number of parameters": a function's arguments are translated
inline, and a parameter there was bound as a WHERE parameter, which the WHERE's own parameters (one slice per
condition) then dropped. `coalesce(n.f, $k)`, `substring(n.name, $d)` and `round(n.f * $k)` in a RETURN lost theirs
the same way. It binds where the call sits now: the SELECT list in a RETURN, the WHERE in a WHERE.

These pin each statement's markers against its parameters; tests/e2e/test_460_r239_param_in_function_arg_e2e.py
checks the answers.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

PARAMS = {"k": 2, "d": 1, "nm": "Alpha", "lo": 1.5}


def _final(q):
    r = translate_to_sql(parse_query(q), dict(PARAMS))
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return sql[-1], r.parameters[-1]


@pytest.mark.parametrize(
    "q, bound",
    [
        ("MATCH (n:L) RETURN toString(n.f * $k)", [2]),
        ("MATCH (n:L) RETURN toString(coalesce(n.f, $k))", [2]),
        ("MATCH (n:L) RETURN toString(substring(n.name, $d))", [1]),
        ("MATCH (n:L) RETURN toString(round(n.f * $k))", [2]),
        ("MATCH (n:L) RETURN coalesce(n.f, $k), substring(n.name, $d), round(n.f * $k)", [2, 1, 2]),
        ("MATCH (n:L) RETURN substring(n.name, $d), toString(n.f * $k)", [1, 2]),
    ],
)
def test_a_parameter_in_a_return_function_binds_in_the_select_list(q, bound):
    sql, params = _final(q)
    assert sql.count("?") == len(params), (sql, params)
    # the select list's markers come first, in the order they are written
    select = sql.split("\nFROM ", 1)[0]
    assert params[: select.count("?")] == bound, (sql, params)


def test_a_where_parameter_keeps_its_place_after_the_select_ones():
    sql, params = _final("MATCH (n:L) WHERE n.name = $nm RETURN toString(n.f * $k)")
    assert sql.count("?") == len(params), (sql, params)
    assert params[0] == 2 and params[-1] == "Alpha", (sql, params)


def test_a_parameter_in_a_where_function_binds_in_the_where():
    sql, params = _final("MATCH (n:L) WHERE coalesce(n.f, $k) > $lo RETURN n.name")
    assert sql.count("?") == len(params), (sql, params)
    where = sql.split("\nWHERE ", 1)[1]
    assert params[-where.count("?") :][-2:] == [2, 1.5], (sql, params)


@pytest.mark.parametrize(
    "q",
    ["MATCH (n:L) RETURN percentileDisc(n.f, $lo) AS p", "MATCH (n:L) RETURN percentileCont(n.f, $lo) AS p"],
)
def test_a_call_that_inlines_its_parameter_binds_none(q):
    """percentile*() spells the parameter's value in its SQL: a bound copy of it would take the label's place
    (TCK Aggregation6 [1]-[4] lost it on the first cut of R239)."""
    sql, params = _final(q)
    assert sql.count("?") == len(params) and 1.5 not in params, (sql, params)
