"""4.7.0 (row 3b's audit): a migration step whose commit fails raises, naming the migration, after rolling it back.

Each migration's `_commit` logged the failure at DEBUG ("Commit not needed") and upgrade_to_4_0_0's TUNE step passed
it over, so an upgrade went on to its next step with the last one unwritten. The connection here is a stand-in (not
IRIS's, so the caller-transaction probe is not sent) whose commit fails.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.migrations import docs_and_edge_vectors, kg_node_stores, upgrade
from iris_vector_graph.migrations._commit import commit_step
from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator

LOCK = "[SQLCODE: <-110>:<Locking conflict in filing>]"


def _conn():
    conn = MagicMock()
    conn.commit.side_effect = RuntimeError(LOCK)
    return conn


@pytest.mark.parametrize(
    "commit, name",
    [
        (lambda c: docs_and_edge_vectors._commit(c), "migrate_docs_and_edge_vectors"),
        (lambda c: kg_node_stores._commit(c), "rekey_kg_node_stores"),
        (lambda c: _Migrator._commit(MagicMock(conn=c)), "migrate_to_graph_scoped_embeddings"),
        (lambda c: upgrade._tune(c, "Graph_KG"), "upgrade_to_4_0_0"),
    ],
    ids=["docs_and_edge_vectors", "kg_node_stores", "graph_scoped_embeddings", "upgrade_tune"],
)
def test_a_failed_commit_raises_naming_the_migration_and_rolls_back(commit, name, monkeypatch):
    monkeypatch.setattr("iris_vector_graph.schema.GraphSchema.tune_tables", lambda *a, **k: {})
    conn = _conn()
    with pytest.raises(RuntimeError, match=rf"^{name}: a step's commit failed, and the step was rolled back: .*-110"):
        commit(conn)
    conn.rollback.assert_called_once()


def test_a_commit_that_succeeds_is_all():
    conn = MagicMock()
    commit_step(conn, "upgrade_to_4_0_0")
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
