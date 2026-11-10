"""4.6.0 R306: the algorithm oracle's generator and reference, checked on hand-worked graphs and against networkx.

The reference (tests/oracle/reference.py) is what tests/e2e/test_460_r306_algorithm_oracle_e2e.py holds IVG to, so
it is checked here first, offline: each expected value below is worked by hand or is networkx's, never IVG's.
"""

from __future__ import annotations

import math

import networkx as nx
import pytest

from tests.oracle import reference as ref
from tests.oracle.compare import compare_partition, compare_ranked, compare_values
from tests.oracle.graphs import ADVERSARIAL_IDS, SHAPES, Edge, OracleGraph, generate


def G(nodes, pairs, **kw) -> OracleGraph:
    return OracleGraph(seed=-1, nodes=list(nodes), edges=[Edge(s, "R", o) for s, o in pairs], **kw)


RING_SOLO = G(["r1", "r2", "r3", "solo"], [("r1", "r2"), ("r2", "r3"), ("r3", "r1")])
#: COMMUNITIES.md's example: two directed triangles joined by c -> x.
TWO_TRIANGLES = G(
    "abcxyz", [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")]
)


# ── collation and ranking ─────────────────────────────────────────────────────────────────────────────────────────


def test_collation_puts_canonical_numbers_first_by_value_then_text_by_code_point():
    ids = ["b", "10", "9", "-1", "07", "1.5", "a", "P1", "B", "0", ".5", "1.50", "-0", "+1", "Ω", "ünï", "a'b"]
    assert sorted(ids, key=ref.collation_key) == [
        "-1", "0", ".5", "1.5", "9", "10",
        "+1", "-0", "07", "1.50", "B", "P1", "a", "a'b", "b", "ünï", "Ω",
    ]


@pytest.mark.parametrize("s", ["0", "7", "-3", "10", "1.5", ".25", "-.5", "-12.75"])
def test_canonical_numbers(s):
    assert ref.is_canonical_number(s)


@pytest.mark.parametrize("s", ["07", "-0", "+1", "1.0", "1.50", "0.5", "1e3", "", "-", ".", "1.", " 1", "1 "])
def test_not_canonical_numbers(s):
    assert not ref.is_canonical_number(s)


def test_ranked_breaks_ties_by_collation_and_cuts_after_ranking():
    scores = {"b": 1.0, "10": 1.0, "9": 1.0, "a": 2.0, "z": 0.5}
    assert ref.ranked(scores) == [("a", 2.0), ("9", 1.0), ("10", 1.0), ("b", 1.0), ("z", 0.5)]
    assert [v for v, _ in ref.ranked(scores, top_k=2)] == ["a", "9"]


def test_ranked_treats_float_noise_as_a_tie():
    assert [v for v, _ in ref.ranked({"b": 0.1 + 0.2, "a": 0.3})] == ["a", "b"]


# ── PageRank ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_pagerank_with_an_edgeless_node_worked_by_hand():
    # KNOWN_ISSUES's example: solo spreads its rank evenly; y = 0.15/4 + 0.85 y/4 gives y = 1/21
    pr = ref.pagerank_global(RING_SOLO, max_iterations=200)
    assert pr["solo"] == pytest.approx(1 / 21)
    assert pr["r1"] == pytest.approx(20 / 63)
    assert sum(pr.values()) == pytest.approx(1.0)
    pre = ref.pagerank_global(RING_SOLO, max_iterations=200, include_isolated=False)
    assert pre == pytest.approx({"r1": 1 / 3, "r2": 1 / 3, "r3": 1 / 3})


def test_pagerank_two_iterations_by_hand():
    # a -> b, b dangling. x0 = (1/2, 1/2). Round 1: dangling b spreads 0.85*0.5/2 to each, a gives 0.85*0.5 to b.
    g = G("ab", [("a", "b")])
    one = ref.pagerank_global(g, max_iterations=1)
    assert one["a"] == pytest.approx(0.075 + 0.2125)
    assert one["b"] == pytest.approx(0.075 + 0.2125 + 0.425)


@pytest.mark.parametrize("seed", range(12))
def test_pagerank_converges_to_networkx_on_the_multigraph(seed):
    g = generate(seed)
    mine = ref.pagerank_global(g, max_iterations=500)
    theirs = nx.pagerank(ref.multidigraph(g), weight=None, tol=1e-13, max_iter=1000)
    assert mine == pytest.approx(theirs, abs=1e-9)
    assert sum(mine.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("seed", range(12))
def test_personalized_pagerank_is_networkx(seed):
    g = generate(seed)
    seeds = g.with_edges()[:2]
    mine = ref.personalized_pagerank(g, seeds, tolerance=1e-12, max_iterations=1000)
    pers = {v: (1.0 if v in seeds else 0.0) for v in g.nodes}
    theirs = nx.pagerank(ref.multidigraph(g), personalization=pers, weight=None, tol=1e-12, max_iter=1000)
    # every node, an unreached one at 0.0 where networkx's iterate is within 1e-6 of it (4.7.0 row 9)
    assert set(mine) == set(theirs)
    assert mine == pytest.approx(theirs, abs=1e-6)


def test_personalized_pagerank_from_an_edgeless_seed_is_all_on_the_seed():
    assert ref.personalized_pagerank(RING_SOLO, ["solo"], tolerance=1e-12) == pytest.approx(
        {"solo": 1.0, "r1": 0.0, "r2": 0.0, "r3": 0.0}
    )


# ── degree, betweenness, closeness ────────────────────────────────────────────────────────────────────────────────


def test_degree_counts_every_node_in_n():
    d = ref.degree_centrality(RING_SOLO, "out")
    assert d["r1"] == (pytest.approx(1 / 3), 1)
    assert d["solo"] == (0.0, 0)
    assert ref.degree_centrality(RING_SOLO, "out", include_isolated=False)["r1"] == (pytest.approx(0.5), 1)


def test_degree_both_counts_a_self_loop_twice_and_parallel_edges_each():
    g = OracleGraph(seed=-1, nodes=["a", "b"], edges=[Edge("a", "R", "a"), Edge("a", "R", "b"), Edge("a", "S", "b")])
    d = ref.degree_centrality(g, "both")
    assert d["a"][1] == 4 and d["b"][1] == 2
    assert ref.degree_centrality(g, "out", predicate="S")["a"] == (1.0, 1)


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("direction", ["out", "both"])
def test_betweenness_is_networkx(seed, direction):
    g = generate(seed)
    G_ = ref.digraph(g) if direction == "out" else ref.undirected_simple(g)
    assert ref.betweenness(g, direction) == pytest.approx(nx.betweenness_centrality(G_, normalized=False))


def test_betweenness_in_equals_out():
    g = generate(3)
    assert ref.betweenness(g, "in") == pytest.approx(ref.betweenness(g, "out"))


def test_betweenness_max_hops_by_hand():
    path = G("abcd", [("a", "b"), ("b", "c"), ("c", "d")])
    assert ref.betweenness(path) == {"a": 0.0, "b": 2.0, "c": 2.0, "d": 0.0}
    # within 2 hops only a..c (through b) and b..d (through c)
    assert ref.betweenness(path, max_hops=2) == {"a": 0.0, "b": 1.0, "c": 1.0, "d": 0.0}


def test_neighbourhood_betweenness_is_directed_on_the_out_neighbourhood():
    # from a within 2 hops: a, b, c (d is 3 hops); in that subgraph only b lies between a and c
    g = G("abcde", [("a", "b"), ("b", "c"), ("c", "d"), ("e", "b")])
    assert ref.betweenness_neighborhood(g, "a", hops=2) == {"a": 0.0, "b": 1.0, "c": 0.0}
    assert ref.betweenness_neighborhood(g, "missing") == {}


@pytest.mark.parametrize("seed", range(12))
def test_harmonic_closeness_out_is_networkx_on_the_reversed_graph(seed):
    g = generate(seed)
    theirs = nx.harmonic_centrality(ref.digraph(g).reverse())
    assert ref.closeness(g, "harmonic", "out") == pytest.approx(theirs)
    assert ref.closeness(g, "harmonic", "in") == pytest.approx(nx.harmonic_centrality(ref.digraph(g)))
    assert ref.closeness(g, "harmonic", "both") == pytest.approx(nx.harmonic_centrality(ref.undirected_simple(g)))


def test_classical_closeness_by_hand():
    # each ring node reaches the other two at 1 and 2: 2/3; with solo counted nobody reaches all three others
    assert ref.closeness(RING_SOLO, "classical", include_isolated=False) == pytest.approx(
        {"r1": 2 / 3, "r2": 2 / 3, "r3": 2 / 3}
    )
    assert set(ref.closeness(RING_SOLO, "classical").values()) == {0.0}
    # "both" on the directed path a -> b -> c: b reaches both at 1 (2/2), a and c at 1 and 2 (2/3)
    path = G("abc", [("a", "b"), ("b", "c")])
    assert ref.closeness(path, "classical", "both") == pytest.approx({"a": 2 / 3, "b": 1.0, "c": 2 / 3})
    assert ref.closeness(path, "classical", "out") == {"a": 1.0 * 2 / 3, "b": 0.0, "c": 0.0}


def test_closeness_max_hops():
    path = G("abcd", [("a", "b"), ("b", "c"), ("c", "d")])
    assert ref.closeness(path, "harmonic", max_hops=2)["a"] == pytest.approx(1 + 1 / 2)


# ── eigenvector ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_eigenvector_converges_to_networkx_on_a_strongly_connected_aperiodic_graph():
    g = G("abcd", [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "a"), ("b", "a")])
    mine, converged = ref.eigenvector(g, max_iter=1000, tol=1e-12)
    assert converged
    assert mine == pytest.approx(nx.eigenvector_centrality_numpy(ref.digraph(g)), abs=1e-6)


def test_eigenvector_edgeless_node_takes_part_as_in_networkx():
    # the shift (A + I) keeps solo's own score while the ring's grows twice as fast, so solo tends to 0 but is not 0
    x, converged = ref.eigenvector(RING_SOLO, max_iter=100)
    assert converged
    assert 0 < x["solo"] < 1e-5 and x["r1"] == pytest.approx(1 / math.sqrt(3), abs=1e-6)


def test_eigenvector_on_a_directed_3_cycle_with_a_tail_does_not_depend_on_max_iter_mod_3():
    # 4.6.0 round 3: an unshifted iteration (x A) cycles with period 3 here
    g = G("abct", [("a", "b"), ("b", "c"), ("c", "a"), ("c", "t")])
    answers = [ref.eigenvector(g, max_iter=m)[0] for m in (999, 1000, 1001)]
    assert answers[0] == answers[1] == answers[2] is not None


def test_eigenvector_reports_networkx_s_failed_convergence():
    assert ref.eigenvector(G("abcd", [("a", "b"), ("b", "c"), ("c", "d")]), max_iter=30) == (None, False)


# ── components and communities (COMMUNITIES.md's worked answers) ─────────────────────────────────────────────────


def test_communities_examples():
    assert ref.wcc_labels(TWO_TRIANGLES) == dict.fromkeys("abcxyz", "a")
    assert ref.scc_sets(TWO_TRIANGLES) == [frozenset("abc"), frozenset("xyz")]
    assert ref.core_numbers(TWO_TRIANGLES) == dict.fromkeys("abcxyz", 2)
    t = ref.triangles(TWO_TRIANGLES)
    assert [(v, t[v][0], round(t[v][1], 3)) for v in "abcxyz"] == [
        ("a", 1, 1.0), ("b", 1, 1.0), ("c", 1, 0.333), ("x", 1, 0.333), ("y", 1, 1.0), ("z", 1, 1.0)
    ]


def test_cdlp_examples():
    assert ref.cdlp(TWO_TRIANGLES) == {"a": "a", "b": "a", "c": "a", "x": "c", "y": "c", "z": "c"}
    path = G("abc", [("a", "b"), ("b", "c")])
    assert ref.cdlp(path, max_iterations=1) == {"a": "b", "b": "a", "c": "b"}
    assert ref.cdlp(path, max_iterations=2) == {"a": "a", "b": "b", "c": "a"}
    assert ref.cdlp(path, max_iterations=3) == {"a": "b", "b": "a", "c": "b"}


def test_cdlp_tie_goes_to_the_number_first_in_collation():
    g = G(["abc", "5", "m"], [("m", "abc"), ("m", "5")])
    assert ref.cdlp(g, max_iterations=1)["m"] == "5"


def test_wcc_labels_by_collation_and_isolated_nodes_are_their_own_component():
    g = G(["b", "10", "9", "solo"], [("b", "10"), ("10", "9")])
    assert ref.wcc_labels(g) == {"b": "9", "10": "9", "9": "9", "solo": "solo"}
    assert "solo" not in ref.wcc_labels(g, include_isolated=False)


def test_modularity_of_the_two_triangles():
    q = ref.modularity(TWO_TRIANGLES, [set("abc"), set("xyz")])
    assert q == pytest.approx(nx.community.modularity(ref.undirected_simple(TWO_TRIANGLES), [set("abc"), set("xyz")]))
    assert q == pytest.approx(5 / 14)
    assert ref.best_louvain_modularity(TWO_TRIANGLES) == pytest.approx(q)


def test_shortest_paths():
    g = OracleGraph(seed=-1, nodes=list("abcd"), edges=[
        Edge("a", "R", "b", 5.0), Edge("a", "S", "b", 1.0), Edge("b", "R", "c"), Edge("a", "R", "c", 3.0)])
    assert ref.hop_distance(g, "a", "c") == 1
    assert ref.weighted_cost(g, "a", "c") == 2.0
    assert ref.hop_distance(g, "c", "a") is None and ref.weighted_cost(g, "a", "d") is None
    assert ref.is_path(g, ["a", "b", "c"]) and not ref.is_path(g, ["a", "d"])


# ── the generator ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_generator_is_deterministic():
    assert generate(5).literal() == generate(5).literal()
    assert generate(5).literal() != generate(6).literal()


def test_generator_literal_rebuilds_the_graph():
    g = generate(9)
    again = eval(g.literal(), {"OracleGraph": OracleGraph, "Edge": Edge})  # noqa: S307 - our own literal
    assert again == g


def test_every_shape_and_id_kind_is_covered_by_the_first_seeds():
    graphs = [generate(s) for s in range(len(SHAPES) * 3)]
    for i, shape in enumerate(SHAPES):
        assert shape in graphs[i].shapes
    assert any(g.isolated() for g in graphs)
    assert any(e.s == e.o for g in graphs for e in g.edges)
    assert any(g.has_named_edges for g in graphs)
    assert any(e.weight is not None for g in graphs for e in g.edges)
    assert any(len({(e.s, e.o) for e in g.edges}) < len(g.edges) for g in graphs)  # parallel edges
    seen = {v for g in graphs for v in g.nodes}
    assert {"9", "10", "07", "-1"} & seen and {"a'b", 'q"t'} & seen
    assert set(ADVERSARIAL_IDS) & seen


@pytest.mark.parametrize("seed", range(40))
def test_generated_graphs_are_well_formed(seed):
    g = generate(seed)
    assert 3 <= len(g.nodes) <= 25
    assert len(set(g.nodes)) == len(g.nodes)
    assert {e.s for e in g.edges} | {e.o for e in g.edges} <= set(g.nodes)
    assert len({(e.s, e.p, e.o) for e in g.edges}) == len(g.edges)
    assert {e.s for e in g.named_edges} | {e.o for e in g.named_edges} <= set(g.named_nodes)


# ── the comparison ────────────────────────────────────────────────────────────────────────────────────────────────

def test_compare_ranked_accepts_the_documented_answer():
    exp = {"a": 2.0, "10": 1.0, "9": 1.0, "z": 0.0}
    assert compare_ranked([("a", 2.0), ("9", 1.0), ("10", 1.0), ("z", 0.0)], exp) == []
    assert compare_ranked([("a", 2.0), ("9", 1.0)], exp, top_k=2) == []
    assert compare_ranked([("a", 2.0), ("9", 1.0), ("10", 1.0)], exp, omit_zero=True) == []


def test_compare_ranked_catches_each_defect_class():
    exp = {"a": 2.0, "10": 1.0, "9": 1.0, "z": 0.5}
    assert any("tie order" in p for p in compare_ranked([("a", 2), ("10", 1), ("9", 1), ("z", 0.5)], exp))
    assert any("order:" in p for p in compare_ranked([("z", 0.5), ("a", 2), ("9", 1), ("10", 1)], exp))
    assert any("missing" in p for p in compare_ranked([("a", 2), ("9", 1), ("10", 1)], exp))
    assert any("scores off" in p for p in compare_ranked([("a", 2), ("9", 1), ("10", 1), ("z", 0.6)], exp))
    # the cut kept the first by id order rather than the highest
    assert compare_ranked([("10", 1), ("9", 1)], exp, top_k=2)


def test_compare_values_and_partition():
    assert compare_values({"a": 1, "b": 2.0}, {"a": 1, "b": 2.0 + 1e-12}) == []
    assert compare_values({"a": 1}, {"a": 1, "solo": 0})
    assert compare_partition({"a": 0, "b": 0, "c": 1}, [{"c"}, {"b", "a"}]) == []
    assert compare_partition({"a": 0, "b": 1, "c": 1}, [{"c"}, {"b", "a"}])
