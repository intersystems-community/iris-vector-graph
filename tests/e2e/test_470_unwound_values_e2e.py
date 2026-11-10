"""4.7.0 rows 21 and 22, live: an UNWIND of a list built at run time reads each element as itself, and a collect() of
a boolean expression lists booleans.

Row 21: `MATCH (f) WITH collect(f.fl) AS l UNWIND l AS x RETURN x` answered '1', '0' and null for true, false and '':
IRIS's JSON_TABLE hands a JSON boolean on as the text 1 / 0 and an empty string as null. `collect(exists(f.s))` and
`collect(f.s IS NOT NULL)` answered [1, 0, 0]. Row 22: an UNWIND of a run-time list read a boolean among nested lists
and maps as '1'.

The graph: three nodes of a label of this file's own, `k` 1, 2, 3, `fl` true, false and '' (`fl`, as `on` is a
keyword IVG does not take as a property name: see the report), `s` 'a' on the first only. The values are openCypher's:
an element is the value the list holds.

    IVG_NAMESPACE=IVG470TIME2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_unwound_values_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

FLAGS = "MATCH (f:W3V) WITH f ORDER BY f.k WITH collect(f.fl) AS l "


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    q = engs["sql"]
    q.execute_cypher("MATCH (n:W3V) DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:W3V {k: 1, fl: true, s: 'a'}), (:W3V {k: 2, fl: false}), (:W3V {k: 3, fl: ''})", {},
                     raise_on_error=True)
    yield engs
    q.execute_cypher("MATCH (n:W3V) DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


def _rows(eng, q):
    return eng.execute_cypher(q, {}, raise_on_error=True).rows


def _t(v):
    """A value beside its type, inside lists and maps too: True and 1 are told apart (1 == True in Python)."""
    if isinstance(v, list):
        return [_t(e) for e in v]
    if isinstance(v, dict):
        return {k: _t(e) for k, e in v.items()}
    return (v, type(v).__name__)


def _typed(rows):
    return [[_t(v) for v in r] for r in rows]


# ------------------------------------------------------------------------------------------------ row 21


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_an_unwound_boolean_and_empty_string_read_as_themselves(engs, route):
    rows = _rows(engs[route], FLAGS + "UNWIND l AS x RETURN x")
    assert _typed(rows) == [[(True, "bool")], [(False, "bool")], [("", "str")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_collect_of_them_lists_them(engs, route):
    assert _typed(_rows(engs[route], FLAGS + "UNWIND l AS x RETURN collect(x) AS c")) == _typed([[[True, False, ""]]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_empty_string_is_the_empty_string_in_a_filter(engs, route):
    assert _rows(engs[route], FLAGS + "UNWIND l AS x WITH x WHERE x = '' RETURN count(*) AS n") == [[1]]
    assert _rows(engs[route], FLAGS + "UNWIND l AS x WITH x WHERE x IS NULL RETURN count(*) AS n") == [[0]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_numbers_and_strings_read_as_before(engs, route):
    q = "UNWIND [1, 1.5, 'a', '1'] AS e WITH collect(e) AS l UNWIND l AS x RETURN x"
    assert _typed(_rows(engs[route], q)) == [[(1, "int")], [(1.5, "float")], [("a", "str")], [("1", "str")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("expr", ["exists(f.s)", "f.s IS NOT NULL", "f.k > 1"])
def test_a_collect_of_a_boolean_expression_lists_booleans(engs, route, expr):
    want = {"exists(f.s)": [True, False, False], "f.s IS NOT NULL": [True, False, False],
            "f.k > 1": [False, True, True]}[expr]
    got = _rows(engs[route], f"MATCH (f:W3V) WITH f ORDER BY f.k RETURN collect({expr}) AS c")
    assert _typed(got) == _typed([[want]])


# ------------------------------------------------------------------------------------------------ row 22


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_an_unwound_run_time_list_reads_nested_values_and_booleans(engs, route):
    q = "UNWIND [[1], true, {a: 1}, false] AS e WITH collect(e) AS l UNWIND l AS x RETURN x"
    assert _typed(_rows(engs[route], q)) == _typed([[[1]], [True], [{"a": 1}], [False]])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_collect_over_nested_values_and_booleans(engs, route):
    assert _typed(_rows(engs[route], "UNWIND [[1], true] AS e WITH collect(e) AS l RETURN l")) == _typed([[[[1], True]]])
