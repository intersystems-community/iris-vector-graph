"""Every method and procedure that takes a direction refuses one outside its documented set (4.6.0, round-3
walkthrough).

``CALL ivg.shortestPath.weighted('fay', 'ann', 'weight', 9999, 10, 'in')`` answered no row on ann -> ... -> fay
(the path walked against the edges, cost 6): ``TraversalPaths.DijkstraJson`` walked out-edges only under "out" and
both under "both", so "in" walked nothing. ``'sideways'`` answered ``[]`` the same way, and ``ivg.closeness`` and
``ivg.degreeCentrality`` took it as "out". Each now raises ``ValueError`` naming the allowed values, before anything
is read. The answers for "out", "in" and "both" are checked against networkx (reversed and undirected views) live,
in tests/e2e/test_460_r306_algorithm_oracle_e2e.py; this file checks the refusals offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import engine as engine_module
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine

BAD = "sideways"


def _engine():
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0,)
    conn.cursor.return_value.fetchall.return_value = []
    return IRISGraphEngine(conn, embedding_dimension=4)


@pytest.mark.parametrize("query", [
    "CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, 'sideways') YIELD path, totalCost "
    "MATCH (n {node_id: 'a'}) RETURN n.node_id, totalCost",
    "CALL ivg.degreeCentrality({direction: 'sideways'}) YIELD node, score RETURN node, score",
    "CALL ivg.closeness({direction: 'sideways'}) YIELD node, score RETURN node, score",
    "CALL ivg.betweenness({direction: 'sideways'}) YIELD node, score RETURN node, score",
    "CALL ivg.neighbors(['a'], 'R', 'sideways') YIELD node RETURN node",
])
def test_the_translator_refuses_an_unknown_direction(query):
    with pytest.raises(ValueError, match="'out', 'in'.*'both'.*sideways"):
        translate_to_sql(parse_query(query), {})


@pytest.mark.parametrize("yields", ["path, totalCost", "totalCost"])
def test_the_weighted_path_handler_refuses_an_unknown_direction_before_searching(yields):
    eng = _engine()
    called = []
    with patch.object(engine_module, "_call_classmethod", lambda *a: called.append(a) or "{}"):
        with pytest.raises(ValueError, match="sideways"):
            eng.execute_cypher(
                f"CALL ivg.shortestPath.weighted('a', 'b', 'weight', 9999, 10, '{BAD}') YIELD {yields}",
                {}, raise_on_error=True)
    assert not [c for c in called if c[2] in ("DijkstraJson", "DijkstraCost")]


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_the_weighted_path_handler_passes_each_direction_and_costs_in_from_both_ends(direction):
    """``YIELD totalCost`` alone is answered by DijkstraCost (searched from both ends), "in" included now that it
    walks in-edges."""
    eng = _engine()
    called = []

    def fake(conn, cls, method, *args):
        called.append((method, args))
        return 6

    eng._graphs_with_edges = lambda: [""]
    with patch.object(engine_module, "_call_classmethod", fake):
        r = eng.execute_cypher(
            f"CALL ivg.shortestPath.weighted('fay', 'ann', 'weight', 9999, 10, '{direction}') YIELD totalCost", {})
    assert r.rows == [[6.0]]
    method, args = called[-1]
    assert method == "DijkstraCost" and args[3] == direction


@pytest.mark.parametrize("call", [
    lambda e: e.degree_centrality(direction=BAD),
    lambda e: e.betweenness_centrality(direction=BAD),
    lambda e: e.closeness_centrality(direction=BAD),
    lambda e: e.kg_NEIGHBORS(["a"], direction=BAD),
    lambda e: e.kg_MENTIONS(["a"], direction=BAD),
    lambda e: e.get_edges_in_window("a", "R", 0, 10, direction=BAD),
    lambda e: e.get_edges_in_window("a", "R", 0, 10, direction="both"),
    lambda e: e.iter_edges_in_window(0, 10, direction=BAD),
    lambda e: e.fhir_expand_concepts("concepts", ["c"], direction=BAD),
    lambda e: e.fhir_concept_ppr("patients", "concepts", ["c"], direction=BAD),
], ids=["degree_centrality", "betweenness_centrality", "closeness_centrality", "kg_NEIGHBORS", "kg_MENTIONS",
        "get_edges_in_window", "get_edges_in_window-both", "iter_edges_in_window", "fhir_expand_concepts",
        "fhir_concept_ppr"])
def test_every_engine_method_with_a_direction_refuses_an_unknown_one(call):
    eng = _engine()
    eng._store = MagicMock()
    with pytest.raises(ValueError, match="direction|literal_error"):
        call(eng)
    eng._store.execute_temporal_window_query.assert_not_called()
