"""The bulk loader's templates have to match the 4.0.0 columns (FR-034, FR-002).

`GraphSchema.get_bulk_insert_sql` is a table of hand-written INSERT statements,
so a re-key does not reach it: the statements still compile, they just name
columns the table no longer has or omit ones it now keys on.

Two concrete breakages this file pins:

- `rdf_labels` and `rdf_props` gained `graph_id` and re-keyed on it, so a bulk
  load that names only `(s, label)` writes every node's labels into the default
  graph — including nodes the same call wrote into a named graph — and its
  `WHERE NOT EXISTS` guard, keyed on `s` alone, reports "already there" for
  another graph's row and skips the write with no error.
- `kg_NodeEmbeddings` has no `id` column any more. The template still names it,
  which is SQLCODE -29 on the first row rather than a wrong answer.

And one behaviour: `bulk_create_nodes` accepts a per-node `graph` and, before
this, put it in a `__graph` property rather than the column — the pseudo-property
spec 214 FR-019 replaced and whose migration deletes on sight (schema.py:614).
So a bulk-loaded node claimed a graph that no graph-aware reader could see, and
the claim was erased by the next migration run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

GRAPH = "ivg227-bulk-A"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (4,)
    cursor.fetchall.return_value = []
    cursor.description = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


def _many(cursor, table: str):
    out = []
    for call in cursor.executemany.call_args_list:
        if not call.args:
            continue
        sql = " ".join(str(call.args[0]).split())
        if table in sql:
            out.append((sql, [list(r) for r in call.args[1]]))
    return out


@pytest.mark.parametrize("table", ["rdf_labels_with_graph", "rdf_props_with_graph"])
def test_the_child_tables_have_a_graph_aware_template(table):
    """`rdf_edges_with_graph` is the naming precedent; the children need the same."""
    sql = GraphSchema.get_bulk_insert_sql(table)

    columns = sql[sql.index("(") : sql.index(" SELECT ")]
    assert "graph_id" in columns, f"the row does not carry its graph:\n{sql}"


@pytest.mark.parametrize("table", ["rdf_labels_with_graph", "rdf_props_with_graph"])
def test_the_graph_aware_guard_is_scoped(table):
    """An unscoped guard skips the insert because another graph holds that key."""
    sql = GraphSchema.get_bulk_insert_sql(table)

    guard = sql[sql.index("NOT EXISTS") :]
    assert "graph_id" in guard, f"the guard reads across graphs:\n{sql}"


def test_the_embedding_template_names_the_columns_the_table_has():
    sql = GraphSchema.get_bulk_insert_sql("kg_NodeEmbeddings")

    columns = sql[sql.index("(") : sql.index(" SELECT ")]
    assert "node_id" in columns, f"4.0.0 renamed `id` to `node_id`:\n{sql}"
    assert "graph_id" in columns, f"the vector does not carry its graph:\n{sql}"
    assert "NOT EXISTS" in sql
    assert "graph_id" in sql[sql.index("NOT EXISTS") :], (
        f"the guard is keyed on the node ID alone, which is not unique any "
        f"more, so the second graph's vector is silently skipped:\n{sql}"
    )


def test_a_bulk_loaded_nodes_labels_land_in_its_graph():
    engine, cursor = _engine()

    engine.bulk_create_nodes(
        [{"id": "shared-1", "labels": ["Patient"], "properties": {}, "graph": GRAPH}],
        disable_indexes=False,
    )

    calls = _many(cursor, "rdf_labels")
    assert calls, "no label insert was issued, so this test proves nothing"
    sql, rows = calls[0]
    assert "graph_id" in sql, f"the bulk label insert names no graph:\n{sql}"
    for row in rows:
        assert GRAPH in row, f"a label row carries a graph other than the node's: {row}"


def test_a_bulk_loaded_nodes_properties_land_in_its_graph():
    engine, cursor = _engine()

    engine.bulk_create_nodes(
        [{"id": "shared-1", "labels": [], "properties": {"side": "l"}, "graph": GRAPH}],
        disable_indexes=False,
    )

    calls = _many(cursor, "rdf_props")
    assert calls
    sql, rows = calls[0]
    assert "graph_id" in sql, f"the bulk property insert names no graph:\n{sql}"
    for row in rows:
        assert GRAPH in row, f"a property row carries a graph other than the node's: {row}"


def test_the_node_row_itself_carries_the_requested_graph():
    engine, cursor = _engine()

    engine.bulk_create_nodes(
        [{"id": "shared-1", "labels": [], "properties": {}, "graph": GRAPH}],
        disable_indexes=False,
    )

    calls = _many(cursor, "nodes")
    assert calls
    _, rows = calls[0]
    for row in rows:
        assert GRAPH in row, (
            f"the node was written to the default graph while its caller asked "
            f"for {GRAPH}: {row}"
        )


def test_the_graph_pseudo_property_is_not_written():
    """Spec 214 FR-019 replaced `__graph` with a column, and its migration
    deletes every `__graph` row (schema.py:614), so writing one here produces a
    graph claim that disappears on the next upgrade."""
    engine, cursor = _engine()

    engine.bulk_create_nodes(
        [{"id": "shared-1", "labels": [], "properties": {}, "graph": GRAPH}],
        disable_indexes=False,
    )

    for _, rows in _many(cursor, "rdf_props"):
        for row in rows:
            assert "__graph" not in row, f"the 214-era pseudo-property is still written: {row}"
