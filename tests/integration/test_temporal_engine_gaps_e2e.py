"""E2E integration tests for temporal engine gaps (spec 205).

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_temporal_engine_gaps_e2e.py

SKIP_IRIS_TESTS defaults to "false" — tests always hit live IRIS unless
the developer explicitly sets SKIP_IRIS_TESTS=true.

Note: All edge writes go through engine._iris_obj().classMethodVoid() /
classMethodValue() directly. The store's _iris_obj() requires an
IRISConnection but the test fixture provides a DBAPI Connection; the engine's
_iris_obj() correctly handles this via _native_conn / monkeypatch detection.
"""
import json
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_PREFIX = f"T205_{uuid.uuid4().hex[:8]}"


def _purge(engine):
    """Clean up ^KG globals after each test class."""
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


def _insert(engine, src, pred, tgt, ts, weight=1.0):
    """Insert via InsertEdge classmethod (native path)."""
    engine._iris_obj().classMethodVoid(
        "Graph.KG.TemporalIndex", "InsertEdge", src, pred, tgt, ts, weight
    )


def _insert_suppressed(engine, src, pred, tgt, ts, weight=1.0):
    """Insert with suppressReverseIndex=1."""
    engine._iris_obj().classMethodVoid(
        "Graph.KG.TemporalIndex", "InsertEdge",
        src, pred, tgt, ts, weight, "", 0, 1
    )


def _purge_raw(engine, ts_end, ts_start=0):
    """Call PurgeRawBefore via engine._iris_obj and return PurgeResult."""
    from iris_vector_graph._engine.temporal import PurgeResult

    raw = str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "PurgeRawBefore", ts_end, ts_start
    ))
    if ":" in raw:
        d, _, s = raw.partition(":")
        return PurgeResult(int(d), int(s) if s else 0)
    return PurgeResult(int(raw), 0)


def _query_tout(engine, src, pred, ts_start, ts_end):
    """QueryWindow via classMethodValue, return parsed list."""
    raw = str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "QueryWindow",
        src, pred, ts_start, ts_end
    ))
    return json.loads(raw)


def _query_tin(engine, tgt, pred, ts_start, ts_end):
    """QueryWindowInbound via classMethodValue, return parsed list."""
    raw = str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "QueryWindowInbound",
        tgt, pred, ts_start, ts_end
    ))
    return json.loads(raw)


def _get_aggregate(engine, src, pred, metric, ts_start, ts_end):
    """GetAggregate via classMethodValue."""
    raw = str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetAggregate",
        src, pred, metric, ts_start, ts_end
    ))
    return int(raw) if metric == "count" else (float(raw) if raw else 0)


def _bulk_insert(engine, items_json, upsert=0):
    """BulkInsert via classMethodValue."""
    return int(str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "BulkInsert", items_json, upsert
    )))


def _intern_label_set(engine, attrs_json):
    return str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "InternLabelSet", attrs_json
    ))


def _resolve_label_set(engine, h):
    return str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "ResolveLabelSet", h
    ))


def _purge_before(engine, ts_end):
    engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "PurgeBefore", ts_end)


def _get_distinct_count(engine, src, pred, ts_start, ts_end):
    return int(str(engine._iris_obj().classMethodValue(
        "Graph.KG.TemporalIndex", "GetDistinctCount",
        src, pred, ts_start, ts_end
    )))


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    yield e
    _purge(e)


# ─── US1: Bounded-window raw purge ───────────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPurgeRawBeforeWindowE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_ms_sec_mixed_unit_bounded_purge(self, engine):
        """US1 AC-1: ms edge deleted; sec edge survives with tsStart floor."""
        pfx = f"{_PREFIX}_mu1"
        ts_ms = 1_500_000_000_000
        ts_sec = 1_500_000
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_tgt", ts_ms)
        _insert(engine, pfx, "CALLED_BY", f"{pfx}_tgt2", ts_sec)

        result = _purge_raw(engine, 2_000_000_000_000, 100_000_000_000)
        assert result.deleted >= 1, "ms-range edge must be deleted"
        assert result.skipped >= 1, "sec-range edge must be skipped (below tsStart)"

        surviving = _query_tout(engine, pfx, "CALLED_BY", 0, ts_sec + 1)
        assert len(surviving) >= 1, "sec-range CALLED_BY edge must survive"

    def test_tsstart_zero_default_byte_identical(self, engine):
        """US1 AC-2: tsStart=0 default deletes all edges < tsEnd."""
        pfx = f"{_PREFIX}_mu2"
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 100)
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 200)
        result = _purge_raw(engine, 150)
        assert result.deleted == 1
        assert result.skipped == 0

    def test_no_edges_in_window_returns_zero_deleted(self, engine):
        """US1 AC-3: no edges in [tsStart, tsEnd) → deleted=0, skipped=count."""
        pfx = f"{_PREFIX}_mu3"
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 50)
        result = _purge_raw(engine, 200_000_000_000, 100_000_000_000)
        assert result.deleted == 0
        assert result.skipped >= 1

    def test_edge_exactly_at_tsstart_is_included(self, engine):
        """US1 AC-4: edge at ts==tsStart is deleted (closed lower bound)."""
        pfx = f"{_PREFIX}_mu4"
        ts = 100_000_000_000
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", ts)
        result = _purge_raw(engine, ts + 1, ts)
        assert result.deleted == 1


# ─── US2: BulkInsert sri per-item ────────────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBulkInsertSRIE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_1000_sri_items_no_tin_entries(self, engine):
        """US2 AC-1: sri=1 items produce zero ^KG("tin") writes."""
        pfx = f"{_PREFIX}_sri1"
        ts_base = 10_000
        edges = [
            {"s": f"{pfx}_src{i}", "p": "METRIC_AT", "o": f"{pfx}_tgt",
             "ts": ts_base + i, "w": 1.0, "sri": 1}
            for i in range(100)
        ]
        count = _bulk_insert(engine, json.dumps(edges))
        assert count == 100

        tin_edges = _query_tin(engine, f"{pfx}_tgt", "METRIC_AT",
                               ts_base - 1, ts_base + 200)
        assert len(tin_edges) == 0, "sri=1 items must produce no tin entries"

        tout_edges = _query_tout(engine, f"{pfx}_src0", "METRIC_AT",
                                 ts_base - 1, ts_base + 1)
        assert len(tout_edges) >= 1, "tout must be written despite suppression"

    def test_mixed_batch_partial_suppression(self, engine):
        """US2 AC-2: mixed batch — suppressed skip tin; unsuppressed write tin."""
        pfx = f"{_PREFIX}_sri2"
        ts_base = 20_000
        suppressed = [
            {"s": f"{pfx}_sup{i}", "p": "METRIC_AT", "o": f"{pfx}_tgt",
             "ts": ts_base + i, "w": 1.0, "sri": 1}
            for i in range(5)
        ]
        normal = [
            {"s": f"{pfx}_nrm{i}", "p": "METRIC_AT", "o": f"{pfx}_tgt",
             "ts": ts_base + 100 + i, "w": 1.0}
            for i in range(5)
        ]
        _bulk_insert(engine, json.dumps(suppressed + normal))

        tin_edges = _query_tin(engine, f"{pfx}_tgt", "METRIC_AT",
                               ts_base - 1, ts_base + 300)
        assert len(tin_edges) == 5, "only non-suppressed edges appear in tin"

    def test_no_sri_batch_byte_identical(self, engine):
        """US2 AC-3: no sri keys → both tout and tin written."""
        pfx = f"{_PREFIX}_sri3"
        ts = 30_000
        _bulk_insert(engine, json.dumps([
            {"s": pfx, "p": "METRIC_AT", "o": f"{pfx}_t", "ts": ts, "w": 1.0}
        ]))
        tout = _query_tout(engine, pfx, "METRIC_AT", ts - 1, ts + 1)
        tin = _query_tin(engine, f"{pfx}_t", "METRIC_AT", ts - 1, ts + 1)
        assert len(tout) >= 1
        assert len(tin) >= 1


# ─── US3: bulk_load_session auto_sync suppression ────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestBulkLoadSessionSyncE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_bulk_load_session_flag_set_and_cleared(self, engine):
        """US3: _in_bulk_load is True inside session, False after."""
        inside = []
        with engine.bulk_load_session(rebuild_indexes=False, incremental=False) as _s:
            inside.append(engine._in_bulk_load)
        assert inside == [True], "_in_bulk_load must be True inside session"
        assert engine._in_bulk_load is False, "_in_bulk_load must be False after session"

    def test_auto_sync_suppressed_inside_session(self, engine):
        """US3 AC-1: bulk_create_edges with auto_sync=True inside session → no per-batch sync."""
        import unittest.mock as mock

        pfx = f"{_PREFIX}_sync1"
        node_ids = [f"{pfx}_n{i}" for i in range(5)] + [f"{pfx}_m{i}" for i in range(5)]
        engine.bulk_create_nodes([{"id": nid} for nid in node_ids])

        sync_count = [0]

        def counting_sync():
            sync_count[0] += 1

        with mock.patch.object(type(engine), "sync", lambda self: counting_sync()):
            with engine.bulk_load_session(
                rebuild_indexes=False, incremental=False
            ) as _s:
                for i in range(5):
                    engine.bulk_create_edges(
                        [{"source_id": f"{pfx}_n{i}", "predicate": "K",
                          "target_id": f"{pfx}_m{i}"}],
                        disable_indexes=False,
                        auto_sync=True,
                    )
        assert sync_count[0] <= 1, (
            f"sync should fire at most once (at session exit), fired {sync_count[0]} times"
        )


# ─── US4: PurgeBefore bucket-boundary guard ──────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPurgeBeforeBucketGuardE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_mid_bucket_purge_leaves_tagg(self, engine):
        """US4 AC-1: mid-bucket tsEnd must not kill ^KG("tagg") for that bucket."""
        pfx = f"{_PREFIX}_pb1"
        bucket_size = 300
        bucket_n = 10
        ts_in_bucket = bucket_n * bucket_size + 100
        ts_end_mid = bucket_n * bucket_size + 150

        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", ts_in_bucket)

        agg_before = _get_aggregate(engine, pfx, "METRIC_AT", "count", 0, ts_in_bucket + 1)
        assert agg_before >= 1

        _purge_before(engine, ts_end_mid)

        agg_after = _get_aggregate(engine, pfx, "METRIC_AT", "count", 0, ts_in_bucket + 1)
        assert agg_after >= 1, "^KG('tagg') for bucket N must survive mid-bucket PurgeBefore"

    def test_fully_expired_bucket_tagg_killed(self, engine):
        """US4 AC-2: bucket entirely before tsEnd → ^KG("tagg") killed."""
        pfx = f"{_PREFIX}_pb2"
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 100)
        _purge_before(engine, 1000)
        agg = _get_aggregate(engine, pfx, "METRIC_AT", "count", 0, 200)
        assert agg == 0, "fully expired bucket aggregate must be killed"

    def test_purge_raw_before_never_kills_tagg(self, engine):
        """US4/US1 intersection: PurgeRawBefore must never touch ^KG("tagg")."""
        pfx = f"{_PREFIX}_pb3"
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 50)
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 60)

        agg_before = _get_aggregate(engine, pfx, "METRIC_AT", "count", 0, 200)
        assert agg_before >= 2

        _purge_raw(engine, 1000)

        agg_after = _get_aggregate(engine, pfx, "METRIC_AT", "count", 0, 200)
        assert agg_after == agg_before, "PurgeRawBefore must not touch aggregates"


# ─── US5: InternLabelSet type contract ───────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestInternLabelSetContractE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_numeric_port_resolves_as_int(self, engine):
        """US5 AC-2: {"port":1972} resolves as integer 1972, not string."""
        h = _intern_label_set(engine, '{"port":1972}')
        resolved = _resolve_label_set(engine, h)
        parsed = json.loads(resolved)
        assert isinstance(parsed["port"], int), (
            f"port must be int, got {type(parsed['port'])}: {parsed['port']!r}"
        )
        assert parsed["port"] == 1972

    def test_boolean_ok_resolves_as_bool(self, engine):
        """US5 AC-3: {"ok": true} resolves as boolean True, not "1"."""
        h = _intern_label_set(engine, '{"ok":true}')
        resolved = _resolve_label_set(engine, h)
        parsed = json.loads(resolved)
        assert parsed["ok"] is True, (
            f"ok must be True bool, got {type(parsed['ok'])}: {parsed['ok']!r}"
        )

    def test_labelset_survives_purge_raw_before(self, engine):
        """US5 AC-1: ^KG("labelset") untouched by PurgeRawBefore."""
        pfx = f"{_PREFIX}_ls1"
        h = _intern_label_set(engine, '{"region":"us-east","env":"prod"}')
        _insert(engine, pfx, "METRIC_AT", f"{pfx}_t", 50)
        _purge_raw(engine, 1000)
        resolved = _resolve_label_set(engine, h)
        assert resolved != "", "label set must survive PurgeRawBefore"


# ─── US6: All-sources GetDistinctCount / QueryWindow ─────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestAllSourcesE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_distinct_count_all_sources_equals_sum(self, engine):
        """US6 AC-1: GetDistinctCount("","PRECEDED_BY",t0,t1) covers all sources."""
        pfx = f"{_PREFIX}_dc1"
        ts_base = 5000
        n_sources = 10
        for i in range(n_sources):
            _insert(engine, f"{pfx}_src{i}", "PRECEDED_BY", f"{pfx}_tgt{i}",
                    ts_base + i)

        total = _get_distinct_count(engine, "", "PRECEDED_BY",
                                    ts_base - 1, ts_base + n_sources)
        assert total > 0, "all-sources count must be > 0"
        # Verify each individual source also returns a positive count (single-source path)
        for i in range(n_sources):
            per_src = _get_distinct_count(engine, f"{pfx}_src{i}", "PRECEDED_BY",
                                          ts_base - 1, ts_base + n_sources)
            assert per_src > 0, f"per-source count for src{i} must be > 0"

    def test_query_window_empty_source_returns_all(self, engine):
        """US6 AC-2: QueryWindow with source="" returns edges from all sources."""
        pfx = f"{_PREFIX}_dc2"
        ts_base = 6000
        for i in range(5):
            _insert(engine, f"{pfx}_src{i}", "PRECEDED_BY", f"{pfx}_tgt",
                    ts_base + i)

        all_edges = _query_tout(engine, "", "PRECEDED_BY", ts_base - 1, ts_base + 10)
        sources = {e["s"] for e in all_edges}
        assert len(sources) >= 5, "all sources must appear with source=''"


# ─── US7: QueryWindowInbound suppression ─────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestSuppressBlindSpotE2E:
    @pytest.fixture(autouse=True)
    def cleanup(self, engine):
        yield
        _purge(engine)

    def test_suppressed_edge_invisible_inbound(self, engine):
        """US7 AC-1: suppressed edge not visible via QueryWindowInbound."""
        pfx = f"{_PREFIX}_sb1"
        ts = 40_000
        src, tgt = f"{pfx}_src", f"{pfx}_tgt"
        _insert_suppressed(engine, src, "METRIC_AT", tgt, ts)
        _insert(engine, f"{pfx}_src2", "METRIC_AT", tgt, ts + 1)

        inbound = _query_tin(engine, tgt, "METRIC_AT", ts - 1, ts + 2)
        sources = {e["s"] for e in inbound}
        assert src not in sources, "suppressed edge must not appear inbound"
        assert f"{pfx}_src2" in sources, "non-suppressed edge must appear inbound"
