"""Unit tests for spec 209: Arno algorithm dispatch routing.

Verifies that execute_ppr/execute_wcc/execute_cdlp/execute_pagerank route to the
correct ObjectScript class and method. No IRIS container required — _arno_call and
_call_classmethod are mocked.

These tests MUST fail on the unfixed iris_sql_store.py and pass after the fixes.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _make_store(arno_available: bool, capabilities: dict | None = None) -> IRISGraphStore:
    conn = MagicMock()
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = conn
    store._arno_available = arno_available
    store._arno_capabilities = capabilities or {}
    store._nkg_dirty = False
    return store


class TestPPRRouting:
    def test_ppr_arno_calls_arnoAccel(self):
        """execute_ppr Arno path MUST call Graph.KG.ArnoAccel.PPRJson, not NKGAccel."""
        store = _make_store(True, {"algorithms": ["ppr"]})
        with patch.object(store, "_arno_call", return_value="[]") as mock_call:
            store.execute_ppr(["node_a"], 0.85, 20)
        mock_call.assert_called_once()
        assert mock_call.call_args[0][0] == "Graph.KG.ArnoAccel", (
            f"Expected Graph.KG.ArnoAccel but got {mock_call.call_args[0][0]!r}"
        )
        assert mock_call.call_args[0][1] == "PPRJson"

    def test_ppr_arno_passes_json_array(self):
        """execute_ppr MUST pass json.dumps(seed_ids) — a JSON array string."""
        store = _make_store(True, {"algorithms": ["ppr"]})
        with patch.object(store, "_arno_call", return_value="[]") as mock_call:
            store.execute_ppr(["node_a", "node_b"], 0.85, 20)
        seeds_arg = mock_call.call_args[0][2]
        parsed = json.loads(seeds_arg)
        assert isinstance(parsed, list), f"Expected JSON array string, got {seeds_arg!r}"
        assert "node_a" in parsed and "node_b" in parsed

    def test_ppr_empty_seed_raises(self):
        """execute_ppr([]) MUST raise ValueError before any IRIS call (FR-006 edge case)."""
        store = _make_store(True, {"algorithms": ["ppr"]})
        with patch.object(store, "_arno_call") as mock_arno, \
             patch.object(store, "_call_classmethod") as mock_cls:
            with pytest.raises(ValueError, match="seed"):
                store.execute_ppr([], 0.85, 20)
        mock_arno.assert_not_called()
        mock_cls.assert_not_called()

    def test_ppr_fallback_calls_pagerank(self):
        """execute_ppr non-Arno path calls Graph.KG.PageRank.PPRJson (unchanged)."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as mock_cls:
            store.execute_ppr(["node_a"], 0.85, 20)
        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][0] == "Graph.KG.PageRank"
        assert mock_cls.call_args[0][1] == "PPRJson"


class TestWCCCDLPRouting:
    def test_wcc_fallback_calls_algorithms(self):
        """execute_wcc non-Arno fallback MUST call Graph.KG.Algorithms.WCCJson."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="{}") as mock_cls:
            store.execute_wcc()
        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][0] == "Graph.KG.Algorithms", (
            f"Expected Graph.KG.Algorithms but got {mock_cls.call_args[0][0]!r}"
        )
        assert mock_cls.call_args[0][1] == "WCCJson"

    def test_cdlp_fallback_calls_algorithms(self):
        """execute_cdlp non-Arno fallback MUST call Graph.KG.Algorithms.CDLPJson."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="{}") as mock_cls:
            store.execute_cdlp(10)
        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][0] == "Graph.KG.Algorithms", (
            f"Expected Graph.KG.Algorithms but got {mock_cls.call_args[0][0]!r}"
        )
        assert mock_cls.call_args[0][1] == "CDLPJson"

    def test_wcc_error_surfaces_in_result(self):
        """execute_wcc MUST populate result.error when exception raised (FR-006)."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod",
                          side_effect=Exception("TEST_ERROR_WCC")):
            result = store.execute_wcc()
        assert result.error, "result.error must be set when exception occurs"
        assert "TEST_ERROR_WCC" in result.error

    def test_cdlp_error_surfaces_in_result(self):
        """execute_cdlp MUST populate result.error when exception raised (FR-006)."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod",
                          side_effect=Exception("TEST_ERROR_CDLP")):
            result = store.execute_cdlp(10)
        assert result.error, "result.error must be set when exception occurs"
        assert "TEST_ERROR_CDLP" in result.error


class TestPageRankRouting:
    def test_pagerank_fallback_calls_pageRankGlobalJson(self):
        """execute_pagerank non-Arno fallback MUST call PageRankGlobalJson, not RunJson."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as mock_cls:
            store.execute_pagerank(0.85, 20)
        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][0] == "Graph.KG.PageRank"
        assert mock_cls.call_args[0][1] == "PageRankGlobalJson", (
            f"Expected PageRankGlobalJson but got {mock_cls.call_args[0][1]!r}"
        )

    def test_pagerank_fallback_passes_numeric_args(self):
        """execute_pagerank fallback MUST pass damping and maxIter as numeric strings."""
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as mock_cls:
            store.execute_pagerank(0.85, 20)
        args = mock_cls.call_args[0]
        assert args[2] == "0.85", f"Expected damping '0.85', got {args[2]!r}"
        assert args[3] == "20", f"Expected maxIter '20', got {args[3]!r}"
