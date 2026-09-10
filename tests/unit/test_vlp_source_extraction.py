"""Unit tests for spec-arch-1 — extract_vlp_source_ids free function.

Tests run with no container and no engine — a mock cursor is sufficient.
Covers all three extraction paths:
  1. Label-based: source_labels populated → query_nodes per label, intersect
  2. Direct node_id: WHERE source.node_id = ? pattern → fast param extraction
  3. Cartesian SQL: full-SQL DISTINCT query through mock cursor
"""
import re
from unittest.mock import MagicMock, call

import pytest

from iris_vector_graph._engine.query import extract_vlp_source_ids
from iris_vector_graph.result import IVGResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _sql_query(sql="", params=None):
    q = MagicMock()
    q.sql = sql
    q.parameters = [params or []]
    return q


def _store(query_nodes_rows=None):
    store = MagicMock()
    if query_nodes_rows is not None:
        result = IVGResult(columns=["node_id"], rows=query_nodes_rows)
        store.query_nodes.return_value = result
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    store.conn.cursor.return_value = cursor
    return store


# ── Path 1: label-based ───────────────────────────────────────────────────────

class TestLabelBasedExtraction:

    def test_single_label_returns_matching_nodes(self):
        store = _store(query_nodes_rows=[["n1"], ["n2"], ["n3"]])
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(),
            source_labels=["Gene"],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        store.query_nodes.assert_called_once_with(label_filter="Gene")
        assert set(ids) == {"n1", "n2", "n3"}

    def test_two_labels_intersected(self):
        store = MagicMock()
        store.query_nodes.side_effect = [
            IVGResult(columns=["node_id"], rows=[["a"], ["b"], ["c"]]),
            IVGResult(columns=["node_id"], rows=[["b"], ["c"], ["d"]]),
        ]
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(),
            source_labels=["Gene", "Protein"],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert set(ids) == {"b", "c"}  # intersection

    def test_label_lookup_failure_returns_empty(self):
        store = MagicMock()
        store.query_nodes.side_effect = Exception("connection lost")
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(),
            source_labels=["Gene"],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert ids == []

    def test_no_matching_nodes_returns_empty(self):
        store = _store(query_nodes_rows=[])
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(),
            source_labels=["Nonexistent"],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert ids == []


# ── Path 2: direct node_id = ? fast path ─────────────────────────────────────

class TestDirectNodeIdExtraction:

    def test_node_id_eq_param_extracts_first_string_param(self):
        sql = "SELECT n1.val FROM Graph_KG.nodes n0 JOIN Graph_KG.nodes n1 ON 1=1 WHERE n0.node_id = ?"
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=["gene:TP53"]),
            source_labels=[],
            source_alias="n0",
            target_alias="n1",
            store=_store(),
        )
        assert ids == ["gene:TP53"]

    def test_node_id_no_match_falls_through_to_sql(self):
        # No node_id = ? pattern → falls through to cartesian SQL path
        sql = "SELECT n1.val FROM Graph_KG.nodes n0 JOIN Graph_KG.nodes n1 ON 1=1 WHERE n0.id = ?"
        cursor = MagicMock()
        cursor.fetchall.return_value = [("gene:TP53",)]
        store = MagicMock()
        store.conn.cursor.return_value = cursor
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=["id", "gene:TP53"]),
            source_labels=[],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        # cartesian SQL path ran (cursor was called)
        assert cursor.execute.called


# ── Path 3: cartesian SQL extraction ─────────────────────────────────────────

class TestCartesianSqlExtraction:

    def test_cartesian_boundary_detected_and_distinct_query_run(self):
        sql = (
            "SELECT p3.val AS b_id\n"
            "FROM Graph_KG.nodes n0\n"
            "JOIN Graph_KG.nodes n1 ON 1=1\n"
            "LEFT JOIN Graph_KG.rdf_props p2 ON p2.s = n0.node_id AND p2.\"key\" = ?\n"
            "LEFT JOIN Graph_KG.rdf_props p3 ON p3.s = n1.node_id AND p3.\"key\" = ?\n"
            "WHERE p2.val = ?"
        )
        cursor = MagicMock()
        cursor.fetchall.return_value = [("gene:TP53",), ("gene:BRCA1",)]
        store = MagicMock()
        store.conn.cursor.return_value = cursor

        ids = extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=["id", "id", "gene:TP53"]),
            source_labels=[],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert set(ids) == {"gene:TP53", "gene:BRCA1"}
        executed_sql = cursor.execute.call_args[0][0]
        assert "SELECT DISTINCT n0.node_id" in executed_sql

    def test_no_cartesian_boundary_returns_empty(self):
        # SQL with no "JOIN n1 ON 1=1" pattern — extraction returns []
        sql = "SELECT val FROM Graph_KG.nodes n0 WHERE n0.label = ?"
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=["Gene"]),
            source_labels=[],
            source_alias="n0",
            target_alias="n1",
            store=_store(),
        )
        assert ids == []

    def test_cursor_failure_returns_empty(self):
        # Use a prop-based WHERE (not node_id = ?) so Path 2 doesn't fire first
        sql = (
            "SELECT p.val\nFROM Graph_KG.nodes n0\n"
            "JOIN Graph_KG.nodes n1 ON 1=1\n"
            "LEFT JOIN Graph_KG.rdf_props p ON p.s = n0.node_id\n"
            "WHERE p.val = ?"
        )
        store = MagicMock()
        store.conn.cursor.return_value.execute.side_effect = Exception("SQL error")
        ids = extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=["some-value"]),
            source_labels=[],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert ids == []


# ── Label takes priority over SQL ────────────────────────────────────────────

class TestExtractionPriority:

    def test_labels_take_priority_over_sql_path(self):
        """If source_labels is populated, SQL cursor is never called."""
        store = _store(query_nodes_rows=[["n1"]])
        sql = "SELECT p.val\nFROM Graph_KG.nodes n0\nJOIN Graph_KG.nodes n1 ON 1=1"
        extract_vlp_source_ids(
            sql_query=_sql_query(sql=sql, params=[]),
            source_labels=["Gene"],
            source_alias="n0",
            target_alias="n1",
            store=store,
        )
        assert not store.conn.cursor.called  # SQL cursor never touched
