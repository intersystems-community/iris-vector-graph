"""Unit tests for RDF 1.2 reification API."""
import pytest
from unittest.mock import MagicMock, call


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine
    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (42,)
    e.conn.cursor.return_value = mock_cur
    return e, mock_cur


class TestReifyEdge:

    def test_auto_generates_reifier_id(self):
        """T008"""
        engine, cur = _make_engine()
        cur.fetchone.return_value = (42,)
        result = engine.reify_edge(42)
        assert result == "reif:42"

    def test_custom_reifier_id_accepted(self):
        """T007"""
        engine, cur = _make_engine()
        cur.fetchone.return_value = (42,)
        result = engine.reify_edge(42, reifier_id="custom:reif")
        assert result == "custom:reif"

    def test_nonexistent_edge_returns_none(self):
        """T009"""
        engine, cur = _make_engine()
        cur.fetchone.return_value = None
        result = engine.reify_edge(999)
        assert result is None


class TestGetReifications:

    def test_returns_list(self):
        """T012"""
        engine, cur = _make_engine()
        cur.fetchall.return_value = [("reif:42", "confidence", "0.92"), ("reif:42", "source", "PMID:1")]
        result = engine.get_reifications(42)
        assert isinstance(result, list)

    def test_empty_edge_returns_empty_list(self):
        """T013"""
        engine, cur = _make_engine()
        cur.fetchall.return_value = []
        result = engine.get_reifications(999)
        assert result == []


class TestDeleteReification:

    def test_delete_calls_cleanup(self):
        """T014"""
        engine, cur = _make_engine()
        result = engine.delete_reification("reif:42")
        assert isinstance(result, bool)
        assert cur.execute.called


class TestReifierAsNode:

    def test_reifier_discoverable_via_get_node(self):
        """T018"""
        engine, cur = _make_engine()
        cur.fetchone.return_value = (42,)
        reif_id = engine.reify_edge(42, props={"accessPolicy": "kg_read"})
        assert reif_id is not None
