"""4.6.0 R273 (the arithmetic cases), live: integer division over a value only the row holds truncates, an integer
expression column is an integer under either driver, and toString(coalesce()) spells a stored float as a float.

Found by fixes35 (R259's leftovers) on the release head:
* `UNWIND [-7, 7] AS x RETURN x / 2` answered -3.5 and 3.5 (Decimal under intersystems-irispython 5.4.0, the text
  '-3.5' under 5.4.1): only a literal integer operand made the division an integer one;
* `toString(coalesce(n.f, $k))` of a stored 3.0 answered '3' (coalesce()'s VARCHAR CASTs).

Found with them, on the same head: `WITH 3 AS x RETURN x * 4` answered the text '12' under 5.4.1 (an integer
expression column was cast to int only from a float or a Decimal); `RETURN $k / 2` failed "Incorrect number of
parameters" (IRIS's TRUNCATE() over a marker) and `$k / $d` "Invalid number of parameters"; `coalesce(n.zz, 1.0)`
answered the integer 1 (IRIS's literal 1.0 is a number, not a $DOUBLE).

Worked by hand (openCypher: integer / integer truncates toward zero, as Java's does):
* x in [-7, 7]: x / 2 is -3, 3; x * 2 is -14, 14; x - 1 is -8, 6; x % 2 is -1, 1; toString(x / 2) '-3', '3';
  x / 2.0 is -3.5, 3.5; through `WITH x / 2 AS y`, y is -3, 3.
* x = -7 from a WITH: x / 2 is -3, 2 / x is 0, x * 4 is -28.
* x in [1, 2], y = 2: x / y is 0, 1. x = -7.0: x / 2 is -3.5.
* k = 7, d = -2, f = 7.0: $k / 2 is 3, $k / $d is -3, $f / $d is -3.5.
* Node (:L {f: 3.0, k: 1}) written by Cypher: coalesce(n.f, $k) is 3.0 and its toString '3.0'; toString(coalesce(n.k,
  9)) '1'; for the missing n.zz, coalesce(n.zz, 1.0) is 1.0 and its toString '1.0', coalesce(n.zz, 2) is 2.

Namespace: an IVG460F* scratch namespace. Native on and off, under intersystems-irispython 5.4.0 and 5.4.1.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r273a"
U = f"USE GRAPH '{G}' "
PARAMS = {"k": 7, "d": -2, "f": 7.0, "z": 0}

CASES = [
    ("x / 2 over an UNWIND of integers", "UNWIND [-7, 7] AS x RETURN x / 2", [[-3], [3]]),
    ("x * 2, x - 1, x % 2", "UNWIND [-7, 7] AS x RETURN x * 2, x - 1, x % 2", [[-14, -8, -1], [14, 6, 1]]),
    ("toString(x / 2)", "UNWIND [-7, 7] AS x RETURN toString(x / 2)", [["-3"], ["3"]]),
    ("x / 2.0", "UNWIND [-7, 7] AS x RETURN x / 2.0", [[-3.5], [3.5]]),
    ("x / 2 through WITH", "UNWIND [-7, 7] AS x WITH x / 2 AS y RETURN y", [[-3], [3]]),
    ("a WITH integer", "WITH -7 AS x RETURN x / 2, 2 / x, x * 4", [[-3, 0, -28]]),
    ("two UNWIND integers", "UNWIND [1, 2] AS x UNWIND [2] AS y RETURN x / y", [[0], [1]]),
    ("an UNWIND float", "UNWIND [-7.0] AS x RETURN x / 2", [[-3.5]]),
    ("parameters", "RETURN $k / 2, $k / $d, $f / $d", [[3, -3, -3.5]]),
    ("toString(coalesce()) of a stored float",
     U + "MATCH (n:L {id: 'r273a'}) RETURN coalesce(n.f, $z), toString(coalesce(n.f, $z))", [[3.0, "3.0"]]),
    ("toString(coalesce()) of a stored integer", U + "MATCH (n:L {id: 'r273a'}) RETURN toString(coalesce(n.k, 9))",
     [["1"]]),
    ("coalesce() to a float literal",
     U + "MATCH (n:L {id: 'r273a'}) RETURN coalesce(n.zz, 1.0), toString(coalesce(n.zz, 1.0)), coalesce(n.zz, 2)",
     [[1.0, "1.0", 2]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (:L {id: 'r273a', f: 3.0, k: 1})", {}, raise_on_error=True)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _typed(v):
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    return [type(v).__name__, v]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_arithmetic_keeps_cypher_types(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, dict(PARAMS), raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert _typed([list(r) for r in result.rows]) == _typed(want)
