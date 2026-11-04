"""4.6.0 R259 (the cases R248 left), live: values keep their stored type, and integer division truncates.

Found by fixes28, measured again on the release head (Community 2026.1), under both driver versions:
- `coalesce()` of a stored number answered its text (`coalesce(n.f, $k)` of 1.25: `'1.25'`; R239's strict xfail);
- `MATCH (n {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H {v: k})` stored the text '5' (a CAST to VARCHAR);
- `RETURN n.g` of a stored whole float answered 5 in some shapes (native) and 5.0 in others;
- static integer division FLOORed (-7/2 = -4) where openCypher, and the stored-value path, truncate (-3).

Graph r259g, written by Cypher: (:L {name: 'A', g: 5.0, f: 1.25, i: 5, t: true}), (:L {name: 'B', g: 2.0}). By hand,
with k = 2:
- coalesce(n.f, $k): A 1.25, B 2; coalesce(n.g, 0): 5.0, 2.0; coalesce(n.i, 0): 5, 0; coalesce(n.t, false): true,
  false; coalesce(n.nope, 2): 2, 2;
- A's g is the float 5.0 in every shape, toString() '5.0';
- -7 / 2 = -3, 7 / -2 = -3, -7 / -2 = 3, 7 / 2 = 3, -7 % 2 = -1 (the dividend's sign), toString(-7 / 2) = '-3'.
The CREATE after a SET runs in the default graph under labels of its own (in a named graph it writes nothing, a
defect of its own, not this one's): the SET makes A's k 5 and the CREATE writes v = 5, an integer; a SET of 2.5 the
float 2.5.

Namespace: `IVG_NAMESPACE`, an IVG460F* scratch namespace with this checkout's routines and classes. Run it under
intersystems-irispython 5.4.0 (the repo .venv) and 5.4.1:

    IVG_NAMESPACE=IVG460FX35 IVG_TEST_CONTAINER=fixes35ce-iris-1 IVG_PORT=42972 \
        pytest tests/e2e/test_460_r259_typed_values_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r259g"
U = f"USE GRAPH '{G}' "

#: (name, query, rows by hand)
CASES = [
    ("R259: coalesce() of stored values and literals",
     U + "MATCH (n:L) RETURN n.name, coalesce(n.f, $k), coalesce(n.g, 0), coalesce(n.i, 0), coalesce(n.t, false), "
         "coalesce(n.nope, 2) ORDER BY n.name",
     [["A", 1.25, 5.0, 5, True, 2], ["B", 2, 2.0, 0, False, 2]]),
    ("coalesce() of a stored float, no ORDER BY",
     U + "MATCH (n:L {name: 'A'}) RETURN coalesce(n.f, $k)", [[1.25]]),
    ("R259: a whole float, bare", U + "MATCH (n:L {name: 'A'}) RETURN n.g", [[5.0]]),
    ("a whole float, aliased beside a string", U + "MATCH (n:L {name: 'A'}) RETURN n.g AS x, n.name", [[5.0, "A"]]),
    ("a whole float, DISTINCT", U + "MATCH (n:L {name: 'A'}) RETURN DISTINCT n.g", [[5.0]]),
    ("a whole float, ORDER BY and LIMIT", U + "MATCH (n:L) RETURN n.g ORDER BY n.g DESC LIMIT 1", [[5.0]]),
    ("a whole float, through WITH n", U + "MATCH (n:L {name: 'A'}) WITH n RETURN n.g", [[5.0]]),
    ("a whole float, a WITH column", U + "MATCH (n:L {name: 'A'}) WITH n.g AS g RETURN g", [[5.0]]),
    ("a whole float, a grouping key", U + "MATCH (n:L) RETURN n.g, count(*) ORDER BY n.g", [[2.0, 1], [5.0, 1]]),
    ("a whole float, max()", U + "MATCH (n:L) RETURN max(n.g)", [[5.0]]),
    ("a whole float, times 1 and toString()", U + "MATCH (n:L {name: 'A'}) RETURN n.g * 1, toString(n.g)",
     [[5.0, "5.0"]]),
    ("R259: static integer division truncates",
     "RETURN -7 / 2, 7 / -2, -7 / -2, 7 / 2, -7 % 2, toString(-7 / 2)", [[-3, -3, 3, 3, -1, "-3"]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (:L {name: 'A', g: 5.0, f: 1.25, i: 5, t: true}), (:L {name: 'B', g: 2.0})", {},
                       raise_on_error=True)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield conn, {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _rows(engine, query, params=None):
    result = engine.execute_cypher(query, {"k": 2, **dict(params or {})}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return [list(r) for r in result.rows]


def _typed(rows):
    """Each value with its type: 5 is not 5.0, 1 is not True, '5' is not 5."""
    return [[(type(v).__name__, v) for v in row] for row in rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_the_value_keeps_its_type(engines, path, name, query, want):
    assert _typed(_rows(engines[1][path], query)) == _typed(want)


@pytest.mark.parametrize(
    "set_value,want",
    [("5", [[5]]), ("2.5", [[2.5]]), ("'x'", [["x"]])],
    ids=["integer", "float", "string"],
)
def test_a_create_after_a_set_writes_the_with_column_s_value_with_its_type(engines, set_value, want):
    conn, eng = engines
    run = uuid.uuid4().hex[:8]
    src, made = f"S{run}", f"H{run}"
    e = eng["sql"]
    try:
        e.execute_cypher(f"CREATE (:{src} {{k: 1}})", {}, raise_on_error=True)
        e.execute_cypher(f"MATCH (n:{src} {{k: 1}}) SET n.k = {set_value} WITH n, n.k AS k CREATE (:{made} {{v: k}})",
                         {}, raise_on_error=True)
        conn.commit()
        for path in ("native", "sql"):
            assert _typed(_rows(eng[path], f"MATCH (h:{made}) RETURN h.v")) == _typed(want), path
    finally:
        e.execute_cypher(f"MATCH (n:{src}) DETACH DELETE n", {}, raise_on_error=True)
        e.execute_cypher(f"MATCH (n:{made}) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
