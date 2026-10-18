"""`ivg.shortestPath.weighted(..., direction, types)` limits Dijkstra to the named relationship types.

Without it DijkstraJson walked every predicate out of every node: from an SNB Person that is
each post, comment and like, so a KNOWS path (Memgraph: `-[r:KNOWS *WSHORTEST ...]-`) cost
565 ms at SF0.1 against Memgraph's 29. The seventh argument reaches DijkstraJson as a JSON
array; omitted, the call keeps its six arguments (a server with the older class still answers).
"""

from unittest.mock import MagicMock, patch

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine


def _dijkstra_args(query, params=None):
    calls = []

    def fake(conn, cls, method, *args):
        calls.append((method, args))
        return '{"nodes": ["a", "b"], "totalCost": 3.0}'

    eng = IRISGraphEngine(MagicMock())
    with patch.object(engine_module, "_call_classmethod", fake):
        eng.execute_cypher(query, params or {})
    return [a for m, a in calls if m == "DijkstraJson"][0]


def test_a_type_list_reaches_dijkstra_as_json():
    args = _dijkstra_args("CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'both', ['KNOWS']) YIELD totalCost RETURN totalCost")
    assert args[6] == '["KNOWS"]'


def test_a_single_type_string_is_a_one_item_list():
    args = _dijkstra_args("CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'both', 'KNOWS') YIELD totalCost RETURN totalCost")
    assert args[6] == '["KNOWS"]'


def test_a_type_parameter_is_its_value():
    args = _dijkstra_args(
        "CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'both', $types) YIELD totalCost RETURN totalCost",
        {"types": ["KNOWS", "LIKES"]},
    )
    assert args[6] == '["KNOWS", "LIKES"]'


def test_without_types_the_call_keeps_six_arguments():
    args = _dijkstra_args("CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'both') YIELD totalCost RETURN totalCost")
    assert len(args) == 6
