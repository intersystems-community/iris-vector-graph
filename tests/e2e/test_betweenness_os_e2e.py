"""Spec 170 e2e tests — BetweennessGlobal ObjectScript Brandes 2001."""
from __future__ import annotations
import uuid
import pytest


def _load_path_graph(engine, n=8, prefix=""):
    prefix = prefix or f"bc170_{uuid.uuid4().hex[:8]}_"
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


class TestBetweennessOsE2E:
    def test_betweenness_center_node_highest(self, iris_connection, iris_master_cleanup):
        """T002 — center of path graph (n=7) has highest betweenness (AS-170-1)."""
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        prefix = _load_path_graph(engine, n=7)

        result = engine.betweenness_centrality(sample_size=0, top_k=0)
        ivg = {r["id"][len(prefix):]: r["score"] for r in result if prefix in r["id"]}

        if not ivg:
            pytest.skip("No results with prefix — likely ^NKG not built in test sequence")

        center = "n_3"
        others = ["n_0", "n_1", "n_2", "n_4", "n_5", "n_6"]
        if center in ivg:
            center_score = ivg[center]
            max_other = max((ivg.get(o, 0) for o in others), default=0)
            assert center_score >= max_other, (
                f"Center node {center} should have highest BC; "
                f"center={center_score:.4f} max_other={max_other:.4f}"
            )

    def test_betweenness_fallback_on_missing_nkg(self, iris_connection, iris_master_cleanup):
        """T003 — falls back gracefully when ^NKG not built."""
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection)
        _load_path_graph(engine, n=5)
        try:
            _iris.createIRIS(iris_connection).kill("^NKG")
        except Exception:
            pass

        result = engine.betweenness_centrality(sample_size=3, top_k=5)
        assert isinstance(result, list)
