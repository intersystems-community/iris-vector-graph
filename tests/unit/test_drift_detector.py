"""Unit tests for the index drift detector (verify_sync / check_drift).

The drift detector closes the gap that the binary _nkg_dirty flag misses:
BYPASS write paths (drop_graph, delete_node, raw SQL, the SQL table bridge)
never set _nkg_dirty, so var-length Cypher silently runs on a stale ^KG/^NKG.
verify_sync() compares the SQL row count of rdf_edges against the global edge
count and reports (or, when heal=True, repairs via sync()) the divergence.

These tests are mock-only — they assert the comparison logic and the public
contract. The live consistency invariant is exercised in the E2E suite.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_engine(sql_edges=0, nkg_nodes=0, nkg_edges=0):
    from iris_vector_graph.engine import IRISGraphEngine

    cursor = MagicMock()
    cursor.fetchone.return_value = (sql_edges,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = "Graph_KG"
    engine.embedding_dimension = 4
    engine._nkg_dirty = False
    engine._store = MagicMock()

    iris_obj = MagicMock()

    def _classmethodvalue(cls, method, *args):
        if method == "NKGNodeCount":
            return nkg_nodes
        if method == "NKGEdgeCount":
            return nkg_edges
        return 0

    iris_obj.classMethodValue.side_effect = _classmethodvalue
    engine._iris_obj = MagicMock(return_value=iris_obj)
    return engine, cursor


class TestVerifySyncContract:

    def test_verify_sync_exists(self):
        engine, _ = _make_engine()
        assert hasattr(engine, "verify_sync")
        assert callable(engine.verify_sync)

    def test_returns_report_dataclass(self):
        engine, _ = _make_engine(sql_edges=10, nkg_edges=10)
        report = engine.verify_sync()
        assert hasattr(report, "in_sync")
        assert hasattr(report, "sql_edges")
        assert hasattr(report, "global_edges")

    def test_clean_when_counts_match(self):
        engine, _ = _make_engine(sql_edges=42, nkg_nodes=20, nkg_edges=42)
        report = engine.verify_sync()
        assert report.in_sync is True
        assert bool(report) is True

    def test_drift_when_sql_exceeds_globals(self):
        # 100 edges in SQL, only 42 in ^NKG → a BYPASS path wrote SQL only.
        engine, _ = _make_engine(sql_edges=100, nkg_nodes=20, nkg_edges=42)
        report = engine.verify_sync()
        assert report.in_sync is False
        assert bool(report) is False
        assert report.sql_edges == 100
        assert report.global_edges == 42

    def test_globals_exceeding_sql_is_not_flagged_by_counts_alone(self):
        # globals > SQL is NOT treated as count-drift: ^NKG meta edgeCount
        # over-counts (append-only interning, graph_id-agnostic). The dirty flag
        # is the signal for the delete case, not the count comparison. Here the
        # flag is clean, so by counts alone this is considered in sync.
        engine, _ = _make_engine(sql_edges=10, nkg_nodes=20, nkg_edges=42)
        report = engine.verify_sync()
        assert report.in_sync is True

    def test_delete_drift_caught_via_dirty_flag(self):
        # drop_graph/delete_node set _nkg_dirty — THAT is how the delete-side
        # drift is surfaced (globals>SQL counts are unreliable, see above).
        engine, _ = _make_engine(sql_edges=10, nkg_edges=42)
        engine._nkg_dirty = True
        report = engine.verify_sync()
        assert report.in_sync is False
        assert report.pending_sync is True

    def test_dirty_flag_forces_drift_even_if_counts_match(self):
        # _nkg_dirty is the in-process "you wrote but didn't sync" signal.
        engine, _ = _make_engine(sql_edges=10, nkg_edges=10)
        engine._nkg_dirty = True
        report = engine.verify_sync()
        assert report.in_sync is False
        assert report.pending_sync is True

    def test_empty_graph_is_in_sync(self):
        engine, _ = _make_engine(sql_edges=0, nkg_nodes=0, nkg_edges=0)
        report = engine.verify_sync()
        assert report.in_sync is True


class TestVerifySyncHeal:

    def test_heal_calls_sync_when_drifted(self):
        engine, _ = _make_engine(sql_edges=100, nkg_edges=42)
        with patch.object(engine, "sync", return_value=True) as mock_sync:
            report = engine.verify_sync(heal=True)
        mock_sync.assert_called_once()
        assert report.healed is True

    def test_heal_skips_sync_when_clean(self):
        engine, _ = _make_engine(sql_edges=10, nkg_edges=10)
        with patch.object(engine, "sync", return_value=True) as mock_sync:
            report = engine.verify_sync(heal=True)
        mock_sync.assert_not_called()
        assert report.healed is False

    def test_heal_false_by_default_does_not_sync(self):
        engine, _ = _make_engine(sql_edges=100, nkg_edges=42)
        with patch.object(engine, "sync", return_value=True) as mock_sync:
            engine.verify_sync()
        mock_sync.assert_not_called()


class TestVerifySyncResilience:

    def test_returns_report_on_global_count_error(self):
        engine, _ = _make_engine(sql_edges=10)
        engine._iris_obj.return_value.classMethodValue.side_effect = Exception("NKG missing")
        # Should not raise — report the indeterminate state.
        report = engine.verify_sync()
        assert report is not None
        assert report.in_sync is False

    def test_to_dict_is_serializable(self):
        import json
        engine, _ = _make_engine(sql_edges=100, nkg_edges=42)
        report = engine.verify_sync()
        json.dumps(report.to_dict())
