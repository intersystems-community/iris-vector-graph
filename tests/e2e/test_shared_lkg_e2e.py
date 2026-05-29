"""Spec 166 e2e tests — shared LazyKG instance reuse on live IRIS container."""
from __future__ import annotations
import uuid
import pytest


def _load_small_graph(engine, n: int = 10, prefix: str = ""):
    prefix = prefix or f"lkg166_{uuid.uuid4().hex[:8]}_"
    for i in range(n):
        engine.create_node(prefix + f"n_{i}")
    for i in range(n - 1):
        engine.create_edge(prefix + f"n_{i}", "EDGE", prefix + f"n_{i+1}")
    engine.rebuild_kg()
    return prefix


class TestSharedLazyKGE2E:
    def test_shared_lkg_object_identity(self, iris_connection, iris_master_cleanup):
        """T004 / AS-166-1 — same LazyKG object before and after second algorithm call."""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        _load_small_graph(engine)

        engine.triangle_count(top_k=5)
        lkg_after_first = engine._shared_lkg

        engine.leiden_communities(random_seed=42, top_k=5)
        lkg_after_second = engine._shared_lkg

        assert lkg_after_first is not None
        assert lkg_after_first is lkg_after_second, (
            "spec 166: _shared_lkg should be the same object across algorithm calls"
        )

    def test_results_unchanged_with_shared_lkg(self, iris_connection, iris_master_cleanup):
        """T003 / AS-166-3 — Leiden partition identical with or without prior triangle_count call."""
        from iris_vector_graph.engine import IRISGraphEngine

        prefix = f"lkg166b_{uuid.uuid4().hex[:8]}_"
        engine_fresh = IRISGraphEngine(iris_connection)
        _load_small_graph(engine_fresh, prefix=prefix)

        result_fresh = engine_fresh.leiden_communities(random_seed=42, top_k=0)
        partition_fresh = {r["id"]: r["community"] for r in result_fresh if prefix in r["id"]}

        engine_warm = IRISGraphEngine(iris_connection)
        engine_warm.triangle_count(top_k=0)
        result_warm = engine_warm.leiden_communities(random_seed=42, top_k=0)
        partition_warm = {r["id"]: r["community"] for r in result_warm if prefix in r["id"]}

        assert partition_fresh == partition_warm, (
            "spec 166: Leiden partition must be identical regardless of prior calls"
        )
