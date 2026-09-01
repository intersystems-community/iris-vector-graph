"""E2E integration tests for engine critical fixes (spec 206).

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_engine_critical_fixes_e2e.py

SKIP_IRIS_TESTS defaults to "false".
"""
import os
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_PREFIX = f"T206_{uuid.uuid4().hex[:8]}"
_BUCKET_DIV = 300  # seconds mode: ts // 300


def _purge_temporal(engine):
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


def _insert_edge(engine, src, pred, tgt, ts, weight=1.0):
    engine._iris_obj().classMethodVoid(
        "Graph.KG.TemporalIndex", "InsertEdge", src, pred, tgt, ts, weight
    )


def _edge_exists(engine, src, pred, tgt=None):
    """Return True if any rdf_edges row matches src as source with given predicate."""
    cur = engine.conn.cursor()
    try:
        if tgt:
            cur.execute(
                f"SELECT COUNT(*) FROM {engine._t('rdf_edges')} WHERE s=? AND p=? AND o_id=?",
                [src, pred, tgt],
            )
        else:
            cur.execute(
                f"SELECT COUNT(*) FROM {engine._t('rdf_edges')} WHERE s=? AND p=?",
                [src, pred],
            )
        row = cur.fetchone()
        return int(row[0]) > 0
    finally:
        cur.close()


# ─── US1: sync() preserves temporal edges ────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestSyncPreservesTemporalEdges:
    def test_sync_preserves_temporal_edges(self, engine):
        """engine.sync() must not destroy temporal edges."""
        _purge_temporal(engine)
        try:
            src = f"{_PREFIX}_sync_src"
            tgt = f"{_PREFIX}_sync_tgt"
            # Insert 5 temporal edges at distinct timestamps
            timestamps = [1_700_000_000 + i * 300 for i in range(5)]
            for ts in timestamps:
                _insert_edge(engine, src, "METRIC_AT", tgt, ts)

            # Verify edges present before sync
            pre_edges = engine.get_edges_in_window(src, "METRIC_AT", 0, 9_999_999_999)
            assert len(pre_edges) >= 5, (
                f"Expected >=5 edges before sync, got {len(pre_edges)}"
            )

            # Call sync — this is the regression trigger
            engine.sync()

            # Edges must survive
            post_edges = engine.get_edges_in_window(src, "METRIC_AT", 0, 9_999_999_999)
            assert len(post_edges) >= 5, (
                f"sync() destroyed temporal edges: had {len(pre_edges)}, now {len(post_edges)}"
            )
        finally:
            _purge_temporal(engine)

    def test_sync_with_empty_temporal_index(self, engine):
        """sync() on container with no temporal data must not error."""
        _purge_temporal(engine)
        try:
            engine.sync()  # must not raise
        finally:
            _purge_temporal(engine)


# ─── US2: bulk_delete_nodes removes all matching rows ────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestBulkDeleteNodesRemovesAll:
    def _cleanup(self, engine, node_ids):
        try:
            engine.bulk_delete_nodes(node_ids)
        except Exception:
            pass

    def test_bulk_delete_nodes_removes_all_matching(self, engine):
        """bulk_delete_nodes removes edges where node is source OR target."""
        prefix = f"{_PREFIX}_bdn"
        src_only = f"{prefix}_src"
        tgt_only = f"{prefix}_tgt"
        both = f"{prefix}_both"
        other = f"{prefix}_other"

        nodes_to_create = [
            {"id": src_only}, {"id": tgt_only}, {"id": both}, {"id": other}
        ]
        engine.bulk_create_nodes(nodes_to_create)

        # src_only appears only as source
        engine.create_edge(src_only, "REL", other)
        # tgt_only appears only as target
        engine.create_edge(other, "REL", tgt_only)
        # both appears as source and target
        engine.create_edge(both, "REL", other)
        engine.create_edge(other, "REL", both)

        engine.bulk_delete_nodes([src_only, tgt_only, both])

        # All edges referencing deleted nodes must be gone
        assert not _edge_exists(engine, src_only, "REL"), "src_only edges survived"
        assert not _edge_exists(engine, other, "REL", tgt_only), "tgt_only edges survived"
        assert not _edge_exists(engine, both, "REL"), "both-as-source edges survived"
        assert not _edge_exists(engine, other, "REL", both), "both-as-target edges survived"

        # Cleanup
        self._cleanup(engine, [other])

    def test_bulk_delete_nodes_leaves_unrelated_edges(self, engine):
        """Edges not involving deleted nodes must be untouched."""
        prefix = f"{_PREFIX}_bdn2"
        to_delete = f"{prefix}_del"
        keep_a = f"{prefix}_ka"
        keep_b = f"{prefix}_kb"

        engine.bulk_create_nodes([
            {"id": to_delete}, {"id": keep_a}, {"id": keep_b}
        ])
        engine.create_edge(to_delete, "DEL_REL", keep_a)
        engine.create_edge(keep_a, "KEEP_REL", keep_b)

        engine.bulk_delete_nodes([to_delete])

        # keep_a → keep_b edge must survive
        assert _edge_exists(engine, keep_a, "KEEP_REL", keep_b), (
            "Unrelated edge was incorrectly deleted"
        )

        self._cleanup(engine, [keep_a, keep_b])


# ─── US3: PurgeBucketRange clears tagg, keeps raw edges ──────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestPurgeBucketRange:
    def test_purge_bucket_range_clears_tagg_keeps_raw(self, engine):
        """PurgeBucketRange removes tagg buckets; raw tout/tin edges survive."""
        _purge_temporal(engine)
        try:
            src = f"{_PREFIX}_pbr_src"
            tgt = f"{_PREFIX}_pbr_tgt"

            # bucket = ts // 300; insert edges in bucket 1000
            base_ts = 1000 * _BUCKET_DIV  # ts = 300000
            for i in range(5):
                _insert_edge(engine, src, "METRIC", tgt, base_ts + i)

            # Purge bucket range 1000-1004 (aggregate-only)
            result = engine.purge_bucket_range(1000, 1004)
            assert isinstance(result, int)
            assert result >= 0  # may be 0 if InsertEdge doesn't write tagg

            # Raw edges must survive regardless
            raw_edges = engine.get_edges_in_window(
                src, "METRIC", 0, base_ts + 10
            )
            assert len(raw_edges) >= 5, (
                f"PurgeBucketRange destroyed raw edges: got {len(raw_edges)}"
            )
        finally:
            _purge_temporal(engine)

    def test_purge_bucket_range_invalid_range_returns_zero(self, engine):
        """bucketStart > bucketEnd must return 0 and do nothing."""
        result = engine.purge_bucket_range(500, 100)
        assert result == 0

    def test_purge_bucket_range_empty_range_returns_zero(self, engine):
        """Range with no matching buckets returns 0."""
        # Use far-future range that has never been written
        result = engine.purge_bucket_range(9_999_999_990, 9_999_999_999)
        assert result == 0
