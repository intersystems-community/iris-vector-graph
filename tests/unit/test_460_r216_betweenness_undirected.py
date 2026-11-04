"""4.6.0 R216: `betweenness_centrality(direction="both")` gives networkx's undirected betweenness.

`direction="both"` is answered by the Python walk (tests/unit/test_411_betweenness_direction.py), which runs Brandes
from every source over out- and in-neighbours. On an undirected graph that meets each pair of endpoints twice, once
from each end, so every score was twice `nx.betweenness_centrality(G.to_undirected(), normalized=False)`, which (like
igraph) counts each pair once. On the path a - b - c, b lies on the one shortest path between a and c: networkx says
1.0, IVG said 2.0.

The live proof, on the walkthrough's graph and a 300-node one, is tests/e2e/test_460_r216_betweenness_exact_e2e.py.
"""

from __future__ import annotations

from unittest import mock

import networkx as nx
import pytest


class _FakeLazyKG:
    """The default graph's adjacency, as LazyKG hands it to the Python walk."""

    def __init__(self, edges):
        self.out, self.inn = {}, {}
        for s, t in edges:
            self.out.setdefault(s, []).append(t)
            self.inn.setdefault(t, []).append(s)
        self.nodes = sorted(set(self.out) | set(self.inn))

    def iter_nodes(self):
        return iter(self.nodes)

    def out_neighbors(self, n):
        return list(self.out.get(n, []))

    def in_neighbors(self, n):
        return list(self.inn.get(n, []))


def _python_walk(edges, direction, sample_size=0):
    """The store's betweenness with its ^NKG tier refused, as where a named graph holds edges."""
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = mock.Mock()
    store._iris_obj = lambda: mock.Mock()
    store._named_graph_with_edges = lambda: "g2"
    with mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=_FakeLazyKG(edges)):
        result = store._betweenness_gref(sample_size, direction, 0, 0, 256, None)
    assert result.error is None
    return {nid: score for nid, score in result.rows}


EDGES = {
    "path": [("a", "b"), ("b", "c")],
    "two triangles and a bridge": [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")],
    "star, edges both ways": [("h", "l1"), ("l2", "h"), ("h", "l3"), ("l3", "h")],
}


@pytest.mark.parametrize("name", sorted(EDGES))
def test_both_is_networkx_undirected(name):
    edges = EDGES[name]
    expected = nx.betweenness_centrality(nx.Graph(edges), normalized=False)
    got = _python_walk(edges, "both")
    assert got == pytest.approx(expected)


def test_the_path_by_hand():
    assert _python_walk(EDGES["path"], "both") == {"a": 0.0, "b": 1.0, "c": 0.0}


@pytest.mark.parametrize("name", sorted(EDGES))
def test_out_on_the_python_walk_is_networkx_directed(name):
    edges = EDGES[name]
    g = nx.DiGraph(edges)
    expected = {n: v for n, v in nx.betweenness_centrality(g, normalized=False).items() if g.out_degree(n)}
    got = _python_walk(edges, "out")
    # "out" walks from nodes with out-edges only (a sink is never a source nor between two nodes)
    assert {n: got[n] for n in expected} == pytest.approx(expected)
    assert all(got.get(n, 0.0) == 0.0 for n in g if not g.out_degree(n))
