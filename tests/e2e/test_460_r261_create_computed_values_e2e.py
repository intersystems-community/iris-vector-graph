"""4.6.0 R261, R263 (the CREATE case), live: a CREATE writes a computed property value with its type.

`UNWIND [1,2] AS i CREATE (:X {f: i * 0.5})` (or `{s: toString(i)}`) and `CREATE (:L {d: 2.5*2})` raised
`<LIST ERROR>` on 2026.1 and `<ARGUMENT ERROR>` on 2026.3 and wrote nothing (`{k: i}` worked): the expression's AST
node was bound as the value. Each case here writes into graph r261g, reads the nodes back on the native and the SQL
path, and erases the graph.

By hand: i * 0.5 for i = 1, 2 is 0.5 and 1.0 (a float, whole or not); toString(i) is '1', '2'; 2.5 * 2 is 5.0;
i / 2 is 0 and 1 (integer division); -7 / 2 is -3 (truncated toward zero). toString() of the stored float 1.0 is
'1.0' and of 5.0 '5.0', which only a stored float answers (an integer 1 answers '1').

Namespace: `IVG_NAMESPACE`, an IVG460F* scratch namespace with this checkout's routines (the fixture runs
`initialize_schema()`). Run it under intersystems-irispython 5.4.0 (the repo .venv) and 5.4.1:

    IVG_NAMESPACE=IVG460FX35 IVG_TEST_CONTAINER=fixes35ce-iris-1 IVG_PORT=42972 \
        pytest tests/e2e/test_460_r261_create_computed_values_e2e.py
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

G = "r261g"

#: (name, the write, the read, rows by hand)
CASES = [
    ("R261: i * 0.5 over an UNWIND",
     "UNWIND [1, 2] AS i CREATE (:X {k: i, f: i * 0.5})",
     "MATCH (x:X) RETURN x.k, x.f, toString(x.f) ORDER BY x.k", [[1, 0.5, "0.5"], [2, 1.0, "1.0"]]),
    ("R261: toString(i) over an UNWIND",
     "UNWIND [1, 2] AS i CREATE (:X {k: i, s: toString(i)})",
     "MATCH (x:X) RETURN x.k, x.s ORDER BY x.k", [[1, "1"], [2, "2"]]),
    ("R263: 2.5 * 2 in a CREATE",
     "CREATE (:X {k: 1, d: 2.5 * 2})",
     "MATCH (x:X) RETURN x.d, toString(x.d)", [[5.0, "5.0"]]),
    ("integer division over an UNWIND, and a static negative one",
     "UNWIND [1, 2] AS i CREATE (:X {k: i, q: i / 2, r: -7 / 2})",
     "MATCH (x:X) RETURN x.k, x.q, x.r ORDER BY x.k", [[1, 0, -3], [2, 1, -3]]),
    ("a parameter list unwound",
     "UNWIND $xs AS i CREATE (:X {k: i, f: i * 0.5})",
     "MATCH (x:X) RETURN x.k, x.f, toString(x.f) ORDER BY x.k", [[1, 0.5, "0.5"], [2, 1.0, "1.0"]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield conn, {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _typed(rows):
    """Each value with its type: 1 is not 1.0, '1' is not 1."""
    return [[(type(v).__name__, v) for v in row] for row in rows]


@pytest.mark.parametrize("name,write,read,want", CASES, ids=[c[0] for c in CASES])
def test_a_create_writes_the_computed_value_with_its_type(engines, name, write, read, want):
    conn, eng = engines
    eng["sql"].erase_graph(G)
    eng["sql"].execute_cypher(f"USE GRAPH '{G}' {write}", {"xs": [1, 2]}, raise_on_error=True)
    conn.commit()
    for path in ("native", "sql"):
        result = eng[path].execute_cypher(f"USE GRAPH '{G}' {read}", {}, raise_on_error=True)
        assert result.error is None, (path, result.error)
        assert _typed([list(r) for r in result.rows]) == _typed(want), path
