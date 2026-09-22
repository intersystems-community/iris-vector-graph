"""Spec 230 — the migration leaves statistics that describe the tables it wrote.

`upgrade_to_4_0_0` re-keys `nodes`, `docs` and `kg_EdgeEmbeddings`: it builds a
staging table, copies the rows in, and drops the source over it. The statistics the
optimizer had described the table that is now gone, and a re-keyed table starts with
none at all. Measured live on `ivg-iris-enterprise`, a `Graph_KG.nodes` with no
statistics plans `WHERE node_id = ? AND graph_id = ?` as

    Read master map Graph_KG.nodes.IDKEY, looping on ID

which is the shape every graph-scoped read and both of `rdf_edges`' composite foreign
keys issue — 5.3ms against 46,343 rows, and 10.2ms per edge insert. So the migration
tunes what it re-keyed before it hands the install back, and says so in its report.

A dry run writes nothing, and statistics are a write.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.migrations.upgrade import upgrade_to_4_0_0


class _Cursor:
    def __init__(self, log):
        self._log = log

    def execute(self, sql, params=None):
        self._log.append(sql)

    def close(self):
        pass


class _Conn:
    """A connection that only records what was asked of it."""

    def __init__(self):
        self.statements: list = []
        self.commits = 0

    def cursor(self):
        return _Cursor(self.statements)

    def commit(self):
        self.commits += 1


class TestTuneAfterMigration:
    def test_a_finished_migration_tunes_the_core_tables(self):
        conn = _Conn()
        report = upgrade_to_4_0_0(conn, steps=[], dry_run=False)
        for table in ("nodes", "rdf_edges", "rdf_labels", "rdf_props"):
            assert f"TUNE TABLE Graph_KG.{table}" in conn.statements, conn.statements
            assert report.tuned[f"Graph_KG.{table}"] is True, report.tuned

    def test_a_dry_run_collects_no_statistics(self):
        conn = _Conn()
        report = upgrade_to_4_0_0(conn, steps=[], dry_run=True)
        assert conn.statements == [], conn.statements
        assert report.tuned == {}, report.tuned

    def test_a_non_default_schema_is_tuned_where_it_lives(self):
        conn = _Conn()
        report = upgrade_to_4_0_0(conn, steps=[], dry_run=False, schema="Scratch_KG")
        assert "TUNE TABLE Scratch_KG.nodes" in conn.statements, conn.statements
        assert "TUNE TABLE Graph_KG.nodes" not in conn.statements, conn.statements
        assert report.tuned["Scratch_KG.nodes"] is True, report.tuned
