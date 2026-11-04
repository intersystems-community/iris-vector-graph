"""4.6.0 R183, live: `CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'})` answers.

Before: no rows and no error, the graph name read as the source and the configuration as the target
(tests/unit/test_460_r183_gds_dijkstra.py pins the mapping).

By hand, in the default graph of a namespace emptied first (`Graph.KG.Eraser.EraseAll`):

- a -R-> b -R-> c, weight 1 each: a to c costs 2, weighted or not, through b. c to a: no path (out-edges only).
- p -R-> q -R-> r, weight 1 each, and p -R-> r, weight 5. GDS without relationshipWeightProperty is unweighted: p to r
  is the one-hop edge, cost 1. With relationshipWeightProperty 'weight': through q, 1 + 1 = 2 (the edge costs 5).
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

#: an IVG460FX* scratch namespace set up for `Graph_KG` (scripts/dev/make_scratch_namespace.py IVG460FXI)
NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

EDGES = (("a", "b", 1.0), ("b", "c", 1.0), ("p", "q", 1.0), ("q", "r", 1.0), ("p", "r", 5.0))


def _id(n: str) -> str:
    return f"r183{n}"


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    eng.initialize_schema()
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    for s, o, w in EDGES:
        eng.create_edge(_id(s), "R", _id(o), weight=w)
    conn.commit()
    eng.sync()
    yield eng
    conn.close()


def _call(engine, s, t, extra="", yields="totalCost"):
    return engine.execute_cypher(
        f"CALL gds.shortestPath.dijkstra.stream('g', {{sourceNode: '{_id(s)}', targetNode: '{_id(t)}'{extra}}}) "
        f"YIELD {yields}",
        raise_on_error=True,
    )


@pytest.mark.parametrize("extra", ["", ", relationshipWeightProperty: 'weight'"], ids=["unweighted", "weighted"])
def test_a_to_c_costs_2(engine, extra):
    assert _call(engine, "a", "c", extra).rows == [[2.0]]


@pytest.mark.parametrize("extra", ["", ", relationshipWeightProperty: 'weight'"], ids=["unweighted", "weighted"])
def test_a_to_c_goes_through_b(engine, extra):
    r = _call(engine, "a", "c", extra, yields="index, sourceNode, targetNode, totalCost, nodeIds, costs")
    assert r.rows == [[0, _id("a"), _id("c"), 2.0, [_id("a"), _id("b"), _id("c")], [0.0, 1.0, 2.0]]]


def test_c_to_a_is_no_row(engine):
    assert _call(engine, "c", "a").rows == []


def test_unweighted_takes_the_one_hop_edge(engine):
    assert _call(engine, "p", "r").rows == [[1.0]]
    assert _call(engine, "p", "r", yields="nodeIds, costs").rows == [[[_id("p"), _id("r")], [0.0, 1.0]]]


def test_weighted_goes_round_the_dear_edge(engine):
    extra = ", relationshipWeightProperty: 'weight'"
    assert _call(engine, "p", "r", extra).rows == [[2.0]]
    assert _call(engine, "p", "r", extra, yields="nodeIds, costs").rows == [
        [[_id("p"), _id("q"), _id("r")], [0.0, 1.0, 2.0]]
    ]


def test_a_type_not_walked_is_no_path(engine):
    assert _call(engine, "a", "c", ", relationshipTypes: ['S']").rows == []
