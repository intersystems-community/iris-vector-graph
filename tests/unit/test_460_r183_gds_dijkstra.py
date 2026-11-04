"""4.6.0 R183: `CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'})` answers by name.

The shim renamed it `ivg.shortestPath.weighted` and passed GDS's arguments on by position: the graph name `'g'`
became the source node and the configuration map the target, so a -> b -> c (cost 2) answered no rows and no error.

The engine answers it now (as R174 does `gds.pageRank.stream`), reading GDS's configuration by key:

- `sourceNode`, `targetNode` (required): IVG node IDs, literals or `$parameters`;
- `relationshipWeightProperty`: absent, the search is unweighted as GDS's is (every relationship costs 1:
  `Graph.KG.TraversalPaths.ShortestLength` / `ShortestPathJson`); `'weight'`, Dijkstra over the stored weights
  (`DijkstraCost` / `DijkstraJson`); any other name raises, as `ivg.shortestPath.weighted` does;
- `relationshipTypes`: the predicates walked (`['*']`, or absent: all).

Out-edges of the default graph, no hop or cost bound. Columns: index, sourceNode, targetNode, totalCost, nodeIds,
costs (accumulated, from 0). `path` (a GDS Path), any other key, and USE GRAPH raise.

The class methods are stubbed here with the answers they give on a -> b -> c; the live answers are
tests/e2e/test_460_r183_gds_dijkstra_e2e.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import IRISGraphEngine

PATH = {"nodes": ["a", "b", "c"], "rels": ["R", "R"], "costs": [1, 1], "length": 2, "totalCost": 2}
SHORTEST = [{"nodes": ["a", "b", "c"], "rels": ["R", "R"], "length": 2}]


@pytest.fixture
def eng():
    e = IRISGraphEngine(MagicMock(), embedding_dimension=8, native_cypher=False)
    calls = []

    def call(conn, cls, method, *args):
        calls.append((method, args))
        return {
            "DijkstraCost": 2,
            "DijkstraJson": json.dumps(PATH),
            "ShortestLength": 2,
            "ShortestPathJson": json.dumps(SHORTEST),
        }[method]

    with patch("iris_vector_graph.engine._call_classmethod", side_effect=call):
        e.calls = calls
        yield e


def _methods(eng):
    return [m for m, _ in eng.calls]


def test_the_example_answers_cost_2(eng):
    r = eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) YIELD totalCost"
    )
    assert (r.columns, r.rows, r.error) == (["totalCost"], [[2.0]], None)
    # unweighted, as GDS is without relationshipWeightProperty: a hop count from a to c over out-edges, unbounded
    method, args = eng.calls[0]
    assert method == "ShortestLength" and args[:2] == ("a", "c") and args[3:] == ("[]", "out"), eng.calls
    assert args[2] >= 2**31 - 1


def test_every_column_but_path(eng):
    r = eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) "
        "YIELD index, sourceNode, targetNode, totalCost, nodeIds, costs"
    )
    assert r.columns == ["index", "sourceNode", "targetNode", "totalCost", "nodeIds", "costs"]
    assert r.rows == [[0, "a", "c", 2.0, ["a", "b", "c"], [0.0, 1.0, 2.0]]]
    assert _methods(eng) == ["ShortestPathJson"]


def test_a_weight_property_is_dijkstra_over_the_stored_weights(eng):
    r = eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: $s, targetNode: $t, "
        "relationshipWeightProperty: 'weight'}) YIELD nodeIds, costs, totalCost",
        {"s": "a", "t": "c"},
    )
    assert r.rows == [[["a", "b", "c"], [0.0, 1.0, 2.0], 2.0]]
    method, args = eng.calls[0]
    # source, target, weightProp, maxCost (no bound), maxHops (0: no bound), direction, types, graph (default)
    assert method == "DijkstraJson" and args[:3] == ("a", "c", "weight") and args[4:] == (0, "out", "[]", ""), args
    assert args[3] >= 1e100


def test_a_weighted_cost_alone_asks_for_the_cost(eng):
    r = eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c', "
        "relationshipWeightProperty: 'weight'}) YIELD totalCost RETURN totalCost"
    )
    assert r.rows == [[2.0]]
    method, args = eng.calls[0]
    assert method == "DijkstraCost" and args[:2] == ("a", "c") and args[3:] == ("out", "[]", "", 0), args


def test_relationship_types_are_the_predicates_walked(eng):
    eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c', "
        "relationshipTypes: ['R', 'S']}) YIELD totalCost"
    )
    assert eng.calls[0][1][3] == json.dumps(["R", "S"])


def test_the_star_type_walks_every_predicate(eng):
    eng.execute_cypher(
        "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c', "
        "relationshipTypes: ['*']}) YIELD totalCost"
    )
    assert eng.calls[0][1][3] == "[]"


def test_no_path_is_no_row(eng):
    with patch("iris_vector_graph.engine._call_classmethod", return_value=-1):
        r = eng.execute_cypher(
            "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'c', targetNode: 'a'}) YIELD totalCost"
        )
    assert (r.rows, r.error) == ([], None)


def test_the_bare_name_answers_the_same(eng):
    r = eng.execute_cypher("CALL gds.shortestPath.dijkstra('g', {sourceNode: 'a', targetNode: 'c'}) YIELD totalCost")
    assert r.rows == [[2.0]]


@pytest.mark.parametrize(
    "config, match",
    [
        ("{targetNode: 'c'}", "sourceNode"),
        ("{sourceNode: 'a'}", "targetNode"),
        ("{sourceNode: 'a', targetNode: 'c', relationshipWeightProperty: 'cost'}", "weight"),
        ("{sourceNode: 'a', targetNode: 'c', concurrency: 4}", "concurrency"),
        ("{sourceNode: 'a', targetNode: 'c', nodeLabels: ['P']}", "nodeLabels"),
    ],
)
def test_what_it_cannot_honour_raises(eng, config, match):
    with pytest.raises(ValueError, match=match):
        eng.execute_cypher(f"CALL gds.shortestPath.dijkstra.stream('g', {config}) YIELD totalCost")
    assert eng.calls == []


def test_the_path_column_raises(eng):
    with pytest.raises(ValueError, match="path"):
        eng.execute_cypher(
            "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) YIELD path"
        )


def test_use_graph_raises(eng):
    with pytest.raises(ValueError, match="USE GRAPH"):
        eng.execute_cypher(
            "USE GRAPH 'g2' CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) "
            "YIELD totalCost"
        )


def test_a_later_clause_raises_naming_its_columns(eng):
    with pytest.raises(ValueError, match=r"gds\.shortestPath\.dijkstra\.stream.*totalCost"):
        eng.execute_cypher(
            "CALL gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) YIELD totalCost "
            "RETURN totalCost ORDER BY totalCost"
        )


def test_it_is_answered_not_shimmed():
    from iris_vector_graph._engine.query import GDS_ANSWERED, GDS_SHIM_MAP

    for name in ("gds.shortestpath.dijkstra.stream", "gds.shortestpath.dijkstra"):
        assert name in GDS_ANSWERED and name not in GDS_SHIM_MAP
