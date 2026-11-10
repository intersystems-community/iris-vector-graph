"""4.7.0 row 35b, live: toBoolean() converts a boolean and the strings 'true' and 'false' (any case) only.

Measured on f12bee92 (IRIS 2026.3, IVG470CY2): `toBoolean('yes')`, `'y'` and `'1'` answered true, `'no'`, `'n'`, `'0'`
false, in a RETURN of a stored string and in `SET n.b = toBoolean(n.s)`; `RETURN toBoolean($s)` failed "Incorrect
number of parameters". openCypher (TCK TypeConversion1 [1]-[4]) answers null for every other string.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470B"


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: 'n1', p: 5, q: 2}})")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


TO_BOOLEAN = [("true", True), ("TRUE", True), ("True", True), ("false", False), ("FALSE", False),
              ("yes", None), ("y", None), ("1", None), ("no", None), ("n", None), ("0", None), (" true ", None),
              ("", None)]


@pytest.mark.parametrize("text, want", TO_BOOLEAN)
def test_to_boolean_of_a_string(engines, text, want):
    for route in ns463.ROUTES:
        assert ns463.typed(ns463.rows(engines[route], "RETURN toBoolean($s) AS b", {"s": text})) == [
            [(want, type(want).__name__)]], (route, text)
        ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.s = $s", {"s": text})
        got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: 'n1'}}) RETURN toBoolean(n.s)")
        assert ns463.typed(got) == [[(want, type(want).__name__)]], (route, text, "stored")


@pytest.mark.parametrize("text, want", TO_BOOLEAN)
def test_set_to_boolean_stores_as_return_answers(engines, text, want):
    e = engines["sql"]
    ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.s = $s", {"s": text})
    ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.b = toBoolean(n.s)")
    got = ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'n1'}}) RETURN n.b, keys(n)")
    assert ns463.typed([[got[0][0]]]) == [[(want, type(want).__name__)]], text
    assert ("b" in got[0][1]) is (want is not None), got


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_to_boolean_of_a_boolean(engines, route):
    """A boolean reaches SQL as 1 or 0, which is not the string '1' (TCK TypeConversion1 [1])."""
    assert ns463.typed(ns463.rows(engines[route], "UNWIND [true, false] AS b RETURN toBoolean(b) AS b")) == [
        [(True, "bool")], [(False, "bool")]]
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'n1'}}) SET n.t = true, n.f = false")
    got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: 'n1'}}) RETURN toBoolean(n.t), toBoolean(n.f)")
    assert ns463.typed(got) == [[(True, "bool"), (False, "bool")]]
