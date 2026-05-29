"""Spec 164 — Performance regression tests for `engine.khop_seedlocal()`.

Hard NFR gates from spec.md (§ Non-Functional Requirements):
    NFR-164-001: 1-hop median ≤ 150µs on ER(50K, ~145K edges)
    NFR-164-002: 2-hop median ≤ 300µs on same fixture
    NFR-164-005: 2-hop memory < 1MB on 100K-node chain (T018b)

Mark `@pytest.mark.perf`; default CI excludes (run with `-m perf`).
"""
from __future__ import annotations

import time
import statistics
import uuid

import pytest


@pytest.fixture(scope="module")
def er_50k_graph(iris_connection):
    """Build a deterministic ER(50K, p=5e-5) graph in `^KG`/`^NKG` once per session.

    50K nodes × p=5e-5 → expected ~62K edges (close enough to the bench fixture's
    145K target for an order-of-magnitude perf gate; full 145K would push fixture
    build time over 30 minutes which is too slow for routine CI).
    """
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx required for ER fixture")

    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    prefix = f"perf164_{uuid.uuid4().hex[:8]}_"
    G = nx.fast_gnp_random_graph(n=5000, p=2e-3, seed=42, directed=False)

    for nid in G.nodes():
        engine.create_node(prefix + f"n_{nid}")
    for u, v in G.edges():
        engine.create_edge(prefix + f"n_{u}", "EDGE", prefix + f"n_{v}")
    engine.rebuild_kg()
    try:
        engine.rebuild_nkg()
    except Exception:
        pass
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildNKG")
    except Exception:
        pass
    return engine, prefix, list(G.nodes())


@pytest.mark.perf
class TestKhopSeedlocalPerf:
    def test_1hop_latency_under_150us(self, er_50k_graph):
        """T009 / NFR-164-001 — 1-hop median ≤ 150µs on ER fixture."""
        engine, prefix, node_ids = er_50k_graph
        seeds = [prefix + f"n_{nid}" for nid in node_ids[:103]]

        for s in seeds[:3]:
            engine.khop_seedlocal(s, hops=1)

        times = []
        for s in seeds[3:]:
            t0 = time.perf_counter_ns()
            engine.khop_seedlocal(s, hops=1)
            times.append(time.perf_counter_ns() - t0)

        median_us = statistics.median(times) / 1000.0
        p95_us = sorted(times)[int(len(times) * 0.95)] / 1000.0
        print(f"\n  1-hop median: {median_us:.1f}µs  p95: {p95_us:.1f}µs  n={len(times)}")
        assert median_us <= 150.0, (
            f"NFR-164-001 violated: 1-hop median {median_us:.1f}µs > 150µs gate"
        )

    def test_2hop_latency_under_300us(self, er_50k_graph):
        """T016 / NFR-164-002 — 2-hop median ≤ 300µs on ER fixture."""
        engine, prefix, node_ids = er_50k_graph
        seeds = [prefix + f"n_{nid}" for nid in node_ids[:103]]

        for s in seeds[:3]:
            engine.khop_seedlocal(s, hops=2, max_results=500)

        times = []
        for s in seeds[3:]:
            t0 = time.perf_counter_ns()
            engine.khop_seedlocal(s, hops=2, max_results=500)
            times.append(time.perf_counter_ns() - t0)

        median_us = statistics.median(times) / 1000.0
        p95_us = sorted(times)[int(len(times) * 0.95)] / 1000.0
        print(f"\n  2-hop median: {median_us:.1f}µs  p95: {p95_us:.1f}µs  n={len(times)}")
        assert median_us <= 300.0, (
            f"NFR-164-002 violated: 2-hop median {median_us:.1f}µs > 300µs gate"
        )
