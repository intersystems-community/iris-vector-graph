"""4.7.0 row 18: a temporal element of a collect()ed list, read at a literal position, compares as the temporal value.

`UNWIND [date('2020-01-01'), 'b'] AS e WITH collect(e) AS l RETURN l[0] < l[1]` answered true: inside the collected
list the date is its ISO text, and CY_JCMP ordered two strings. openCypher orders a date against a string as null, and
against another temporal kind as null too; two dates order by date. Collecting an UNWIND of a list literal, with
nothing else in the WITH and no row before the UNWIND, makes that list less its nulls, in its order, which is known
before the query runs; the translation answers it as it answers `WITH [date('2020-01-01'), 'b'] AS l` (4.6.3). These
are tests/oracle/crosstype.py's 36 `collected` cases (KNOWN_WRONG_461, now empty); the live family is
tests/e2e/test_460_r308_crosstype_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _select_of(cypher) -> str:
    sql = translate_to_sql(parse_query(cypher), {}).sql
    sql = sql if isinstance(sql, str) else sql[-1]
    return sql[sql.rindex("SELECT "):]


@pytest.mark.parametrize("lst, want", [
    ("[date('2020-01-01'), 'b']", "NULL"),
    ("['b', date('2020-01-01')]", "NULL"),
    ("[date('2020-01-01'), datetime('2020-01-02T00:00:00Z')]", "NULL"),
    ("[localdatetime('2020-01-01T00:00:00'), time('12:00:00Z')]", "NULL"),
    ("[time('12:00:00Z'), [1, 2]]", "NULL"),
    ("[{a: 1}, date('2020-01-01')]", "NULL"),
    ("[date('2020-01-01'), date('2020-01-02')]", "1"),
    ("[time('13:00:00Z'), time('12:00:00Z')]", "0"),
])
def test_an_element_compares_as_its_value(lst, want):
    sel = _select_of(f"UNWIND {lst} AS e WITH collect(e) AS l RETURN l[0] < l[1] AS a")
    assert re.match(rf"SELECT {want} AS a\b", sel), sel


def test_a_null_is_not_collected():
    """collect() drops the null, so l[1] is the date."""
    sel = _select_of("UNWIND ['b', null, date('2020-01-01')] AS e WITH collect(e) AS l RETURN l[0] < l[1] AS a")
    assert re.match(r"SELECT NULL AS a\b", sel), sel


@pytest.mark.parametrize("query", [
    # rows before the UNWIND: the list is one per row, of the rows' values
    "MATCH (n) UNWIND [date('2020-01-01'), 'b'] AS e WITH collect(e) AS l RETURN l[0] < l[1] AS a",
    # a grouping key beside the collect
    "UNWIND [date('2020-01-01'), 'b'] AS e WITH e AS k, collect(e) AS l RETURN l[0] < l[1] AS a",
    # DISTINCT, a WHERE and a LIMIT on the WITH change which elements are collected
    "UNWIND [date('2020-01-01'), 'b'] AS e WITH collect(DISTINCT e) AS l RETURN l[0] < l[1] AS a",
    "UNWIND [date('2020-01-01'), 'b'] AS e WITH e WHERE e <> 'b' WITH collect(e) AS l RETURN l[0] < l[1] AS a",
])
def test_any_other_shape_compares_at_run_time(query):
    """Not folded: the comparison runs (as before 4.7.0) rather than answer a constant the shape does not imply."""
    assert not re.match(r"SELECT (NULL|0|1) AS a\b", _select_of(query)), _select_of(query)


@pytest.mark.parametrize("rebind", [
    "WITH [1, 2] AS l",
    "WITH l AS k, 'x' AS l",
    "UNWIND [['b', date('2020-01-01')]] AS l",
])
def test_a_later_binding_of_the_name_forgets_the_collected_list(rebind):
    sel = _select_of(f"UNWIND [date('2020-01-01'), 'b'] AS e WITH collect(e) AS l {rebind} RETURN l[0] < l[1] AS a")
    assert not re.match(r"SELECT NULL AS a\b", sel), sel
