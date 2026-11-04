"""4.6.0 R140: `CALL ivg.shortestPath.weighted(...) YIELD ...` followed by MATCH, WITH or RETURN keeps those clauses.

The engine's handler (`_proc_ivg_shortestpath_weighted`) answered any query whose leading clause was this CALL, so
`YIELD path, totalCost WITH totalCost WHERE totalCost < 5 RETURN totalCost` answered the path and cost whatever the
WHERE said, and `RETURN totalCost` answered two columns. The translator's own route (`_route_var_length`) also dropped
them: it called the store's Dijkstra and returned that. As R118 did for `ivg.vector.search`: the handler answers only
the CALL with at most a RETURN of yielded names (projected to them); anything more is translated, the search a CTE
over the `DijkstraPath` SQL function, which later clauses read like any other procedure's.

The live answers are in `tests/e2e/test_460_weighted_path_later_clauses_e2e.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import engine as engine_module
from iris_vector_graph._engine.query import _handler_may_answer
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine

CALL = "CALL ivg.shortestPath.weighted('a', 'd', 'weight', 50, 6, 'both', ['R']) YIELD path, totalCost "


@pytest.mark.parametrize(
    "rest, answers",
    [
        ("", True),
        ("RETURN path, totalCost", True),
        ("RETURN totalCost", True),
        ("RETURN totalCost, path", True),
        ("RETURN totalCost AS c", False),
        ("RETURN totalCost * 2 AS c", False),
        ("RETURN DISTINCT totalCost", False),
        ("RETURN totalCost LIMIT 1", False),
        ("WITH totalCost WHERE totalCost < 5 RETURN totalCost", False),
        ("MATCH (n {node_id: 'a'}) RETURN n.node_id, totalCost", False),
    ],
)
def test_the_handler_answers_only_the_call_and_a_return_of_yielded_names(rest, answers):
    assert _handler_may_answer(parse_query(CALL + rest)) is answers


def test_a_return_of_some_yielded_names_is_projected_to_them():
    def fake(conn, cls, method, *args):
        return '{"nodes": ["a", "b"], "totalCost": 3.0}'

    eng = IRISGraphEngine(MagicMock())
    with patch.object(engine_module, "_call_classmethod", fake):
        r = eng.execute_cypher(CALL + "RETURN totalCost", {})
    assert r.columns == ["totalCost"] and r.rows == [[3.0]]


def _tr(query, params=None):
    return translate_to_sql(parse_query(query), params or {})


def test_later_clauses_are_translated_over_a_dijkstra_cte():
    t = _tr(CALL + "WITH totalCost WHERE totalCost < 5 RETURN totalCost")
    assert not t.var_length_paths  # not the route that answered the search alone
    assert "WS AS (" in t.sql
    assert "Graph_KG.DijkstraPath('a', 'd', 'weight', 50.0, 6, 'both', '[\"R\"]', '')" in t.sql
    assert "FROM WS" in t.sql and "< " in t.sql


def test_parameters_are_inlined_as_their_values():
    t = _tr("CALL ivg.shortestPath.weighted($a, $b) YIELD totalCost MATCH (n {node_id: $a}) RETURN n.node_id, totalCost",
            {"a": "x'1", "b": "y"})
    assert "DijkstraPath('x''1', 'y', 'weight', 9999.0, 10, 'out', '[]', '')" in t.sql


def test_no_types_sends_an_empty_json_list():
    t = _tr("CALL ivg.shortestPath.weighted('a', 'd') YIELD path RETURN path")
    assert "DijkstraPath('a', 'd', 'weight', 9999.0, 10, 'out', '[]', '')" in t.sql


def test_a_yield_it_does_not_produce_is_refused():
    with pytest.raises(ValueError, match="yields path and totalCost"):
        _tr("CALL ivg.shortestPath.weighted('a', 'd') YIELD node RETURN node")
