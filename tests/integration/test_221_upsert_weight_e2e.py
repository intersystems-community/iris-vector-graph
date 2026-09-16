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


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBulkUpsertWeightUpdate:
    """US-3: the batch path means what the single path means.

    The class above pins ``InsertEdge``. ``BulkInsert`` carried its own copy of
    the store-write body and kept the pre-spec-221 meaning of ``upsert``: a
    re-ingested sample was skipped, and the first weight won.
    """

    def _one(self, src, tgt, ts, weight, attrs=None):
        e = {"s": src, "p": "METRIC", "o": tgt, "ts": ts, "w": weight}
        if attrs:
            e["attrs"] = attrs
        return [e]

    def _weights(self, eng, src, ts):
        edges = eng.get_edges_in_window(src, "METRIC", 0, 999999999)
        return [e["w"] for e in edges if e["ts"] == ts]

    def test_bulk_upsert_updates_weight(self, eng):
        src = f"{_PREFIX}_b1"; tgt = f"{_PREFIX}_bt1"
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5000, 0.5), upsert=True)
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5000, 0.9), upsert=True)
        weights = self._weights(eng, src, 5000)
        assert len(weights) == 1, f"Expected 1 edge at ts=5000, got {len(weights)}"
        assert abs(weights[0] - 0.9) < 1e-6, f"Expected weight 0.9, got {weights[0]}"

    def test_bulk_mode_update_updates_weight(self, eng):
        """The explicit spelling, which reached nothing before: the parameter was
        accepted by ``bulk_create_edges_temporal`` and never passed on."""
        src = f"{_PREFIX}_b2"; tgt = f"{_PREFIX}_bt2"
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5100, 0.5), mode="update")
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5100, 0.9), mode="update")
        weights = self._weights(eng, src, 5100)
        assert len(weights) == 1
        assert abs(weights[0] - 0.9) < 1e-6

    def test_bulk_upsert_replaces_attrs(self, eng):
        """Replaced, not merged: the stale edgeprop subtree is killed first."""
        src = f"{_PREFIX}_b3"; tgt = f"{_PREFIX}_bt3"
        eng.bulk_create_edges_temporal(
            self._one(src, tgt, 5200, 1.0, {"status": "old", "only_in_first": "x"}),
            upsert=True,
        )
        eng.bulk_create_edges_temporal(
            self._one(src, tgt, 5200, 1.0, {"status": "new"}), upsert=True
        )
        attrs = eng.get_edge_attrs(5200, src, "METRIC", tgt)
        assert attrs.get("status") == "new", f"Expected status='new', got {attrs}"
        assert "only_in_first" not in attrs, f"Stale attr survived: {attrs}"

    def test_bulk_mode_skip_keeps_the_first_write(self, eng):
        """The old batch behaviour, still available under its real name."""
        src = f"{_PREFIX}_b4"; tgt = f"{_PREFIX}_bt4"
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5300, 0.5), mode="skip")
        written = eng.bulk_create_edges_temporal(
            self._one(src, tgt, 5300, 0.9), mode="skip"
        )
        assert written == 0, "A skipped edge must not be counted as written"
        weights = self._weights(eng, src, 5300)
        assert len(weights) == 1
        assert abs(weights[0] - 0.5) < 1e-6, f"Expected the first weight, got {weights[0]}"

    def test_bulk_default_is_insert(self, eng):
        """No mode and no upsert: written unconditionally, as before."""
        src = f"{_PREFIX}_b5"; tgt = f"{_PREFIX}_bt5"
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5400, 0.5))
        written = eng.bulk_create_edges_temporal(self._one(src, tgt, 5400, 0.9))
        assert written == 1
        weights = self._weights(eng, src, 5400)
        assert len(weights) == 1
        assert abs(weights[0] - 0.9) < 1e-6

    def test_bulk_upsert_is_scoped_to_its_graph(self, eng):
        """An update in one graph leaves the same coordinates in another alone."""
        src = f"{_PREFIX}_b6"; tgt = f"{_PREFIX}_bt6"
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5500, 0.5), graph="u221_a")
        eng.bulk_create_edges_temporal(self._one(src, tgt, 5500, 0.5), graph="u221_b")
        eng.bulk_create_edges_temporal(
            self._one(src, tgt, 5500, 0.9), graph="u221_a", mode="update"
        )
        a = eng.get_edges_in_window(src, "METRIC", 0, 999999999, graph="u221_a")
        b = eng.get_edges_in_window(src, "METRIC", 0, 999999999, graph="u221_b")
        assert [e["w"] for e in a if e["ts"] == 5500] == pytest.approx([0.9])
        assert [e["w"] for e in b if e["ts"] == 5500] == pytest.approx([0.5])
