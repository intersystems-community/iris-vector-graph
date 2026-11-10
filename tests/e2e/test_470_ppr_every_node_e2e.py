"""4.7.0 row 9, live: personalized PageRank answers every node of the graph, 0.0 for a node it never reaches.

The walk scored only the nodes it reached, so a node with no edge (and one the seeds never reach) had no row; networkx's
``pagerank(G, personalization=...)`` answers it 0.0. On a -> b plus a loner c, seed a, alpha 0.85: b is dangling and
hands its score back to the seed (the convention IVG keeps, networkx's ``dangling=None``), so a = 0.15 + 0.85b and
b = 0.85a: a = 1 / 1.85 = 0.540541, b = 0.85 / 1.85 = 0.459459, c = 0.0. Each route is checked against those values,
which the fixture first checks against networkx.

    IVG_NAMESPACE=IVG470ALG IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_ppr_every_node_e2e.py
"""

from __future__ import annotations

from unittest.mock import patch

import networkx as nx
import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import call_scores, conn, load  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

#: name -> (nodes, edges, hand-worked scores from seed a at alpha 0.85)
GRAPHS = {
    # b dangling: a = 0.15 + 0.85b, b = 0.85a
    "a loner": (["a", "b", "c"], [("a", "R", "b")], {"a": 1 / 1.85, "b": 0.85 / 1.85, "c": 0.0}),
    # d -> a: the seeds never reach d, so it is 0 too; a and b as above
    "a node not reached": (["a", "b", "c", "d"], [("a", "R", "b"), ("d", "R", "a")],
                           {"a": 1 / 1.85, "b": 0.85 / 1.85, "c": 0.0, "d": 0.0}),
}


def _networkx(nodes, edges):
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from((s, o) for s, _p, o in edges)
    return nx.pagerank(g, alpha=0.85, personalization={"a": 1}, max_iter=1000, tol=1e-12)


@pytest.fixture(scope="module", params=list(GRAPHS))
def world(request, conn):  # noqa: F811
    nodes, edges, hand = GRAPHS[request.param]
    # the hand-worked scores are networkx's, so the dangling convention here is networkx's dangling=None
    assert _networkx(nodes, edges) == pytest.approx(hand, abs=1e-6)
    return load(conn, nodes, edges), hand


def _assert_every_node(got, want):
    assert set(got) == set(want), f"rows {sorted(got)}, the graph's nodes {sorted(want)}"
    assert got == pytest.approx(want, abs=1e-4)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_call_ivg_ppr_answers_every_node(world, route):
    engs, want = world
    got, n = call_scores(engs[route], "CALL ivg.ppr(['a'], 0.85, 100) YIELD node, score "
                                      "RETURN node.node_id AS id, score")
    assert n == len(want)
    _assert_every_node(got, want)


def test_kg_personalized_pagerank_store_route_answers_every_node(world):
    engs, want = world
    _assert_every_node(engs["default"].kg_PERSONALIZED_PAGERANK(["a"]), want)


def test_kg_personalized_pagerank_fast_path_answers_every_node(world, monkeypatch):
    engs, want = world
    from iris_vector_graph import IRISGraphEngine

    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    eng = IRISGraphEngine(engs["default"].conn, namespace=ns463.NS, schema_prefix=engs["default"]._schema_prefix)
    eng._store_capabilities = dict(eng._store_capabilities, ppr=False)
    eng.capabilities.objectscript_deployed = True
    eng.capabilities.kg_built = True
    # pinned: a failing RunJson call would otherwise be answered by the fallback
    with patch.object(eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")):
        got = eng.kg_PERSONALIZED_PAGERANK(["a"])
    _assert_every_node(got, want)


def test_python_fallback_answers_every_node(world):
    engs, want = world
    _assert_every_node(engs["default"]._kg_PERSONALIZED_PAGERANK_python_fallback(["a"], 0.85, 100, 1e-6), want)


def test_ppr_method_answers_every_node_highest_first(world):
    engs, want = world
    scores = engs["default"].ppr("a", max_iter=100)["scores"]
    _assert_every_node({d["id"]: d["score"] for d in scores}, want)
    # highest first, the zeros last, ties by id
    assert [d["id"] for d in scores] == sorted(want, key=lambda k: (-round(want[k], 9), k))


def test_top_k_cuts_the_zeros_first(world):
    engs, _want = world
    got = engs["default"].kg_PERSONALIZED_PAGERANK(["a"], return_top_k=3)
    assert list(got) == ["a", "b", "c"]
    assert got["c"] == 0.0


def test_kg_ppr_guided_subgraph_keeps_only_scored_nodes(world):
    """The guided subgraph grows from the nodes PPR scores, not from the zeros now in its answer."""
    engs, _want = world
    sub = engs["default"].kg_PPR_GUIDED_SUBGRAPH(["a"], ppr_top_k=10)
    assert [k for k, _v in sub.ppr_scores] == ["a", "b"]
