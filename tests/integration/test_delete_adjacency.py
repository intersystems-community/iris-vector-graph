"""Deleting an edge leaves nothing behind — verified by the Phase 1 oracle.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_delete_adjacency.py

`create_edge` then `delete_edge` should return the graph to exactly the state it
was in before. It did not. Three defects stacked:

1. `EdgeScan.DeleteAdjacency` removed `^KG("out")` and `^KG("in")` but delegated
   the counters to `GraphIndex.DeleteIndex`.
2. `DeleteIndex` hardcoded the graph key to `0`, so a *named* graph's adjacency
   survived deletion entirely.
3. `DeleteIndex` spelled the counters `^KG("deg", s)` — no graph subscript — so
   the node id landed in the graph-key position. The real counter was never
   decremented and a bogus entry appeared beside every real graph.

The strongest test here is the round trip: seed, delete, then ask
`verify_graph` whether anything is left. That reuses the Phase 1 oracle as the
test surface instead of hand-checking subscripts, so these tests keep working if
the layout changes but the invariant does not.

The subscript-level tests below exist for the one thing the oracle cannot see:
pollution at the *graph-key* level of `^KG("deg")`, which belongs to no graph and
so is never walked by a graph-scoped check.
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "deladj_acme"


@pytest.fixture()
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


@pytest.fixture()
def kg(engine):
    """Raw global access, for the assertions the oracle cannot make."""
    import iris

    return iris.createIRIS(engine.conn)


def _deg(kg, graph, node):
    raw = kg.get("^KG", "deg", graph, node)
    return None if raw is None else int(raw)


def _degp(kg, graph, node, predicate):
    raw = kg.get("^KG", "degp", graph, node, predicate)
    return None if raw is None else int(raw)


# --- the round trip, through the Phase 1 oracle ------------------------------


def test_delete_edge_leaves_a_named_graph_clean(engine):
    """The whole point, stated once: delete must undo create."""
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    assert engine.verify_graph(GRAPH)["ok"] == 1, "the seed itself drifted"

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    report = engine.verify_graph(GRAPH)
    assert report["ok"] == 1, f"drift left behind by delete_edge: {report['drift']}"
    assert report["counts"]["sqlEdges"] == 0
    assert report["counts"]["outEdges"] == 0, (
        "^KG('out') entries survived the delete — DeleteIndex hardcoded graph key 0, "
        "so a named graph's adjacency was never reached"
    )
    assert report["counts"]["degNodes"] == 0, (
        "the degree counter outlived the edge it counted"
    )


def test_delete_edge_leaves_the_default_graph_clean(engine):
    engine.create_edge("d1", "KNOWS", "d2")
    engine.delete_edge("d1", "KNOWS", "d2")

    report = engine.verify_graph("")
    assert report["ok"] == 1, f"drift left behind in the default graph: {report['drift']}"
    assert report["counts"]["degNodes"] == 0


def test_deleting_one_of_two_edges_leaves_the_other_intact(engine):
    """The counter must be decremented, not killed outright."""
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("a", "KNOWS", "c", graph=GRAPH)

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    report = engine.verify_graph(GRAPH)
    assert report["ok"] == 1, f"unexpected drift: {report['drift']}"
    assert report["counts"]["sqlEdges"] == 1
    assert report["counts"]["outEdges"] == 1


def test_deleting_in_one_graph_does_not_touch_another(engine):
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("a", "KNOWS", "b", graph="deladj_other")

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    assert engine.verify_graph(GRAPH)["counts"]["outEdges"] == 0
    other = engine.verify_graph("deladj_other")
    assert other["ok"] == 1, f"the untouched graph drifted: {other['drift']}"
    assert other["counts"]["outEdges"] == 1, (
        "deleting from one graph removed another graph's adjacency"
    )


def test_deleting_a_named_graph_edge_does_not_delete_the_default_graphs(engine):
    """`DeleteIndex` killed `^KG("out", 0, ...)` with the key hardcoded to 0.

    `DeleteAdjacency` had already removed the named graph's entry correctly, so
    the hardcoded call did not fail loudly — it reached past its own graph and
    removed the *default* graph's entry for the same triple.

    The path only runs when `^NKG` is built (`DeleteAdjacency` guards on
    `^NKG("$meta","nodeCount")`), so this test builds it first. That guard is
    also why the defect stayed hidden: most tests never intern nodes.
    """
    engine.create_edge("a", "KNOWS", "b")  # default graph
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildNKG")

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    default = engine.verify_graph("")
    assert default["counts"]["outEdges"] == 1, (
        "deleting the named graph's edge removed the default graph's adjacency "
        "for the same triple — a cross-graph deletion"
    )
    assert default["ok"] == 1, f"the default graph drifted: {default['drift']}"


# --- the counters, at subscript level ---------------------------------------


def test_the_degree_counter_is_decremented_under_the_graph_key(engine, kg):
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("a", "KNOWS", "c", graph=GRAPH)
    assert _deg(kg, GRAPH, "a") == 2, "WriteAdjacency did not count both edges"

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)
    assert _deg(kg, GRAPH, "a") == 1, (
        "the degree counter was not decremented under the graph key"
    )


def test_the_per_predicate_counter_is_decremented(engine, kg):
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.create_edge("a", "KNOWS", "c", graph=GRAPH)
    assert _degp(kg, GRAPH, "a", "KNOWS") == 2

    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)
    assert _degp(kg, GRAPH, "a", "KNOWS") == 1, (
        "^KG('degp') was not decremented — the per-predicate counter is stale"
    )


def test_the_last_edge_removes_the_counter_rather_than_zeroing_it(engine, kg):
    """A zero-valued counter is drift: it claims a node exists in the graph."""
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    assert _deg(kg, GRAPH, "a") is None, (
        f"^KG('deg', {GRAPH!r}, 'a') survived as {_deg(kg, GRAPH, 'a')!r}; "
        "the last edge must remove the counter, not leave it at zero"
    )
    assert _degp(kg, GRAPH, "a", "KNOWS") is None


def test_a_node_id_never_lands_in_the_graph_key_position(engine, kg):
    """The defect the oracle cannot see, because it belongs to no graph.

    `DeleteIndex` wrote `^KG("deg", s)`, one subscript short. `s` therefore
    occupied the slot a graph key occupies, creating an entry beside every real
    graph that no graph-scoped walk will ever visit.
    """
    engine.create_edge("a", "KNOWS", "b", graph=GRAPH)
    engine.delete_edge("a", "KNOWS", "b", graph=GRAPH)

    assert kg.get("^KG", "deg", "a") is None, (
        "^KG('deg', 'a') exists — the node id was written into the graph-key "
        "position, so it collides with a graph literally named 'a' and is "
        "invisible to every graph-scoped check"
    )
    assert kg.get("^KG", "degp", "a") is None


# --- bulk node deletion carries the same obligation -------------------------


def test_deleting_a_node_leaves_no_stale_out_degree_on_its_sources(engine):
    """Removing a node removes inbound edges, so its sources lose out-degree."""
    engine.create_edge("src", "KNOWS", "victim", graph=GRAPH)
    engine.create_edge("src", "KNOWS", "survivor", graph=GRAPH)

    engine.delete_edge("src", "KNOWS", "victim", graph=GRAPH)

    report = engine.verify_graph(GRAPH)
    assert report["ok"] == 1, (
        f"src's out-degree still counts the deleted edge: {report['drift']}"
    )


def test_bulk_delete_adjacency_clears_a_nodes_inbound_and_outbound_edges(engine, kg):
    """`bulk_delete_adjacency` is the node-scoped path, and it reached neither end.

    It killed `^KG("out", g, node)` and `^KG("in", g, node)` directly. That left
    the `^KG("in")` mirror at each of the node's targets, and left every source
    that pointed at the node counting the removed edge in its own out-degree.
    """
    engine.create_edge("hub", "KNOWS", "downstream", graph=GRAPH)
    engine.create_edge("upstream", "KNOWS", "hub", graph=GRAPH)
    engine.create_edge("upstream", "KNOWS", "keeper", graph=GRAPH)
    assert _deg(kg, GRAPH, "upstream") == 2

    assert engine.bulk_delete_adjacency(["hub"]) == 1

    assert _deg(kg, GRAPH, "hub") is None, "the deleted node kept its own counter"
    assert _degp(kg, GRAPH, "hub", "KNOWS") is None, "^KG('degp') survived the node"
    assert _deg(kg, GRAPH, "upstream") == 1, (
        "upstream still counts its edge into the deleted node"
    )
    assert kg.get("^KG", "in", GRAPH, "downstream", "KNOWS", "hub") is None, (
        "the ^KG('in') mirror at the deleted node's target survived"
    )
    assert kg.get("^KG", "out", GRAPH, "upstream", "KNOWS", "hub") is None, (
        "the ^KG('out') entry pointing at the deleted node survived"
    )
    # The edge that touches neither end of the deletion is untouched.
    assert kg.get("^KG", "out", GRAPH, "upstream", "KNOWS", "keeper") is not None
