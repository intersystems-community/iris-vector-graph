"""4.6.0 R292 (the WITH + item), live: a WITH column of `+` over a stored number keeps the row's int or float type.

Found by fixes43 on the release head, under intersystems-irispython 5.4.0 and 5.4.1: `MATCH (n) WITH n.k + 1 AS k
RETURN k` answered 4 for k = 2.5, where `RETURN n.k + 1` answered 3.5. R240 types arithmetic over a stored value with
CY_ARITH; in a WITH it did so only for `-`, `*` and `/` (the duration CASE makes those columns text already), so `+`
stayed `CAST(val AS DOUBLE) + 1`, and the static typing called it an integer (a bare property defaults to 'int'), so
the engine rounded the column: 3.5 to 4, -0.5 to 0.

Worked by hand. A292 nodes {k: 2.5}, {k: 2}, {k: -1.5}, {k: 10}.
* `WITH n.k + 1 AS k` is 3.5, 3, -0.5, 11: a float where k is one, an integer where k is one. In order: -0.5, 3, 3.5,
  11. `1 + n.k` is the same.
* Read on: `k + 1` -0.5 + 1 = 0.5, 4, 4.5, 12; `k * 2` -1.0, 6, 7.0, 22; `toString(k)` '-0.5', '3', '3.5', '11'.
* `WHERE k > 3` keeps 3.5 and 11. `WITH k + 1 AS j`: 0.5, 4, 4.5, 12.
* Found with it: `k + 1` of a WITH column of arithmetic (`n.k - 1`, or now `n.k + 1`) was LIST_CONCAT's (a stage
  column may be a list), which intersystems-irispython 5.4.1 reads as text ('.5'): `WITH n.k - 1 AS k RETURN k, k +
  1` is (-2.5, -1.5), (1, 2), (1.5, 2.5), (9, 10).
* sum(k) = -0.5 + 3 + 3.5 + 11 = 17.0 (a float: some values are); min(k) = -0.5; max(k) = 11.
* `WITH k ORDER BY k RETURN collect(k)`: [-0.5, 3, 3.5, 11].
* Beside the property: (2.5, 3.5), (2, 3), (-1.5, -0.5), (10, 11).
* Found with it: `toString(n.k + 1)`, in a RETURN or a WITH, spelled -0.5 as IRIS does ('-.5'); openCypher's text is
  '-0.5' (as `toString(n.k - 2)` already answered), '3', '3.5', '11'.

Namespace: an IVG460F* scratch namespace; the labels are this file's own. Native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

M = "MATCH (n:A292) WITH n.k + 1 AS k "
CASES = [
    ("the column", M + "RETURN k ORDER BY k", [[-0.5], [3], [3.5], [11]]),
    ("the literal first", "MATCH (n:A292) WITH 1 + n.k AS k RETURN k ORDER BY k", [[-0.5], [3], [3.5], [11]]),
    ("read on", M + "RETURN k, k + 1 AS a, k * 2 AS b, toString(k) AS s ORDER BY k",
     [[-0.5, 0.5, -1.0, "-0.5"], [3, 4, 6, "3"], [3.5, 4.5, 7.0, "3.5"], [11, 12, 22, "11"]]),
    ("a WHERE on it", M + "WHERE k > 3 RETURN k ORDER BY k", [[3.5], [11]]),
    ("a second WITH adds again", M + "WITH k + 1 AS j RETURN j ORDER BY j", [[0.5], [4], [4.5], [12]]),
    ("a column of minus, plus one", "MATCH (n:A292) WITH n.k - 1 AS k RETURN k, k + 1 AS a ORDER BY k",
     [[-2.5, -1.5], [1, 2], [1.5, 2.5], [9, 10]]),
    ("aggregates", M + "RETURN sum(k), min(k), max(k)", [[17.0, -0.5, 11]]),
    ("collected in order", M + "WITH k ORDER BY k RETURN collect(k)", [[[-0.5, 3, 3.5, 11]]]),
    ("beside the property", "MATCH (n:A292) WITH n.k AS j, n.k + 1 AS k RETURN j, k ORDER BY j",
     [[-1.5, -0.5], [2, 3], [2.5, 3.5], [10, 11]]),
    ("toString of the sum in a RETURN", "MATCH (n:A292) RETURN n.k AS j, toString(n.k + 1) AS s ORDER BY j",
     [[-1.5, "-0.5"], [2, "3"], [2.5, "3.5"], [10, "11"]]),
    ("toString of the sum in a WITH", "MATCH (n:A292) WITH n.k AS j, toString(n.k + 1) AS s RETURN j, s ORDER BY j",
     [[-1.5, "-0.5"], [2, "3"], [2.5, "3.5"], [10, "11"]]),
]


def _typed(v):
    if isinstance(v, str) and v[:1] == "[":
        v = json.loads(v)
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    return [type(v).__name__, v]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    sql.execute_cypher("MATCH (n:A292) DETACH DELETE n", {}, raise_on_error=True)
    sql.execute_cypher("CREATE (:A292 {k: 2.5}), (:A292 {k: 2}), (:A292 {k: -1.5}), (:A292 {k: 10})", {},
                       raise_on_error=True)
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        sql.execute_cypher("MATCH (n:A292) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_with_sum_keeps_the_row_s_type(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert [[_typed(v) for v in r] for r in result.rows] == [[_typed(v) for v in r] for r in want]
