"""E2E integration tests for spec 221 — upsert=True must update weight.

Requires ivg-iris-enterprise (port 31972).
"""
import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
_PREFIX = f"u221_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection, embedding_dimension=768)
    e.initialize_schema()
    yield e
    try:
        e._store._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestUpsertWeightUpdate:

    def test_upsert_updates_weight(self, eng):
        """T001: upsert=True with new weight → new weight returned by get_edges_in_window."""
        src = f"{_PREFIX}_s1"; tgt = f"{_PREFIX}_t1"
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=2000, weight=0.5, upsert=True)
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=2000, weight=0.9, upsert=True)
        edges = eng.get_edges_in_window(src, "METRIC", 0, 999999999)
        weights = [e["w"] for e in edges if e["ts"] == 2000]
        assert len(weights) == 1, f"Expected 1 edge at ts=2000, got {len(weights)}"
        assert abs(weights[0] - 0.9) < 1e-6, f"Expected weight 0.9, got {weights[0]}"

    def test_upsert_true_idempotent_on_same_weight(self, eng):
        """T002: upsert=True is idempotent when weight unchanged."""
        src = f"{_PREFIX}_s2"; tgt = f"{_PREFIX}_t2"
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=3000, weight=0.7, upsert=True)
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=3000, weight=0.7, upsert=True)
        edges = eng.get_edges_in_window(src, "METRIC", 0, 999999999)
        weights = [e["w"] for e in edges if e["ts"] == 3000]
        assert len(weights) == 1
        assert abs(weights[0] - 0.7) < 1e-6

    def test_upsert_updates_attrs(self, eng):
        """US-1 AC2: upsert=True replaces attrs (edgeprop) with new value."""
        src = f"{_PREFIX}_s3"; tgt = f"{_PREFIX}_t3"
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=4000, weight=1.0,
                                  attrs={"status": "old"}, upsert=True)
        eng.create_edge_temporal(src, "METRIC", tgt, timestamp=4000, weight=1.0,
                                  attrs={"status": "new"}, upsert=True)
        attrs = eng.get_edge_attrs(4000, src, "METRIC", tgt)
        assert attrs.get("status") == "new", f"Expected attrs.status='new', got {attrs}"
