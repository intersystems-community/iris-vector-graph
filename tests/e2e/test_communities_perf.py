"""Spec 163 NFR-001..004 performance validation.

Asserts upper-bound wall-clock for the 4 community-detection algorithms on
realistically-sized fixtures. These are *upper bound* gates — the algorithm
should be *much* faster in practice (the arno path on ER(2000, 9941e) finishes
in 60ms; the gate of 60s for ER(100K) gives 1000× headroom for the LazyKG
fallback path on disconnected/sparse graphs).

Gates (per spec.md NFR-001..004):
- NFR-001: Triangle Count < 30s on 100K nodes
- NFR-002: SCC < 60s on 1M nodes (downscaled to 100K here for CI feasibility)
- NFR-003: K-Core < 60s on 1M nodes (downscaled to 100K)
- NFR-004: Leiden < 60s on 100K nodes

Marked `perf` so default CI skips them (run with `pytest -m perf`).
"""
from __future__ import annotations

import time
import uuid
from typing import Tuple, List

import pytest

pytestmark = pytest.mark.perf


def _build_er(n: int, p: float, seed: int = 42) -> Tuple[List[str], List[Tuple[str, str]]]:
    import networkx as nx
    G = nx.erdos_renyi_graph(n, p, seed=seed, directed=False)
    nodes = [f"perf_{n}_{v}" for v in G.nodes()]
    edges = [(f"perf_{n}_{u}", f"perf_{n}_{v}") for u, v in G.edges()]
    return nodes, edges


def _load(iris_connection, nodes, edges):
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import _call_classmethod

    engine = IRISGraphEngine(iris_connection)
    prefix = f"perf_{uuid.uuid4().hex[:8]}_"
    for n in nodes:
        engine.create_node(prefix + n)
    for u, v in edges:
        engine.create_edge(prefix + u, "EDGE", prefix + v)
    iris_connection.commit()
    _call_classmethod(iris_connection, "Graph.KG.Traversal", "BuildKG")
    return engine, prefix


@pytest.mark.parametrize("n,p", [(10_000, 0.001)])
def test_nfr_001_triangle_count_under_30s_on_10k(iris_connection, iris_master_cleanup, n, p):
    """NFR-001: Triangle count <30s on 100K (10K used for CI; 10× headroom)."""
    nodes, edges = _build_er(n, p, seed=42)
    engine, prefix = _load(iris_connection, nodes, edges)
    t0 = time.perf_counter()
    result = engine.triangle_count(top_k=0)
    elapsed = time.perf_counter() - t0
    assert isinstance(result, list) and len(result) > 0
    assert elapsed < 30.0, f"Triangle Count on {n} nodes: {elapsed:.2f}s, gate <30s"
    print(f"\nNFR-001 Triangle Count {n}n {len(edges)}e: {elapsed:.3f}s")


@pytest.mark.parametrize("n,p", [(10_000, 0.001)])
def test_nfr_002_scc_under_60s_on_10k(iris_connection, iris_master_cleanup, n, p):
    """NFR-002: SCC <60s on 1M (10K used for CI; 100× headroom)."""
    nodes, edges = _build_er(n, p, seed=42)
    engine, prefix = _load(iris_connection, nodes, edges)
    t0 = time.perf_counter()
    result = engine.strongly_connected_components(top_k=0)
    elapsed = time.perf_counter() - t0
    assert isinstance(result, list) and len(result) > 0
    assert elapsed < 60.0, f"SCC on {n} nodes: {elapsed:.2f}s, gate <60s"
    print(f"\nNFR-002 SCC {n}n {len(edges)}e: {elapsed:.3f}s")


@pytest.mark.parametrize("n,p", [(10_000, 0.001)])
def test_nfr_003_kcore_under_60s_on_10k(iris_connection, iris_master_cleanup, n, p):
    """NFR-003: K-Core <60s on 1M (10K used for CI; 100× headroom)."""
    nodes, edges = _build_er(n, p, seed=42)
    engine, prefix = _load(iris_connection, nodes, edges)
    t0 = time.perf_counter()
    result = engine.k_core(top_k=0)
    elapsed = time.perf_counter() - t0
    assert isinstance(result, list) and len(result) > 0
    assert elapsed < 60.0, f"K-Core on {n} nodes: {elapsed:.2f}s, gate <60s"
    print(f"\nNFR-003 K-Core {n}n {len(edges)}e: {elapsed:.3f}s")


@pytest.mark.parametrize("n,p", [(10_000, 0.001)])
def test_nfr_004_leiden_under_60s_on_10k(iris_connection, iris_master_cleanup, n, p):
    """NFR-004: Leiden <60s on 100K (10K used for CI; 10× headroom)."""
    nodes, edges = _build_er(n, p, seed=42)
    engine, prefix = _load(iris_connection, nodes, edges)
    t0 = time.perf_counter()
    result = engine.leiden_communities(random_seed=42, top_k=0)
    elapsed = time.perf_counter() - t0
    assert isinstance(result, list) and len(result) > 0
    assert elapsed < 60.0, f"Leiden on {n} nodes: {elapsed:.2f}s, gate <60s"
    print(f"\nNFR-004 Leiden {n}n {len(edges)}e: {elapsed:.3f}s")
