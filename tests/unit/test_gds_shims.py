"""Unit tests for GDS → ivg procedure name shims (US4).

Tests cover the five shimmed procedures and the unknown-gds error path.
All tests use mocked engine — no IRIS connection required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_engine():
    """Build a minimal mock IRISGraphEngine with _try_system_procedure."""
    from iris_vector_graph.engine import IRISGraphEngine
    eng = MagicMock(spec=IRISGraphEngine)
    # Wire _try_system_procedure to the real implementation bound to the mock
    eng._SYSTEM_PROCEDURES = {}
    return eng


# ---------------------------------------------------------------------------
# T041 — pageRank shim resolves
# ---------------------------------------------------------------------------

class TestPageRankShim:

    def test_pagerank_shim_dispatches_to_ivg_ppr(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        assert "gds.pagerank.stream" in GDS_SHIM_MAP

    def test_gds_pagerank_stream_returns_shimmed_sentinel(self):
        """CALL gds.pageRank.stream(...) → returns (shimmed_proc, None) tuple."""
        from iris_vector_graph._engine.query import _handle_gds_shim
        proc = MagicMock()
        proc.procedure_name = "gds.pageRank.stream"
        proc.arguments = []
        result = _handle_gds_shim(proc)
        # Should be a (shimmed_proc, None) tuple
        assert isinstance(result, tuple)
        assert result[1] is None
        assert result[0].procedure_name == "ivg.ppr"


# ---------------------------------------------------------------------------
# T042 — remaining 4 shims resolve
# ---------------------------------------------------------------------------

class TestRemainingShims:

    def test_shortestpath_in_shim_map(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        assert "gds.shortestpath.dijkstra.stream" in GDS_SHIM_MAP or \
               "gds.shortestpath.dijkstra" in GDS_SHIM_MAP

    def test_betweenness_in_shim_map(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        assert "gds.betweenness.stream" in GDS_SHIM_MAP

    def test_louvain_in_shim_map(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        assert "gds.louvain.stream" in GDS_SHIM_MAP

    def test_nodesimilarity_in_shim_map(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        assert "gds.nodesimilarity.stream" in GDS_SHIM_MAP

    def test_all_five_shims_present(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        keys = set(GDS_SHIM_MAP.keys())
        assert len(keys) >= 5


# ---------------------------------------------------------------------------
# T043 — unknown gds procedure returns error
# ---------------------------------------------------------------------------

class TestUnknownGdsError:

    def test_unknown_gds_procedure_returns_error_result(self):
        from iris_vector_graph._engine.query import _handle_gds_shim
        from iris_vector_graph.result import IVGResult
        proc = MagicMock()
        proc.procedure_name = "gds.unknownProcedure"
        proc.arguments = []
        result = _handle_gds_shim(proc)
        assert result is not None
        assert isinstance(result, IVGResult)
        # error field or error message in result
        has_error = (result.error is not None) or (
            result.rows and any("not shimmed" in str(r).lower() or "ivg" in str(r).lower()
                                for r in result.rows)
        )
        assert has_error

    def test_unknown_gds_error_message_names_ivg(self):
        from iris_vector_graph._engine.query import _handle_gds_shim
        proc = MagicMock()
        proc.procedure_name = "gds.someUnknownAlgo.stream"
        proc.arguments = []
        result = _handle_gds_shim(proc)
        result_str = str(result.error or result.rows)
        assert "ivg" in result_str.lower() or "not shimmed" in result_str.lower()


# ---------------------------------------------------------------------------
# T044 — non-gds procedures are unaffected
# ---------------------------------------------------------------------------

class TestNonGdsProceduresUnaffected:

    def test_ivg_ppr_not_reshimmed(self):
        from iris_vector_graph._engine.query import _handle_gds_shim
        proc = MagicMock()
        proc.procedure_name = "ivg.ppr"
        proc.arguments = []
        # _handle_gds_shim should return None for non-gds procedures
        result = _handle_gds_shim(proc)
        assert result is None

    def test_apoc_proc_not_reshimmed(self):
        from iris_vector_graph._engine.query import _handle_gds_shim
        proc = MagicMock()
        proc.procedure_name = "apoc.meta.data"
        proc.arguments = []
        result = _handle_gds_shim(proc)
        assert result is None

    def test_db_labels_not_reshimmed(self):
        from iris_vector_graph._engine.query import _handle_gds_shim
        proc = MagicMock()
        proc.procedure_name = "db.labels"
        proc.arguments = []
        result = _handle_gds_shim(proc)
        assert result is None


# ---------------------------------------------------------------------------
# Shim map correctness
# ---------------------------------------------------------------------------

class TestShimMapCorrectness:

    def test_shim_map_values_are_strings(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        for k, v in GDS_SHIM_MAP.items():
            assert isinstance(k, str), f"Key {k!r} is not a string"
            assert isinstance(v, str), f"Value {v!r} for key {k!r} is not a string"

    def test_shim_values_start_with_ivg(self):
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        for k, v in GDS_SHIM_MAP.items():
            assert v.startswith("ivg."), f"Shim target {v!r} does not start with 'ivg.'"

    def test_shim_keys_are_lowercase(self):
        """Keys must be lowercase for case-insensitive matching."""
        from iris_vector_graph._engine.query import GDS_SHIM_MAP
        for k in GDS_SHIM_MAP.keys():
            assert k == k.lower(), f"Shim key {k!r} is not lowercase"
