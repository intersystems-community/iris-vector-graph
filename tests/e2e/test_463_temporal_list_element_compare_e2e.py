"""4.6.3, live: a temporal element of a WITH-held literal list, read at a literal position, compares as its value.

openCypher (CIP2015-08-06-date-time, "Comparability"): values of two temporal types, or a temporal value and a
string, do not order: `<` `<=` `>` `>=` answer null, `=` false and `<>` true. Two values of one type order by value.

- `WITH [date('2020-01-01'), 'b'] AS l RETURN l[0] < l[1], ...`: null, null, null, null, false, true. Before 4.6.3:
  true, true, false, false, false, true (the date's ISO text ordered against 'b' as a string).
- `[date('2020-01-01'), datetime('2020-01-02T00:00:00Z')]`: the same nulls (before: true for `<`).
- `[date('2020-01-02'), date('2020-01-01')]`: false, false, true, true, false, true (by date).

The collect()ed form (`UNWIND [...] AS e WITH collect(e) AS l RETURN l[0] < l[1]`) still compares the text: the
KNOWN_ISSUES entry and tests/oracle/crosstype.py's KNOWN_WRONG_461. Each case runs on the default and the SQL route.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

OPS = ("<", "<=", ">", ">=", "=", "<>")


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    yield ns463.engines(conn)
    conn.close()


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize(
    "lst,want",
    [
        ("[date('2020-01-01'), 'b']", [None, None, None, None, False, True]),
        ("['b', time('12:00:00Z')]", [None, None, None, None, False, True]),
        ("[date('2020-01-01'), datetime('2020-01-02T00:00:00Z')]", [None, None, None, None, False, True]),
        ("[localdatetime('2020-01-01T00:00:00'), date('2020-01-01')]", [None, None, None, None, False, True]),
        ("[date('2020-01-02'), date('2020-01-01')]", [False, False, True, True, False, True]),
    ],
)
def test_an_element_compares_as_its_value(engines, route, lst, want):
    cols = ", ".join(f"l[0] {op} l[1] AS c{i}" for i, op in enumerate(OPS))
    got = ns463.rows(engines[route], f"WITH {lst} AS l RETURN {cols}")
    assert ns463.typed(got) == [[(v, type(v).__name__) for v in want]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_an_element_of_a_list_literal_compares_as_its_value(engines, route):
    """KNOWN_ISSUES's old check: true before 4.6.3, null by openCypher."""
    q = "RETURN [date('2020-01-01'), 'b'][0] < [date('2020-01-01'), 'b'][1] AS a"
    assert ns463.rows(engines[route], q) == [[None]]
