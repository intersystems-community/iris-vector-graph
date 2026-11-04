"""4.6.0 R273 (the USE GRAPH case), live: a CREATE after a SET and a WITH writes in a named graph too.

Found by fixes35 (R259's leftover): `USE GRAPH 'g' MATCH (n {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H {v: k})`
wrote nothing, where the same query without USE GRAPH writes H {v: 5} (R241, R259). Each statement after the WITH
reads the stage again; R241 has the stage read the rows the SET captured, without the MATCH's tests on the keys
the query writes. Under USE GRAPH those tests carry the graph inline, so they were not recognised and stayed: the
stage tested k = 1 after the SET had made it 5, and kept no row.

Worked by hand, graph r273s with one Cypher-written (:S {k: 1}), each query on the last's result:
* SET n.k = 5 then CREATE (:H {v: k}) writes H.v = 5, an integer;
* SET n.k = 2.5 then CREATE (:H2 {v: k}): H2.v = 2.5;
* SET n.k = 3, `WITH n, n.k AS k WHERE k = 3` then CREATE (:H3 {v: k}): H3.v = 3;
* SET n.k = 4 then CREATE (m:H4 {v: k}) RETURN m.v, k: the row 4, 4.
Control: the first query in the default graph (label S273d) writes H273d.v = 5.

Namespace: an IVG460F* scratch namespace. Native on and off (writes go through SQL either way).
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

G = "r273s"
U = f"USE GRAPH '{G}' "


@pytest.fixture(params=["native", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=request.param == "native")
    eng.initialize_schema()
    eng._native_privileges_cached = True
    eng.native_route_ms = 0
    eng.erase_graph(G)
    eng.execute_cypher("MATCH (n) WHERE n:S273d OR n:H273d DETACH DELETE n", {}, raise_on_error=True)
    conn.commit()
    try:
        yield eng
    finally:
        eng.erase_graph(G)
        eng.execute_cypher("MATCH (n) WHERE n:S273d OR n:H273d DETACH DELETE n", {}, raise_on_error=True)
        conn.commit()
        conn.close()


def _run(eng, q):
    r = eng.execute_cypher(q, {}, raise_on_error=True)
    assert getattr(r, "error", None) is None, r.error
    eng.conn.commit()
    return [list(row) for row in r.rows]


def _typed(rows):
    return [[(type(v).__name__, v) for v in r] for r in rows]


def test_a_create_after_a_set_and_a_with_writes_in_a_named_graph(engine):
    _run(engine, U + "CREATE (:S {k: 1})")
    _run(engine, U + "MATCH (n {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H {v: k})")
    assert _typed(_run(engine, U + "MATCH (h:H) RETURN h.v")) == [[("int", 5)]]
    assert _typed(_run(engine, U + "MATCH (n:S) RETURN n.k")) == [[("int", 5)]]
    _run(engine, U + "MATCH (n:S {k: 5}) SET n.k = 2.5 WITH n, n.k AS k CREATE (:H2 {v: k})")
    assert _typed(_run(engine, U + "MATCH (h:H2) RETURN h.v")) == [[("float", 2.5)]]
    _run(engine, U + "MATCH (n:S {k: 2.5}) SET n.k = 3 WITH n, n.k AS k WHERE k = 3 CREATE (:H3 {v: k})")
    assert _typed(_run(engine, U + "MATCH (h:H3) RETURN h.v")) == [[("int", 3)]]
    rows = _run(engine, U + "MATCH (n:S {k: 3}) SET n.k = 4 WITH n, n.k AS k CREATE (m:H4 {v: k}) RETURN m.v, k")
    assert _typed(rows) == [[("int", 4), ("int", 4)]]
    # one node each, in the graph
    assert _run(engine, U + "MATCH (h) WHERE h:H OR h:H2 OR h:H3 OR h:H4 RETURN count(h)") == [[4]]


def test_the_default_graph_control(engine):
    _run(engine, "CREATE (:S273d {k: 1})")
    _run(engine, "MATCH (n:S273d {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:H273d {v: k})")
    assert _typed(_run(engine, "MATCH (h:H273d) RETURN h.v")) == [[("int", 5)]]
