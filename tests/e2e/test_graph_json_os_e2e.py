"""Spec 167 e2e tests — ivg_graph_json_build server-side graph JSON export."""
from __future__ import annotations
import uuid
import time
import pytest


def _load_graph(engine, n=10, prefix=""):
    prefix = prefix or f"gj167_{uuid.uuid4().hex[:8]}_"
    for i in range(n):
        engine.create_node(prefix + f"n_{i}")
    for i in range(n - 1):
        engine.create_edge(prefix + f"n_{i}", "EDGE", prefix + f"n_{i+1}")
    engine.rebuild_kg()
    return prefix


class TestGraphJsonOsE2E:
    def test_leiden_result_identical_before_after_ddl(self, iris_connection, iris_master_cleanup):
        """T002 / AS-167-1 — Leiden partition unchanged after DDL install."""
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.stores.arno_bridge import build_graph_json_serverside

        engine = IRISGraphEngine(iris_connection)
        prefix = _load_graph(engine)

        result_lazykg = engine.leiden_communities(random_seed=42, top_k=0)
        partition_lazykg = {r["id"]: r["community"] for r in result_lazykg if prefix in r["id"]}

        engine._invalidate_shared_lkg()
        result_server = engine.leiden_communities(random_seed=42, top_k=0)
        partition_server = {r["id"]: r["community"] for r in result_server if prefix in r["id"]}

        assert partition_lazykg == partition_server, (
            "spec 167: Leiden partition must be identical with or without server-side JSON"
        )

    def test_graph_json_perf(self, iris_connection, iris_master_cleanup):
        """T003 / NFR-167-001 — server-side JSON build completes quickly."""
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.stores.arno_bridge import build_graph_json_serverside

        engine = IRISGraphEngine(iris_connection)
        _load_graph(engine, n=20)

        t0 = time.perf_counter()
        result = build_graph_json_serverside(iris_connection)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is not None, "build_graph_json_serverside must return a result"
        assert "nodes" in result
        assert "edges" in result
        assert elapsed_ms < 500, (
            f"NFR-167-001: build_graph_json_serverside took {elapsed_ms:.0f}ms, expected < 500ms"
        )
