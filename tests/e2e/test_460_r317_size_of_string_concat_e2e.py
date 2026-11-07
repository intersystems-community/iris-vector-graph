"""4.6.0 R317, live: size() of a string built with `+` answers its length; of a list built with `+`, its elements.

By hand: 'ab' + 'c' is 'abc' (3); a node with name 'Ann' gives size(n.name + 'x') = 4; [1, 2] + [3] has 3 elements;
tags ['a', 'b'] + ['x'] has 3. On b5cf1ac4 each string case failed with "GRAPH_KG.JSON_ARRAYLENGTH ... SQLCODE=-400".

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    eng.execute_cypher("MATCH (n:R317) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher("CREATE (:R317 {id: 'r317a', name: 'Ann', tags: ['a', 'b']})", raise_on_error=True)
    yield eng
    eng.execute_cypher("MATCH (n:R317) DETACH DELETE n", raise_on_error=True)
    conn.close()


@pytest.mark.parametrize(
    "q, params, expected",
    [
        ("RETURN size('ab' + 'c') AS s", {}, 3),
        ("RETURN size($s + 'c') AS s", {"s": "ab"}, 3),
        ("WITH 'ab' AS s RETURN size(s + 'c') AS s", {}, 3),
        ("MATCH (n:R317) RETURN size(n.name + 'x') AS s", {}, 4),
        ("RETURN size([1, 2] + [3]) AS s", {}, 3),
        ("MATCH (n:R317) RETURN size(n.tags + ['x']) AS s", {}, 3),
    ],
)
def test_size_of_a_concatenation(engine, q, params, expected):
    r = engine.execute_cypher(q, params, raise_on_error=True)
    assert r.rows == [[expected]], (q, r.rows, r.error)
