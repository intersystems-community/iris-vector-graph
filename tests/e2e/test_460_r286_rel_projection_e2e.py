"""4.6.0 R286 (the relationship projection items), live: a map projection of a relationship, and a key of
`properties()`, read the relationship's (or node's) properties.

Found by fixes40 on the release head, under intersystems-irispython 5.4.0 and 5.4.1: `RETURN r {.s, .u}` failed -29
(the projection read rdf_props by `e3.node_id`, a column an edge row does not have), and `RETURN properties(r).s`
failed -25 (`<properties() SQL>.s`: the key was glued onto the function's SQL text). Found with them: `properties(n).k`
of a node failed the same way.

Worked by hand. The graph: (:A286 {name: 'a'}) -[:T286 {s: 'x', u: 3, f: 2.5, b: true, e: ''}]-> (:A286 {name: 'b'}).
* `r {.s, .u}` is {s: 'x', u: 3}; `r {.f, .b}` {f: 2.5, b: true}; `r {.s, .missing}` {s: 'x', missing: null};
  `r {.e}` {e: ''} (R274: an empty string relationship property reads as '').
* `properties(r).s` is 'x', `.u` 3, `.f` 2.5, `.b` true, `.missing` null.
* Through `WITH r`: `r {.s, .u}` {s: 'x', u: 3} and `properties(r).u` 3.
* `properties(n).name` of the start node is 'a'; of a node without the key, null.
* No relationship in the match: `OPTIONAL MATCH (b:A286 {name: 'b'})-[r:T286]->() RETURN properties(r).s` is null.

Namespace: an IVG460F* scratch namespace; the labels and type are this file's own. Native on and off.
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

R = "MATCH (:A286 {name: 'a'})-[r:T286]->() "
CASES = [
    ("r {.s, .u}", R + "RETURN r {.s, .u}", [[{"s": "x", "u": 3}]]),
    ("r {.f, .b}", R + "RETURN r {.f, .b}", [[{"f": 2.5, "b": True}]]),
    ("a key r lacks", R + "RETURN r {.s, .missing}", [[{"s": "x", "missing": None}]]),
    ("an empty string", R + "RETURN r {.e}", [[{"e": ""}]]),
    ("properties(r).s", R + "RETURN properties(r).s", [["x"]]),
    ("properties(r).u, .f, .b", R + "RETURN properties(r).u, properties(r).f, properties(r).b", [[3, 2.5, True]]),
    ("properties(r).missing", R + "RETURN properties(r).missing", [[None]]),
    ("through WITH: r {.s, .u}", R + "WITH r RETURN r {.s, .u}", [[{"s": "x", "u": 3}]]),
    ("through WITH: properties(r).u", R + "WITH r RETURN properties(r).u", [[3]]),
    ("properties(n).name", "MATCH (n:A286 {name: 'a'}) RETURN properties(n).name", [["a"]]),
    ("properties(n) of a key it lacks", "MATCH (n:A286 {name: 'a'}) RETURN properties(n).zz", [[None]]),
    ("no relationship", "OPTIONAL MATCH (b:A286 {name: 'b'})-[r:T286]->() RETURN properties(r).s", [[None]]),
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
    sql.execute_cypher("MATCH (n:A286) DETACH DELETE n", {}, raise_on_error=True)
    sql.execute_cypher(
        "CREATE (:A286 {name: 'a'})-[:T286 {s: 'x', u: 3, f: 2.5, b: true, e: ''}]->(:A286 {name: 'b'})", {},
        raise_on_error=True,
    )
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        sql.execute_cypher("MATCH (n:A286) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


def _value(v):
    if isinstance(v, str) and v[:1] == "{":
        return json.loads(v)
    return v


def _typed(v):
    if isinstance(v, dict):
        return {k: _typed(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_typed(x) for x in v]
    return [type(v).__name__, v]


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_relationship_s_projection_reads_its_properties(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert _typed([[_value(v) for v in r] for r in result.rows]) == _typed(want)
