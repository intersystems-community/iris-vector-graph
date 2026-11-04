"""4.6.0 R242: a SET reads a value the WITH before it projected.

`MATCH (n) WITH n.k AS k, n SET n.r = k` raised UndefinedVariable: a SET value could name a parameter or a
property, not a WITH column. Each case builds, with a Cypher CREATE, two nodes labelled L<run>:
A `{name: 'A', k: 3, s: 'hi', f: 2.5, g: 2.0}` and B `{name: 'B', k: 5}`. Worked by hand, rows ordered by name:

- `WITH n.k AS k, n SET n.r = k`: A r = 3, B r = 5 (integers)
- `WITH n, n.k * 2 AS d SET n.r = d`: A r = 6, B r = 10 (an integer times an integer is an integer)
- `WITH n, n.g * 2 AS d SET n.t = d`: A t = 4.0 (a float times an integer is a float); B's null g, no t
- `WITH n, n.k AS k WHERE k > 4 SET n.r = k`: B alone, r = 5; A has no r
- `WITH n, n.s AS s SET n.t = s`: A t = 'hi'; B's s is null, so B has no t
- `WITH n, n.f AS f SET n.t = f`: A t = 2.5; B none
- `WITH n, n.g AS g SET n.t = g`: A t = 2.0, a float; B none
- `WITH n, 7 AS c SET n.r = c`: both r = 7
- `WITH n.k AS k, n SET n += {r: k}`: as the first
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
    run_q(f"CREATE (:{label} {{name: 'A', k: 3, s: 'hi', f: 2.5, g: 2.0}}), (:{label} {{name: 'B', k: 5}})")
    yield run_q, label
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _typed(rows):
    return [[(v, type(v).__name__) for v in r] for r in rows]


@pytest.mark.parametrize(
    "tail, key, stored",
    [
        ("WITH n.k AS k, n SET n.r = k", "r", [3, 5]),
        ("WITH n, n.k * 2 AS d SET n.r = d", "r", [6, 10]),  # an integer since R240
        ("WITH n, n.g * 2 AS d SET n.t = d", "t", [4.0, None]),  # a float since R240
        ("WITH n, n.k AS k WHERE k > 4 SET n.r = k", "r", [None, 5]),
        ("WITH n, n.s AS s SET n.t = s", "t", ["hi", None]),
        ("WITH n, n.f AS f SET n.t = f", "t", [2.5, None]),
        ("WITH n, n.g AS g SET n.t = g", "t", [2.0, None]),
        ("WITH n, 7 AS c SET n.r = c", "r", [7, 7]),
        ("WITH n.k AS k, n SET n += {r: k}", "r", [3, 5]),
    ],
    ids=["column", "arith", "float_arith", "where", "string", "float", "whole_float", "constant", "merge_map"],
)
def test_set_reads_the_with_column(g, tail, key, stored):
    run_q, label = g
    run_q(f"MATCH (n:{label}) {tail}")
    rows = run_q(f"MATCH (n:{label}) RETURN n.{key}, toString(n.{key}) ORDER BY n.name")
    # the value, and its type through toString(): a bare RETURN of a whole stored float answers an int on some
    # shapes (R218), whatever was stored
    assert [r[0] for r in rows] == stored
    assert [r[1] for r in rows] == [None if v is None else _text(v) for v in stored]


def _text(v):
    return repr(v) if isinstance(v, float) else str(v)


def test_the_return_after_the_set(g):
    run_q, label = g
    rows = run_q(f"MATCH (n:{label}) WITH n.k AS k, n SET n.r = k RETURN n.name, n.r, k ORDER BY n.name")
    assert _typed(rows) == _typed([["A", 3, 3], ["B", 5, 5]])
