"""ivg.shortestPath.weighted ... YIELD totalCost asks for the cost alone.

Only the cost is visible to such a call, so the engine skips building the path: TraversalPaths.DijkstraCost searches
from both ends (BIW at SF1: 452 ms against Neo4j's 15.5 on AWS through DijkstraJson, a one-sided search over
process-private globals). Any other YIELD keeps DijkstraJson, and so do classes deployed before DijkstraCost.
"""

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.engine import IRISGraphEngine

CALL = "CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'both', ['KNOWS']) YIELD {y} RETURN {r}"


def _run(yield_items, ret, cost_reply="2.5"):
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    calls = []

    def fake(conn, cls, method, *args):
        calls.append(method)
        if method == "DijkstraCost":
            if isinstance(cost_reply, Exception):
                raise cost_reply
            return cost_reply
        return '{"nodes": ["a", "b"], "rels": ["KNOWS"], "costs": [2.5], "length": 1, "totalCost": 2.5}'

    proc = parse_query(CALL.format(y=yield_items, r=ret)).procedure_call
    with patch("iris_vector_graph.engine._call_classmethod", side_effect=fake):
        r = e._proc_ivg_shortestpath_weighted(proc, {})
    return calls, r


def test_a_cost_alone_takes_the_cost_search():
    calls, r = _run("totalCost", "totalCost")
    assert calls == ["DijkstraCost"]
    assert r.rows == [["", 2.5]]


def test_no_path_is_no_row():
    calls, r = _run("totalCost", "totalCost", cost_reply="")
    assert calls == ["DijkstraCost"] and r.rows == []


@pytest.mark.parametrize("y,ret", [("path, totalCost", "path, totalCost"), ("path", "path")])
def test_a_path_keeps_dijkstra_json(y, ret):
    calls, r = _run(y, ret)
    assert calls == ["DijkstraJson"]
    assert r.rows[0][1] == 2.5


def test_classes_deployed_before_the_cost_search_keep_dijkstra_json():
    calls, r = _run("totalCost", "totalCost", cost_reply=RuntimeError("<METHOD DOES NOT EXIST> *DijkstraCost,Graph.KG.Traversal"))
    assert calls == ["DijkstraCost", "DijkstraJson"] and r.rows[0][1] == 2.5


def test_a_cost_of_zero_is_a_cost():
    calls, r = _run("totalCost", "totalCost", cost_reply=0)
    assert r.rows == [["", 0.0]]


def _cost_args(query, params=None):
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    calls = []

    def fake(conn, cls, method, *args):
        calls.append((method, args))
        return 1.5

    with patch("iris_vector_graph.engine._call_classmethod", side_effect=fake):
        e.execute_cypher(query, params or {})
    return [a for m, a in calls if m == "DijkstraCost"][0]


def test_the_cost_search_gets_the_endpoints_cap_direction_and_types():
    args = _cost_args("CALL ivg.shortestPath.weighted($a, $b, 'weight', 50, 10, 'both', ['KNOWS']) YIELD totalCost RETURN totalCost", {"a": "n1", "b": "n2"})
    assert args == ("n1", "n2", 50.0, "both", '["KNOWS"]')


def test_without_types_the_cost_search_gets_no_types_argument():
    args = _cost_args("CALL ivg.shortestPath.weighted('n1', 'n2', 'weight', 9999, 10, 'out') YIELD totalCost RETURN totalCost")
    assert args == ("n1", "n2", 9999.0, "out")


def test_any_other_error_is_raised():
    """Not swallowed into an empty answer: a cost the search could not find is not a cost of nothing."""
    with pytest.raises(RuntimeError, match="STORE"):
        _run("totalCost", "totalCost", cost_reply=RuntimeError("<STORE> DijkstraCost+40^Graph.KG.TraversalPaths.1"))
