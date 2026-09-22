"""Spec 230 — the schema tells IRIS the shape of the tables it just created.

Found by the T074 gate, chasing `test_bulk_ingest_throughput_10k` at 20 edges/s.
IVG creates `Graph_KG.nodes` with `pk_nodes_graph (node_id, graph_id)` and runs
`TUNE TABLE` nowhere — not after creating the schema, not after the 4.0.0 migration
re-keys the table. On an install spec 227 had re-keyed, `WHERE node_id = ? AND
graph_id = ?` — the shape every graph-scoped read and both of `rdf_edges`' composite
foreign keys issue — planned as

    Read master map Graph_KG.nodes.IDKEY, looping on ID

a full scan of the table, measured at 5.3ms against 46,343 rows. One
`TUNE TABLE Graph_KG.nodes` moved the same lookup to `Read index map
Graph_KG.nodes.pk_nodes_graph, using the given node_id and graph_id` and the insert
from 10.2ms to 0.23ms — it both measures the rows that are there and discards plans
cached before the current indexes existed.

So tuning is part of creating the schema, not an operator's afterthought.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.schema import GraphSchema


class _Cursor:
    """Records statements; fails the ones a caller names."""

    def __init__(self, failing=()):
        self.statements: list = []
        self._failing = tuple(failing)

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if any(f in sql for f in self._failing):
            raise RuntimeError(f"[SQLCODE: <-30>:<Table not found>] {sql}")


class TestTuneTables:
    def test_every_core_table_is_tuned(self):
        cursor = _Cursor()
        status = GraphSchema.tune_tables(cursor)
        assert status, "tune_tables reported nothing"
        for table in ("nodes", "rdf_edges", "rdf_labels", "rdf_props"):
            assert f"TUNE TABLE Graph_KG.{table}" in cursor.statements, cursor.statements
            assert status[f"Graph_KG.{table}"] is True, status

    def test_a_table_this_install_lacks_does_not_stop_the_rest(self):
        """A 3.2.0 install has no `docs.graph_id` and may have no `ledger_revisions`."""
        cursor = _Cursor(failing=("ledger_revisions",))
        status = GraphSchema.tune_tables(
            cursor, tables=["nodes", "ledger_revisions", "rdf_edges"]
        )
        assert status["Graph_KG.nodes"] is True, status
        assert status["Graph_KG.ledger_revisions"] is False, status
        assert status["Graph_KG.rdf_edges"] is True, status

    def test_an_explicit_table_list_and_schema_are_honoured(self):
        cursor = _Cursor()
        status = GraphSchema.tune_tables(cursor, tables=["nodes"], schema="Scratch_KG")
        assert cursor.statements == ["TUNE TABLE Scratch_KG.nodes"], cursor.statements
        assert status == {"Scratch_KG.nodes": True}, status

    def test_a_qualified_table_name_is_left_alone(self):
        """The routed embedding tables are named by the registry, schema included."""
        cursor = _Cursor()
        GraphSchema.tune_tables(cursor, tables=["Graph_KG.kg_emb_a1b2c3"])
        assert cursor.statements == ["TUNE TABLE Graph_KG.kg_emb_a1b2c3"], (
            cursor.statements
        )
