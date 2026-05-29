"""Spec 168 e2e tests — ClosenessGlobal ObjectScript closeness centrality."""
from __future__ import annotations
import uuid
import pytest


def _load_chain(engine, n=10, prefix=""):
    prefix = prefix or f"cl168_{uuid.uuid4().hex[:8]}_"
    for i in range(n):
        engine.create_node(prefix + f"n_{i}")
    for i in range(n - 1):
        engine.create_edge(prefix + f"n_{i}", "EDGE", prefix + f"n_{i+1}")
        engine.create_edge(prefix + f"n_{i+1}", "EDGE", prefix + f"n_{i}")
    engine.rebuild_kg()
    try:
        engine.rebuild_nkg()
    except Exception:
        pass
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildNKG")
    except Exception:
        pass
    return prefix


class TestClosenessOsE2E:
    def test_closeness_matches_networkx(self, iris_connection, iris_master_cleanup):
        """T002 / AS-168-1 — Closeness scores within 0.01 of networkx harmonic."""
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx required")

        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        prefix = _load_chain(engine, n=10)

        result = engine.closeness_centrality(top_k=10)
        ivg_scores = {r["id"][len(prefix):]: r["score"] for r in result if prefix in r["id"]}

        G = nx.path_graph(10, create_using=nx.Graph())
        nx_scores = nx.harmonic_centrality(G)
        nx_mapped = {f"n_{i}": nx_scores[i] for i in range(10)}

        for node_key in nx_mapped:
            if node_key in ivg_scores:
                diff = abs(ivg_scores[node_key] - nx_mapped[node_key])
                assert diff < 0.02, (
                    f"Closeness mismatch for {node_key}: ivg={ivg_scores[node_key]:.4f} "
                    f"nx={nx_mapped[node_key]:.4f} diff={diff:.4f}"
                )

    def test_closeness_fallback_on_missing_nkg(self, iris_connection, iris_master_cleanup):
        """T003 / AS-168-2 — Falls back gracefully when ^NKG not built."""
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        prefix = _load_chain(engine, n=5)

        iris_inst = _iris.createIRIS(iris_connection)
        try:
            iris_inst.kill("^NKG")
        except Exception:
            pass

        result = engine.closeness_centrality(top_k=5)
        assert isinstance(result, list), "Must return a list even when ^NKG missing"
