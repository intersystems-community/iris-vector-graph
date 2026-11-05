"""4.6.0 R286 (the ^NKG item), live: with the dictionary kept, a named graph's nodes left in ^NKG after its edges are
deleted still do not answer for the default graph.

R274 made a rebuild after a named graph's last edge drop that graph's nodes from ^NKG's dictionary (the node sweep).
Found by fixes40: TraversalBuild.DictSweepAllowed keeps the dictionary (no index gaps) where an Arno library is
configured that does not declare the "nkg_dict_gaps" capability, or cannot be asked, and there the leftover nodes
stayed: `CALL ivg.scc()`, `CALL ivg.closeness()` and `CALL ivg.eigenvector()` (and engine.closeness_centrality())
answered p and q with the default graph's x and y.

The kept case is made here the way DictSweepAllowed reads it: ^ArnoAccel("lib_path") names a library that cannot be
loaded ("cannot be asked keeps the dead entries"). That is done only in a namespace with no library configured (a
Community container: no Arno), and the entry is removed afterwards; where a library is configured the kept case is
not made and only the swept control runs (an Arno 1.2.1 library declares the capability).

^NKG is built here only by the engine's sync() and the readers' EnsureNKG, and changed only by the engine's writes and
Graph.KG.Eraser.EraseAll (GraphIndex.DropNKG); nothing kills it.

Worked by hand: default graph x -R-> y, y -R-> x; graph g286 p -R-> q; sync(); then g286's edge deleted:
* SCC of the default graph: {x, y}, one component of size 2; CALL ivg.scc() answers x and y;
* harmonic closeness: x reaches y at distance 1 and y reaches x: 1.0 each; CALL ivg.closeness() answers x and y;
* eigenvector: x and y feed each other, so they score alike, 1/sqrt(2) each after L2 normalisation; CALL
  ivg.eigenvector() answers x and y.
* p -R-> q added again to g286, then deleted, then read: x and y again; p keeps the index it had (an interned name is
  never given a second index), and nodeCount does not grow.

Namespace: an IVG460FX*/IVG460FY* scratch namespace, emptied first (Graph.KG.Eraser.EraseAll).
"""

from __future__ import annotations

import math
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")
MISSING_LIB = "/nonexistent/r286/libarno_callout_missing.so"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith(("IVG460FX", "IVG460FY")),
                       reason="IVG_NAMESPACE must name an IVG460FX*/IVG460FY* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

QUERIES = (
    "CALL ivg.scc() YIELD node RETURN node",
    "CALL ivg.closeness() YIELD node RETURN node",
    "CALL ivg.eigenvector() YIELD node RETURN node",
)


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
        if native.get("ArnoAccel", "lib_path") == MISSING_LIB:
            native.kill("ArnoAccel", "lib_path")
        conn.commit()
        conn.close()


def _build_and_drop_the_named_graph(engine):
    engine.create_edge("x", "R", "y")
    engine.create_edge("y", "R", "x")
    engine.create_edge("p", "R", "q", graph="g286")
    engine.conn.commit()
    assert engine.sync()
    engine.execute_cypher("USE GRAPH 'g286' MATCH ()-[r]->() DELETE r", {}, raise_on_error=True)
    engine.conn.commit()
    assert engine._named_graph_with_edges() is None


def _assert_the_default_graph_alone(engine):
    for q in QUERIES:
        r = engine.execute_cypher(q, {}, raise_on_error=True)
        assert getattr(r, "error", None) is None, r.error
        assert sorted(str(row[0]) for row in r.rows) == ["x", "y"], q
    closeness = engine.closeness_centrality()
    assert sorted((c["id"], round(c["score"], 6)) for c in closeness) == [("x", 1.0), ("y", 1.0)]
    eigen = engine.eigenvector_centrality(max_iter=1000, tol=1e-9)
    assert sorted((c["id"], round(c["score"], 6)) for c in eigen) == [
        ("x", round(1 / math.sqrt(2), 6)), ("y", round(1 / math.sqrt(2), 6))]
    scc = engine.strongly_connected_components()
    assert sorted((c["id"], c["size"]) for c in scc) == [("x", 2), ("y", 2)]


def test_swept_the_default_graph_answers_alone(engine):
    """The control: the dictionary as this namespace's DictSweepAllowed has it (swept with no library or with 1.2.1)."""
    _build_and_drop_the_named_graph(engine)
    _assert_the_default_graph_alone(engine)


def test_kept_the_default_graph_answers_alone(engine):
    if engine.native.get("ArnoAccel", "lib_path") not in (None, ""):
        pytest.skip("an Arno library is configured here: the kept dictionary is made only where none is")
    engine.native.set(MISSING_LIB, "ArnoAccel", "lib_path")
    engine.conn.commit()
    assert engine.native.classMethodValue("Graph.KG.TraversalBuild", "DictSweepAllowed") == 0
    _build_and_drop_the_named_graph(engine)
    _assert_the_default_graph_alone(engine)
    p_index = engine.native.get("NKG", "$ND", 2)
    count = engine.native.get("NKG", "$meta", "nodeCount")
    # p and q are interned after x and y (sync walks the default graph first): the kept entries are still there
    assert (p_index, count) == ("p", 4)
    # the name comes back with its index: no second index for p
    engine.create_edge("p", "R", "q", graph="g286")
    engine.conn.commit()
    assert engine.native.get("NKG", "$NI", "p") == 2
    assert engine.native.get("NKG", "$meta", "nodeCount") == 4
    engine.execute_cypher("USE GRAPH 'g286' MATCH ()-[r]->() DELETE r", {}, raise_on_error=True)
    engine.conn.commit()
    _assert_the_default_graph_alone(engine)
    assert engine.native.get("NKG", "$meta", "nodeCount") == 4
