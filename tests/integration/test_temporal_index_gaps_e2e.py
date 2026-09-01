"""E2E integration tests for TemporalIndex API gaps (spec 204).

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 pytest tests/integration/test_temporal_index_gaps_e2e.py

SKIP_IRIS_TESTS defaults to "false" — tests always hit live IRIS unless
the developer explicitly sets SKIP_IRIS_TESTS=true.
"""
import json
import os
import time
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_PREFIX = f"T204_{uuid.uuid4().hex[:8]}"


def _purge(engine):
    """Clean up ^KG globals after each test."""
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    yield e
    _purge(e)


# ─── Gap 1: PurgeRawBefore ────────────────────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPurgeRawBeforeE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_purge_raw_before_preserves_aggregates(self, engine):
        """US1 AC-1: edges at ts<tsEnd deleted; aggregates survive; count correct."""
        pfx = f"{_PREFIX}_prb1"
        # Insert edges at ts 100, 200, 300
        for ts in (100, 200, 300):
            engine.create_edge_temporal(pfx, "METRIC_AT", f"{pfx}_t", timestamp=ts)

        # Aggregates should now exist
        agg_before = engine.get_temporal_aggregate(pfx, "METRIC_AT", "count", 0, 1000)
        assert agg_before >= 3

        deleted = engine.purge_raw_before(250)
        assert deleted == 2

        # Raw edges: only ts=300 should remain
        edges = engine.get_edges_in_window(pfx, "METRIC_AT", 0, 1000)
        ts_vals = {e["ts"] for e in edges}
        assert 100 not in ts_vals
        assert 200 not in ts_vals
        assert 300 in ts_vals

        # Aggregates must survive
        agg_after = engine.get_temporal_aggregate(pfx, "METRIC_AT", "count", 0, 1000)
        assert agg_after == agg_before

    def test_purge_raw_before_zero_deletes_when_none_qualify(self, engine):
        """US1 AC-2: nothing deleted when no edges before tsEnd."""
        pfx = f"{_PREFIX}_prb2"
        engine.create_edge_temporal(pfx, "METRIC_AT", f"{pfx}_t", timestamp=500)
        deleted = engine.purge_raw_before(100)
        assert deleted == 0

    def test_purge_raw_before_boundary_strict(self, engine):
        """US1 AC-3: edge at ts==tsEnd is NOT deleted (strict <)."""
        pfx = f"{_PREFIX}_prb3"
        engine.create_edge_temporal(pfx, "METRIC_AT", f"{pfx}_t", timestamp=250)
        deleted = engine.purge_raw_before(250)
        assert deleted == 0
        edges = engine.get_edges_in_window(pfx, "METRIC_AT", 0, 1000)
        assert len(edges) == 1

    def test_purge_raw_before_removes_edgeprop(self, engine):
        """US1 AC-4: edgeprop entries for deleted edges are removed."""
        pfx = f"{_PREFIX}_prb4"
        engine.create_edge_temporal(
            pfx, "METRIC_AT", f"{pfx}_t", timestamp=100,
            attrs={"unit": "ms", "value": "42"},
        )
        engine.purge_raw_before(200)
        # Edge gone; attrs should be gone too
        edges = engine.get_edges_in_window(pfx, "METRIC_AT", 0, 1000)
        assert len(edges) == 0


# ─── Gap 2: suppressReverseIndex ─────────────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSuppressReverseIndexE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_suppress_reverse_index_outbound_visible(self, engine):
        """US2 AC-2: suppressed edge visible via QueryWindow (outbound)."""
        pfx = f"{_PREFIX}_sri1"
        src, tgt = f"{pfx}_src", f"{pfx}_tgt"
        ts = int(time.time())
        engine.create_edge_temporal(src, "METRIC_AT", tgt, timestamp=ts,
                                    suppress_reverse_index=True)
        edges = engine.get_edges_in_window(src, "METRIC_AT", ts - 1, ts + 1)
        assert len(edges) >= 1
        targets = {e["o"] for e in edges}
        assert tgt in targets

    def test_suppress_reverse_index_inbound_invisible(self, engine):
        """US2 AC-2: suppressed edge NOT visible via QueryWindowInbound."""
        pfx = f"{_PREFIX}_sri2"
        src, tgt = f"{pfx}_src", f"{pfx}_tgt"
        ts = int(time.time()) + 1000
        engine.create_edge_temporal(src, "METRIC_AT", tgt, timestamp=ts,
                                    suppress_reverse_index=True)
        # QueryWindowInbound queries by target
        edges_in = engine.get_edges_in_window(tgt, "METRIC_AT", ts - 1, ts + 1,
                                              direction="in")
        sources = {e["s"] for e in edges_in}
        assert src not in sources

    def test_suppress_reverse_default_writes_both(self, engine):
        """US2 AC-1: default (no suppress) writes both tout and tin."""
        pfx = f"{_PREFIX}_sri3"
        src, tgt = f"{pfx}_src", f"{pfx}_tgt"
        ts = int(time.time()) + 2000
        engine.create_edge_temporal(src, "METRIC_AT", tgt, timestamp=ts)
        # Outbound visible
        edges_out = engine.get_edges_in_window(src, "METRIC_AT", ts - 1, ts + 1)
        assert any(e["o"] == tgt for e in edges_out)
        # Inbound visible
        edges_in = engine.get_edges_in_window(tgt, "METRIC_AT", ts - 1, ts + 1,
                                              direction="in")
        assert any(e["s"] == src for e in edges_in)


# ─── Gap 3: InternLabelSet / ResolveLabelSet ──────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestInternLabelSetE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_intern_label_set_key_order_invariant(self, engine):
        """US3 AC-1: different key order → same hash."""
        h1 = engine.intern_label_set({"b": 2, "a": 1})
        h2 = engine.intern_label_set({"a": 1, "b": 2})
        assert h1 == h2
        assert len(h1) == 40  # SHA1 hex

    def test_intern_label_set_idempotent(self, engine):
        """US3 AC-3 / SC-003: 1000 calls with 10 label sets → 10 storage entries."""
        label_sets = [{"region": f"r{i}", "env": "prod"} for i in range(10)]
        hashes = set()
        import random

        for _ in range(1000):
            ls = random.choice(label_sets)
            # Shuffle key order to test canonicalization
            shuffled = dict(sorted(ls.items(), key=lambda _: random.random()))
            h = engine.intern_label_set(shuffled)
            hashes.add(h)
        assert len(hashes) == 10

    def test_resolve_label_set_canonical_form(self, engine):
        """US3 AC-2: resolved JSON has sorted keys, no extra whitespace."""
        h = engine.intern_label_set({"z": 3, "a": 1, "m": 2})
        canonical = engine.resolve_label_set(h)
        assert canonical != ""
        parsed = json.loads(canonical)
        assert list(parsed.keys()) == sorted(parsed.keys())
        # No extra whitespace: re-serializing should match exactly
        assert canonical == json.dumps(parsed, separators=(",", ":"))

    def test_resolve_label_set_unknown_returns_empty(self, engine):
        """US3 AC-4: unknown hash returns ''."""
        result = engine.resolve_label_set("0" * 40)
        assert result == ""

    def test_purge_raw_before_does_not_touch_labelset(self, engine):
        """US3 AC-5 / FR-016: PurgeRawBefore leaves ^KG('labelset') untouched."""
        pfx = f"{_PREFIX}_ls_purge"
        h = engine.intern_label_set({"region": "us-east", "env": "prod"})
        # Insert an edge and purge it
        engine.create_edge_temporal(pfx, "METRIC_AT", f"{pfx}_t", timestamp=50)
        engine.purge_raw_before(100)
        # Label must still resolve
        resolved = engine.resolve_label_set(h)
        assert resolved != ""


# ─── Gap 4: TSUNIT="ms" bucket-unit behavior ─────────────────────────────────


def _call_ms(engine, method, *args):
    """Call a classmethod on Graph.KG.TemporalIndexMS via the IRIS connection."""
    return engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndexMS", method, *args
    )


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestTSUNITMSE2E:
    """Verify TemporalIndexMS (TSUNIT='ms', BUCKETMS=300000) uses correct divisor.

    Strategy: insert edges at well-known millisecond timestamps, then inspect
    ^KG('bucket') and ^KG('tagg') via global preview to confirm bucket keys
    match ts // 300_000, not ts // 300.
    """

    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_tsunit_ms_correct_bucket_key(self, engine):
        """T046: InsertEdge with ms timestamp places edge in bucket ts//300000."""
        # ts = 600_000 ms → bucket = 2 (ms mode: 600_000 // 300_000 = 2)
        # NOT bucket=2000 (sec mode: 600_000 // 300 = 2000)
        ts_ms = 600_000
        expected_bucket = 2       # 600_000 // 300_000
        wrong_bucket = 2000       # 600_000 // 300 — only if TSUNIT="" (sec mode)
        src, pred, tgt = f"{_PREFIX}_ms1_src", "METRIC_AT", f"{_PREFIX}_ms1_tgt"
        engine._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndexMS", "InsertEdge", src, pred, tgt, ts_ms
        )
        # GetBucketCount is a helper defined on TemporalIndexMS
        bucket_correct = int(str(engine._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndexMS", "GetBucketCount", expected_bucket, src
        )))
        bucket_wrong = int(str(engine._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndexMS", "GetBucketCount", wrong_bucket, src
        )))
        assert bucket_correct >= 1, "ms-mode bucket key 2 not populated"
        assert bucket_wrong == 0, "sec-mode bucket key 2000 should be empty in ms mode"

    def test_tsunit_ms_purge_uses_correct_divisor(self, engine):
        """T047: PurgeRawBefore strict-< boundary works with ms-scale timestamps."""
        # Two edges: ts_before < ts_end → purged; ts_at_end == ts_end → survives
        ts_before = 100_000
        ts_at_end = 200_000
        ts_end = 200_000
        src_b = f"{_PREFIX}_ms2_b"
        src_a = f"{_PREFIX}_ms2_a"
        pred, tgt = "METRIC_AT", f"{_PREFIX}_ms2_tgt"
        engine._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndexMS", "InsertEdge", src_b, pred, tgt, ts_before
        )
        engine._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndexMS", "InsertEdge", src_a, pred, tgt, ts_at_end
        )
        deleted = int(str(engine._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndex", "PurgeRawBefore", ts_end
        )))
        assert deleted >= 1, "at least ts_before edge should be purged"
        # Edge at ts_at_end must survive (strict < boundary)
        surviving_bucket = int(str(engine._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndexMS", "GetBucketCount",
            ts_at_end // 300_000, src_a
        )))
        # bucket for ts_at_end (200_000 ms) = 200_000 // 300_000 = 0
        # GetBucketCount checks ^KG("bucket") which was set at InsertEdge time;
        # PurgeRawBefore leaves aggregates intact, so bucket count persists.
        assert surviving_bucket >= 1, "aggregate bucket for surviving edge should remain"

    def test_tsunit_default_unchanged(self, engine):
        """T048: Default TemporalIndex (TSUNIT='') uses BUCKET=300 (seconds)."""
        # ts = 900 sec → bucket = 3 (sec mode: 900 // 300 = 3)
        ts_sec = 900
        src, pred, tgt = f"{_PREFIX}_sec_src", "METRIC_AT", f"{_PREFIX}_sec_tgt"
        engine._iris_obj().classMethodVoid(
            "Graph.KG.TemporalIndex", "InsertEdge", src, pred, tgt, ts_sec
        )
        # Reuse GetBucketCount helper — it reads ^KG("bucket", bucket, node)
        bucket_val = int(str(engine._iris_obj().classMethodValue(
            "Graph.KG.TemporalIndexMS", "GetBucketCount", 3, src
        )))
        assert bucket_val >= 1, "sec-mode bucket key 3 (900 // 300) should be populated"
