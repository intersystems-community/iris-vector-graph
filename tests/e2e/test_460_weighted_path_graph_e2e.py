"""4.6.0 R154: `ivg.shortestPath.weighted` honours `USE GRAPH`, `weightProp` and `maxHops`.

`DijkstraJson` hard-coded `pGraph = 0`, so every search walked the default graph's ^KG whatever `USE GRAPH` said; it
never read `maxHops`; and the weight was the ^KG entry's value whatever `weightProp` named. ^KG holds each graph's
adjacency under its own key (`^KG("out", <graph>, s, p, o) = weight`), so the graph is a matter of passing it. The
weight in ^KG is the relationship's `weight` property (`create_edge(weight=...)`, `{weight: x}`), the one property the
adjacency index stores: `weightProp` 'weight' is honoured, any other name is refused with an error saying so (no
silent fall back to `weight`).

The graphs, worked by hand. Both hold nodes a, b, d of the same ids, relationship type R, weights as `weight`:
- G1: a -R(1)-> b -R(1)-> d, and a -R(5)-> d. Cheapest a to d: a, b, d at 1 + 1 = 2 (two hops). With maxHops 1 the
  only path of one hop is a, d at 5.
- G2: a -R(3)-> d alone: a, d at 3.
- The default graph holds none of these nodes: no path (`USE GRAPH ''`). Naming no graph answers the union of
  each graph's own path (R192).
Every edge of G1 also has a `cost` property of 7, which no weightProp may read.

Each answer is checked on the three routes a call can take: the engine's handler (`YIELD path, totalCost`), the
cost-only search (`YIELD totalCost`, `DijkstraCost`) and the translation (`... WITH totalCost RETURN totalCost`).
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

ROUTES = {
    "handler": ("YIELD path, totalCost", lambda r: [(json.loads(row[0])["nodes"], float(row[1])) for row in r.rows]),
    "cost": ("YIELD totalCost", lambda r: [float(row[0]) for row in r.rows]),
    "translated": (
        "YIELD path, totalCost WITH path, totalCost RETURN path, totalCost",
        lambda r: [(json.loads(row[0])["nodes"], float(row[1])) for row in r.rows],
    ),
}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g1, g2 = f"ivg154a_{run}", f"ivg154b_{run}"
    ids = {k: f"ivg154_{run}_{k}" for k in "abd"}
    for s, o, w in (("a", "b", 1), ("b", "d", 1), ("a", "d", 5)):
        assert engine.create_edge(ids[s], "R", ids[o], weight=w, qualifiers={"cost": 7}, graph=g1)
    assert engine.create_edge(ids["a"], "R", ids["d"], weight=3, graph=g2)
    assert engine.create_node(ids["b"], graph=g2)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, g1, g2, ids
    for graph in (g1, g2):
        engine.erase_graph(graph)


def _run(g, route, graph=None, weight="'weight'", max_hops=10):
    engine, _, _, ids = g
    tail, read = ROUTES[route]
    use = "" if graph is None else f"USE GRAPH '{graph}' "
    r = engine.execute_cypher(
        f"{use}CALL ivg.shortestPath.weighted($a, $d, {weight}, 9999, {max_hops}, 'out') {tail}",
        {"a": ids["a"], "d": ids["d"]},
    )
    assert r.error is None, r.error
    return read(r)


def _expect(g, route, nodes, cost):
    _, _, _, ids = g
    path = [ids[k] for k in nodes]
    return [cost] if route == "cost" else [(path, cost)]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_use_graph_searches_that_graph(g, route):
    _, g1, g2, _ = g
    assert _run(g, route, g1) == _expect(g, route, "abd", 2.0)
    assert _run(g, route, g2) == _expect(g, route, "ad", 3.0)


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_use_graph_default_searches_the_default_graph(g, route):
    assert _run(g, route, "") == []


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_no_use_graph_answers_each_graphs_own_path(g, route):
    """Since 4.6.0 R192 a CALL naming no graph answers the union of each graph's own answer: G1's a, b, d at 2 and
    G2's a, d at 3 (the default graph has no path). It expected the default graph's [] alone (R154 predates R192)."""
    assert sorted(_run(g, route)) == sorted(_expect(g, route, "abd", 2.0) + _expect(g, route, "ad", 3.0))


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_max_hops_bounds_the_path(g, route):
    _, g1, _, _ = g
    assert _run(g, route, g1, max_hops=1) == _expect(g, route, "ad", 5.0)
    assert _run(g, route, g1, max_hops=2) == _expect(g, route, "abd", 2.0)


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_weight_property_other_than_weight_is_refused(g, route):
    engine, g1, _, ids = g
    tail, _ = ROUTES[route]
    with pytest.raises(Exception, match="weightProp"):
        r = engine.execute_cypher(
            f"USE GRAPH '{g1}' CALL ivg.shortestPath.weighted($a, $d, 'cost', 9999, 10, 'out') {tail}",
            {"a": ids["a"], "d": ids["d"]},
        )
        if r.error:
            raise RuntimeError(r.error)
