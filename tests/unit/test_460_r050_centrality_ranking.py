"""4.6.0 R050 (Python-newcomer walkthrough W1): the Python tiers rank centrality highest first, ties by id, then cut.

`closeness_centrality()` answered in node order and `top_k=3` kept the first three nodes it scored, dropping a node
that scored higher than one it kept. The ObjectScript tiers (ClosenessGlobal, ClosenessGlobalMSBFS, EigenvectorGlobal)
are checked live in tests/e2e/test_460_r050_centrality_ranking_e2e.py; this file checks the Python walks over ^KG,
which answer where a named graph holds edges.

Expected answers worked by hand. Closeness graph C: z->a, z->b, z->d, a->b, b->c, d->c (out-direction distances).
  harmonic (sum of 1/d over the nodes reached): z 1+1+1+1/2 = 3.5, a 1+1/2 = 1.5, b 1, d 1, c 0
  classical ((n-1)/sum d, 0 unless every other node is reached): z 4/5 = 0.8; a, b, c, d 0
  harmonic, direction "in": c 1+1+1/2+1/2 = 3, b 2, a 1, d 1, z 0
Eigenvector graph E: z->y, y->x, x->z, x->y, a->z, b->z. a and b have no in-edge (0). On the cycle, with the
eigenvalue L: L*x = y, L*y = z + x, L*z = x, so L^3 = L + 1 (L = 1.3247...), and y : x : z = L : 1 : 1/L.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

C_EDGES = [("z", "a"), ("z", "b"), ("z", "d"), ("a", "b"), ("b", "c"), ("d", "c")]
E_EDGES = [("z", "y"), ("y", "x"), ("x", "z"), ("x", "y"), ("a", "z"), ("b", "z")]


class _FakeKG:
    """LazyKG's reads over an edge list, nodes in LazyKG's order: every source in id order, then (include_sinks)
    the sinks in id order. So node order is not id order, and a sink is missing unless asked for."""

    edges: list = []

    def __init__(self, conn=None, include_sinks=False, **_):
        self._out, self._in = {}, {}
        for s, t in self.edges:
            self._out.setdefault(s, []).append(t)
            self._in.setdefault(t, []).append(s)
        self._nodes = sorted(self._out)
        if include_sinks:
            self._nodes += sorted(set(self._in) - set(self._out))

    def iter_nodes(self):
        return iter(self._nodes)

    def out_neighbors(self, n):
        return list(self._out.get(n, []))

    def in_neighbors(self, n):
        return list(self._in.get(n, []))


@pytest.fixture
def walk(monkeypatch):
    """A store whose ^NKG tiers are skipped (a named graph holds edges), so the Python walk over ^KG answers;
    ``walk(edges)`` sets the graph it walks."""
    import iris_vector_graph.stores.lazy_kg as lazy_kg

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._named_graph_with_edges = lambda: "g"
    store._iris_obj = MagicMock

    def use(edges):
        monkeypatch.setattr(lazy_kg, "LazyKG", type("KG", (_FakeKG,), {"edges": list(edges)}))
        return store

    return use


def _closeness(walk, formula="harmonic", direction="out", top_k=0):
    r = walk(C_EDGES)._closeness_gref(formula, direction, 0, top_k, None)
    assert r.error is None, r.error
    return [(n, pytest.approx(s)) for n, s in r.rows]


def test_harmonic_closeness_is_ranked_highest_first_ties_by_id(walk):
    assert _closeness(walk) == [("z", 3.5), ("a", 1.5), ("b", 1.0), ("d", 1.0), ("c", 0.0)]


def test_harmonic_closeness_top_k_keeps_the_highest(walk):
    # node order would keep a, b, c and drop z, the highest
    assert _closeness(walk, top_k=3) == [("z", 3.5), ("a", 1.5), ("b", 1.0)]


def test_classical_closeness_puts_the_one_nonzero_node_first(walk):
    assert _closeness(walk, "classical") == [("z", 0.8), ("a", 0.0), ("b", 0.0), ("c", 0.0), ("d", 0.0)]
    assert _closeness(walk, "classical", top_k=2) == [("z", 0.8), ("a", 0.0)]


def test_in_closeness_is_ranked(walk):
    assert _closeness(walk, direction="in", top_k=4) == [("c", 3.0), ("b", 2.0), ("a", 1.0), ("d", 1.0)]


def test_eigenvector_walk_is_ranked_and_cut(walk):
    lam = 1.3247179572447460  # the real root of L^3 = L + 1
    x = 1.0 / math.sqrt(lam**2 + 1 + 1 / lam**2)
    r = walk(E_EDGES)._eigenvector_gref(1000, 1e-12, 3, None)
    assert r.error is None, r.error
    assert [n for n, _ in r.rows] == ["y", "x", "z"]
    assert [s for _, s in r.rows] == pytest.approx([lam * x, x, x / lam], abs=1e-9)


def test_eigenvector_walk_breaks_ties_by_id(walk):
    r = walk(E_EDGES + [("q", "p")])._eigenvector_gref(1000, 1e-12, 0, None)
    # a, b, q have no in-edge; p only q's, which is 0: four zeros, in id order after the cycle
    assert [n for n, _ in r.rows] == ["y", "x", "z", "a", "b", "p", "q"]


def test_betweenness_walk_breaks_ties_by_id(walk):
    # path m->k->j: k alone lies between two nodes; j and m score 0 and come in id order
    r = walk([("m", "k"), ("k", "j")])._betweenness_lazy(0, "out", 0, 0, 256, None)
    assert r.error is None, r.error
    assert [(n, s) for n, s in r.rows] == [("k", 1.0), ("j", 0.0), ("m", 0.0)]
