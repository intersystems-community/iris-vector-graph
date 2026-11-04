"""4.6.0 R274 (^NKG leftovers), live: a named graph's nodes left in ^NKG after its edges are deleted do not answer
for the default graph.

^NKG is one dictionary of every graph's nodes with no graph key (Graph.KG.TraversalBuild). R184 lets an algorithm read
it for the default graph only while no named graph has an edge. Found by fixes38: once a named graph's last edge was
deleted, ^NKG still held that graph's interned nodes, and `CALL ivg.scc()` and `CALL ivg.closeness()` listed them
with the default graph's.

^NKG is built here only by the engine's sync() and changed only by Graph.KG.GraphIndex (DropNKG) and the engine's
writes; nothing kills it.

Worked by hand: default graph x -R-> y, y -R-> x; graph g274 p -R-> q; sync(); then g274's edge deleted:
* the default graph's SCC is {x, y}, one component of size 2; CALL ivg.scc() answers x and y;
* its harmonic closeness: x reaches y at distance 1 and y reaches x, so each scores 1.0; CALL ivg.closeness()
  answers x and y.

Namespace: an IVG460FX*/IVG460FY* scratch namespace, emptied first (Graph.KG.Eraser.EraseAll).
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith(("IVG460FX", "IVG460FY")),
                       reason="IVG_NAMESPACE must name an IVG460FX*/IVG460FY* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    native = iris.createIRIS(conn)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    eng.native = native
    try:
        yield eng
    finally:
        native.classMethodValue("Graph.KG.Eraser", "EraseAll")
        conn.commit()
        conn.close()


def test_a_named_graph_s_leftover_nodes_do_not_answer_for_the_default_graph(engine):
    engine.create_edge("x", "R", "y")
    engine.create_edge("y", "R", "x")
    engine.create_edge("p", "R", "q", graph="g274")
    engine.conn.commit()
    assert engine.sync()
    engine.execute_cypher("USE GRAPH 'g274' MATCH ()-[r]->() DELETE r", {}, raise_on_error=True)
    engine.conn.commit()
    assert engine._named_graph_with_edges() is None
    for q in ("CALL ivg.scc() YIELD node RETURN node", "CALL ivg.closeness() YIELD node RETURN node"):
        r = engine.execute_cypher(q, {}, raise_on_error=True)
        assert getattr(r, "error", None) is None, r.error
        assert sorted(str(row[0]) for row in r.rows) == ["x", "y"], q
    scc = engine.strongly_connected_components()
    assert sorted((c["id"], c["size"]) for c in scc) == [("x", 2), ("y", 2)]
    closeness = engine.closeness_centrality()
    assert sorted((c["id"], round(c["score"], 6)) for c in closeness) == [("x", 1.0), ("y", 1.0)]

