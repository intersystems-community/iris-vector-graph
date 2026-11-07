"""4.6.0 R308 (cmptypes), offline: a math function of a value that is no number, and size() of one that is no list
or string, raise TypeError as openCypher does.

Before, abs('a'), sqrt(true), log([1]) and every other math function answered 0 for a string, boolean, list or map
(IRIS reads one as 0), in every read form: a literal, a WITH column, a parameter, a list element, an UNWIND variable,
a collected element and a stored property. size() of a stored number answered its text's length (1; a stored false
5), of a map 1, 7 or 8, of a list element its JSON text's length.

The check is before the query runs where the value or its kind is known then (a literal, a parameter, a WITH of one;
an element of a literal list); at run time, through CypherFn_IVGTYPEERROR as R297's arithmetic check raises, for a
value read from a row (an UNWIND variable of known kind, a list element, a stored property). A null stays null and a
number is answered as before. Live: tests/e2e/test_460_r308_crosstype_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.oracle.crosstype import MATH


def sql_of(q, params=None):
    s = translate_to_sql(parse_query(q), params or {})
    return s.sql if isinstance(s.sql, str) else "\n".join(s.sql)


@pytest.mark.parametrize("fn", MATH)
@pytest.mark.parametrize("q, params", [
    ("RETURN {fn}('a') AS c", {}),
    ("RETURN {fn}(true) AS c", {}),
    ("RETURN {fn}([1]) AS c", {}),
    ("RETURN {fn}({{k: 1}}) AS c", {}),
    ("WITH 'a' AS x RETURN {fn}(x) AS c", {}),
    ("WITH false AS x RETURN {fn}(x) AS c", {}),
    ("WITH {{k: 1}} AS x RETURN {fn}(x) AS c", {}),
    ("RETURN {fn}($x) AS c", {"x": "a"}),
    ("RETURN {fn}($x) AS c", {"x": [1]}),
    ("WITH ['a'] AS l RETURN {fn}(l[0]) AS c", {}),
])
def test_a_value_known_to_be_no_number_raises_before_the_query_runs(fn, q, params):
    with pytest.raises(TypeError, match="TypeError"):
        sql_of(q.format(fn=fn), params)


@pytest.mark.parametrize("fn", MATH)
@pytest.mark.parametrize("q", [
    "RETURN {fn}(null) AS c",
    "RETURN {fn}(0.5) AS c",
    "RETURN {fn}(1) AS c",
    "WITH 0.5 AS x RETURN {fn}(x) AS c",
    "WITH [null] AS l RETURN {fn}(l[0]) AS c",
    "RETURN {fn}(size([1])) AS c",
])
def test_a_number_or_null_needs_no_check(fn, q):
    assert "IVGTYPEERROR" not in sql_of(q.format(fn=fn), {})


@pytest.mark.parametrize("fn", MATH)
@pytest.mark.parametrize("q", [
    "MATCH (n) RETURN {fn}(n.v) AS c",
    "UNWIND ['a', 'b'] AS x RETURN {fn}(x) AS c",
    "UNWIND [1, 2] AS e WITH collect(e) AS l RETURN {fn}(l[0]) AS c",
])
def test_a_value_read_from_a_row_is_checked_at_run_time(fn, q):
    assert "CypherFn_IVGTYPEERROR" in sql_of(q.format(fn=fn), {})


def test_the_number_answer_is_unchanged():
    assert sql_of("RETURN sqrt(4) AS c") == "SELECT SQRT(4) AS c"


@pytest.mark.parametrize("q, params", [
    ("RETURN size({k: 1}) AS c", {}),
    ("WITH {k: 1} AS x RETURN size(x) AS c", {}),
    ("WITH false AS x RETURN size(x) AS c", {}),
    ("WITH 1.5 AS x RETURN size(x) AS c", {}),
    ("RETURN size($x) AS c", {"x": {"k": 1}}),
    ("WITH [1] AS l RETURN size(l[0]) AS c", {}),
    ("WITH [{k: 1}] AS l RETURN size(l[0]) AS c", {}),
])
def test_size_of_a_value_known_to_be_no_list_or_string_raises(q, params):
    with pytest.raises(TypeError, match="TypeError"):
        sql_of(q, params)


@pytest.mark.parametrize("q", [
    "MATCH (n) RETURN size(n.v) AS c",
    "UNWIND [{k: 1}, {k: 2}] AS x RETURN size(x) AS c",
    "UNWIND [false] AS e WITH collect(e) AS l RETURN size(l[0]) AS c",
])
def test_size_of_a_value_read_from_a_row_is_checked_at_run_time(q):
    assert "CypherFn_IVGTYPEERROR" in sql_of(q)


@pytest.mark.parametrize("q", [
    "RETURN size('abc') AS c",
    "WITH [1, 2] AS l RETURN size(l) AS c",
    "UNWIND ['a', 'bc'] AS x RETURN size(x) AS c",
])
def test_size_of_a_list_or_string_is_not_checked(q):
    assert "IVGTYPEERROR" not in sql_of(q)


def test_size_of_a_list_element_that_is_a_list_counts_its_elements():
    sql = sql_of("UNWIND [[1], [1, 2]] AS e WITH collect(e) AS l RETURN size(l[0]) AS c")
    assert "JSON_ARRAYLENGTH" in sql and "CY_LIST_ELEM" in sql, sql
