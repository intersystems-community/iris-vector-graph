"""4.6.0 R219: ``node_count()`` / ``edge_count()`` count the default graph, and ``graph=`` counts another.

Walkthrough 2 (W8) counted 14 nodes and 21 edges where the default graph held 10 and 18 (graph ``proj`` the rest).

By hand: graph ``g1`` (unique to the run) gets 4 nodes and 3 edges (a->b, b->c, c->d), graph ``g2`` 2 nodes and 1
edge, and the default graph 3 new nodes and 2 new edges. So ``node_count(graph=g1)`` is 4, ``edge_count(graph=g1)``
3, ``g2`` 2 and 1, and the default graph's counts grow by exactly 3 and 2 across the run: the writes to g1 and g2
leave them as they were.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g1, g2 = f"r219a_{run}", f"r219b_{run}"
    before = (e.node_count(), e.edge_count())
    ids = {k: f"r219_{run}_{k}" for k in ("a", "b", "c", "d", "x", "y", "z")}
    for k in ("a", "b", "c", "d"):
        assert e.create_node(ids[k], labels=["C219"], graph=g1)
    for s, o in (("a", "b"), ("b", "c"), ("c", "d")):
        assert e.create_edge(ids[s], "R219", ids[o], graph=g1)
    for k in ("a", "b"):
        assert e.create_node(ids[k], labels=["C219"], graph=g2)
    assert e.create_edge(ids["a"], "R219", ids["b"], graph=g2)
    for k in ("x", "y", "z"):
        assert e.create_node(ids[k], labels=["C219"])
    assert e.create_edge(ids["x"], "R219", ids["y"])
    assert e.create_edge(ids["y"], "R219", ids["z"])
    yield e, g1, g2, before
    for graph in (g1, g2):
        try:
            e.erase_graph(graph)
        except Exception:
            pass
    for k in ("x", "y", "z"):
        try:
            e.delete_node(ids[k], graph=None)
        except Exception:
            pass


def test_named_graph_counts(g):
    e, g1, g2, _ = g
    assert (e.node_count(graph=g1), e.edge_count(graph=g1)) == (4, 3)
    assert (e.node_count(graph=g2), e.edge_count(graph=g2)) == (2, 1)


def test_the_default_graph_grew_by_its_own_writes_only(g):
    e, _, _, (nodes_before, edges_before) = g
    assert e.node_count() - nodes_before == 3
    assert e.edge_count() - edges_before == 2
    assert (e.node_count(graph=None), e.edge_count(graph="")) == (e.node_count(), e.edge_count())


def test_a_graph_with_nothing_counts_zero(g):
    e, g1, _, _ = g
    assert (e.node_count(graph=g1 + "_none"), e.edge_count(graph=g1 + "_none")) == (0, 0)
