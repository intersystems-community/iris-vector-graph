"""4.6.0 R174: `CALL gds.pageRank.stream('g') YIELD nodeId, score` and `CALL gds.betweenness.stream('g') YIELD nodeId,
score` answer, on a 3-cycle r174a -> r174b -> r174c -> r174a, the scores worked by hand.

Before: no rows and no error. The namespace (IVG262CONA, or IVG_R174_NAMESPACE; IVG262* only) is made anew and set up
by this release, so the cycle is its only graph.

By hand:
- PageRank (IVG's, which sums to 1): every node has one in-edge from a node of out-degree 1, so the uniform vector is
  the fixed point, 1/3 each, at any damping.
- Betweenness, directed (shortest paths between ordered pairs of other nodes): r174a -> r174c goes through r174b only,
  r174b -> r174a through r174c, r174c -> r174b through r174a; every other ordered pair is one edge. 1 each. The shim
  passes ivg.betweenness's configuration through, and IVG's betweenness counts ordered pairs, unnormalized.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262, scratch_vars

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_R174_NAMESPACE")]

NS = os.environ.get("IVG_R174_NAMESPACE", "")
CYCLE = ("r174a", "r174b", "r174c")


@pytest.fixture(scope="module")
def engine():
    if not NS.startswith(("IVG262", "IVG460FX", "IVG470")):  # the scratch prefixes make_scratch_namespace drops
        pytest.fail(f"refusing {NS}: an IVG262*, IVG460FX* or IVG470* scratch namespace only")
    from iris_vector_graph import IRISGraphEngine

    ns262.recreate(NS)
    c = ns262.connect(NS)
    e = IRISGraphEngine(c, embedding_dimension=8, namespace=NS, native_cypher=False)
    e.initialize_schema()
    for n in CYCLE:
        assert e.create_node(n, labels=["R174"])
    for a, b in zip(CYCLE, CYCLE[1:] + CYCLE[:1]):
        assert e.create_edge(a, "R", b)
    c.commit()
    e.sync()
    yield e
    c.close()


@pytest.mark.parametrize("raise_on_error", [False, True])
def test_pagerank_stream_answers_a_third_each(engine, raise_on_error):
    r = engine.execute_cypher("CALL gds.pageRank.stream('g') YIELD nodeId, score", raise_on_error=raise_on_error)
    assert r.error is None and r.columns == ["nodeId", "score"]
    assert sorted(n for n, _ in r.rows) == list(CYCLE)
    assert [round(s, 6) for _, s in sorted(r.rows)] == [round(1 / 3, 6)] * 3


def test_pagerank_stream_with_configuration(engine):
    r = engine.execute_cypher(
        "CALL gds.pageRank.stream('g', {dampingFactor: 0.5, maxIterations: 40}) YIELD nodeId, score RETURN score")
    assert r.columns == ["score"] and [round(s, 6) for (s,) in r.rows] == [round(1 / 3, 6)] * 3


@pytest.mark.parametrize("raise_on_error", [False, True])
def test_betweenness_stream_answers_one_each(engine, raise_on_error):
    r = engine.execute_cypher("CALL gds.betweenness.stream('g') YIELD nodeId, score", raise_on_error=raise_on_error)
    assert r.error is None and r.columns == ["nodeId", "score"]
    assert sorted((n, round(s, 6)) for n, s in r.rows) == [(n, 1.0) for n in CYCLE]


def test_what_the_shim_cannot_answer_raises(engine):
    with pytest.raises(ValueError, match="nothing more"):
        engine.execute_cypher("CALL gds.pageRank.stream('g') YIELD nodeId, score RETURN nodeId ORDER BY score DESC")
    with pytest.raises(ValueError, match="USE GRAPH"):
        engine.execute_cypher("USE GRAPH 'g2' CALL gds.betweenness.stream('g') YIELD nodeId, score")
    with pytest.raises(ValueError, match="ivg.leiden"):
        engine.execute_cypher("CALL gds.louvain.stream('g') YIELD nodeId, communityId")
