"""Spec 227 — `initialize_schema` on an install the migration has not run yet.

The 4.0.0 `kg_KNN_VEC` body names `n.node_id` and `n.graph_id`. A 3.2.0 embedding
table has neither: its key column is `id`. So on a real consumer install — package
upgraded, migration not yet run — the procedure cannot compile, and IRIS says so with
`SQLCODE -29 … Field 'N.NODE_ID' not found`.

Until this, that was a hard `RuntimeError` out of `initialize_schema`, which is the
worst possible ordering trap: the migration needs `embedding_quarantine`, and
`initialize_schema` is the only thing that creates it, so the failing call was a
prerequisite of the call that fixes it. Neither order worked.

The fix is to decide by *shape* rather than by error text, and before issuing the
statement: a table still keyed `id` is awaiting the migration, so its core procedure is
deferred with a message naming the migration, and the migration installs it once the
reshape has given the table the columns the body reads.

`tests/e2e/test_227_migration.py` is the live gate for the whole sequence; this file
pins the two halves apart, because the failure mode of both is a procedure that
silently never gets installed.
"""

from __future__ import annotations

import pytest
from tests.unit.migration_fakes_227 import LEGACY_COLUMNS, ROUTED_COLUMNS, MigrationRegistry
from tests.unit.route_fakes_227 import engine_with


def _install(columns):
    """An engine over a fake whose `kg_NodeEmbeddings` declares ``columns``."""
    registry = MigrationRegistry(columns={"kg_NodeEmbeddings": tuple(columns)})
    engine = engine_with(registry)
    return registry, engine


def _procedures(registry):
    """Every statement *declaring* kg_KNN_VEC.

    Matched on the declaration rather than the name: `kg_RRF_FUSE`'s body calls
    `kg_KNN_VEC`, so a bare name match reports the optional procedure as the core one.
    """
    return [sql for sql, _ in registry.statements if "PROCEDURE Graph_KG.kg_KNN_VEC" in sql]


# --- the shape probe --------------------------------------------------------------


def test_a_table_still_keyed_id_is_awaiting_the_migration():
    registry, engine = _install(LEGACY_COLUMNS)

    assert engine._embeddings_await_migration(registry.cursor()) is True


def test_a_reshaped_table_is_not_awaiting_the_migration():
    registry, engine = _install(ROUTED_COLUMNS)

    assert engine._embeddings_await_migration(registry.cursor()) is False


def test_a_table_with_neither_column_is_not_treated_as_pre_migration():
    """Absence is not evidence of 3.2.0.

    A missing table means the DDL earlier in `initialize_schema` failed, and deferring
    the procedure there would hide that behind a message about a migration that has
    nothing to do with it.
    """
    registry, engine = _install(("emb", "metadata"))

    assert engine._embeddings_await_migration(registry.cursor()) is False


# --- what the install loop does with it -------------------------------------------


def test_the_core_procedure_is_not_even_attempted_before_the_migration():
    """Not "attempted and tolerated": the statement cannot compile against this table,
    and an attempt would put an IRIS error in the log for an install that is simply
    mid-upgrade."""
    registry, engine = _install(LEGACY_COLUMNS)

    engine._install_procedures(registry.cursor())

    assert _procedures(registry) == []


def test_deferring_it_says_which_call_finishes_the_job(caplog):
    registry, engine = _install(LEGACY_COLUMNS)

    with caplog.at_level("WARNING"):
        engine._install_procedures(registry.cursor())

    assert "migrate_to_graph_scoped_embeddings" in caplog.text, (
        "the deferral has to name the call that resolves it; an operator reading "
        "'deferred' with no next step will conclude the install is broken"
    )


def test_the_core_procedure_is_attempted_on_a_reshaped_table():
    registry, engine = _install(ROUTED_COLUMNS)

    engine._install_procedures(registry.cursor())

    assert _procedures(registry), "kg_KNN_VEC was never installed on a 4.0.0 schema"


def test_a_genuine_failure_on_a_reshaped_table_still_raises():
    """The deferral is narrow on purpose. Once the columns are there, a procedure that
    will not compile is a broken install, and it was reported as one since 2.x."""
    registry, engine = _install(ROUTED_COLUMNS)
    cursor = _RefusingCursor(registry)

    with pytest.raises(RuntimeError, match="stored procedure"):
        engine._install_procedures(cursor)


# --- the other half: the migration installs what the deferral left ----------------


def test_the_migration_installs_the_procedure_it_made_installable():
    """The reshape is what gives `kg_KNN_VEC`'s body its columns, so the migration is
    the only place that can finish the install an upgrade deferred."""
    from tests.unit.migration_fakes_227 import legacy_install, migration_conn
    from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings

    registry = legacy_install(node_graphs={"a": [""]})

    migrate_to_graph_scoped_embeddings(migration_conn(registry))

    assert _procedures(registry), (
        "the migration reshaped the table and left the namespace without the "
        "procedure every server-side vector search goes through"
    )


def test_a_dry_run_installs_nothing():
    from tests.unit.migration_fakes_227 import legacy_install, migration_conn
    from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings

    registry = legacy_install(node_graphs={"a": [""]})

    migrate_to_graph_scoped_embeddings(migration_conn(registry), dry_run=True)

    assert _procedures(registry) == []


def test_a_second_run_over_an_already_routed_install_reinstalls_nothing():
    """Nothing was reshaped, so nothing needs re-declaring — and a re-run that
    re-issued the DDL would report a procedure install on an install it did not touch."""
    from tests.unit.migration_fakes_227 import legacy_install, migration_conn
    from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings

    registry = legacy_install(node_graphs={"a": [""]})
    conn = migration_conn(registry)
    migrate_to_graph_scoped_embeddings(conn)
    registry.statements.clear()

    migrate_to_graph_scoped_embeddings(conn)

    assert _procedures(registry) == []


class _RefusingCursor:
    """Refuses every `CREATE PROCEDURE`, answers the column probe from the fake."""

    def __init__(self, registry):
        self._inner = registry.cursor()

    def execute(self, sql, params=None):
        if "PROCEDURE" in str(sql).upper():
            raise RuntimeError("[SQLCODE: <-29>] Field 'N.NODE_ID' not found")
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)
