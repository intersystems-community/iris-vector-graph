"""4.6.0 R274 (NKGPopulated), live: Graph.KG.Traversal.NKGPopulated says 0 once GraphIndex.DropNKG has emptied ^NKG.

It answered `$Data(^NKG) > 0`, which counts the ^NKG("$meta","version") DropNKG leaves, so the engine's status
(nkg_adjacency) and the ^NKG health probe reported a dropped ^NKG as built. "Built" is now what EnsureNKG reads: a
dictionary in ^NKG("$NI").

^NKG is built here only by the engine's sync() and emptied only by Graph.KG.GraphIndex.DropNKG. Worked by hand: an
edge x -R-> y, sync(): 1; DropNKG: 0; sync() again: 1.

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


def _populated(eng) -> int:
    return int(eng.native.classMethodValue("Graph.KG.Traversal", "NKGPopulated"))


def test_nkg_populated_after_drop(engine):
    engine.create_edge("x", "R", "y")
    engine.conn.commit()
    assert engine.sync()
    assert _populated(engine) == 1
    engine.native.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
    assert _populated(engine) == 0
    assert engine.sync()
    assert _populated(engine) == 1
