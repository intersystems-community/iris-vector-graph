"""4.6.0 R209: `toString()` of a float that is not a literal answers openCypher's text.

`UNWIND [0.5, 1.0] AS x RETURN toString(x)` answered `'.5'`, `'1'` and `toString(0.1 + 0.2)` `'.30000000000000004441'`:
IRIS's spelling of the double. openCypher spells a FLOAT as Java's Double.toString does and leaves an INTEGER and a
string as they are. Worked by hand (0.1 + 0.2 is the double 0.30000000000000004; 7 / 2 is integer division):

- UNWIND [0.5, 1.0]: '0.5', '1.0'; UNWIND [1, 0.1, 'a']: '1', '0.1', 'a'; UNWIND [1, 2]: '1', '2'
- 0.1 + 0.2: '0.30000000000000004'; 1.5 * 2: '3.0'; 7 / 2.0: '3.5'; 7 / 2: '3'; 2 ^ 3: '8.0'; sqrt(16): '4.0'
- WITH 0.5, 1.0, 0.1 + 0.2: '0.5', '1.0', '0.30000000000000004'
- $v = 2.5, $w = 1.0: '2.5', '1.0'
- avg over [1, 2]: '1.5'; over [1, 3]: '2.0'
- a node {f: 0.25, i: 3, s: '.5'} a Cypher CREATE wrote: f * 2 '0.5', i * 2 '6', f '0.25', s '.5' (a string as it
  is); WITH f * 2, i * 2: '0.5', '6'
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def run_q(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)

    def _run(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    return _run


@pytest.mark.parametrize(
    "query, params, want",
    [
        ("UNWIND [0.5, 1.0] AS x RETURN toString(x)", {}, [["0.5"], ["1.0"]]),
        ("UNWIND [1, 0.1, 'a'] AS x RETURN toString(x)", {}, [["1"], ["0.1"], ["a"]]),
        ("UNWIND [1, 2] AS x RETURN toString(x)", {}, [["1"], ["2"]]),
        (
            "RETURN toString(0.1 + 0.2), toString(1.5 * 2), toString(7 / 2.0), toString(7 / 2), toString(2 ^ 3), "
            "toString(sqrt(16))",
            {},
            [["0.30000000000000004", "3.0", "3.5", "3", "8.0", "4.0"]],
        ),
        (
            "WITH 0.5 AS x, 1.0 AS y, 0.1 + 0.2 AS z RETURN toString(x), toString(y), toString(z)",
            {},
            [["0.5", "1.0", "0.30000000000000004"]],
        ),
        ("RETURN toString($v), toString($w)", {"v": 2.5, "w": 1.0}, [["2.5", "1.0"]]),
        ("UNWIND [1, 2] AS x RETURN toString(avg(x))", {}, [["1.5"]]),
        ("UNWIND [1, 3] AS x RETURN toString(avg(x))", {}, [["2.0"]]),
    ],
    ids=["unwind_floats", "unwind_mixed", "unwind_ints", "arithmetic", "with", "params", "avg", "avg_whole"],
)
def test_tostring_of_a_value(run_q, query, params, want):
    assert run_q(query, params) == want


def test_tostring_of_arithmetic_over_stored_values(run_q):
    label = f"TS{uuid.uuid4().hex[:8]}"
    run_q(f"CREATE (:{label} {{f: 0.25, i: 3, s: '.5'}})")
    try:
        assert run_q(f"MATCH (n:{label}) RETURN toString(n.f * 2), toString(n.i * 2), toString(n.f), toString(n.s)") == [
            ["0.5", "6", "0.25", ".5"]
        ]
        assert run_q(f"MATCH (n:{label}) WITH n.f * 2 AS h, n.i * 2 AS j RETURN toString(h), toString(j)") == [
            ["0.5", "6"]
        ]
    finally:
        run_q(f"MATCH (n:{label}) DETACH DELETE n")
