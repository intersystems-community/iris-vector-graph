"""4.6.3, live: a weighted path's costs and totalCost are numbers of the type a weight has, a float.

``CALL ivg.shortestPath.weighted`` answers ``path`` as ``{nodes, rels, costs, length, totalCost}``: ``costs`` is each
relationship's weight, in path order, and ``totalCost`` their sum. ``Graph.KG.TraversalPaths.DijkstraJson`` wrote
both with ``%ToJSON``, which writes a whole number with no decimal point, so a path over weights written as 1.0
answered ``costs: [1, 1]`` and ``totalCost: 2`` (the yielded ``totalCost`` column was already 2.0). An edge's
``r.weight`` reads back 1.0. ``gds.shortestPath.dijkstra.stream``'s ``costs`` is another list: the running totals,
one per node of the path, starting at 0.0 and ending at ``totalCost``, as Neo4j GDS answers it.

Every route a path takes is checked: the engine's handler (the CALL alone), and the translated CALL (a RETURN of a
key, a WITH, a MATCH after it). The reference is the weights as written, summed by networkx's
``shortest_path_length(weight=...)`` on the same graph.

Runs in the scratch namespace ``IVG_NAMESPACE`` names (IVG463*, IVG460* or IVG262*, IVG installed); erases its graph.

    IVG_NAMESPACE=IVG463B pytest tests/e2e/test_463_weighted_path_costs_e2e.py
"""

from __future__ import annotations

import os
from itertools import accumulate

import networkx as nx
import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith(("IVG463", "IVG460", "IVG262")),
                       reason="IVG_NAMESPACE must name an IVG463*/IVG460*/IVG262* scratch namespace: it erases its graph"),
]

#: a -1.0-> b -1.0-> c -2.5-> d, and a dearer a -5.0-> d
EDGES = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "d", 2.5), ("a", "d", 5.0)]
W = "CALL ivg.shortestPath.weighted('a', 'c') YIELD path, totalCost "


def _exact(value, want):
    """``value == want`` and of the same type, through lists and dicts (``1 == 1.0`` is not enough)."""
    assert type(value) is type(want), (value, want)
    if isinstance(want, list):
        assert len(value) == len(want), (value, want)
        for v, w in zip(value, want):
            _exact(v, w)
    elif isinstance(want, dict):
        assert list(value) == list(want), (value, want)
        for k in want:
            _exact(value[k], want[k])
    else:
        assert value == want, (value, want)


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(ns262.connect(NS))
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    for v in "abcd":
        e.create_node(v)
    for s, o, w in EDGES:
        e.create_edge(s, "R", o, weight=w)
    e.sync()
    yield e
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    e.conn.close()


def _reference(target: str) -> tuple:
    G = nx.DiGraph()
    G.add_weighted_edges_from(EDGES)
    nodes = nx.shortest_path(G, "a", target, weight="weight")
    costs = [G[u][v]["weight"] for u, v in zip(nodes, nodes[1:])]
    return nodes, costs, nx.shortest_path_length(G, "a", target, weight="weight")


def _path(target: str) -> dict:
    nodes, costs, total = _reference(target)
    return {"nodes": nodes, "rels": ["R"] * len(costs), "costs": costs, "length": len(costs), "totalCost": total}


def rows(engine, q):
    r = engine.execute_cypher(q)
    assert not getattr(r, "error", None), r.error
    return r.rows


def test_the_weight_reads_back_as_written(engine):
    _exact(rows(engine, "MATCH ({node_id: 'a'})-[r]->({node_id: 'b'}) RETURN r.weight"), [[1.0]])


def test_the_handlers_path_holds_floats(engine):
    _exact(rows(engine, W), [[_path("c"), 2.0]])
    _exact(rows(engine, W + "RETURN path, totalCost"), [[_path("c"), 2.0]])


def test_a_path_of_mixed_weights(engine):
    _exact(rows(engine, "CALL ivg.shortestPath.weighted('a', 'd') YIELD path, totalCost RETURN path, totalCost"),
           [[_path("d"), 4.5]])


def test_the_translated_calls_keys_hold_floats(engine):
    _exact(rows(engine, W + "RETURN path.costs AS c, path.totalCost AS t, totalCost"), [[[1.0, 1.0], 2.0, 2.0]])
    _exact(rows(engine, W + "RETURN path.costs[0] AS c0"), [[1.0]])
    _exact(rows(engine, W + "WITH path RETURN path"), [[_path("c")]])
    _exact(rows(engine, W + "MATCH (n {node_id: 'a'}) RETURN path, n.node_id"), [[_path("c"), "a"]])


def test_a_path_to_itself_costs_zero_point_zero(engine):
    _exact(rows(engine, "CALL ivg.shortestPath.weighted('a', 'a') YIELD path, totalCost RETURN path, totalCost"),
           [[{"nodes": ["a"], "rels": [], "costs": [], "length": 0, "totalCost": 0.0}, 0.0]])


def test_gds_costs_are_the_running_totals(engine):
    nodes, costs, total = _reference("d")
    running = list(accumulate(costs, initial=0.0))
    assert running == [0.0, 1.0, 2.0, 4.5] and running[-1] == total
    q = ("USE GRAPH '' CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'd', "
         "relationshipWeightProperty: 'weight'}) YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs "
         "RETURN index, sourceNode, targetNode, totalCost, nodeIds, costs")
    _exact(rows(engine, q), [[0, "a", "d", total, nodes, running]])
