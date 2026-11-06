"""4.6.0 R310, live: an unwound element of a list a WITH or collect() holds answers as its openCypher value, the same
under intersystems-irispython 5.4.0 and 5.4.1 (fixes64; tests/unit/test_460_r310_unwind_json_typed.py).

Before: `WITH [3, 1.5] AS l UNWIND l AS x RETURN x` answered 3, Decimal('1.5') under 5.4.0 and '3', '1.5' under 5.4.1.
The expected answers are tests/oracle/values.py's `oc`, written by hand from openCypher's rules. Left out, each for a
reason recorded in KNOWN_ISSUES: booleans and the empty string (JSON_TABLE hands true on as IRIS's 1, which is not
told from the string '1', and '' as null), the "spelled" strings (a string that spells a list, a map or a boolean
reads as one), the temporal values (their own readers), and 'café ☕ 日本' (a long list literal holding it fails at
Query Open, an R310 follow-up).
"""

from __future__ import annotations

import math
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.oracle.values import VALUES

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

CHECKED = [
    v for v in VALUES
    if v.literal_writable and v.group not in ("bool", "spelled", "dur", "date") and v.key not in ("suni", "sempty")
]


def _typed(x):
    if isinstance(x, float) and math.isnan(x):
        return ("float", "nan")
    if isinstance(x, list):
        return ("list", [_typed(e) for e in x])
    if isinstance(x, dict):
        return ("map", {k: _typed(e) for k, e in x.items()})
    return (type(x).__name__, x)


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:J310) DETACH DELETE n", raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:J310) DETACH DELETE n")


def _col(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    assert not r.error, r.error
    return [row[0] for row in r.rows]


@pytest.mark.parametrize("v", CHECKED, ids=[v.key for v in CHECKED])
def test_an_element_of_a_with_list(eng, v):
    assert [_typed(x) for x in _col(eng, f"WITH [{v.cypher}, 0] AS l UNWIND l AS x RETURN x")] == [
        _typed(v.oc), _typed(0)
    ]


def test_every_element_of_one_with_list_in_order(eng):
    lits = ", ".join(v.cypher for v in CHECKED)
    assert [_typed(x) for x in _col(eng, f"WITH [{lits}] AS l UNWIND l AS x RETURN x")] == [
        _typed(v.oc) for v in CHECKED
    ]


def test_numbers_of_a_collected_list_and_arithmetic_over_them(eng):
    # by hand: 3 and 1.5 collected in that order; x + 1 is 4 and 2.5, x * 2 is 6 and 3.0
    q = "UNWIND [3, 1.5] AS v WITH collect(v) AS l UNWIND l AS x RETURN x, x + 1 AS y, x * 2 AS z"
    r = eng.execute_cypher(q, raise_on_error=True)
    assert [[_typed(c) for c in row] for row in r.rows] == [
        [_typed(3), _typed(4), _typed(6)], [_typed(1.5), _typed(2.5), _typed(3.0)]
    ]


def test_an_unwound_number_carried_by_a_with(eng):
    assert [_typed(x) for x in _col(eng, "WITH [3, 1.0, -2] AS l UNWIND l AS x WITH x AS y RETURN y")] == [
        _typed(3), _typed(1.0), _typed(-2)
    ]


def test_a_collected_node_property_list(eng):
    # Cypher writes the integers and the float as numbers; collected in i order
    eng.execute_cypher(
        "CREATE (:J310 {i: 1, p: 7}), (:J310 {i: 2, p: 0.95}), (:J310 {i: 3, p: 'x'})", raise_on_error=True
    )
    q = "MATCH (n:J310) WITH n ORDER BY n.i WITH collect(n.p) AS l UNWIND l AS x RETURN x"
    assert [_typed(x) for x in _col(eng, q)] == [_typed(7), _typed(0.95), _typed("x")]
