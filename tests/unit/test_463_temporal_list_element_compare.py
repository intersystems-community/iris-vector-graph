"""4.6.3: a temporal element of a WITH-held literal list, read at a literal position, compares as the temporal value.

`WITH [date('2020-01-01'), 'b'] AS l RETURN l[0] < l[1]` answered true: the list holds the date as its ISO text, so
CY_JCMP ordered two strings. openCypher orders a date against a string as null, and against another temporal kind as
null too; two dates order by date. The element is known before the query runs (as `WITH ['a', 'b'] AS l` already
was, which answers its comparisons while translating), so the translation answers it the same way. This is the
`subscript` half of tests/oracle/crosstype.py's KNOWN_WRONG_461; the live family is
tests/e2e/test_460_r308_crosstype_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _select_of(cypher) -> str:
    sql = translate_to_sql(parse_query(cypher), {}).sql
    return sql[sql.rindex("SELECT "):]


@pytest.mark.parametrize(
    "lst,want",
    [
        ("[date('2020-01-01'), 'b']", "NULL"),
        ("['b', date('2020-01-01')]", "NULL"),
        ("[date('2020-01-01'), datetime('2020-01-02T00:00:00Z')]", "NULL"),
        ("[localdatetime('2020-01-01T00:00:00'), time('12:00:00Z')]", "NULL"),
        ("[date('2020-01-01'), date('2020-01-02')]", "1"),
        ("[time('13:00:00Z'), time('12:00:00Z')]", "0"),
    ],
)
def test_an_element_compares_as_its_value(lst, want):
    sel = _select_of(f"WITH {lst} AS l RETURN l[0] < l[1] AS a")
    assert re.match(rf"SELECT {want} AS a\b", sel), sel


def test_an_element_of_a_list_literal_compares_as_its_value():
    sel = _select_of("RETURN [date('2020-01-01'), 'b'][0] < [date('2020-01-01'), 'b'][1] AS a")
    assert re.match(r"SELECT NULL AS a\b", sel), sel


def test_an_element_beside_a_row_value_keeps_the_runtime_comparison():
    sel = _select_of("WITH [date('2020-01-01'), 'b'] AS l MATCH (n) RETURN l[0] < n.x AS a")
    assert "CY_JCMP" in sel or "CY_CMP" in sel or "Graph_KG" in sel, sel
