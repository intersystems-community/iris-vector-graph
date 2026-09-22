"""Spec 227 — the migration survives being interrupted and re-run (T060, FR-029).

A migration over every vector in a namespace is exactly the kind of job that gets
killed half way: a session timeout, a restart, an operator's `Ctrl-C`. What matters is
not that it never happens but that the fix is "run it again".

Two mechanisms carry that, and both are tested here rather than assumed:

* **the target's `(graph_id, node_id)` unique constraint**, which absorbs a row the
  killed pass had already moved. A check-then-insert would look equivalent and is not:
  between the check and the insert, a second writer places the row, and the migration
  then reports a duplicate it did not create.
* **a per-source-table watermark**, so a re-run over a million-row table does not
  re-attempt every row it already moved. The watermark is read from the destinations
  the source's rows go to, not recorded in a table of its own — there is no progress
  table to go stale.

The load-bearing assertion in every test is the same: the state after interrupt +
re-run equals the state after one clean run.
"""

import pytest

from tests.unit.migration_fakes_227 import legacy_install, migration_conn
from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings
from iris_vector_graph.routing import route_table_name

LEGACY = "kg_NodeEmbeddings"
ROUTE_A = route_table_name("graph-a", None)
ROUTE_B = route_table_name("graph-b", None)


def mixed_install(**kwargs):
    return legacy_install(
        node_graphs={"a": ["graph-a"], "b": ["graph-b"], "c": [""]},
        **kwargs,
    )


def run(registry, **kwargs):
    return migrate_to_graph_scoped_embeddings(migration_conn(registry), **kwargs)


def placement(registry):
    """Every `(table, graph_id, node_id)` the install now holds, sorted."""
    return sorted(
        (table, graph, node)
        for table in (LEGACY, ROUTE_A, ROUTE_B)
        for graph, node in registry.placed_in(table)
    )


# --- interrupted, then re-run ---------------------------------------------------------


def test_a_killed_pass_resumes_to_the_same_placement():
    interrupted = mixed_install(fail_after=1)
    with pytest.raises(RuntimeError):
        run(interrupted)
    assert placement(interrupted) != [], "nothing moved, so nothing is being resumed"

    run(interrupted)
    clean = mixed_install()
    run(clean)

    assert placement(interrupted) == placement(clean)


def test_a_resumed_run_duplicates_no_row():
    registry = mixed_install(fail_after=1)
    with pytest.raises(RuntimeError):
        run(registry)

    run(registry)

    for table in (LEGACY, ROUTE_A, ROUTE_B):
        placed = registry.placed_in(table)
        assert len(placed) == len(set(placed)), f"{table} holds a duplicate: {placed}"


def test_a_resumed_run_still_accounts_for_every_row():
    """The report is state, not a log of this pass: rows the killed pass placed are
    counted where they sit. An operator reconciling against the pre-upgrade count has
    no way to add up two partial reports."""
    registry = mixed_install(fail_after=1)
    with pytest.raises(RuntimeError):
        run(registry)

    report = run(registry)

    assert report.rows_accounted == 3


def test_a_row_the_killed_pass_moved_is_not_counted_twice():
    registry = mixed_install(fail_after=1)
    with pytest.raises(RuntimeError):
        run(registry)

    report = run(registry)

    assert sum(report.rows_placed.values()) == len(placement(registry))


def test_the_staging_table_the_killed_pass_left_is_reused_not_refused():
    """`CREATE TABLE` answers `SQLCODE -201` for a table that already exists, and a
    migration that treated that as fatal could never be run twice."""
    registry = mixed_install(fail_after=1)
    with pytest.raises(RuntimeError):
        run(registry)

    run(registry)  # must not raise

    assert registry.placed_in(LEGACY) == [("", "c")]


# --- run twice ------------------------------------------------------------------------


def test_a_second_complete_run_moves_nothing():
    """The second run finds `graph_id` and `node_id` declared on the legacy table,
    which is the shape probe saying the drain is done. It is a migration, not a sync."""
    registry = mixed_install()
    run(registry)
    moved_by_the_first = registry.moves

    report = run(registry)

    assert registry.moves == moved_by_the_first
    assert registry.dropped.count(LEGACY) == 1, "the reshape ran a second time"
    assert report.rows_accounted == 3, "a completed migration reports the state it left"


def test_a_second_run_reports_the_same_placement():
    registry = mixed_install()
    first = run(registry)

    second = run(registry)

    assert second.rows_placed == first.rows_placed
    assert second.rows_quarantined == first.rows_quarantined


def test_a_quarantined_row_is_not_quarantined_twice():
    """The quarantine's watermark is filtered by `source_table`, so a second pass over
    `kg_NodeEmbeddings` does not re-add a row it quarantined, and a pass over the
    optimized table is not skipped by the first table's progress."""
    registry = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})
    run(registry)

    run(registry)

    assert len(registry.quarantined()) == 1


# --- the mechanisms, not just the outcome --------------------------------------------


def test_the_drain_reads_from_a_watermark_rather_than_re_reading_the_table():
    registry = mixed_install(fail_after=2)
    with pytest.raises(RuntimeError):
        run(registry)

    registry.statements.clear()
    run(registry)

    keyed = [
        (sql, params)
        for sql, params in registry.statements
        if sql.upper().startswith("SELECT") and " ORDER BY " in sql.upper()
    ]
    assert keyed, "the drain did not read its source in key order"
    assert any(params and str(params[0]) for _sql, params in keyed), (
        "the resumed drain bound an empty watermark, so it re-read every row: "
        f"{keyed}"
    )


def test_a_row_already_at_its_target_is_tolerated_not_fatal():
    """The unique constraint is the idempotence mechanism, so the migration has to
    survive the violation it is relying on — including one a concurrent writer caused
    rather than a previous pass."""
    registry = mixed_install()
    registry.columns[ROUTE_A] = ("emb_rowid", "graph_id", "node_id", "emb", "metadata")
    registry.data[ROUTE_A] = [
        {"graph_id": "graph-a", "node_id": "a", "emb": "<a>", "metadata": None}
    ]
    registry.tables.append(ROUTE_A)
    registry.rows.append(
        {
            "table_name": ROUTE_A,
            "graph_id": "graph-a",
            "mechanism": None,
            "model_key": None,
            "declared_config": None,
            "dimension": 4,
            "dtype": "DOUBLE",
            "set_by": "routed",
            "index_state": "present",
            "index_error": None,
        }
    )

    report = run(registry)

    assert registry.placed_in(ROUTE_A) == [("graph-a", "a")]
    assert report.rows_accounted == 3
