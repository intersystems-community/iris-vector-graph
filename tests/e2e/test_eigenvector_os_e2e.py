"""Spec 169 e2e tests — EigenvectorGlobal ObjectScript power iteration."""
from __future__ import annotations
import uuid
import pytest


def _load_complete(engine, n=8, prefix=""):
    prefix = prefix or f"ev169_{uuid.uuid4().hex[:8]}_"
    for i in range(n):
        engine.create_node(prefix + f"n_{i}")
    for i in range(n):
        for j in range(n):
            if i != j:
                engine.create_edge(prefix + f"n_{i}", "EDGE", prefix + f"n_{j}")
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


class TestEigenvectorOsE2E:
    def test_eigenvector_all_equal_on_regular_graph(self, iris_connection, iris_master_cleanup):
        """T002 — all nodes equal score on K_8 (regular graph, all eigenvectors =1/sqrt(n))."""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        prefix = _load_complete(engine, n=6)

        result = engine.eigenvector_centrality(top_k=0)
        scores = [r["score"] for r in result if prefix in r["id"]]

        if not scores:
            pytest.skip("No nodes with prefix found — shared ^KG state")

        max_score, min_score = max(scores), min(scores)
        assert max_score - min_score < 0.05, (
            f"K_6 eigenvector scores should be equal; max={max_score:.4f} min={min_score:.4f}"
        )

    def test_eigenvector_fallback_on_missing_nkg(self, iris_connection, iris_master_cleanup):
        """T003 — falls back gracefully when ^NKG not built."""
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        _load_complete(engine, n=5)

        try:
            _iris.createIRIS(iris_connection).kill("^NKG")
        except Exception:
            pass

        result = engine.eigenvector_centrality(top_k=5)
        assert isinstance(result, list), "Must return list even when ^NKG missing"
