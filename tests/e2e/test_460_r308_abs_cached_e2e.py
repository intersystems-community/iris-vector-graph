"""4.6.0 R308 (cmptypes), live: a number function's answer does not depend on the call before it.

IRIS caches a statement with its literals replaced, and ABS() kept the type the first call's argument had: after
`RETURN abs($x)` of 1, the same statement of 0.5 answered 1.0, and abs(n.v) of a stored 0.5 after a stored 1 answered
0 (measured on 2026.3, both drivers). Each test purges the cached queries once, at its start, then runs its calls in
order without purging: the second call reads the first one's plan, which is the bug. Answers worked by hand
(openCypher: abs keeps its argument's type; ceil, floor and round answer a float, sign an integer). The other IRIS
built-ins whose result type follows their argument (ROUND, SIGN, FLOOR, CEILING, MOD, POWER) run the same orders.

Namespace, container and drivers as the R308 differential (tests/e2e/test_460_r308_cypher_differential_e2e.py).
"""

from __future__ import annotations

import math

import pytest

from tests.e2e import ns262
from tests.e2e.test_460_r308_cypher_differential_e2e import ROUTES, engines, run  # noqa: F401 (engines: fixture)

pytestmark = [pytest.mark.e2e, ns262.requires_container]

LABEL = "ZZA308"


def _purge(engines):
    import iris

    iris.createIRIS(engines["conn"]).classMethodValue("%SYSTEM.SQL", "Purge")


@pytest.fixture(scope="module")
def stored(engines):
    sql = engines["sql"]
    sql.execute_cypher(f"MATCH (n:{LABEL}) DETACH DELETE n", raise_on_error=True)
    for k, v in (("one", 1), ("half", 0.5), ("mhalf", -0.5), ("big", 9007199254740993), ("three", 3), ("f25", 2.5)):
        sql.execute_cypher(f"CREATE (:{LABEL} {{k: '{k}', v: {v!r}}})", raise_on_error=True)
    yield
    sql.execute_cypher(f"MATCH (n:{LABEL}) DETACH DELETE n", raise_on_error=True)


def _same(got, want) -> bool:
    if isinstance(want, bool) or want is None:
        return got is want
    if isinstance(got, bool) or not isinstance(got, (int, float)):
        try:
            from decimal import Decimal

            if not isinstance(got, Decimal):
                return False
        except ImportError:
            return False
    if isinstance(want, int) and not isinstance(got, int):
        return False  # an integer answer stays an integer
    return math.isclose(float(got), float(want), rel_tol=1e-12, abs_tol=1e-12) and (
        not isinstance(want, int) or int(got) == want)


def _calls(engines, route, calls):
    out = []
    for text, params in calls:
        a = run(engines, route, text, params)
        out.append(a.error or (a.rows[0][0] if a.rows else None))
    return out


ABS_ORDERS = [
    ("param 1 then 0.5", [("RETURN abs($x) AS a", {"x": 1}), ("RETURN abs($x) AS a", {"x": 0.5})], [1, 0.5]),
    ("param 0.5 then 1", [("RETURN abs($x) AS a", {"x": 0.5}), ("RETURN abs($x) AS a", {"x": 1})], [0.5, 1]),
    ("literal 1 then 0.5", [("RETURN abs(1) AS a", {}), ("RETURN abs(0.5) AS a", {})], [1, 0.5]),
    ("literal -0.0, -1, a large integer", [("RETURN abs(-0.0) AS a", {}), ("RETURN abs(-1) AS a", {}),
                                           ("RETURN abs(-9007199254740993) AS a", {})], [0.0, 1, 9007199254740993]),
    ("stored 1 then 0.5", [(f"MATCH (n:{LABEL} {{k: 'one'}}) RETURN abs(n.v) AS a", {}),
                           (f"MATCH (n:{LABEL} {{k: 'half'}}) RETURN abs(n.v) AS a", {}),
                           (f"MATCH (n:{LABEL} {{k: 'mhalf'}}) RETURN abs(n.v) AS a", {})], [1, 0.5, 0.5]),
    ("stored 0.5 then 1", [(f"MATCH (n:{LABEL} {{k: 'half'}}) RETURN abs(n.v) AS a", {}),
                           (f"MATCH (n:{LABEL} {{k: 'one'}}) RETURN abs(n.v) AS a", {}),
                           (f"MATCH (n:{LABEL} {{k: 'big'}}) RETURN abs(n.v) AS a", {})], [0.5, 1, 9007199254740993]),
]


@pytest.mark.parametrize("calls, want", [(c, w) for _, c, w in ABS_ORDERS], ids=[n for n, _, _ in ABS_ORDERS])
def test_abs_answers_each_call_in_its_own_type(engines, stored, calls, want):
    for route in ROUTES:
        _purge(engines)
        got = _calls(engines, route, calls)
        assert all(_same(g, w) for g, w in zip(got, want)), (route, got, want)


# the other built-ins whose result type follows the argument: an integer first, then a float, then the reverse
OTHERS = {
    "round": ("round({})", 1, 1.0, 2.5, 3.0),
    "sign": ("sign({})", 1, 1, -0.5, -1),
    "floor": ("floor({})", 1, 1.0, 2.5, 2.0),
    "ceil": ("ceil({})", 1, 1.0, 2.5, 3.0),
    "mod": ("{} % 2", 3, 1, 2.5, 0.5),
    "power": ("{} ^ 2", 3, 9.0, 0.5, 0.25),
}


@pytest.mark.parametrize("name", sorted(OTHERS))
@pytest.mark.parametrize("form", ["param", "stored"])
@pytest.mark.parametrize("order", ["int first", "float first"])
def test_a_number_function_answers_each_call_alike(engines, stored, name, form, order):
    shape, i, i_want, f, f_want = OTHERS[name]
    keys = {1: "one", 3: "three", 2.5: "f25", -0.5: "mhalf", 0.5: "half"}

    def call(v):
        if form == "param":
            return (f"RETURN {shape.format('$x')} AS a", {"x": v})
        return (f"MATCH (n:{LABEL} {{k: '{keys[v]}'}}) RETURN {shape.format('n.v')} AS a", {})

    calls = [call(i), call(f)] if order == "int first" else [call(f), call(i)]
    want = [i_want, f_want] if order == "int first" else [f_want, i_want]
    for route in ROUTES:
        _purge(engines)
        got = _calls(engines, route, calls)
        # the value is checked; the int/float type only for sign, whose answer openCypher types as an integer
        ok = all(not isinstance(g, str) and g is not None and math.isclose(float(g), float(w), abs_tol=1e-12)
                 for g, w in zip(got, want))
        assert ok, (route, got, want)
