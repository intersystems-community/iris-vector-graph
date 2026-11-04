"""4.6.0 R154: `ivg.shortestPath.weighted` sends the `USE GRAPH` graph and `maxHops` to the search, and refuses a
`weightProp` it cannot read.

`DijkstraJson` walked the default graph's ^KG (`pGraph = 0`) whatever `USE GRAPH` said, never read `maxHops`, and
costed every edge by its `weight` property whatever `weightProp` named. The graph is now the search's eighth argument
(and the cost search's sixth, `maxHops` its seventh); only `weight` (or '') is accepted as `weightProp`, the property
the adjacency index stores. The live answers are in `tests/e2e/test_460_weighted_path_graph_e2e.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import engine as engine_module
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql, _weighted_path_weight_prop
from iris_vector_graph.engine import IRISGraphEngine


def _calls(query, reply='{"nodes": ["a", "d"], "totalCost": 3.0}'):
    calls = []

    def fake(conn, cls, method, *args):
        calls.append((method, args))
        return "3.0" if method == "DijkstraCost" else reply

    with patch.object(engine_module, "_call_classmethod", fake):
        IRISGraphEngine(MagicMock()).execute_cypher(query, {})
    return calls


def test_the_handler_sends_the_use_graph_graph():
    [(method, args)] = _calls("USE GRAPH 'g1' CALL ivg.shortestPath.weighted('a', 'd', 'weight', 50, 3) YIELD path, totalCost")
    assert method == "DijkstraJson"
    assert args == ("a", "d", "weight", 50.0, 3, "out", "[]", "g1")


def test_the_cost_search_sends_the_graph_and_max_hops():
    [(method, args)] = _calls("USE GRAPH 'g1' CALL ivg.shortestPath.weighted('a', 'd', 'weight', 50, 3) YIELD totalCost")
    assert method == "DijkstraCost"
    assert args == ("a", "d", 50.0, "out", "[]", "g1", 3)


def test_no_use_graph_is_the_default_graph():
    [(_, args)] = _calls("CALL ivg.shortestPath.weighted('a', 'd') YIELD path, totalCost")
    assert args[-1] == ""


def test_the_translation_sends_the_use_graph_graph():
    t = translate_to_sql(
        parse_query("USE GRAPH 'g1' CALL ivg.shortestPath.weighted('a', 'd') YIELD totalCost WITH totalCost RETURN totalCost"),
        {},
    )
    assert "DijkstraPath('a', 'd', 'weight', 9999.0, 10, 'out', '[]', 'g1')" in t.sql


@pytest.mark.parametrize("name, read", [("weight", "weight"), ("", "weight"), (None, "weight")])
def test_the_weight_property_is_read(name, read):
    assert _weighted_path_weight_prop(name) == read


@pytest.mark.parametrize("query", [
    "CALL ivg.shortestPath.weighted('a', 'd', 'cost') YIELD path, totalCost",
    "CALL ivg.shortestPath.weighted('a', 'd', 'cost') YIELD totalCost",
])
def test_another_property_is_refused_by_the_handler(query):
    with pytest.raises(ValueError, match="weightProp 'cost' is not supported"):
        _calls(query)


def test_another_property_is_refused_by_the_translation():
    with pytest.raises(ValueError, match="weightProp 'cost' is not supported"):
        translate_to_sql(
            parse_query("CALL ivg.shortestPath.weighted('a', 'd', 'cost') YIELD totalCost WITH totalCost RETURN totalCost"),
            {},
        )
