"""Unit tests for spec 216 — ledger.history() correlation_id and source filters."""
import pytest
from unittest.mock import MagicMock, patch
from iris_vector_graph.ledger.replay import fetch_history, _REV_COLS


def _make_mock_cursor(rows=None):
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    return cur


def _make_mock_conn(rows=None):
    conn = MagicMock()
    conn.cursor.return_value = _make_mock_cursor(rows)
    return conn


class TestHistoryCorrelationIdFilter:

    def test_correlation_id_filter_adds_where_clause(self):
        """T001: correlation_id= → SQL contains correlation_id = ?"""
        conn = _make_mock_conn()
        fetch_history(conn, "Graph_KG", correlation_id="acme-health|iris-acme-health")
        call_args = conn.cursor().execute.call_args
        sql, params = call_args[0]
        assert "correlation_id = ?" in sql
        assert "acme-health|iris-acme-health" in params

    def test_source_filter_adds_where_clause(self):
        """T002: source= → SQL contains source = ?"""
        conn = _make_mock_conn()
        fetch_history(conn, "Graph_KG", source="auto-ingest")
        call_args = conn.cursor().execute.call_args
        sql, params = call_args[0]
        assert "source = ?" in sql
        assert "auto-ingest" in params

    def test_combined_filters_both_in_where(self):
        """T003: correlation_id + actor_type → both in WHERE."""
        conn = _make_mock_conn()
        fetch_history(
            conn, "Graph_KG",
            correlation_id="acme-health|iris-acme-health",
            actor_type="ingest",
            limit=5,
        )
        call_args = conn.cursor().execute.call_args
        sql, params = call_args[0]
        assert "correlation_id = ?" in sql
        assert "actor_type = ?" in sql
        assert "acme-health|iris-acme-health" in params
        assert "ingest" in params

    def test_no_filter_no_correlation_id_in_where_clause(self):
        """T004: history() with no filters → WHERE clause has no correlation_id filter."""
        conn = _make_mock_conn()
        fetch_history(conn, "Graph_KG")
        call_args = conn.cursor().execute.call_args
        sql, params = call_args[0]
        # correlation_id appears in SELECT columns (_REV_COLS) but must NOT be in WHERE
        where_part = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        assert "correlation_id = ?" not in where_part
        assert "source = ?" not in where_part
        assert len([p for p in params if p in ("auto-ingest", "acme-health")]) == 0
