"""4.6.0 R196, live: the `^NKG` SQL functions, called directly, refuse a namespace where a named graph has edges.

`SELECT Graph_KG.kg_SCC(100)` (and kg_Betweenness, kg_Closeness, kg_Eigenvector, kg_Leiden, kg_TriangleCount,
kg_KCore) read `^NKG`, which holds every graph's edges with no graph key. R184 refused their Cypher form once a named
graph has an edge; the functions themselves answered every graph merged, as the default graph's. They raise now
(`Graph.KG.NKGAccel.RefuseMergedGraphs`), and answer as before while the default graph is the only one with edges.

Namespace: `IVG_NAMESPACE`, an IVG460FX* scratch namespace set up for `Graph_KG` (IVG460FXM when this was written);
the fixture empties it first (`Graph.KG.Eraser.EraseAll`).

By hand: the default graph a 3-cycle a -> b -> c -> a, one strongly connected component of 3. Graph r196g: a -> x,
x -> a. Merged in `^NKG`, a's component is {a, b, c, x}.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

GRAPH = "r196g"
FUNCTIONS = [
    "kg_Betweenness(0, 'out', 0, 100, 256)",
    "kg_Closeness('harmonic', 'out', 0, 100)",
    "kg_Eigenvector(30, 0.000001, 100)",
    "kg_Leiden(10, 1.0, 0.0001, 100, 256, 1)",
    "kg_TriangleCount(100)",
    "kg_SCC(100)",
    "kg_KCore(100)",
]


def _id(n: str) -> str:
    return f"r196:{n}"


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for n in "abc":
        eng.create_node(_id(n))
    for s, o in (("a", "b"), ("b", "c"), ("c", "a")):
        eng.create_edge(_id(s), "R", _id(o))
    for n in "ax":
        eng.create_node(_id(n), graph=GRAPH)
    conn.commit()
    eng.sync()
    yield eng
    conn.close()


def _named_edges(engine, present: bool) -> None:
    for s, o in (("a", "x"), ("x", "a")):
        if present:
            engine.create_edge(_id(s), "R", _id(o), graph=GRAPH)
        else:
            engine.delete_edge(_id(s), "R", _id(o), graph=GRAPH)
    engine.conn.commit()
    engine.sync()


def _select(engine, call):
    cur = engine.conn.cursor()
    try:
        cur.execute(f"SELECT Graph_KG.{call}")
        return cur.fetchone()[0]
    finally:
        cur.close()


def test_with_the_default_graph_alone_scc_answers_its_component(engine):
    if engine._named_graph_with_edges() is not None:
        _named_edges(engine, False)
    rows = json.loads(_select(engine, "kg_SCC(100)"))
    mine = {d["id"]: d["component"] for d in rows if d["id"].startswith("r196:")}
    assert set(mine) == {_id(n) for n in "abc"} and len(set(mine.values())) == 1, rows


@pytest.mark.parametrize("call", FUNCTIONS, ids=[f.split("(")[0] for f in FUNCTIONS])
def test_with_a_named_graph_a_function_refuses(engine, call):
    if engine._named_graph_with_edges() is None:
        _named_edges(engine, True)
    with pytest.raises(Exception) as raised:
        _select(engine, call)
    text = str(raised.value)
    assert GRAPH in text and "^NKG" in text and call.split("(")[0] in text, text
