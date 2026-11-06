"""4.6.0 R306: the wrong answers the algorithm oracle found, each on the smallest graph that shows it, worked by hand.

tests/e2e/test_460_r306_algorithm_oracle_e2e.py found them on generated graphs; these pin each one. Before R306:

1. A node with no edge was in no answer, and the n of degree and classical closeness counted only nodes with an edge.
2. ``closeness_centrality(direction="both")`` and ``CALL ivg.closeness({direction: 'both'})`` answered "out" scores.
3. The Cypher procedures over ``Graph.KG.Communities`` (and neighbourhood betweenness, through the k-hop walk) built
   their JSON by concatenation: an id with a quote failed the query.
4. ``kg_CDLP`` counted a self-loop as two votes for a node's own label.
5. Two nodes in the same position were ranked by float noise in their scores, not by id.
6. ``betweenness_centrality_neighborhood`` walked a named graph's edges too, once one had edges.
7. ``eigenvector_centrality`` counted an edge under each predicate on the ObjectScript route (``^NKG``) and each
   source once on the ``^KG`` walk, so parallel edges changed the answer by route.
8. The Python ranking read ``1.5`` as text; IRIS collation puts it among the numbers.
9. ``betweenness_centrality_neighborhood`` of a node with no edge raised ("seed node not found").
10. Leiden on a graph whose only edges are self-loops: the ObjectScript Louvain put every node in community 0, each
    of size 1 (read in the code; where leidenalg answers in IRIS the answer was right).

Runs in the scratch namespace ``IVG_NAMESPACE`` names (an IVG460*/IVG262* one with IVG installed: the R309 gate makes
one, or scripts/dev/make_scratch_namespace.py), or in USER with ``IVG_ORACLE_USER=1``, erasing its graph before each
case. Each case runs with Arno as found and with ``IVG_DISABLE_ARNO=1``.
"""

from __future__ import annotations

import os

import networkx as nx
import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")
_RUNS_HERE = NS.startswith(("IVG460", "IVG262")) or (NS == "USER" and os.environ.get("IVG_ORACLE_USER") == "1")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not _RUNS_HERE, reason="IVG_NAMESPACE must name an IVG460*/IVG262* scratch namespace "
                                              "(or USER, with IVG_ORACLE_USER=1): every case erases its graph"),
]


@pytest.fixture(scope="module")
def conn():
    c = ns262.connect(NS)
    yield c
    ns262.native(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.close()


@pytest.fixture(params=["arno", "no_arno"])
def load(conn, request, monkeypatch):
    if request.param == "no_arno":
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
        monkeypatch.setenv("IVG_DISABLE_ALGO_CACHE", "1")
    else:
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)

    def _load(edges, nodes=(), named=()):
        from iris_vector_graph import IRISGraphEngine

        ns262.native(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
        e = IRISGraphEngine(conn)
        for v in nodes:
            e.create_node(v)
        for s, p, o in edges:
            e.create_edge(s, p, o)
        for s, p, o in named:
            e.create_edge(s, p, o, graph="g2")
        e.sync()
        return e

    return _load


RING = [("r1", "R", "r2"), ("r2", "R", "r3"), ("r3", "R", "r1")]


def rows(e, q, **params):
    return e.execute_cypher(q, parameters=params or None, raise_on_error=True).rows


# 1 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_1_pagerank_sums_over_every_node(load):
    # solo spreads its rank evenly: y = 0.15/4 + 0.85 y/4, so y = 1/21; each ring node (1 - y)/3 = 20/63
    pr = dict(load(RING, nodes=["solo"]).kg_PAGERANK(max_iterations=200))
    assert pr == pytest.approx({"r1": 20 / 63, "r2": 20 / 63, "r3": 20 / 63, "solo": 1 / 21}, abs=1e-9)


def test_1_degree_and_closeness_count_every_node(load):
    e = load(RING, nodes=["solo"])
    assert [(d["id"], d["degree"], d["score"]) for d in e.degree_centrality(direction="out")] == [
        ("r1", 1, pytest.approx(1 / 3)), ("r2", 1, pytest.approx(1 / 3)), ("r3", 1, pytest.approx(1 / 3)), ("solo", 0, 0)
    ]
    # nobody reaches solo, so no node reaches all three others
    assert [(d["id"], d["score"]) for d in e.closeness_centrality(formula="classical")] == [
        ("r1", 0), ("r2", 0), ("r3", 0), ("solo", 0)
    ]
    assert [(d["id"], d["score"]) for d in e.closeness_centrality()] == [
        ("r1", 1.5), ("r2", 1.5), ("r3", 1.5), ("solo", 0)
    ]
    assert rows(e, "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, score, degree "
                   "RETURN node.node_id, score, degree") == [
        ["r1", pytest.approx(1 / 3), 1], ["r2", pytest.approx(1 / 3), 1], ["r3", pytest.approx(1 / 3), 1], ["solo", 0, 0]
    ]


def test_1_components_communities_cores_and_triangles_list_the_node(load):
    e = load(RING, nodes=["solo"])
    assert e.kg_WCC() == {"r1": "r1", "r2": "r1", "r3": "r1", "solo": "solo"}
    assert e.kg_CDLP()["solo"] == "solo"
    assert [(r["id"], r["coreness"]) for r in e.k_core(top_k=0)] == [("r1", 2), ("r2", 2), ("r3", 2), ("solo", 0)]
    assert [(r["id"], r["triangles"], r["lcc"]) for r in e.triangle_count(top_k=0)] == [
        ("r1", 1, 1.0), ("r2", 1, 1.0), ("r3", 1, 1.0), ("solo", 0, 0)
    ]
    assert [(r["id"], r["size"]) for r in e.strongly_connected_components(top_k=0)][-1] == ("solo", 1)
    assert [(r["id"], r["size"]) for r in e.leiden_communities(random_seed=1, top_k=0)][-1] == ("solo", 1)
    assert rows(e, "CALL ivg.kcore({topK: 100}) YIELD node, coreness RETURN node.node_id, coreness") == [
        ["r1", 2], ["r2", 2], ["r3", 2], ["solo", 0]
    ]
    assert rows(e, "CALL ivg.scc({topK: 100}) YIELD node, component, size RETURN node.node_id, size")[-1] == ["solo", 1]


# 2 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_2_closeness_both_reads_the_undirected_graph(load):
    # a -> b -> c read both ways: b is 1 from each end, a and c are 1 and 2 from the others
    e = load([("a", "R", "b"), ("b", "R", "c")])
    assert [(d["id"], d["score"]) for d in e.closeness_centrality(formula="classical", direction="both")] == [
        ("b", 1.0), ("a", pytest.approx(2 / 3)), ("c", pytest.approx(2 / 3))
    ]
    assert rows(e, "CALL ivg.closeness({direction: 'both'}) YIELD node, score RETURN node.node_id, score") == [
        ["b", 2.0], ["a", 1.5], ["c", 1.5]
    ]


# 3 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_3_an_id_with_a_quote_comes_back_from_every_procedure(load):
    q = 'q"t'
    e = load([(q, "R", "b"), ("b", "R", "c"), ("c", "R", q)])
    assert rows(e, "CALL ivg.triangleCount({topK: 10}) YIELD node, triangles RETURN node.node_id, triangles") == [
        ["b", 1], ["c", 1], [q, 1]
    ]
    assert rows(e, "CALL ivg.kcore({topK: 10}) YIELD node, coreness RETURN node.node_id, coreness") == [
        ["b", 2], ["c", 2], [q, 2]
    ]
    assert sorted(r[0] for r in rows(e, "CALL ivg.scc({topK: 10}) YIELD node RETURN node.node_id")) == ["b", "c", q]
    assert sorted(r[0] for r in rows(e, "CALL ivg.leiden({randomSeed: 1}) YIELD node RETURN node.node_id")) == [
        "b", "c", q
    ]
    # from q"t within 2 hops: the whole cycle, where each node lies on the one path between the other two
    assert e.betweenness_centrality_neighborhood(q, hops=2) == [
        {"id": "b", "score": 1.0}, {"id": "c", "score": 1.0}, {"id": q, "score": 1.0}
    ]


# 4 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_4_cdlp_a_self_loop_is_no_vote(load):
    # x -> y, y -> y: y's only neighbour other than itself is x, so round 1 gives x y's label and y x's
    e = load([("x", "R", "y"), ("y", "R", "y")])
    assert e.kg_CDLP(max_iterations=1) == {"x": "y", "y": "x"}


# 5 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_5_nodes_in_the_same_position_tie_and_go_by_id(load):
    # the oracle's seed 14 (tests/oracle/graphs.py): undirected, 10 and v56 both lie on shortest paths worth 4.2
    # (networkx), summed in another order; v56 came first by a last-digit difference
    from tests.oracle.graphs import generate

    g = generate(14)
    e = load([(x.s, x.p, x.o) for x in g.edges], nodes=g.nodes)
    G = nx.Graph([(x.s, x.o) for x in g.edges if x.s != x.o])
    assert nx.betweenness_centrality(G, normalized=False)["10"] == pytest.approx(4.2)
    assert nx.betweenness_centrality(G, normalized=False)["v56"] == pytest.approx(4.2)
    got = e.betweenness_centrality(sample_size=e.node_count(), direction="both", top_k=2)
    assert [(d["id"], round(d["score"], 9)) for d in got] == [("10", 4.2), ("v56", 4.2)]


# 6 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_6_neighbourhood_betweenness_stays_in_the_default_graph(load):
    # default a -> b -> c; g2 has b -> z. From a within 2 hops: a, b, c, and b lies between a and c alone
    e = load([("a", "R", "b"), ("b", "R", "c")], named=[("b", "R", "z")])
    assert e.betweenness_centrality_neighborhood("a", hops=2) == [{"id": "b", "score": 1.0}]


# 7 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_7_eigenvector_counts_each_source_once(load):
    edges = [("a", "R", "b"), ("a", "S", "b"), ("b", "R", "a"), ("b", "R", "c"), ("c", "R", "a")]
    G = nx.DiGraph([(s, o) for s, _, o in edges])
    want = nx.eigenvector_centrality(G, max_iter=1000, tol=1e-12)
    got = load(edges).eigenvector_centrality(max_iter=1000, tol=1e-12)
    assert {d["id"]: d["score"] for d in got} == pytest.approx(want, abs=1e-6)


# 8 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_8_a_decimal_id_sorts_among_the_numbers(load):
    # a named graph with an edge sends closeness to the Python walk, which ranks in Python
    e = load([("hub", "R", "a"), ("hub", "R", "1.5"), ("hub", "R", "10")], named=[("x", "R", "y")])
    assert [d["id"] for d in e.closeness_centrality()] == ["hub", "1.5", "10", "a"]


# 9 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_9_neighbourhood_of_a_node_with_no_edge_is_empty(load):
    assert load(RING, nodes=["solo"]).betweenness_centrality_neighborhood("solo") == []


# 10 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_10_leiden_with_only_self_loops_gives_each_node_its_own_community(load):
    e = load([("a", "R", "a"), ("b", "R", "b")])
    assert sorted((r["id"], r["size"]) for r in e.leiden_communities(top_k=0)) == [("a", 1), ("b", 1)]
    assert len({r["community"] for r in e.leiden_communities(top_k=0)}) == 2
    got = rows(e, "CALL ivg.leiden({topK: 10}) YIELD node, community, size RETURN node.node_id, community, size")
    assert len({r[1] for r in got}) == 2 and {r[2] for r in got} == {1}
