"""A scoped mutation must not reach into another graph (FR-034, FR-009).

The destructive half of tests/unit/test_227_cypher_child_scope.py. That file
covers the reads and the CREATE; this one covers DELETE, SET and REMOVE, where
being unscoped costs data rather than accuracy.

Dumped before this file existed, `USE GRAPH 'A' MATCH (n {node_id: 'x'}) DETACH
DELETE n` emitted five statements and not one of them named a graph: the node
row, the labels, the properties, the vector and the edges of *every* graph
holding node `x` were deleted. Same for `SET n.side` (it updated both graphs'
property rows to graph A's value) and both spellings of `REMOVE`.

That was correct before the re-key, when `node_id` was namespace-unique and
`WHERE s = 'x'` could only mean one node. `UNIQUE (graph_id, node_id)` makes the
same statement a cross-graph delete.

Two more things this file pins:

- The DML subquery itself has to be scoped, not just the outer statement. If the
  subquery resolves `MATCH (n:Patient)` against every graph's labels, then a
  correctly scoped `DELETE ... WHERE s IN (<ids>)` still deletes graph A's rows
  for a node that only graph B calls a `Patient`.
- `DELETE FROM kg_NodeEmbeddings WHERE id IN (...)` names a column 4.0.0
  removed: the table is keyed `(graph_id, node_id)` now, with `emb_rowid` as its
  identity (schema.py, contracts/sql-schema.md §2). An unfixed `id` is SQLCODE
  -29 on every `DETACH DELETE`.

Unscoped mutations are deliberately left at 3.2.0 behaviour, matching the read
side: a statement with no `USE GRAPH` still spans every graph. Narrowing it to
the default graph would silently stop deleting rows that today's callers expect
to be deleted, which is a worse failure than the one being fixed here. Recorded
in reader-inventory.md §10.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

GRAPH = "ivg227-A"


def _statements(cypher: str) -> list:
    result = translate_to_sql(parse_query(cypher), {})
    sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
    # A statement may span lines (the subquery is newline-formatted), so split on
    # the verbs rather than on newlines.
    out = []
    for line in sql.split("\n"):
        if line[:6].upper() in ("DELETE", "UPDATE", "INSERT") or line.startswith("WITH "):
            out.append(line)
        elif out:
            out[-1] += "\n" + line
    return out


def _targeting(cypher: str, table: str) -> list:
    """Statements whose *target* is `table` — the verb's table, not a subquery's.

    `_schema_prefix` is a module global another test may have set to "Graph_KG",
    so the bare name is matched at the end of the qualified name.
    """
    found = []
    for stmt in _statements(cypher):
        head = stmt.split("\n", 1)[0]
        for verb in ("DELETE FROM ", "UPDATE ", "INSERT INTO "):
            if verb in head:
                target = head.split(verb, 1)[1].split()[0].split("(")[0]
                if target.endswith(table):
                    found.append(stmt)
                break
    return found


@pytest.mark.parametrize("table", ["rdf_labels", "rdf_props", "nodes", "kg_NodeEmbeddings"])
def test_a_scoped_detach_delete_only_deletes_that_graphs_rows(table):
    stmts = _targeting(f"USE GRAPH '{GRAPH}' MATCH (n {{node_id: 'x'}}) DETACH DELETE n", table)

    assert stmts, f"no statement targets {table}, so this test proves nothing"
    for stmt in stmts:
        # The predicate has to be on the deleted table, not only inside the
        # subquery: the subquery narrows *which node IDs*, and every graph's rows
        # share the ID.
        target_clause = stmt.split("\n")[0]
        assert GRAPH in target_clause or GRAPH in stmt.split(")")[-1], (
            f"the delete from {table} names no graph, so it deletes every graph's "
            f"rows for that node ID:\n{stmt}"
        )


def test_the_subquery_that_picks_the_rows_is_scoped_too():
    """A label match resolved across graphs picks the wrong node IDs."""
    stmts = _targeting(
        f"USE GRAPH '{GRAPH}' MATCH (n:Patient) DETACH DELETE n",
        "rdf_labels",
    )

    assert stmts
    subquery = stmts[0][stmts[0].index("(") :]
    assert GRAPH in subquery, (
        f"the subquery resolves the pattern against every graph, so a node only "
        f"another graph labels `Patient` still selects this graph's rows for "
        f"deletion:\n{stmts[0]}"
    )


def test_the_vector_delete_names_the_column_the_table_actually_has():
    """4.0.0 re-keyed the embedding tables: `id` is gone, `node_id` replaced it."""
    stmts = _targeting(
        f"USE GRAPH '{GRAPH}' MATCH (n {{node_id: 'x'}}) DETACH DELETE n",
        "kg_NodeEmbeddings",
    )

    assert stmts
    head = stmts[0].split("\n")[0]
    assert "node_id IN" in head, (
        f"the vector delete still names the `id` column 4.0.0 removed, so every "
        f"DETACH DELETE fails with SQLCODE -29: {head}"
    )


def test_a_scoped_set_updates_only_that_graphs_property():
    for stmt in _targeting(f"USE GRAPH '{GRAPH}' MATCH (n {{node_id: 'x'}}) SET n.side = 'z'", "rdf_props"):
        assert GRAPH in stmt, (
            f"SET writes through to every graph's property row for that node ID:\n{stmt}"
        )


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n {node_id: 'x'}) REMOVE n:Patient",
        "MATCH (n {node_id: 'x'}) REMOVE n.side",
    ],
    ids=["label", "property"],
)
def test_a_scoped_remove_removes_only_that_graphs_row(cypher):
    stmts = _statements(f"USE GRAPH '{GRAPH}' {cypher}")

    assert stmts, "no statement was emitted, so this test proves nothing"
    for stmt in stmts:
        assert GRAPH in stmt, f"REMOVE reaches every graph holding that node ID:\n{stmt}"


def test_an_unscoped_mutation_is_left_alone():
    """No `USE GRAPH` keeps 3.2.0 reach. Narrowing it to the default graph would
    stop deleting rows callers expect to be deleted — a silent behaviour change
    dressed as a fix."""
    for stmt in _statements("MATCH (n {node_id: 'x'}) DETACH DELETE n"):
        assert "graph_id" not in stmt, f"an unscoped delete grew a graph predicate:\n{stmt}"
