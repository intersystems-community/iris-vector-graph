"""4.6.0 R240: arithmetic over a stored number keeps the number's int/float type.

`toString(n.g * 2)` with `g = 2.0` answered `'4'` (openCypher `'4.0'`), and worse, `RETURN n.f * 3` with `f = 1.5`
answered `4` (4.5 rounded: the column was decoded as an integer), `n.f + 1` answered 2, `n.i / 2` with `i = 3` answered 2
(1.5 rounded; openCypher's integer division is 1). Each case reads two nodes labelled L<run>, both
`{g: 2.0, f: 1.5, i: 3}`: A written by a Cypher CREATE (a $DOUBLE, a decimal and an integer), B by `create_node` (the
texts '2.0', '1.5', '3', as every 4.x API write stores them). Worked by hand, the same for A and B:

- `n.g * 2` 4.0, `n.f * 2` 3.0, `n.f * 3` 4.5, `n.i * 2` 6, `n.i * n.i` 9, `n.g * n.i` 6.0
- `n.g + 1` 3.0, `n.i + 1` 4, `n.g - 1` 1.0, `0 - n.i` -3
- `n.i / 2` 1 (integer division), `n.g / 2` 1.0, `n.f / 2` 0.75
- `(n.g * 2) * 2` 8.0, `(n.i * 2) - 1` 5
- `n.g * $k` (k = 2) 4.0, `n.i * $k` 6
- `toString(n.g * 2)` '4.0', `toString(n.i * 2)` '6', `toString(n.f * 3)` '4.5', `toString(n.g * $k)` '4.0'
- `WITH n.g * 2 AS d RETURN d, toString(d)`: 4.0, '4.0'; `WITH n.i * 2 AS d`: 6, '6'
- `SET n.r = n.g * 2` stores 4.0 ('4.0'), `n.i * 2` 6, `n.f * 3` 4.5, `n.g * $k` 4.0; `SET n.i = n.i + 1` 4
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
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(f"CREATE (:{label} {{name: 'A', g: 2.0, f: 1.5, i: 3}})")
    assert e.create_node(f"{label}_b", labels=[label], properties={"name": "B", "g": 2.0, "f": 1.5, "i": 3})
    yield run_q, label
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _typed(v):
    return (v, type(v).__name__)


@pytest.mark.parametrize(
    "expr, answer",
    [
        ("n.g * 2", 4.0),
        ("n.f * 2", 3.0),
        ("n.f * 3", 4.5),
        ("n.i * 2", 6),
        ("n.i * n.i", 9),
        ("n.g * n.i", 6.0),
        ("n.g + 1", 3.0),
        ("n.i + 1", 4),
        ("n.g - 1", 1.0),
        ("0 - n.i", -3),
        ("n.i / 2", 1),
        ("n.g / 2", 1.0),
        ("n.f / 2", 0.75),
        ("(n.g * 2) * 2", 8.0),
        ("(n.i * 2) - 1", 5),
        ("n.g * $k", 4.0),
        ("n.i * $k", 6),
        ("toString(n.g * 2)", "4.0"),
        ("toString(n.i * 2)", "6"),
        ("toString(n.f * 3)", "4.5"),
        ("toString(n.g * $k)", "4.0"),
    ],
)
def test_return_keeps_the_stored_type(g, expr, answer):
    run_q, label = g
    rows = run_q(f"MATCH (n:{label}) RETURN n.name, {expr} ORDER BY n.name", {"k": 2})
    assert [[r[0], _typed(r[1])] for r in rows] == [["A", _typed(answer)], ["B", _typed(answer)]]


@pytest.mark.parametrize("expr, value, text", [("n.g * 2", 4.0, "4.0"), ("n.i * 2", 6, "6")])
def test_a_with_column_keeps_the_stored_type(g, expr, value, text):
    run_q, label = g
    rows = run_q(f"MATCH (n:{label}) WITH n.name AS name, {expr} AS d RETURN name, d, toString(d) ORDER BY name")
    assert [[r[0], _typed(r[1]), r[2]] for r in rows] == [["A", _typed(value), text], ["B", _typed(value), text]]


def test_order_by_the_typed_column_is_numeric(g):
    run_q, label = g
    run_q(f"CREATE (:{label} {{name: 'C', g: 0.5, f: 1.5, i: 10}})")
    rows = run_q(f"MATCH (n:{label}) RETURN n.name, n.i * 2 AS x ORDER BY x DESC, n.name")
    assert rows == [["C", 20], ["A", 6], ["B", 6]]


@pytest.mark.parametrize(
    "set_clause, key, text",
    [
        ("SET n.r = n.g * 2", "r", "4.0"),
        ("SET n.r = n.i * 2", "r", "6"),
        ("SET n.r = n.f * 3", "r", "4.5"),
        ("SET n.i = n.i + 1", "i", "4"),
        ("SET n.r = n.g * $k", "r", "4.0"),
    ],
)
def test_set_stores_the_typed_value(g, set_clause, key, text):
    """A SET of arithmetic over a stored value stored CAST(... AS NUMERIC): 2.0 * 2 as the integer 4."""
    run_q, label = g
    run_q(f"MATCH (n:{label}) {set_clause}", {"k": 2})
    rows = run_q(f"MATCH (n:{label}) RETURN n.name, toString(n.{key}) ORDER BY n.name")
    assert rows == [["A", text], ["B", text]]
