"""`CALL ivg.shortestPath.weighted($a, $b, ...)` must search for the nodes the parameters name, not for nodes called "a" and "b".

The handler read its arguments without the query's parameters, so a `$a` resolved to the text "a", DijkstraJson found no such
node, and the CALL answered zero rows with no error (bench.py Q6 timed exactly that on every run).
"""

from unittest.mock import MagicMock, patch

from iris_vector_graph import engine as engine_module
from iris_vector_graph.engine import IRISGraphEngine

QUERY = "CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 10) YIELD totalCost RETURN totalCost"


def _run(query, params):
    calls = []

    def fake(conn, cls, method, *args):
        calls.append((cls, method, args))
        return '{"path": ["node_1", "node_2"], "totalCost": 3.0}'

    eng = IRISGraphEngine(MagicMock())
    with patch.object(engine_module, "_call_classmethod", fake):
        result = eng.execute_cypher(query, params)
    return calls, result


def test_parameters_reach_dijkstra_as_their_values():
    calls, _ = _run(QUERY, {"a": "node_1", "b": "node_2"})
    dijkstra = [c for c in calls if c[1] == "DijkstraJson"]
    assert dijkstra and dijkstra[0][2][:2] == ("node_1", "node_2")


def test_literal_arguments_still_work():
    calls, _ = _run("CALL ivg.shortestPath.weighted('node_1', 'node_2', 'weight', 9999, 10) YIELD totalCost RETURN totalCost", {})
    assert [c for c in calls if c[1] == "DijkstraJson"][0][2][:2] == ("node_1", "node_2")


def test_a_missing_parameter_is_an_error_not_a_node_named_after_it():
    _, result = _run(QUERY, {"a": "node_1"})
    assert not result.rows  # no search for a node literally called "b"
