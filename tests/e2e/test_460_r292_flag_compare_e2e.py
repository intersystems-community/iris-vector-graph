"""4.6.0 R292 (the comparison items), live: an equality reads a row value's boolean flag, and a whole float equals a
row value of the same number.

Found by fixes43 on the release head, under intersystems-irispython 5.4.0 and 5.4.1:
* after `UNWIND [true, 1] AS v`, `v = true` and `v = 1` were both true on both rows: the UNWIND row holds true as IRIS's
  1 and a flag beside it (R273), which the comparison did not read;
* a row list's boolean through a WITH (`WITH ['true', true] AS l WITH l[1] AS b`) compared `b = true` false: CY_LIST_VAL
  holds the boolean as the text 'true' and its flag says so (R286); the comparison was `b = 1`;
* `WITH [1.0, 2] AS l WITH l[0] AS x RETURN x = 1.0` was false: the element is a $DOUBLE whose text is '1', and IRIS
  compared that text with the literal's, '1.0'. Found with it: `UNWIND [1.0, 2] AS x RETURN x = 1.0` and, with
  `WITH n.k AS k` of a stored 2, `k = 2.0` were false too.

Worked by hand, openCypher's equality: a boolean equals only a boolean, a string only a string, and numbers by value
(1 = 1.0); null against anything is null.
* `UNWIND [true, 1] AS v`: `v = true` true then false, `v = 1` false then true; `<>` the opposite; the same after
  `WITH v` and `WITH v AS w`, and against parameters $t = true and $one = 1. `WHERE v = true` keeps the first row,
  `WHERE v = 1` (or `w = 1`, renamed) the second. With a null row: null, so `WHERE v <> true` keeps only 1.
* `UNWIND [false, 0] AS v`: `v = false` true then false, `v = 0` false then true.
* `UNWIND ['true', true] AS v`: `v = 'true'` true then false, `v = true` false then true.
* `WITH ['true', true] AS l WITH l[0] AS s, l[1] AS b`: `s = 'true'` true, `b = true` true, `s = true` false,
  `b = 'true'` false.
* `WITH [true, 1, false] AS l WITH l[0] AS b, l[1] AS i, l[2] AS f`: `b = true` true, `b = 1` false, `i = 1` true,
  `i = true` false, `f = false` true, `f = 0` false.
* `WITH [1.0, 2] AS l WITH l[0] AS x, l[1] AS y`: `x = 1.0` true, `x = 1` true, `x <> 1.0` false, `y = 2.0` true,
  `x = 1.5` false. `WITH [1.5, 2]`: `x = 1.5` true. Carried on, `WITH x WHERE x = 1.0` keeps the row: [[1.0]].
* `UNWIND [1.0, 2] AS x`: `x = 1.0` true then false, `x = 2.0` false then true.
* X292 nodes {k: 2}, {k: 2.5}: `WITH n.k AS k RETURN k, k = 2.0, k = 2.5` in order: (2, true, false), (2.5, false,
  true).

Namespace: an IVG460F* scratch namespace; the labels are this file's own. Native on and off.
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

T, F = True, False
U = "UNWIND [true, 1] AS v "
CASES = [
    ("an UNWIND row", U + "RETURN v = true, v = 1", [[T, F], [F, T]]),
    ("an UNWIND row, <>", U + "RETURN v <> true, v <> 1", [[F, T], [T, F]]),
    ("through a WITH", U + "WITH v RETURN v = true, v = 1", [[T, F], [F, T]]),
    ("renamed", U + "WITH v AS w RETURN w = true, w = 1", [[T, F], [F, T]]),
    ("parameters", U + "RETURN v = $t, v = $one", [[T, F], [F, T]]),
    ("a WHERE on true", U + "WITH v WHERE v = true RETURN v", [[T]]),
    ("a WHERE on 1", U + "WITH v WHERE v = 1 RETURN v", [[1]]),
    ("a WHERE on a renamed row", U + "WITH v AS w WHERE w = 1 RETURN w", [[1]]),
    ("a WHERE on <>", "UNWIND [true, 1, null] AS v WITH v WHERE v <> true RETURN v", [[1]]),
    ("a WHERE on a whole float element", "WITH [1.0, 2] AS l WITH l[0] AS x WITH x WHERE x = 1.0 RETURN x",
     [[1.0]]),
    ("a null row", "UNWIND [true, 1, null] AS v RETURN v = true", [[T], [F], [None]]),
    ("false and 0", "UNWIND [false, 0] AS v RETURN v = false, v = 0", [[T, F], [F, T]]),
    ("the string 'true'", "UNWIND ['true', true] AS v RETURN v = 'true', v = true", [[T, F], [F, T]]),
    ("a row list's elements", "WITH ['true', true] AS l WITH l[0] AS s, l[1] AS b "
     "RETURN s = 'true', b = true, s = true, b = 'true'", [[T, T, F, F]]),
    ("a row list's boolean and numbers", "WITH [true, 1, false] AS l WITH l[0] AS b, l[1] AS i, l[2] AS f "
     "RETURN b = true, b = 1, i = 1, i = true, f = false, f = 0", [[T, F, T, F, T, F]]),
    ("a whole float element", "WITH [1.0, 2] AS l WITH l[0] AS x, l[1] AS y "
     "RETURN x = 1.0, x = 1, x <> 1.0, y = 2.0, x = 1.5", [[T, T, F, T, F]]),
    ("a fraction element (control)", "WITH [1.5, 2] AS l WITH l[0] AS x RETURN x = 1.5", [[T]]),
    ("an UNWIND of numbers", "UNWIND [1.0, 2] AS x RETURN x = 1.0, x = 2.0", [[T, F], [F, T]]),
    ("a WITH column of a property", "MATCH (n:X292) WITH n.k AS k RETURN k, k = 2.0, k = 2.5 ORDER BY k",
     [[2, T, F], [2.5, F, T]]),
]


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
    sql.execute_cypher("MATCH (n:X292) DETACH DELETE n", {}, raise_on_error=True)
    sql.execute_cypher("CREATE (:X292 {k: 2}), (:X292 {k: 2.5})", {}, raise_on_error=True)
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        sql.execute_cypher("MATCH (n:X292) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_an_equality_reads_the_row_s_type(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {"t": True, "one": 1}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    got = [[(type(v).__name__, v) for v in r] for r in result.rows]
    assert got == [[(type(v).__name__, v) for v in r] for r in want]
