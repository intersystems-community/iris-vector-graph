"""4.6.0 R308 (cmptypes), offline: a comparison of two values of different kinds answers as openCypher does.

Before, `WITH 1 AS x, 'b' AS y RETURN x < y` answered true (SQL compared the text), `WITH 1 AS x, true AS y RETURN
x = y` true (a boolean is 1 in a stage), `[1, null][0] < [1, null][1]` false, `[1] < [1, 2]` false and `1 < {k: 2}`
true. openCypher: values of two kinds are not equal and do not order (null), a null compares null, lists compare
element by element, a map orders with nothing.

A comparison whose two operands' values are known before the query runs is answered then; one whose operands' kinds
are known and differ is too (a WITH of a literal or parameter is never null); anything else that reads a list, a map,
a list element or values of two kinds compares at run time through CY_JCMP over the operands' typed JSON. Two values
of one scalar kind (numbers, strings, booleans) and stored properties keep the plain comparison. Live:
tests/e2e/test_460_r308_crosstype_e2e.py (the cross-type grid, every read form, every route).
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
    """The statement's last top-level SELECT line (the RETURN)."""
    return [line for line in sql.splitlines() if line.startswith("SELECT ")][-1]


@pytest.mark.parametrize("q, params", [
    ("WITH 1 AS x, 'b' AS y RETURN x < y AS c", {}),
    ("WITH 1 AS x, true AS y RETURN x <= y AS c", {}),
    ("WITH [1] AS x, 2 AS y RETURN x > y AS c", {}),
    ("WITH {k: 1} AS x, {k: 2} AS y RETURN x >= y AS c", {}),
    ("RETURN $x < $y AS c", {"x": 1, "y": "b"}),
    ("WITH [1, null] AS l RETURN l[0] < l[1] AS c", {}),
    ("RETURN 1 < {k: 2} AS c", {}),
])
def test_an_ordering_that_cannot_hold_is_null_before_the_query_runs(q, params):
    sel = final_select(sql_of(q, params))
    assert re.search(r"\bNULL\b", sel) and "CY_JCMP" not in sel, sel


@pytest.mark.parametrize("q, params, value", [
    ("WITH 1 AS x, true AS y RETURN x = y AS c", {}, "0"),
    ("WITH 1 AS x, 'b' AS y RETURN x <> y AS c", {}, "1"),
    ("RETURN $x = $y AS c", {"x": False, "y": 0}, "0"),
    ("WITH [1, 'a'] AS l RETURN l[0] = l[1] AS c", {}, "0"),
    ("RETURN {k: 1} = [1] AS c", {}, "0"),
    ("WITH [[1], [1, 2]] AS l RETURN l[0] < l[1] AS c", {}, "1"),
    ("WITH [1] AS x, [1, 2] AS y RETURN x < y AS c", {}, "1"),
])
def test_an_answer_known_before_the_query_runs_is_a_constant(q, params, value):
    sel = final_select(sql_of(q, params))
    assert "CY_JCMP" not in sel and "Stage1.x" not in sel and "JSON_ARRAYGET" not in sel, sel
    assert re.search(r"(\(1=%s\)|\b%s AS c)" % (value, value), sel), sel


@pytest.mark.parametrize("q", [
    "UNWIND [{k: 1}, {k: 2}] AS x UNWIND [{k: 1}] AS y RETURN x = y AS c",
    "UNWIND [1, 'b'] AS e WITH collect(e) AS l RETURN l[0] < l[1] AS c",
    "UNWIND [[1]] AS x UNWIND [[1, 2]] AS y RETURN x < y AS c",
    "UNWIND [1] AS x UNWIND ['b'] AS y RETURN x = y AS c",
])
def test_a_list_a_map_or_two_kinds_compare_at_run_time(q):
    sel = final_select(sql_of(q))
    assert "CY_JCMP(" in sel, sel


@pytest.mark.parametrize("q", [
    "WITH 1 AS x, 2.5 AS y RETURN x < y AS c",
    "WITH 'a' AS x, 'b' AS y RETURN x < y AS c",
    "WITH false AS x, true AS y RETURN x < y AS c",
    "MATCH (n) WHERE n.age > 3 RETURN n",
    "MATCH (n) WHERE n.age > $a RETURN n",
    "MATCH (a), (b) WHERE a.v < b.v RETURN a",
    "UNWIND [1, 2] AS x WITH x WHERE x > 1 RETURN x",
])
def test_one_kind_and_stored_values_keep_the_plain_comparison(q):
    assert "CY_JCMP" not in sql_of(q, {"a": 3})


def test_the_function_is_deployed():
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list("Graph_KG"))
    assert "FUNCTION Graph_KG.CY_JCMP(" in ddl


@pytest.mark.parametrize("idx", ["true", "1.5", "'a'", "[1]", "{k: 1}"])
def test_a_non_integer_index_still_raises(idx):
    """TCK List1 [8]: the typed read of a literal list's element by a row's index left a known non-integer index
    unchecked."""
    q = f"WITH [1, 2, 3] AS list, {idx} AS idx RETURN list[idx] AS v"
    try:
        sql = sql_of(q)
    except (TypeError, SyntaxError):
        return
    assert "IVGTYPEERROR" in sql, sql
