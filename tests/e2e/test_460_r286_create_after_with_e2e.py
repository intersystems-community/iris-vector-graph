"""4.6.0 R286 (the CREATE item), live: a CREATE after a WITH that carries a node writes the node's property, once per
row, with its type.

Found by fixes40 on the release head, under intersystems-irispython 5.4.0 and 5.4.1: `MATCH (n) WITH n CREATE (:H {v:
n.k})` failed "<ARGUMENT ERROR> Unsupported argument type" and wrote nothing, in any graph. R120 runs a CREATE after a
WITH once per row when the WITH carries values (`WITH n.k AS k CREATE (:H {v: k})`), and left a WITH carrying a node
to the static statements, which have one node id for every row and bound `n.k` (its AST) as a parameter.

Worked by hand. K286 nodes: {k: 7}, {k: 2.5}, {k: '7'}, {k: true}, {name: 'nok'} (no k).
* `MATCH (n:K286) WITH n CREATE (:H286 {v: n.k})` runs once per row: five H286 nodes, four with v 7 (an integer), 2.5
  (a float), '7' (a string), true, and one with no v (null is no property).
* With `WITH n WHERE n.k IS NOT NULL`: four nodes, the same four values.
* Without the WITH, `MATCH (n:K286) CREATE (:H286 {v: n.k})` (the per-row statements, R189) writes the same five
  nodes and values. Found with the item: it wrote each value as text ('7' for 7, '2.5' for 2.5).
* N286 nodes {k: 3}, {k: 1.5}: `CREATE (:H286 {v: n.k * 2})` writes 6 and 3.0; `CREATE (h:H286 {v: n.k}) RETURN h.v`
  answers 3 and 1.5.
* L286 {tags: ['a', 'b']}: `CREATE (:H286 {v: n.tags})` writes the list ['a', 'b'].
* Under USE GRAPH 'g286', which holds G286 {k: 1}, {k: 2}: two H286 nodes, v 1 and 2, both in g286 (their
  rdf_labels rows carry graph_id 'g286'; no H286 row has another graph).

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

LABELS = ("K286", "N286", "L286", "G286", "H286")


def _typed(v):
    if isinstance(v, str) and v[:1] == "[":
        v = json.loads(v)
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    return [type(v).__name__, v]


def _key(t):
    return json.dumps(t, sort_keys=True)


def _values(eng, graph=None):
    use = f"USE GRAPH '{graph}' " if graph else ""
    r = eng.execute_cypher(use + "MATCH (h:H286) RETURN h.v", {}, raise_on_error=True)
    return sorted((_typed(row[0]) for row in r.rows), key=_key)


def _want(values):
    return sorted((_typed(v) for v in values), key=_key)


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def engines(conn):
    from iris_vector_graph import IRISGraphEngine

    out = {}
    for path in ("native", "sql"):
        e = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=path == "native")
        e._native_privileges_cached = True
        e.native_route_ms = 0
        out[path] = e
    out["native"].initialize_schema()
    return out


@pytest.fixture(params=["native", "sql"])
def eng(request, engines, conn):
    e = engines[request.param]
    e.erase_graph("g286")
    for label in LABELS:
        e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)
    e.execute_cypher(
        "CREATE (:K286 {k: 7}), (:K286 {k: 2.5}), (:K286 {k: '7'}), (:K286 {k: true}), (:K286 {name: 'nok'}), "
        "(:N286 {k: 3}), (:N286 {k: 1.5}), (:L286 {tags: ['a', 'b']})", {}, raise_on_error=True,
    )
    e.execute_cypher("USE GRAPH 'g286' CREATE (:G286 {k: 1}), (:G286 {k: 2})", {}, raise_on_error=True)
    conn.commit()
    try:
        yield e
    finally:
        e.erase_graph("g286")
        for label in LABELS:
            e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()


def _count(eng, graph=None):
    use = f"USE GRAPH '{graph}' " if graph else ""
    return eng.execute_cypher(use + "MATCH (h:H286) RETURN count(h)", {}, raise_on_error=True).rows[0][0]


def test_a_node_s_property_is_written_once_per_row(eng):
    eng.execute_cypher("MATCH (n:K286) WITH n CREATE (:H286 {v: n.k})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _count(eng) == 5
    assert _values(eng) == _want([7, 2.5, "7", True, None])


def test_without_the_with_the_copy_keeps_its_type(eng):
    eng.execute_cypher("MATCH (n:K286) CREATE (:H286 {v: n.k})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _count(eng) == 5
    assert _values(eng) == _want([7, 2.5, "7", True, None])


def test_a_with_s_where_keeps_its_rows(eng):
    eng.execute_cypher("MATCH (n:K286) WITH n WHERE n.k IS NOT NULL CREATE (:H286 {v: n.k})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _values(eng) == _want([7, 2.5, "7", True])


def test_arithmetic_over_the_property(eng):
    eng.execute_cypher("MATCH (n:N286) WITH n CREATE (:H286 {v: n.k * 2})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _values(eng) == _want([6, 3.0])


def test_a_return_of_the_created_node(eng):
    r = eng.execute_cypher("MATCH (n:N286) WITH n CREATE (h:H286 {v: n.k}) RETURN h.v", {}, raise_on_error=True)
    eng.conn.commit()
    assert sorted((_typed(row[0]) for row in r.rows), key=_key) == _want([3, 1.5])
    assert _values(eng) == _want([3, 1.5])


def test_a_list_property(eng):
    eng.execute_cypher("MATCH (n:L286) WITH n CREATE (:H286 {v: n.tags})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _values(eng) == _want([["a", "b"]])


def test_under_use_graph(eng):
    eng.execute_cypher("USE GRAPH 'g286' MATCH (n:G286) WITH n CREATE (:H286 {v: n.k})", {}, raise_on_error=True)
    eng.conn.commit()
    assert _values(eng, "g286") == _want([1, 2])
    cur = eng.conn.cursor()
    cur.execute("SELECT COALESCE(graph_id, ''), COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'H286' "
                "GROUP BY COALESCE(graph_id, '')")
    assert [tuple(r) for r in cur.fetchall()] == [("g286", 2)]
    cur.close()
