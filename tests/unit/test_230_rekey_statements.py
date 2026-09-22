"""Spec 230 US2 (FR-007, FR-018, FR-019, FR-020) — the re-key statement order.

`Graph_KG.docs` and `Graph_KG.kg_EdgeEmbeddings` are re-keyed either side of a
placement: the column or the table has to exist before rows can be placed, and the
key can only be tightened once every row has a graph. That makes the order
load-bearing in a way a single DDL list is not, so the statements are returned
rather than executed and asserted here without a database — the same treatment
spec 227 gave `get_graph_scope_migration_sql`.

Two things the order has to get right, and both are a wrong report rather than an
error if it does not:

* `docs.graph_id` arrives **nullable**. `NULL` is what `place_documents` reads as
  "not yet placed"; a `NOT NULL DEFAULT ''` column would arrive with every row
  already claiming the default graph, and the placement would find nothing to do
  and report a clean migration.
* the source table is dropped **after** its rows have a home, never before.
"""

from __future__ import annotations

from iris_vector_graph.migrations.docs_and_edge_vectors import (
    docs_finish_sql,
    docs_prepare_sql,
    edge_vectors_finish_sql,
    edge_vectors_prepare_sql,
)


def _index_of(statements, needle):
    for i, sql in enumerate(statements):
        if needle.lower() in sql.lower():
            return i
    raise AssertionError(f"no statement contains {needle!r}: {statements}")


class TestNoStatementCarriesASemicolon:
    """IRIS rejects a trailing semicolon on a statement sent through the DB-API."""

    def test_every_list(self):
        for statements in (
            docs_prepare_sql(),
            docs_finish_sql(),
            edge_vectors_prepare_sql(4),
            edge_vectors_finish_sql(),
        ):
            for sql in statements:
                assert not sql.strip().endswith(";"), sql


class TestRenameTargetsAreUnqualified:
    """IRIS takes the new name unqualified and refuses a qualified one at Prepare
    with SQLCODE -1, because a rename cannot move a table between schemas."""

    def test_every_rename(self):
        for statements in (docs_finish_sql(), edge_vectors_finish_sql()):
            rename = statements[_index_of(statements, "RENAME")]
            target = rename.split(" RENAME ", 1)[1].strip().split()[0]
            assert "." not in target, rename


class TestDocs:
    def test_the_graph_column_arrives_nullable(self):
        add = docs_prepare_sql()[_index_of(docs_prepare_sql(), "ADD COLUMN graph_id")]

        assert "%EXACT" in add, "a graph key that stops comparing exactly is worse"
        assert "NOT NULL" not in add.upper(), add
        assert "DEFAULT" not in add.upper(), (
            "a default would place every pre-migration row in the default graph"
        )

    def test_the_prepare_creates_the_quarantine_before_anything_is_placed(self):
        statements = docs_prepare_sql()

        quarantine = statements[_index_of(statements, "docs_quarantine")]
        assert quarantine.upper().startswith("CREATE TABLE"), quarantine
        assert "text" in quarantine, "a quarantine that drops the document is a delete"

    def test_the_prepare_tightens_nothing(self):
        """Tightening here would fail on exactly the rows the placement exists to
        decide, and it would fail half way through the list."""
        for sql in docs_prepare_sql():
            assert "ALTER COLUMN" not in sql.upper(), sql
            assert "DROP TABLE" not in sql.upper(), sql

    def test_the_finish_copies_before_it_drops(self):
        statements = docs_finish_sql()

        assert _index_of(statements, "INSERT INTO") < _index_of(statements, "DROP TABLE")

    def test_the_finish_copies_only_placed_rows(self):
        insert = docs_finish_sql()[_index_of(docs_finish_sql(), "INSERT INTO")]

        assert "graph_id IS NOT NULL" in insert, insert

    def test_the_finish_leaves_the_fresh_install_shape(self):
        create = docs_finish_sql()[_index_of(docs_finish_sql(), "CREATE TABLE")]

        assert "PRIMARY KEY (graph_id, id)" in create, create
        assert "graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''" in create, create

    def test_the_finish_renames_the_rebuilt_table_into_place(self):
        statements = docs_finish_sql()

        assert _index_of(statements, "DROP TABLE") < _index_of(statements, "RENAME")

    def test_the_finish_puts_the_indexes_back(self):
        """A rebuild loses the original table's indexes with it. `kg_TXT` searches
        `docs.text` through the iFind index, so an upgraded install without it either
        answers nothing or table-scans — and both read as a working text leg on a
        fixture small enough."""
        statements = docs_finish_sql()

        graph_index = _index_of(statements, "idx_docs_graph")
        ifind = statements[_index_of(statements, "idx_docs_text_ifind")]

        assert "%iFind.Index.Basic" in ifind, ifind
        assert _index_of(statements, "RENAME") < graph_index, (
            "an index on the staging name is an index on a table about to be renamed"
        )


class TestEdgeVectors:
    def test_the_staging_table_has_the_shape_a_generated_route_has(self):
        create = edge_vectors_prepare_sql(768)[
            _index_of(edge_vectors_prepare_sql(768), "kg_EdgeEmbeddings_ivg400")
        ]

        assert "emb_rowid BIGINT IDENTITY PRIMARY KEY" in create, create
        assert "graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT ''" in create, create
        assert "VECTOR(DOUBLE, 768)" in create, create
        assert "metadata" in create, "the routed writers name metadata unconditionally"
        assert "UNIQUE (graph_id, s, p, o_id)" in create, create

    def test_the_declared_width_is_the_one_it_is_given(self):
        create = edge_vectors_prepare_sql(4, dtype="FLOAT")[
            _index_of(edge_vectors_prepare_sql(4, dtype="FLOAT"), "kg_EdgeEmbeddings_ivg400")
        ]

        assert "VECTOR(FLOAT, 4)" in create, create

    def test_the_quarantine_keeps_the_vector(self):
        quarantine = edge_vectors_prepare_sql(4)[
            _index_of(edge_vectors_prepare_sql(4), "edge_vector_quarantine")
        ]

        assert "emb" in quarantine, "a quarantine without the vector is a delete"
        assert "reason" in quarantine, quarantine

    def test_the_prepare_drops_nothing(self):
        for sql in edge_vectors_prepare_sql(4):
            assert "DROP" not in sql.upper(), sql

    def test_the_finish_drops_the_source_before_the_rename(self):
        statements = edge_vectors_finish_sql()

        assert _index_of(statements, "DROP TABLE") < _index_of(statements, "RENAME")
