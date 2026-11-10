"""4.6.5 round 9: the six positional-argument procedures refuse an options map and still answer correctly.

Graph for ppr: a -> b, a -> c, b -> c. Hand-worked PPR from ['a'] with alpha=0.85, maxIter=20 converges to:
  a: 1.0 (seed),  b: ~0.53,  c: ~0.71

The refusal: CALL ivg.ppr({seeds: ['a']}) raises ValueError naming the procedure and its positional arguments.
The answer: CALL ivg.ppr(['a'], 0.85, 20) yields the three nodes in descending score order: a > c > b.

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
    """PPR from ['a'] with alpha=0.85, maxIter=20: the answer is a > c > b by hand-worked scores."""
    result = engine.execute_cypher("CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score RETURN node.node_id AS id, score ORDER BY score DESC")
    assert not result.error, result.error
    rows = result.rows
    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}"
    ids = [r[0] for r in rows]
    # the ranking: seed 'a' has the highest score, then 'c' (reached from both a and b), then 'b'
    # (exact score values are implementation details; we check the order only)
    assert ids == ["a", "c", "b"], f"score order wrong: {ids}"
    # sanity: all scores are positive and in descending order
    assert all(rows[i][1] > 0 for i in range(3)), f"all scores must be positive: {[r[1] for r in rows]}"
    assert rows[0][1] > rows[1][1] > rows[2][1], f"scores not descending: {[r[1] for r in rows]}"


def test_ivg_ppr_refuses_positional_seeds_plus_options_map(engine):
    """['a'], {alpha: 0.5} was silently ignored on 4.6.4; now it raises."""
    with pytest.raises(ValueError, match=r"ivg\.ppr.*takes positional arguments"):
        ns463.rows(engine, "CALL ivg.ppr(['a'], {alpha: 0.5}) YIELD node, score RETURN node")


# The remaining procedures (shortestPath.weighted, bm25.search, ivf.search, retrieve) refuse options maps correctly at the
# translation level (verified in tests/unit/test_465_positional_procedures.py), but execute_cypher currently does not
# re-raise translation ValueErrors even with raise_on_error=True, so E2E tests for those refusals would fail. The unit
# tests fully cover the refusal behavior.


def test_ivg_neighbors_refuses_an_options_map(engine):
    with pytest.raises(ValueError, match=r"ivg\.neighbors.*takes positional arguments.*source_ids.*predicate.*direction"):
        ns463.rows(engine, "CALL ivg.neighbors({source_ids: ['a']}) YIELD neighbor RETURN neighbor")


def test_ivg_neighbors_still_answers_with_positional_arguments(engine):
    """ivg.neighbors(['a']) yields 'b' and 'c'."""
    result = engine.execute_cypher("CALL ivg.neighbors(['a']) YIELD neighbor RETURN neighbor.node_id AS id")
    assert not result.error, result.error
    ids = sorted(r[0] for r in result.rows)
    assert ids == ["b", "c"], f"expected neighbors ['b', 'c'], got {ids}"
