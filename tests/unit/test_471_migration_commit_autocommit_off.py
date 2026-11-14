"""4.7.1 (H3): with the driver's autocommit off, a migration's step commit commits the migration's own writes.

4.7.0's `commit_step` asked `refuse_in_caller_transaction` again before each `conn.commit()`. With autocommit off and
nothing written by the caller, `$TLEVEL` is 0 at the migration's entry, so the entry refusal lets it run; the
migration's first write raises `$TLEVEL` to 1, which the second check took for the caller's, and every migration
stopped at its first commit with `CallerTransactionOpenError` ("Nothing was done") over its own open writes. The
caller's transaction is refused at entry only (each migration's own first statement), which is unchanged.

The connection is a mock that claims to be an IRIS connection (`spec=iris.IRISConnection`) with autocommit off, whose
cursor answers `SELECT $TLEVEL` with 1: the state at a step's commit after the migration's own first write (the entry
case below: after the caller's). tests/e2e/test_471_migration_autocommit_off_e2e.py has the live answers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import iris
import pytest

from iris_vector_graph.errors import CallerTransactionOpenError
from iris_vector_graph.migrations import docs_and_edge_vectors, kg_node_stores, upgrade
from iris_vector_graph.migrations._commit import commit_step
from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator


def _conn(level=1):
    conn = MagicMock(spec=iris.IRISConnection)
    conn.autocommit = False
    cur = conn.cursor.return_value
    sent = []

    def execute(sql, *a):
        sent.append(sql)
        assert sql == "SELECT $TLEVEL", f"sent {sql!r}"

    cur.execute.side_effect = execute
    cur.fetchone.return_value = (level,)
    conn.sent = sent
    return conn


STEP_COMMITS = [
    (lambda c: docs_and_edge_vectors._commit(c), "migrate_docs_and_edge_vectors"),
    (lambda c: kg_node_stores._commit(c), "rekey_kg_node_stores"),
    (lambda c: _Migrator._commit(MagicMock(conn=c)), "migrate_to_graph_scoped_embeddings"),
    (lambda c: upgrade._tune(c, "Graph_KG"), "upgrade_to_4_0_0"),
]
IDS = ["docs_and_edge_vectors", "kg_node_stores", "graph_scoped_embeddings", "upgrade_tune"]


@pytest.mark.parametrize("commit, name", STEP_COMMITS, ids=IDS)
def test_a_step_commit_with_autocommit_off_commits_the_migrations_own_writes(commit, name, monkeypatch):
    monkeypatch.setattr("iris_vector_graph.schema.GraphSchema.tune_tables", lambda *a, **k: {})
    conn = _conn(level=1)
    commit(conn)
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    assert "SELECT $TLEVEL" not in conn.sent, "the step's commit asked again whose transaction it is"


def test_commit_step_on_an_iris_connection_with_autocommit_off_only_commits():
    conn = _conn(level=1)
    commit_step(conn, "upgrade_to_4_0_0")
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    assert conn.sent == []


ENTRIES = [
    (lambda c: upgrade.upgrade_to_4_0_0(c), "upgrade_to_4_0_0"),
    (lambda c: __import__("iris_vector_graph.migrations.graph_scoped_embeddings", fromlist=["x"])
     .migrate_to_graph_scoped_embeddings(c), "migrate_to_graph_scoped_embeddings"),
    (lambda c: docs_and_edge_vectors.migrate_docs_and_edge_vectors(c, 8), "migrate_docs_and_edge_vectors"),
    (lambda c: kg_node_stores.rekey_kg_node_stores(c), "rekey_kg_node_stores"),
]


@pytest.mark.parametrize("call, name", ENTRIES, ids=[n for _, n in ENTRIES])
def test_a_callers_write_before_the_call_is_still_refused_at_entry(call, name):
    """Autocommit off and the caller's own write ($TLEVEL 1): refused before the migration sends anything else."""
    conn = _conn(level=1)
    with pytest.raises(CallerTransactionOpenError) as info:
        call(conn)
    assert (info.value.operation, info.value.level) == (f"{name}()", 1)
    assert "Nothing was done" in str(info.value)
    assert conn.sent == ["SELECT $TLEVEL"]
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


SUB_STEPS = [
    (lambda c: docs_and_edge_vectors.prepare_docs(c), "prepare_docs"),
    (lambda c: docs_and_edge_vectors.finish_docs(c), "finish_docs"),
    (lambda c: docs_and_edge_vectors.prepare_edge_vectors(c, 8), "prepare_edge_vectors"),
    (lambda c: docs_and_edge_vectors.finish_edge_vectors(c), "finish_edge_vectors"),
    (lambda c: docs_and_edge_vectors.place_documents(c), "place_documents"),
    (lambda c: docs_and_edge_vectors.place_edge_vectors(c), "place_edge_vectors"),
]


@pytest.mark.parametrize("call, name", SUB_STEPS, ids=[n for _, n in SUB_STEPS])
def test_a_docs_sub_step_called_on_its_own_refuses_the_callers_transaction_before_writing(call, name):
    """The six steps `upgrade_to_4_0_0` and `migrate_docs_and_edge_vectors` sequence are public on their own (an
    operator is told to run `prepare_docs()`). 4.7.0 refused a caller's transaction at their commit, after writing; with
    the step commit no longer asking, each asks at its own entry, before it sends anything else."""
    conn = _conn(level=1)
    with pytest.raises(CallerTransactionOpenError) as info:
        call(conn)
    assert (info.value.operation, info.value.level) == (f"{name}()", 1)
    assert conn.sent == ["SELECT $TLEVEL"]
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()


@pytest.mark.parametrize("name", ["place_documents", "place_edge_vectors"])
def test_a_placement_dry_run_sends_no_probe(name):
    """A dry run writes nothing, so it is not refused: its first statement is its own read, not `SELECT $TLEVEL`
    (the stand-in fails that read, which ends the call)."""
    conn = _conn(level=1)
    with pytest.raises(AssertionError, match=r"^sent "):
        getattr(docs_and_edge_vectors, name)(conn, dry_run=True)
    assert conn.sent and conn.sent[0] != "SELECT $TLEVEL", conn.sent
