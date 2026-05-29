"""Spec 165 — Unit tests for BuildNKGComplete single-round-trip optimization.

Tests verify that engine.rebuild_nkg() dispatches to BuildNKGComplete (1 call)
rather than the legacy 5-call sequence, and falls back gracefully when the
new ClassMethod is absent.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch, call
import pytest


def _make_engine_with_mock_iris(complete_return: str = "OS:20:10",
                                 complete_raises: bool = False):
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._arno_available = None
    eng._nkg_dirty = True
    eng.capabilities = MagicMock()
    eng.capabilities.objectscript_deployed = True

    mock_iris_obj = MagicMock()
    if complete_raises:
        mock_iris_obj.classMethodValue.side_effect = Exception("BuildNKGComplete not found")
        mock_iris_obj.classMethodVoid.return_value = None
    else:
        mock_iris_obj.classMethodValue.return_value = complete_return
        mock_iris_obj.classMethodVoid.return_value = None

    eng._iris_obj = MagicMock(return_value=mock_iris_obj)
    eng._detect_arno = MagicMock(return_value=False)
    eng._arno_capabilities = {}
    return eng, mock_iris_obj


class TestBuildNKGComplete:
    def test_rebuild_nkg_calls_build_nkg_complete_once(self):
        """T002 — rebuild_nkg() dispatches to BuildNKGComplete: exactly 1 classMethodValue call."""
        eng, mock_iris_obj = _make_engine_with_mock_iris("OS:20:10")
        result = eng.rebuild_nkg()
        traversal_calls = [c for c in mock_iris_obj.classMethodValue.call_args_list
                           if c.args[0] == "Graph.KG.Traversal" and c.args[1] == "BuildNKGComplete"]
        assert len(traversal_calls) == 1, (
            f"Expected exactly 1 BuildNKGComplete call, got {len(traversal_calls)}: "
            f"{mock_iris_obj.classMethodValue.call_args_list}"
        )
        assert result["path"] == "objectscript"
        assert result["edge_count"] == 20
        assert result["node_count"] == 10
        assert eng._nkg_dirty is False

    def test_rebuild_nkg_rust_path_sets_arno_available(self):
        """T002 variant — RUST: prefix sets _arno_available = True."""
        eng, mock_iris_obj = _make_engine_with_mock_iris("RUST:145:50")
        result = eng.rebuild_nkg()
        assert result["path"] == "rust"
        assert result["edge_count"] == 145
        assert result["node_count"] == 50
        assert eng._arno_available is True

    def test_rebuild_nkg_os_path_sets_arno_available_false(self):
        """T002 variant — OS: prefix sets _arno_available = False."""
        eng, mock_iris_obj = _make_engine_with_mock_iris("OS:145:50")
        result = eng.rebuild_nkg()
        assert result["path"] == "objectscript"
        assert eng._arno_available is False

    def test_rebuild_nkg_falls_back_when_complete_raises(self):
        """T003 — falls back to legacy 5-call sequence when BuildNKGComplete raises."""
        eng, mock_iris_obj = _make_engine_with_mock_iris(complete_raises=True)
        eng._detect_arno = MagicMock(return_value=False)
        result = eng.rebuild_nkg()
        assert result["path"] == "fallback"
        assert eng._nkg_dirty is False
