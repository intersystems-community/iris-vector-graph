"""4.6.5 round 9: the six positional-argument procedures refuse an options map and still answer correctly.

Graph for ppr: a -> b, a -> c, b -> c. Hand-worked PPR from ['a'] with alpha=0.85, maxIter=100 (to converge):
  a = 0.15 / 0.3316875 = 0.4522, c = 0.78625a = 0.3556, b = 0.425a = 0.1922, which sum to 1.

The refusal: CALL ivg.ppr({seeds: ['a']}) raises ValueError naming the procedure and its positional arguments.
The answer: CALL ivg.ppr(['a'], 0.85, 100) yields the three nodes in descending score order: a > c > b.

    IVG_NAMESPACE=IVG463PPR pytest tests/e2e/test_465_positional_procedures_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

NODES = ["a", "b", "c"]
EDGES = [("a", "KNOWS", "b"), ("a", "KNOWS", "c"), ("b", "KNOWS", "c")]


@pytest.fixture(scope="module")
def engine():
    import iris

    conn = ns463.connect()
    eng = ns463.engines(conn)["default"]
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    for n in NODES:
        eng.create_node(n)
    for s, p, o in EDGES:
        eng.create_edge(s, p, o)
    eng.sync()
    assert eng.node_count() == 3, "the graph did not load"
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.close()


def test_ivg_ppr_refuses_an_options_map(engine):
    """The refusal names the procedure and the positional arguments USER_GUIDE documents."""
    with pytest.raises(ValueError, match=r"ivg\.ppr.*takes positional arguments.*seeds.*alpha.*maxIter"):
        ns463.rows(engine, "CALL ivg.ppr({seeds: ['a']}) YIELD node, score RETURN node")


def test_ivg_ppr_still_answers_a_correct_ranking_with_positional_arguments(engine):
    """PPR from ['a'] with alpha=0.85, maxIter=100: scores match hand-worked values within 1e-3, order a > c > b."""
    result = engine.execute_cypher("CALL ivg.ppr(['a'], 0.85, 100) YIELD node, score RETURN node.node_id AS id, score ORDER BY score DESC")
    assert not result.error, result.error
    rows = result.rows
    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
    ids = [r[0] for r in rows]
    scores = {r[0]: r[1] for r in rows}
    # hand-worked: a = 0.4522, c = 0.3556, b = 0.1922
    assert ids == ["a", "c", "b"], f"score order wrong: {ids}"
    assert abs(scores["a"] - 0.4522) < 1e-3, f"a score: expected 0.4522, got {scores['a']}"
    assert abs(scores["c"] - 0.3556) < 1e-3, f"c score: expected 0.3556, got {scores['c']}"
    assert abs(scores["b"] - 0.1922) < 1e-3, f"b score: expected 0.1922, got {scores['b']}"


def test_ivg_ppr_refuses_positional_seeds_plus_options_map(engine):
    """['a'], {alpha: 0.5} was silently ignored on 4.6.4; now it raises."""
    with pytest.raises(ValueError, match=r"ivg\.ppr.*takes positional arguments"):
        ns463.rows(engine, "CALL ivg.ppr(['a'], {alpha: 0.5}) YIELD node, score RETURN node")


def test_ivg_shortestPath_weighted_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.shortestPath\.weighted.*takes positional arguments.*from.*to.*weightProp"):
        ns463.rows(engine, "CALL ivg.shortestPath.weighted({from: 'a', to: 'b'}) YIELD path RETURN path")


def test_ivg_bm25_search_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.bm25\.search.*takes positional arguments.*name.*query.*k"):
        ns463.rows(engine, "CALL ivg.bm25.search({name: 'idx', query: 'q', k: 10}) YIELD node RETURN node")


def test_ivg_ivf_search_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.ivf\.search.*takes positional arguments.*name.*query_vec.*k.*nprobe"):
        ns463.rows(engine, "CALL ivg.ivf.search({name: 'idx', query_vec: [1.0], k: 10, nprobe: 2}) YIELD node RETURN node")


def test_ivg_retrieve_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.retrieve.*takes positional arguments.*query.*limit"):
        ns463.rows(engine, "CALL ivg.retrieve({query: 'q', limit: 10}) YIELD node RETURN node")


def test_ivg_neighbors_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.neighbors.*takes positional arguments.*source_ids.*predicate.*direction"):
        ns463.rows(engine, "CALL ivg.neighbors({source_ids: ['a']}) YIELD neighbor RETURN neighbor")


def test_ivg_neighbors_still_answers_with_positional_arguments(engine):
    """ivg.neighbors(['a']) yields 'b' and 'c'."""
    result = engine.execute_cypher("CALL ivg.neighbors(['a']) YIELD neighbor RETURN neighbor.node_id AS id")
    assert not result.error, result.error
    ids = sorted(r[0] for r in result.rows)
    assert ids == ["b", "c"], f"expected neighbors ['b', 'c'], got {ids}"
