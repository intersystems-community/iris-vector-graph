"""4.6.5: a centrality or community procedure takes the values its engine method takes, and refuses the rest.

On 4.6.4 ``CALL ivg.closeness({formula: 'classic'})`` (also ``'bogus'`` and ``1``) answered the harmonic scores and
``{maxHops: -1}`` answered 0.0 for every node, where ``closeness_centrality(formula="classic")`` raised. The graph is
the lead's: a -> b, b -> c, c -> d, a -> c, worked by hand.

Out-edges, shortest hops: a reaches b (1), c (1), d (2); b reaches c (1), d (2); c reaches d (1); d reaches nothing.

  harmonic (the sum of 1/d over the nodes reached)   a 1 + 1 + 1/2 = 2.5, b 1 + 1/2 = 1.5, c 1, d 0
  classical ((n - 1) / the sum of d, for a node that reaches all n - 1 others, else 0)   a 3 / 4 = 0.75, the rest 0
  harmonic within 1 hop                               a 2, b 1, c 1, d 0

The undirected view is the triangle a, b, c with d hanging off c: coreness a 2, b 2, c 2, d 1; one triangle each for
a, b and c and none for d.

    IVG_NAMESPACE=IVG463WB pytest tests/e2e/test_465_procedure_options_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

NODES = ["a", "b", "c", "d"]
EDGES = [("a", "R", "b"), ("b", "R", "c"), ("c", "R", "d"), ("a", "R", "c")]


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
    assert eng.node_count() == 4, "the graph did not load"
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.close()


def scores(engine, options, proc="ivg.closeness"):
    rows = ns463.rows(engine, f"CALL {proc}({{{options}}}) YIELD node, score RETURN node.node_id, score")
    return [[n, pytest.approx(s)] for n, s in rows]


def test_closeness_defaults_to_the_harmonic_scores(engine):
    assert scores(engine, "") == [["a", 2.5], ["b", 1.5], ["c", 1.0], ["d", 0.0]]


def test_closeness_classical_is_not_the_harmonic_ranking(engine):
    """'classical' is a value both forms accept, and the answer is the classical one."""
    assert scores(engine, "formula: 'classical'") == [["a", 0.75], ["b", 0.0], ["c", 0.0], ["d", 0.0]]
    assert [[d["id"], d["score"]] for d in engine.closeness_centrality(formula="classical")] == [
        ["a", 0.75], ["b", 0.0], ["c", 0.0], ["d", 0.0]]


def test_closeness_max_hops_bounds_the_walk(engine):
    assert scores(engine, "maxHops: 1") == [["a", 2.0], ["b", 1.0], ["c", 1.0], ["d", 0.0]]


@pytest.mark.parametrize("options,option", [
    ("formula: 'classic'", "formula"),
    ("formula: 'bogus'", "formula"),
    ("formula: 1", "formula"),
    ("maxHops: -1", "maxHops"),
    ("topK: -1", "topK"),
])
def test_closeness_refuses_what_the_method_refuses_naming_the_option(engine, options, option):
    with pytest.raises(ValueError) as caught:
        ns463.rows(engine, f"CALL ivg.closeness({{{options}}}) YIELD node, score RETURN node, score")
    assert "ivg.closeness" in str(caught.value) and option in str(caught.value), str(caught.value)


def test_the_method_and_the_procedure_refuse_the_same_value(engine):
    with pytest.raises(ValueError, match="formula"):
        engine.closeness_centrality(formula="classic")
    with pytest.raises(ValueError, match="max_hops"):
        engine.closeness_centrality(max_hops=-1)


def test_degree_centrality_reads_its_options(engine):
    rows = ns463.rows(engine, "CALL ivg.degreeCentrality({direction: 'out', topK: 1}) "
                              "YIELD node, score, degree RETURN node.node_id, score, degree")
    assert [[n, pytest.approx(s), d] for n, s, d in rows] == [["a", pytest.approx(2 / 3), 2]]


def test_triangle_count_reads_topk(engine):
    rows = ns463.rows(engine, "CALL ivg.triangleCount({topK: 4}) YIELD node, triangles RETURN node.node_id, triangles")
    assert sorted((n, t) for n, t in rows) == [("a", 1), ("b", 1), ("c", 1), ("d", 0)]


def test_kcore_reads_topk(engine):
    rows = ns463.rows(engine, "CALL ivg.kcore({topK: 4}) YIELD node, coreness RETURN node.node_id, coreness")
    assert sorted((n, k) for n, k in rows) == [("a", 2), ("b", 2), ("c", 2), ("d", 1)]


@pytest.mark.parametrize("proc,yields,options,option", [
    ("ivg.degreeCentrality", "node, score", "topK: -1", "topK"),
    ("ivg.betweenness", "node, score", "maxHops: -1", "maxHops"),
    ("ivg.betweenness", "node, score", "memBudgetMB: 1", "memBudgetMB"),
    ("ivg.eigenvector", "node, score", "tol: 5", "tol"),
    ("ivg.leiden", "node, community", "gamma: 0", "gamma"),
    ("ivg.triangleCount", "node, triangles", "topK: 'x'", "topK"),
    ("ivg.scc", "node, component", "topK: -2", "topK"),
    ("ivg.kcore", "node, coreness", "topK: 1.5", "topK"),
])
def test_every_centrality_and_community_procedure_refuses_a_bad_option(engine, proc, yields, options, option):
    with pytest.raises(ValueError) as caught:
        ns463.rows(engine, f"CALL {proc}({{{options}}}) YIELD {yields} RETURN {yields}")
    assert proc in str(caught.value) and option in str(caught.value), str(caught.value)


def test_gds_betweenness_refuses_a_fractional_sample_size_it_used_to_truncate(engine):
    with pytest.raises(ValueError, match=r"gds\.betweenness\.stream.*sampleSize"):
        ns463.rows(engine, "CALL gds.betweenness.stream('g', {sampleSize: 2.7}) YIELD nodeId, score")


def test_bm25_search_refuses_a_zero_k_before_the_index_is_read(engine):
    with pytest.raises(ValueError, match=r"ivg\.bm25\.search.*k"):
        ns463.rows(engine, "CALL ivg.bm25.search('no_such_index', 'q', 0) YIELD node, score RETURN node, score")
