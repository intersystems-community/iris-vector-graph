"""4.6.0 R239: a parameter inside a function's argument keeps its binding.

`MATCH (n) RETURN toString(n.f * $k)` failed "Incorrect number of parameters". Each case builds, with a Cypher CREATE,
two nodes labelled L<run>: A `{name: 'Alpha', f: 1.25}` and B `{name: 'Beta'}` (no f). Parameters k = 2, d = 1.
Worked by hand, rows ordered by name (A, then B):

- `toString(n.f * $k)`: 1.25 * 2 = 2.5 -> `'2.5'`; B's null * 2 is null -> null
- `toString(coalesce(n.f, $k))`: `'1.25'`; B's coalesce(null, 2) = 2 -> `'2'`
- `toString(substring(n.name, $d))`: `'lpha'`, `'eta'`
- `substring(n.name, $d), coalesce(n.name, $k)`: `['lpha', 'Alpha']`, `['eta', 'Beta']`
- `coalesce(n.f, $k)`: 1.25 and 2, with their types (a strict xfail until R259: a stored number answered its text
  '1.25', with or without a parameter; not R239's binding)
- `round(n.f * $k)`: round(2.5) = 3.0 (openCypher rounds half away from zero), null for B
- `WHERE n.name = $nm RETURN toString(n.f * $k)` with nm = 'Alpha': `[['2.5']]`
- `WHERE coalesce(n.f, $k) > $lo RETURN n.name` with lo = 1.5: A's 1.25 is not, B's 2 is: `[['Beta']]`
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    label = f"L{uuid.uuid4().hex[:8]}"

    def run_q(query, params=None):
        r = e.execute_cypher(query, {"k": 2, "d": 1, **dict(params or {})})
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(f"CREATE (:{label} {{name: 'Alpha', f: 1.25}}), (:{label} {{name: 'Beta'}})")
    yield run_q, label
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


@pytest.mark.parametrize(
    "ret, answer",
    [
        ("toString(n.f * $k)", [["2.5"], [None]]),
        ("toString(coalesce(n.f, $k))", [["1.25"], ["2"]]),
        ("toString(substring(n.name, $d))", [["lpha"], ["eta"]]),
        ("substring(n.name, $d), coalesce(n.name, $k)", [["lpha", "Alpha"], ["eta", "Beta"]]),
        ("round(n.f * $k)", [[3.0], [None]]),
    ],
    ids=["tostring_arith", "tostring_coalesce", "tostring_substring", "substring_coalesce", "round"],
)
def test_return_function_with_a_parameter(g, ret, answer):
    run_q, label = g
    assert run_q(f"MATCH (n:{label}) RETURN {ret} ORDER BY n.name") == answer


def test_coalesce_of_a_stored_number_keeps_its_type(g):
    # was a strict xfail: the text '1.25' (COALESCE(CAST(prop AS VARCHAR), ...)); typed since 4.6.0 R259
    run_q, label = g
    rows = run_q(f"MATCH (n:{label}) RETURN coalesce(n.f, $k) ORDER BY n.name")
    assert [[(type(v).__name__, v) for v in r] for r in rows] == [[("float", 1.25)], [("int", 2)]]


def test_where_parameter_and_select_parameter(g):
    run_q, label = g
    assert run_q(f"MATCH (n:{label}) WHERE n.name = $nm RETURN toString(n.f * $k)", {"nm": "Alpha"}) == [["2.5"]]


def test_where_function_with_a_parameter(g):
    run_q, label = g
    assert run_q(f"MATCH (n:{label}) WHERE coalesce(n.f, $k) > $lo RETURN n.name", {"lo": 1.5}) == [["Beta"]]
