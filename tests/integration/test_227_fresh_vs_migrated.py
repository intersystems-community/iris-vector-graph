"""Spec 227 T067 — an upgraded install and a fresh install are the same schema.

Two code paths build a 4.0.0 namespace: `initialize_schema`, which runs the 4.0.0 DDL
against an empty schema, and `migrate_to_graph_scoped_embeddings`, which drains a 3.2.0
table into a staging table its own `CREATE TABLE` declares and renames that into place.
Two declarations of the same table in two places drift — the migration's is at
`iris_vector_graph/migrations/graph_scoped_embeddings.py::_create_embedding_table`, the
DDL's at `iris_vector_graph/schema.py` — and the drift is invisible to every other test
in the suite, because each one exercises exactly one of the paths. An upgraded install
would keep working until the first statement that names a column or relies on a
constraint the other path declares, and then fail in a way no test predicted.

So this compares the catalogs: the tables, their columns, their constraints and the key
columns of those constraints, the foreign keys' targets, the declared vector widths and
the stored procedures. Both installs are built at the same width, so a width difference
is a real difference rather than a fixture artefact.

It runs against the live `ivg-iris-enterprise` container because the catalog is the
subject: `INFORMATION_SCHEMA` is the only honest witness to what IRIS actually declared.
"""

from __future__ import annotations

import contextlib
import os
import re

import pytest

from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings
from iris_vector_graph.schema import GraphSchema
from tests.e2e.test_227_migration import (
    DIM,
    ROUTE,
    Env320,
    _VIEWS,
    _assert_is_320,
    _procedures,
    _quietly,
    _rebuild_320,
    _tables,
    _tear_down_tables,
)

#: The tables both paths declare, and the only ones a drift could hide in. `docs`,
#: `fhir_bridges` and the ledger tables are declared once, by the DDL, and neither path
#: touches them.
_COMPARED = (
    "nodes",
    "rdf_labels",
    "rdf_props",
    "rdf_edges",
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
)

#: Built by a `CREATE TABLE` on both paths — `nodes` already declared `graph_id` at
#: 3.2.0 (spec 214), and the embedding tables are drained into a new table and renamed.
_REBUILT_BY_MIGRATION = (
    "nodes",
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
)

#: Reached by `ALTER TABLE … ADD COLUMN graph_id` on the upgrade path.
_ALTERED_BY_MIGRATION = ("rdf_labels", "rdf_props", "rdf_edges")

#: An identity column's default is the name of its own storage counter global
#: (`$i(^t52S.CE3O.1)`), which IRIS derives per compiled class. Two installs of the
#: same DDL differ there by construction, so the name is normalised away while the
#: defaults that are part of the declaration — `graph_id DEFAULT ''`, reported as
#: `$c(0)` — are still compared.
_COUNTER = re.compile(r"\$i\(\^[^)]*\)")


def _normalise_default(value) -> str:
    if value is None:
        return ""
    return _COUNTER.sub("$i(^<identity counter>)", str(value))


def _table_columns(cursor, table: str) -> dict:
    cursor.execute(
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, "
        "IS_NULLABLE, COLUMN_DEFAULT, ORDINAL_POSITION "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION",
        [table],
    )
    return {
        str(r[0]): (str(r[1]), r[2], r[3], str(r[4]), _normalise_default(r[5]))
        for r in cursor.fetchall() or []
    }


def _table_order(cursor, table: str) -> list:
    """``table``'s columns in declaration order.

    Kept apart from the declarations because the two paths genuinely disagree here and
    nowhere else: see
    `test_the_upgrade_appends_graph_id_where_a_fresh_install_declares_it_first`.
    """
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION",
        [table],
    )
    return [str(r[0]) for r in cursor.fetchall() or []]


def _table_constraints(cursor, table: str) -> dict:
    """Each constraint of ``table`` as name → (type, ordered column list)."""
    cursor.execute(
        "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
        [table],
    )
    kinds = {str(r[0]): str(r[1]) for r in cursor.fetchall() or []}
    cursor.execute(
        "SELECT CONSTRAINT_NAME, COLUMN_NAME, ORDINAL_POSITION "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
        [table],
    )
    columns: dict = {}
    for row in cursor.fetchall() or []:
        columns.setdefault(str(row[0]), []).append((int(row[2]), str(row[1])))
    return {
        name: (kind, [c for _, c in sorted(columns.get(name, []))])
        for name, kind in kinds.items()
    }


def _references(cursor) -> set:
    """Every foreign key as (child constraint, the unique constraint it points at)."""
    cursor.execute(
        "SELECT CONSTRAINT_NAME, UNIQUE_CONSTRAINT_NAME "
        "FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = 'Graph_KG'"
    )
    return {(str(r[0]), str(r[1])) for r in cursor.fetchall() or []}


def _catalog(conn) -> dict:
    cursor = conn.cursor()
    try:
        return {
            "tables": sorted(_tables(cursor)),
            "columns": {t: _table_columns(cursor, t) for t in _COMPARED},
            "order": {t: _table_order(cursor, t) for t in _COMPARED},
            "constraints": {t: _table_constraints(cursor, t) for t in _COMPARED},
            "references": _references(cursor),
            "widths": {
                t: GraphSchema.get_embedding_dimension(cursor, f"Graph_KG.{t}")
                for t in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized")
            },
            "procedures": _procedures(cursor),
            # The implementing class of each compared table, read from the dictionary.
            # Not compared for equality — the migration renames the table and not the
            # class — but it must *resolve* on both installs, which is the property
            # every reader in the package actually depends on.
            "classes": {
                t: GraphSchema.resolve_table_class(cursor, f"Graph_KG.{t}")
                for t in _COMPARED
            },
        }
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _install_fresh(conn, dimension: int) -> None:
    """Drop what the DDL declares and let `initialize_schema` declare it again.

    This is the fresh path in the only sense that matters to a catalog comparison: the
    tables are gone, so every column, constraint and index in them comes from the 4.0.0
    DDL rather than from an earlier release plus an ALTER.
    """
    from iris_vector_graph import IRISGraphEngine

    cursor = conn.cursor()
    try:
        for table in _tables(cursor):
            if ROUTE.match(table) or table.endswith("_ivg400"):
                _quietly(cursor, f"DROP TABLE Graph_KG.{table}")
        for view in _VIEWS:
            _quietly(cursor, f"DROP VIEW SQLUser.{view}")
        # Same teardown the 3.2.0 rebuild uses: release the foreign keys by name and
        # keep dropping until `nodes` is gone. A single pass leaves it standing.
        _tear_down_tables(cursor, [])
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    IRISGraphEngine(conn, embedding_dimension=dimension).initialize_schema(
        auto_deploy_objectscript=False
    )


@pytest.fixture(scope="module")
def catalogs(iris_connection):
    """The migrated catalog and the fresh catalog, in that order.

    A missing container is a FAILURE, never a skip (constitution VIII gate 1): the
    subject is what IRIS declared, and a skipped catalog comparison reads exactly like
    a passing one.

    The migrated install is captured first because the fresh install destroys it. The
    namespace is left fresh at the width the rest of the suite assumes.
    """
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "T067 compares two catalogs, which only IRIS can report. Start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail("no live IRIS connection: a catalog cannot be mocked")

    _rebuild_320(iris_connection)
    # The migrated catalog is only the upgraded one if the upgrade had 3.2.0 to work on.
    _assert_is_320(iris_connection)
    env = Env320(iris_connection)
    env.node("ivg227fvm:1")
    env.label("ivg227fvm:1")
    env.vector("ivg227fvm:1")
    env.vector("ivg227fvm:1", table="kg_NodeEmbeddings_optimized")
    env.commit()
    report = migrate_to_graph_scoped_embeddings(iris_connection)
    assert report.rows_placed, (
        "the migration placed nothing, so the catalog it produced is not the upgraded "
        f"one this compares: {report.rows_placed} / {report.rows_quarantined}"
    )
    migrated = _catalog(iris_connection)

    _install_fresh(iris_connection, DIM)
    fresh = _catalog(iris_connection)

    yield migrated, fresh

    # 768 is what the session fixture installs and what the rest of the e2e and
    # integration suites write through.
    with contextlib.suppress(Exception):
        _install_fresh(iris_connection, 768)


def test_both_installs_declare_the_same_tables(catalogs):
    migrated, fresh = catalogs
    assert migrated["tables"] == fresh["tables"]


def test_the_migration_leaves_no_staging_table_behind(catalogs):
    migrated, _ = catalogs
    leftovers = [t for t in migrated["tables"] if t.endswith("_ivg400")]
    assert leftovers == [], (
        f"the drain's staging tables outlived the reshape: {leftovers}. A fresh install "
        "has no such table, and one left here would be read as a route by the inventory."
    )


@pytest.mark.parametrize("table", _COMPARED)
def test_both_installs_declare_the_same_columns(catalogs, table):
    migrated, fresh = catalogs
    assert migrated["columns"][table] == fresh["columns"][table], (
        f"Graph_KG.{table} differs between an upgraded and a fresh install"
    )


@pytest.mark.parametrize("table", _REBUILT_BY_MIGRATION)
def test_a_rebuilt_table_declares_its_columns_in_the_same_order(catalogs, table):
    """`nodes` and the embedding tables are built by a `CREATE TABLE` on both paths."""
    migrated, fresh = catalogs
    assert migrated["order"][table] == fresh["order"][table]


@pytest.mark.parametrize("table", _ALTERED_BY_MIGRATION)
def test_the_upgrade_appends_graph_id_where_a_fresh_install_declares_it_first(
    catalogs, table
):
    """The one catalog difference the upgrade leaves, pinned so nothing joins it.

    3.2.0's `rdf_labels`, `rdf_props` and `rdf_edges` have no `graph_id`, and the
    upgrade adds it with `ALTER TABLE … ADD COLUMN`, which puts it last; the 4.0.0 DDL
    declares it first. Rebuilding those three tables to fix the position would mean
    copying every row of `rdf_edges` — the largest table in the schema — for a
    difference no named-column statement can observe. It is visible to `SELECT *` and
    to an `INSERT` with no column list, so it is documented rather than hidden.

    Asserting the exact expected order, rather than comparing the two as sets, is what
    keeps this from becoming a licence for further drift: a second column arriving by
    ALTER on one path only still fails here.
    """
    migrated, fresh = catalogs
    assert migrated["order"][table] == [
        c for c in fresh["order"][table] if c != "graph_id"
    ] + ["graph_id"]


@pytest.mark.parametrize("table", _COMPARED)
def test_both_installs_declare_the_same_constraints(catalogs, table):
    migrated, fresh = catalogs
    assert migrated["constraints"][table] == fresh["constraints"][table], (
        f"Graph_KG.{table}'s constraints differ between an upgraded and a fresh "
        "install, so a statement that relies on one of them works on only one of them"
    )


def test_both_installs_point_their_foreign_keys_at_the_same_keys(catalogs):
    migrated, fresh = catalogs
    assert migrated["references"] == fresh["references"]


def test_both_installs_declare_the_same_vector_width(catalogs):
    migrated, fresh = catalogs
    assert migrated["widths"] == fresh["widths"], (
        "INFORMATION_SCHEMA reports a VECTOR column as varchar(2767), so the width is "
        "read from %Dictionary.CompiledProperty; a mismatch here is a column that "
        "refuses the other install's vectors at INSERT (SQLCODE -104)"
    )


def test_both_installs_declare_the_same_procedures(catalogs):
    migrated, fresh = catalogs
    assert migrated["procedures"] == fresh["procedures"], (
        "the upgrade has to finish the install `initialize_schema` had to defer: while "
        "kg_NodeEmbeddings is keyed `id` the 4.0.0 kg_KNN_VEC body cannot compile, so "
        "the migration installs it after the reshape"
    )


def test_every_compared_table_resolves_a_class_on_both_installs(catalogs):
    """The migrated install's class name differs, and that has to stay inert.

    The upgrade drains into `kg_NodeEmbeddings_ivg400` and renames the SQL table into
    place, which moves the table name but not the class name. Nothing in the package
    derives a class from a table name for real work — `resolve_table_class` reads
    `%Dictionary.CompiledClass` — so what must hold is that both installs answer a
    class for every table, not that they answer the same one. `derive_class_name` is
    the guess used only when the dictionary cannot be read, and on a migrated install
    it is wrong; that is why it is documented as a guess.
    """
    migrated, fresh = catalogs
    unresolved = sorted(
        t for t in migrated["classes"] if not migrated["classes"][t] or not fresh["classes"][t]
    )
    assert not unresolved, (
        "these tables have no projecting class on one of the two installs, so every "
        f"index report and classmethod bridge against them is blind: {unresolved}"
    )


def test_the_upgraded_embedding_table_is_keyed_by_graph_and_node(catalogs):
    """Guards the comparison itself: two identical *wrong* catalogs would pass above."""
    migrated, _ = catalogs
    columns = migrated["columns"]["kg_NodeEmbeddings"]
    assert "graph_id" in columns and "node_id" in columns and "id" not in columns
    assert migrated["constraints"]["kg_NodeEmbeddings"]["uq_emb_graph_node"] == (
        "UNIQUE",
        ["graph_id", "node_id"],
    )
