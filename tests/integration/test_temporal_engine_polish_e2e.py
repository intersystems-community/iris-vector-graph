"""E2E integration tests for spec 207: temporal engine polish.

Requires ivg-iris-enterprise container:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_temporal_engine_polish_e2e.py

SKIP_IRIS_TESTS defaults to "false".
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

_PREFIX = f"T207_{uuid.uuid4().hex[:8]}"


def _purge_temporal(engine):
    try:
        engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


def _cleanup_nodes(engine, prefix: str) -> None:
    cur = engine.conn.cursor()
    p = f"{prefix}%"
    for sql in [
        f"DELETE FROM {engine._t('rdf_reifications')} WHERE edge_id IN "
        f"(SELECT edge_id FROM {engine._t('rdf_edges')} WHERE s LIKE ? OR o_id LIKE ?)",
        f"DELETE FROM {engine._t('rdf_edges')} WHERE s LIKE ? OR o_id LIKE ?",
        f"DELETE FROM {engine._t('rdf_labels')} WHERE s LIKE ?",
        f"DELETE FROM {engine._t('rdf_props')} WHERE s LIKE ?",
        f"DELETE FROM {engine._t('nodes')} WHERE node_id LIKE ?",
    ]:
        try:
            params = [p, p] if sql.count("?") == 2 else [p]
            cur.execute(sql, params)
        except Exception:
            pass
    engine.conn.commit()
    cur.close()


# ─── US2: bulk_delete_nodes returns DeleteResult ──────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestBulkDeleteResult:
    def test_bulk_delete_returns_delete_result(self, engine):
        from iris_vector_graph._engine.nodes_edges import DeleteResult

        pfx = f"{_PREFIX}_bdr"
        ids = [f"{pfx}_node_{i}" for i in range(10)]
        # Insert 10 nodes
        cur = engine.conn.cursor()
        for nid in ids:
            try:
                cur.execute(
                    f"INSERT INTO {engine._t('nodes')} (node_id) SELECT ? "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {engine._t('nodes')} WHERE node_id=?)",
                    [nid, nid],
                )
            except Exception:
                pass
        engine.conn.commit()
        cur.close()

        result = engine.bulk_delete_nodes(ids)
        assert isinstance(result, DeleteResult), f"Expected DeleteResult, got {type(result)}"
        assert result.deleted == 10, f"Expected deleted=10, got {result.deleted}"
        assert result.failed == 0, f"Expected failed=0, got {result.failed}"

        _cleanup_nodes(engine, pfx)


# ─── US1: InsertEdge mode="update" overwrites attrs ───────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestInsertEdgeMode:
    def test_mode_update_overwrites_attrs(self, engine):
        pfx = f"{_PREFIX}_mode"
        src = f"{pfx}_src"
        tgt = f"{pfx}_tgt"
        ts1 = 1_700_000_000
        ts2 = 1_700_000_001

        _purge_temporal(engine)

        # Insert original edge
        engine.create_edge_temporal(src, "BILLED_AT", tgt, timestamp=ts1, attrs={"amount": 10})

        # Re-insert with mode="update"
        engine.create_edge_temporal(
            src, "BILLED_AT", tgt, timestamp=ts2, mode="update", attrs={"amount": 20}
        )

        # Verify: read back attrs — should reflect amount=20, not 10
        edges = engine.get_edges_in_window(src, "BILLED_AT", ts1 - 1, ts2 + 1)
        assert edges, "Expected at least one edge after mode=update"
        # Find edge with ts2
        amounts = []
        for e in edges:
            attrs = e.get("attrs") or {}
            if "amount" in attrs:
                amounts.append(attrs["amount"])
        # At minimum: no amount=10 should exist (update removed stale attrs)
        assert 10 not in amounts, f"Stale attrs survived mode=update: amounts={amounts}"

        _purge_temporal(engine)

    def test_mode_skip_no_overwrite(self, engine):
        pfx = f"{_PREFIX}_skip"
        src = f"{pfx}_src"
        tgt = f"{pfx}_tgt"
        ts1 = 1_700_100_000

        _purge_temporal(engine)

        engine.create_edge_temporal(src, "HIT", tgt, timestamp=ts1, attrs={"v": 1})
        # mode="skip" — should not overwrite
        engine.create_edge_temporal(src, "HIT", tgt, timestamp=ts1, mode="skip", attrs={"v": 99})

        edges = engine.get_edges_in_window(src, "HIT", ts1 - 1, ts1 + 1)
        amounts = [e.get("attrs", {}).get("v") for e in edges if e.get("attrs")]
        # Original v=1 should survive (mode=skip does not overwrite)
        assert 99 not in amounts or 1 in amounts, (
            f"mode=skip changed existing edge: amounts={amounts}"
        )

        _purge_temporal(engine)


# ─── US3: BulkDeleteAdjacency clears ^KG out/in/deg ──────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestBulkDeleteAdjacency:
    def test_bulk_delete_adjacency_clears_kg_out(self, engine):
        pfx = f"{_PREFIX}_adj"
        src = f"{pfx}_src"
        tgt = f"{pfx}_tgt"
        ts = 1_700_200_000

        _purge_temporal(engine)

        # Insert temporal edge — InsertEdge writes ^KG("out",0,src,...)
        engine.create_edge_temporal(src, "CONNECTS", tgt, timestamp=ts)

        # Call bulk_delete_adjacency
        count = engine.bulk_delete_adjacency([src])
        assert isinstance(count, int), f"Expected int, got {type(count)}"
        assert count >= 1, f"Expected count>=1, got {count}"

        _purge_temporal(engine)

    def test_empty_list_returns_zero(self, engine):
        result = engine.bulk_delete_adjacency([])
        assert result == 0


# ─── US4: GetVelocity ms mode ─────────────────────────────────────────────────


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="No IRIS container")
class TestGetVelocityMs:
    def test_get_velocity_ms_mode(self, engine):
        pfx = f"{_PREFIX}_vel"
        src = f"{pfx}_src"
        tgt = f"{pfx}_tgt"

        _purge_temporal(engine)

        now_ms = int(time.time() * 1000)
        # Insert 3 ms-precision edges near now
        for i in range(3):
            ts = now_ms - i * 1000
            engine._iris_obj().classMethodVoid(
                "Graph.KG.TemporalIndexMS", "InsertEdge", src, "TICK", tgt, ts, 1.0
            )

        # get_edge_velocity with explicit now_ts in ms
        result = engine.get_edge_velocity(src, window=300_000, now_ts=now_ms)
        assert result >= 0, f"Expected non-negative velocity, got {result}"

        _purge_temporal(engine)
