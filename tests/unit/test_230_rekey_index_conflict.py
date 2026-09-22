"""Spec 230 — the `nodes` re-key survives a unique key that is already there.

Measured against a real 3.2.0 install upgraded by the working tree, after the
`rdf_edges` rescue was fixed. The rescue has to add `uq_nodes_graph_node` itself
before it can rebuild a table whose foreign keys reference
`nodes (graph_id, node_id)` (FR-027). The embeddings migration then runs the same
`ALTER TABLE` from `get_graph_scope_migration_sql()`, and IRIS refuses the second
one by the *index* name it derives from the constraint:

    SQLCODE -400: ERROR #5067: Index name conflict: uqnodesgraphnode

which says none of the things `_tolerate` was looking for — not "already", not
"not unique", not `-201`. So a statement whose work was already done aborted the
whole upgrade, with `rdf_labels` still unscoped and the `^KG` re-key never reached.

The needle is the point: `_tolerate` exists so that a step a re-run finds finished
is not fatal, and "the constraint is already there" is exactly that case.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator

ALTER = (
    "ALTER TABLE Graph_KG.nodes ADD CONSTRAINT uq_nodes_graph_node "
    "UNIQUE (graph_id, node_id)"
)

#: What IRIS answered on the live container, verbatim.
INDEX_CONFLICT = (
    "<SQL ERROR>; Details: [SQLCODE: <-400>:<Fatal error occurred>]\n"
    "[Location: <ServerLoop>]\n"
    "[%msg: <ERROR #5067: Index name conflict: uqnodesgraphnode>]"
)


class FakeCursor:
    """Refuses statements matching `fail_on` with `error`, records the rest."""

    def __init__(self, fail_on=(), error=INDEX_CONFLICT):
        self.statements: list[str] = []
        self.fail_on = tuple(fail_on)
        self.error = error

    def execute(self, sql, params=None):  # noqa: D102
        self.statements.append(sql)
        for needle in self.fail_on:
            if needle.lower() in sql.lower():
                raise RuntimeError(self.error)

    def fetchall(self):  # noqa: D102
        return []

    def fetchone(self):  # noqa: D102
        return None

    def close(self):  # noqa: D102
        pass


def tolerate(cursor, sql):
    """`_Migrator._tolerate` without building a migrator — it uses no state."""
    return _Migrator._tolerate(_Migrator.__new__(_Migrator), cursor, sql)


class TestAnIndexNameConflictIsAlreadyDone:
    def test_the_alter_is_tolerated(self):
        cursor = FakeCursor(fail_on=("uq_nodes_graph_node",))
        assert tolerate(cursor, ALTER) is False

    def test_the_statement_was_actually_attempted(self):
        """Tolerated means "ran and was refused", not "skipped"."""
        cursor = FakeCursor(fail_on=("uq_nodes_graph_node",))
        tolerate(cursor, ALTER)
        assert cursor.statements == [ALTER]

    def test_a_statement_that_succeeds_still_reports_that_it_took_effect(self):
        cursor = FakeCursor()
        assert tolerate(cursor, ALTER) is True

    def test_an_unrelated_fatal_error_still_raises(self):
        """-400 alone is not a licence to continue: only the conflict is benign."""
        cursor = FakeCursor(
            fail_on=("uq_nodes_graph_node",),
            error=(
                "<SQL ERROR>; Details: [SQLCODE: <-400>:<Fatal error occurred>]\n"
                "[%msg: <ERROR #5002: ObjectScript error: <UNDEFINED>>]"
            ),
        )
        with pytest.raises(RuntimeError):
            tolerate(cursor, ALTER)
