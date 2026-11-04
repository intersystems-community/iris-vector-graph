"""4.6.0 R095: `khop` answers one shape, the same with the Arno library loaded and without it.

With Arno loaded (hops above 1) the answer was the library's `KHopNeighbors`: `{"seed", "hops", "totalNodes",
"nodes": [{"id", "dist"}]}`, no edges. Without it, `{"nodes": [ids], "edges": [...]}` from
`Graph.KG.Traversal.BFSFastJson`, its node list built from a set (any order) and missing the seed when the seed has
no out-edge. Now always `{"nodes", "edges"}` from the walk: the seed first, then the nodes in the order the walk
reached them, at most `max_nodes`, and the edges walked between listed nodes, each `{"s", "p", "o", "w", "step"}`.

Worked by hand on alice -> bob (KNOWS), alice -> acme (WORKS_AT), bob -> carol, bob -> alice, carol -> dave,
erin -> alice (all KNOWS but WORKS_AT). Outbound from alice: hop 1 walks alice->bob and alice->acme; hop 2 walks bob's
edges, bob->alice and bob->carol (acme has none). carol->dave is a third hop. dave has no out-edge.

Run on the enterprise container with the Arno library installed (arno-callout 1.2.1), once as loaded and once with
IVG_DISABLE_ARNO=1; the Arno case skips where the library is not loaded.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

EDGES = [("alice", "KNOWS", "bob"), ("alice", "WORKS_AT", "acme"), ("bob", "KNOWS", "carol"),
         ("bob", "KNOWS", "alice"), ("carol", "KNOWS", "dave"), ("erin", "KNOWS", "alice")]


def _e(s, p, o, step):
    return {"s": s, "p": p, "o": o, "w": 1, "step": step}


TWO_HOPS = {
    "nodes": ["alice", "bob", "acme", "carol"],
    "edges": [_e("alice", "KNOWS", "bob", 1), _e("alice", "WORKS_AT", "acme", 1),
              _e("bob", "KNOWS", "alice", 2), _e("bob", "KNOWS", "carol", 2)],
}
ONE_HOP = {"nodes": ["alice", "bob", "acme"],
           "edges": [_e("alice", "KNOWS", "bob", 1), _e("alice", "WORKS_AT", "acme", 1)]}
CAPPED = {"nodes": ["alice", "bob"], "edges": [_e("alice", "KNOWS", "bob", 1), _e("bob", "KNOWS", "alice", 2)]}


@pytest.fixture(scope="module")
def graph(iris_connection):
    import iris

    from iris_vector_graph import IRISGraphEngine

    iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Eraser", "EraseAll")
    e = IRISGraphEngine(iris_connection)
    for n in ("alice", "bob", "carol", "dave", "acme", "erin"):
        e.create_node(n)
    for s, p, o in EDGES:
        e.create_edge(s, p, o)
    e.sync()
    yield iris_connection
    iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Eraser", "EraseAll")


@pytest.fixture(params=["arno", "no_arno"])
def engine(graph, request, monkeypatch):
    from iris_vector_graph import IRISGraphEngine

    if request.param == "no_arno":
        monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    else:
        monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    e = IRISGraphEngine(graph)
    loaded = e._detect_arno() and "khop" in e._arno_capabilities.get("algorithms", [])
    if request.param == "arno" and not loaded:
        pytest.skip("the Arno library is not loaded on this container")
    assert loaded == (request.param == "arno")
    return e


def test_two_hops(engine):
    assert engine.khop("alice", hops=2) == TWO_HOPS


def test_one_hop(engine):
    assert engine.khop("alice", hops=1) == ONE_HOP


def test_max_nodes_cuts_in_walk_order_and_keeps_the_edges_between_listed_nodes(engine):
    assert engine.khop("alice", hops=2, max_nodes=2) == CAPPED


def test_a_seed_with_no_out_edge_is_its_own_neighbourhood(engine):
    assert engine.khop("dave", hops=2) == {"nodes": ["dave"], "edges": []}


def test_a_missing_seed_reaches_nothing(engine):
    assert engine.khop("no-such-node", hops=2) == {"nodes": [], "edges": []}
