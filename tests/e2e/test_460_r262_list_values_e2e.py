"""4.6.0 R262, R267 (the list cases), live: a value keeps its type inside a list, and x[-1] of a row list reads.

Found by fixes32/fixes33, on the release head under both driver versions:
- a parameter whose only element is a list was flattened by JSON_TABLE (`UNWIND $p AS x` with p = [[1, 2]] answered
  the rows 1 and 2; `[y IN $p | size(y)]` [1, 1]);
- a boolean relationship property (create_edge's JSON true) read as 1 in a list;
- `[x IN ms | x]` over maps or lists listed strings, `[x IN [true] | x]` ["1"];
- a float inside a list lost its point (`WITH 1.0 AS f RETURN [f]` [1]), a stored 5.0 too (`[n.g]`, `collect(n.g)`);
- a stored true read "true" in a list (`collect(n.t)`, `last(collect(n.t))`);
- `x[-1]` of a row list failed the statement with <RANGE> (-149) in a WITH or an operand.

A list in a result row is JSON text (USER_GUIDE); each answer here is compared as its parsed JSON, with types.

Graph r262g: (:L {name: 'A', g: 5.0, t: true}) and (:L {name: 'B', g: 2.0}) written by Cypher, and a relationship
A-[:R]->B written by create_edge with qualifiers {flag: True}. Worked by hand, p = [[1, 2]]:
- UNWIND $p is one row, [1, 2]; [y IN $p | size(y)] is [2];
- [r.flag], collect(r.flag), [x IN [r.flag] | x]: [true];
- [x IN [{a: 1}, {a: 2}] | x] is the two maps, [x IN [[1], [2]] | x] the two lists, [x IN [true] | x] [true];
- [f] for f = 1.0 is [1.0], collect() of UNWIND [1.0, 2.5] [1.0, 2.5], [toFloat(1)] [1.0];
- [n.g] and collect(n.g) of A are [5.0]; over A and B in name order [5.0, 2.0];
- [n.t] of A [true], collect(n.t) over A and B [true] (B has none), last() of it true;
- of x = [1, 2, 3]: x[-1] is 3, x[-1] * 2 is 6, x[-4] null; through WITH x[-1] is 3, and x[-1] + 1 is 4.

Namespace: `IVG_NAMESPACE`, an IVG460F* scratch namespace with this checkout's routines and classes. Run it under
intersystems-irispython 5.4.0 (the repo .venv) and 5.4.1:

    IVG_NAMESPACE=IVG460FX35 IVG_TEST_CONTAINER=fixes35ce-iris-1 IVG_PORT=42972 \
        pytest tests/e2e/test_460_r262_list_values_e2e.py
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

G = "r262g"
U = f"USE GRAPH '{G}' "
X = "UNWIND [1, 2, 3] AS v WITH collect(v) AS x "

#: (name, query, rows by hand: a list is its parsed JSON)
CASES = [
    ("R262: UNWIND of a parameter holding one list", "UNWIND $p AS x RETURN x", [[[1, 2]]]),
    ("R262: a comprehension over a parameter holding one list", "RETURN [y IN $p | size(y)]", [[[2]]]),
    ("a parameter holding two lists", "UNWIND $q AS x RETURN x", [[[1, 2]], [[3]]]),
    ("R262: a boolean relationship property in a list",
     U + "MATCH ()-[r:R]->() RETURN [r.flag], collect(r.flag), [x IN [r.flag] | x]", [[[True], [True], [True]]]),
    ("R262: [x IN ms | x] over maps", "WITH [{a: 1}, {a: 2}] AS ms RETURN [x IN ms | x]", [[[{"a": 1}, {"a": 2}]]]),
    ("[x IN ls | x] over lists", "UNWIND [[[1], [2]]] AS ls RETURN [x IN ls | x]", [[[[1], [2]]]]),
    ("R262: [x IN [true] | x]", "RETURN [x IN [true] | x], [x IN [true, false] | x]", [[[True], [True, False]]]),
    ("R262: 1.0 inside a list", "WITH 1.0 AS f RETURN [f], [toFloat(1)]", [[[1.0], [1.0]]]),
    ("1.0 collected from an UNWIND", "UNWIND [1.0, 2.5] AS x RETURN collect(x)", [[[1.0, 2.5]]]),
    ("a stored whole float in a list", U + "MATCH (n:L {name: 'A'}) RETURN [n.g], collect(n.g)", [[[5.0], [5.0]]]),
    ("stored whole floats collected", U + "MATCH (n:L) WITH n ORDER BY n.name RETURN collect(n.g)",
     [[[5.0, 2.0]]]),
    ("R267: a stored true in a list", U + "MATCH (n:L {name: 'A'}) RETURN [n.t], collect(n.t)", [[[True], [True]]]),
    ("R267: a stored true collected, and its last()",
     U + "MATCH (n:L) WITH collect(n.t) AS ts RETURN ts, last(ts)", [[[True], True]]),
    ("R267: x[-1] of a row list", X + "RETURN x[-1], x[-1] * 2, x[-4]", [[3, 6, None]]),
]

#: x[-1] no longer fails (the R267 defect); an element read through a WITH was JSON_ARRAYGET's text ('3'), as
#: x[0]'s was, and one added to was text under intersystems-irispython 5.4.1 ('4'), until 4.6.0 R273
ELEMENT_TEXT = [
    pytest.param("x[-1] through WITH", X + "WITH x[-1] AS y RETURN y", [[3]], id="x[-1] through WITH"),
    pytest.param("x[-1] + 1", X + "RETURN x[-1] + 1", [[4]], id="x[-1] + 1"),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (:L {id: 'r262a', name: 'A', g: 5.0, t: true}), (:L {id: 'r262b', name: 'B', g: 2.0})",
                       {}, raise_on_error=True)
    nat.create_edge("r262a", "R", "r262b", qualifiers={"flag": True}, graph=G)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _value(v):
    """A list or map in a row is JSON text: its parsed value."""
    if isinstance(v, str) and v[:1] in "[{":
        return json.loads(v)
    return v


def _typed(v):
    """`v` with every value's type: 5 is not 5.0, 1 is not True, '1' is not 1."""
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    if isinstance(v, dict):
        return ["map", {k: _typed(x) for k, x in v.items()}]
    return [type(v).__name__, v]


def _rows(engine, query):
    result = engine.execute_cypher(query, {"p": [[1, 2]], "q": [[1, 2], [3]]}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    return [[_value(v) for v in r] for r in result.rows]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_list_holds_its_values_with_their_types(engines, path, name, query, want):
    assert _typed(_rows(engines[path], query)) == _typed(want)


# found with R267, fixed by R273: a row list's element read through a WITH or added to (x[0]'s too)
@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", ELEMENT_TEXT)
def test_an_element_through_with_or_an_operand_keeps_its_type(engines, path, name, query, want):
    assert _typed(_rows(engines[path], query)) == _typed(want)
