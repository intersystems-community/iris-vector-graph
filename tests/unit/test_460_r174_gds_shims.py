"""4.6.0 R174: `CALL gds.pageRank.stream(...)` and `CALL gds.betweenness.stream(...)` answer, and what they cannot
answer raises.

The shim renamed `gds.pageRank.stream` to `ivg.ppr` (personalized PageRank, which needs seeds) and
`gds.betweenness.stream` to `ivg.betweenness`, then looked the new name up among the engine's procedure handlers.
Neither is a handler (the translator answers them), so the lookup found nothing and the call went on to the
translator under its gds name, which answers every `gds.*` with a placeholder statement: no rows. Spec 195's data
model promised `gds.pageRank.stream(graph, config)` with `config.dampingFactor` and `config.maxIterations`, and
`gds.betweenness.stream(graph, config)` passing its configuration through.

The engine's own methods are patched here (the live answer, 1/3 each and 1 each on a 3-cycle, is
tests/e2e/test_460_r174_gds_shims_e2e.py); what is checked is what they are asked and how their answer comes back.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph import IRISGraphEngine

THIRD = 1 / 3
RANKS = [("a", THIRD), ("b", THIRD), ("c", THIRD)]
BETWEENNESS = [{"id": "a", "score": 1.0}, {"id": "b", "score": 1.0}, {"id": "c", "score": 1.0}]


@pytest.fixture
def eng():
    e = IRISGraphEngine(MagicMock(), embedding_dimension=8, native_cypher=False)
    with patch.object(IRISGraphEngine, "kg_PAGERANK", return_value=RANKS) as pr, patch.object(
        IRISGraphEngine, "betweenness_centrality", return_value=BETWEENNESS
    ) as bc:
        e.pr, e.bc = pr, bc
        yield e


def test_pagerank_stream_answers_global_pagerank(eng):
    r = eng.execute_cypher("CALL gds.pageRank.stream('g') YIELD nodeId, score")
    assert (r.columns, r.rows, r.error) == (["nodeId", "score"], [list(x) for x in RANKS], None)
    eng.pr.assert_called_once_with(damping=0.85, max_iterations=20)


def test_pagerank_stream_reads_its_configuration(eng):
    eng.execute_cypher(
        "CALL gds.pageRank.stream('g', {dampingFactor: 0.5, maxIterations: $m}) YIELD nodeId, score", {"m": 7}
    )
    eng.pr.assert_called_once_with(damping=0.5, max_iterations=7)


def test_pagerank_stream_return_of_the_yielded_names_is_projected(eng):
    r = eng.execute_cypher("CALL gds.pageRank.stream('g') YIELD nodeId, score RETURN score, nodeId")
    assert (r.columns, r.rows) == (["score", "nodeId"], [[s, n] for n, s in RANKS])


def test_pagerank_stream_refuses_a_configuration_key_it_does_not_read(eng):
    with pytest.raises(ValueError, match=r"tolerance.*dampingFactor"):
        eng.execute_cypher("CALL gds.pageRank.stream('g', {tolerance: 0.1}) YIELD nodeId, score")
    eng.pr.assert_not_called()


@pytest.mark.parametrize("tail", ["RETURN nodeId, score ORDER BY score DESC", "RETURN nodeId LIMIT 2",
                                  "MATCH (n) WHERE n.id = nodeId RETURN n, score"])
def test_what_the_shim_cannot_answer_raises(eng, tail):
    with pytest.raises(ValueError, match=r"gds\.pageRank\.stream"):
        eng.execute_cypher(f"CALL gds.pageRank.stream('g') YIELD nodeId, score {tail}")
    eng.pr.assert_not_called()


def test_pagerank_stream_under_use_graph_raises(eng):
    """IVG's PageRank reads the default graph; answering another graph's question with it would be wrong."""
    with pytest.raises(ValueError, match="USE GRAPH"):
        eng.execute_cypher("USE GRAPH 'g2' CALL gds.pageRank.stream('g') YIELD nodeId, score")
    eng.pr.assert_not_called()


def test_betweenness_stream_answers_betweenness(eng):
    r = eng.execute_cypher("CALL gds.betweenness.stream('g') YIELD nodeId, score")
    assert (r.columns, r.rows, r.error) == (["nodeId", "score"], [["a", 1.0], ["b", 1.0], ["c", 1.0]], None)
    eng.bc.assert_called_once_with(sample_size=0, direction="out", max_hops=0, top_k=0)


def test_betweenness_stream_passes_its_configuration_through(eng):
    eng.execute_cypher(
        "CALL gds.betweenness.stream('g', {sampleSize: 3, direction: 'both', maxHops: 2, topK: 2}) "
        "YIELD nodeId, score"
    )
    eng.bc.assert_called_once_with(sample_size=3, direction="both", max_hops=2, top_k=2)


def test_betweenness_stream_refuses_an_unknown_key(eng):
    with pytest.raises(ValueError, match="samplingSize"):
        eng.execute_cypher("CALL gds.betweenness.stream('g', {samplingSize: 3}) YIELD nodeId, score")


def test_betweenness_stream_under_use_graph_raises(eng):
    with pytest.raises(ValueError, match="USE GRAPH"):
        eng.execute_cypher("USE GRAPH 'g2' CALL gds.betweenness.stream('g') YIELD nodeId, score")


def test_a_shim_whose_ivg_procedure_has_no_handler_raises(eng):
    """gds.louvain.stream was renamed ivg.leiden, which no handler answers: it fell through to no rows."""
    with pytest.raises(ValueError, match=r"gds\.louvain\.stream.*ivg\.leiden"):
        eng.execute_cypher("CALL gds.louvain.stream('g') YIELD nodeId, communityId")
