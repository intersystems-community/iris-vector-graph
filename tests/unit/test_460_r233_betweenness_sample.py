"""4.6.0 R233: a sampled betweenness takes a fixed pseudo-random sample of sources, the same on every route.

`betweenness_centrality(sample_size=0)` above 200 nodes, and any 0 < sample_size < N, ran Brandes from the first
sources in node-ID order on the server (`BetweennessGlobalParallel` read `^NKG("$NI")` in order), and from a
`random.sample` on the Python walk: the server's sample was a contiguous run of IDs (one community, one load batch),
the walk's a different one per call. The walk also took every source for sample_size=0 whatever N was.

The sources now are the `k` node IDs that come first by the SHA-1 of their UTF-8 bytes (the first 7 bytes, big-endian,
then the ID): uniform over the nodes, fixed, the same on every call and route; k is sample_size, or min(N, 200) for
0. Scores are scaled by N/k. Any sample_size >= N, and 0 on a graph of at most 200 nodes, still takes every source:
exact.

Expected answers: `nx.betweenness_centrality_subset(G, sources=S, targets=G, normalized=False) * N / k`, which is
Brandes from the sources in S over every target. The graph has 300 nodes: a directed 300-cycle (no node isolated)
plus G(300, 0.012, seed 7). The live proof on both routes is tests/e2e/test_460_r233_betweenness_sample_e2e.py.
"""

from __future__ import annotations

import hashlib
from unittest import mock

import networkx as nx
import pytest

from tests.unit.test_460_r216_betweenness_undirected import _FakeLazyKG


def graph300() -> nx.DiGraph:
    g = nx.DiGraph((f"v{i:03d}", f"v{(i + 1) % 300:03d}") for i in range(300))
    extra = nx.gnp_random_graph(300, 0.012, seed=7, directed=True)
    g.add_edges_from((f"v{s:03d}", f"v{t:03d}") for s, t in extra.edges() if s != t)
    assert g.number_of_nodes() == 300
    return g


def sample(nodes, k: int) -> list:
    """The documented rule, computed here: first by SHA-1 (7 bytes, big-endian), then by ID."""
    return sorted(nodes, key=lambda n: (int.from_bytes(hashlib.sha1(n.encode("utf-8")).digest()[:7], "big"), n))[:k]


def expected(g, k: int) -> dict:
    s = sample(list(g), k)
    sub = nx.betweenness_centrality_subset(g, sources=s, targets=list(g), normalized=False)
    return {n: v * g.number_of_nodes() / k for n, v in sub.items()}


def _python_walk(edges, direction, sample_size):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = mock.Mock()
    store._iris_obj = lambda: mock.Mock()
    store._named_graph_with_edges = lambda: "g2"
    with mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=_FakeLazyKG(edges)):
        result = store._betweenness_gref(sample_size, direction, 0, 0, 256, None)
    assert result.error is None
    return {nid: score for nid, score in result.rows}


def test_the_sample_is_not_the_first_ids():
    g = graph300()
    assert sample(list(g), 200) != sorted(g)[:200]


@pytest.mark.parametrize("sample_size,k", [(0, 200), (50, 50), (299, 299)])
def test_the_python_walk_samples_by_the_rule(sample_size, k):
    g = graph300()
    got = _python_walk(list(g.edges()), "out", sample_size)
    want = expected(g, k)
    assert {n: got.get(n, 0.0) for n in want} == pytest.approx(want)


def test_the_python_walk_undirected_samples_by_the_rule():
    g = graph300()
    u = nx.Graph(g.to_undirected())
    got = _python_walk(list(g.edges()), "both", 0)
    want = expected(u, 200)
    assert {n: got.get(n, 0.0) for n in want} == pytest.approx(want)


@pytest.mark.parametrize("sample_size", [300, 1000])
def test_at_or_above_n_is_exact(sample_size):
    g = graph300()
    got = _python_walk(list(g.edges()), "out", sample_size)
    want = nx.betweenness_centrality(g, normalized=False)
    assert {n: got.get(n, 0.0) for n in want} == pytest.approx(want)


def test_the_same_call_answers_the_same():
    g = graph300()
    assert _python_walk(list(g.edges()), "out", 40) == _python_walk(list(g.edges()), "out", 40)


def test_an_id_outside_ascii_hashes_its_utf8_bytes():
    g = nx.DiGraph([("é1", "b"), ("b", "ü2"), ("ü2", "é1"), ("b", "c")])
    got = _python_walk(list(g.edges()), "out", 2)
    want = expected(g, 2)
    assert {n: got.get(n, 0.0) for n in want} == pytest.approx(want)
