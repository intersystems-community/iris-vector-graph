"""`USE GRAPH` has to reach the child tables, not just the node row (FR-010, FR-034).

Found while writing the Phase 3 read-isolation gate. tasks.md T014 asserts that
"`cypher/translator.py` is untouched, but the tables its SQL joins are not". The
first half turned out to be false. Before this file, `USE GRAPH 'A'` reached:

- the `nodes` INSERT (`graph_id` in the column list, and in the guard)
- the `rdf_edges` INSERT
- a *named* relationship variable's alias on the read side

and nothing else. Specifically:

- `CREATE (n:Patient {side: 'a'})` wrote the node into A and its label and its
  properties into the default graph, because neither INSERT names `graph_id`.
  Before T016 that was invisible — `rdf_labels` had no such column. After T016
  it is a silent cross-graph write.
- `MATCH (n:Patient) RETURN n` emitted no graph predicate at all: not on `n0`,
  not on the `rdf_labels` join, not on the `labels(n)` / `properties(n)`
  subqueries. So graph A's read returned graph B's node, graph B's labels and
  graph B's property values, and a label filter on a label held only in B
  matched a node in A.

The re-key is what makes this live: while `node_id` was unique namespace-wide, a
child row keyed on `s` alone could only ever belong to one node, so an unscoped
join could not be wrong. Now it can.

Scope of this file: the single-statement read and write paths, which is what
`USE GRAPH` is used for. Stage/CTE pipelines (`WITH`, `UNWIND` then `MATCH`) do
not project `graph_id` through their CTE columns, so they are out of reach of
this fix and are recorded in reader-inventory.md §10 instead of being asserted
here — a test that pins behaviour nobody has fixed is a test that has to be
deleted later.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

GRAPH = "ivg227-A"


def _sql(cypher: str) -> str:
    result = translate_to_sql(parse_query(cypher), {})
    return result.sql if isinstance(result.sql, str) else "\n".join(result.sql)


def _statements(cypher: str):
    return [s for s in _sql(cypher).split("\n") if s.strip()]


def _insert_into(cypher: str, table: str) -> list:
    # The table may or may not be schema-qualified: `_schema_prefix` is a module
    # global that another test in the same session may have set to "Graph_KG",
    # so matching on the bare name is a pollution-dependent pass.
    return [
        s
        for s in _statements(cypher)
        if s.startswith("INSERT INTO") and s.split(" (", 1)[0].endswith(table)
    ]


# --- writes -----------------------------------------------------------------


def test_a_scoped_create_writes_the_label_into_that_graph():
    inserts = _insert_into(f"USE GRAPH '{GRAPH}' CREATE (n:Patient {{node_id: 'x'}})", "rdf_labels")

    assert inserts, "no rdf_labels insert was emitted, so this test proves nothing"
    for sql in inserts:
        assert "graph_id" in sql, (
            f"the label insert names no graph, so `USE GRAPH '{GRAPH}'` puts the node "
            f"in one graph and its label in the default graph: {sql}"
        )


def test_a_scoped_create_writes_properties_into_that_graph():
    inserts = _insert_into(
        f"USE GRAPH '{GRAPH}' CREATE (n:Patient {{node_id: 'x', side: 'a'}})", "rdf_props"
    )

    assert inserts, "no rdf_props insert was emitted, so this test proves nothing"
    for sql in inserts:
        assert "graph_id" in sql, f"the property insert names no graph: {sql}"


def test_the_label_guard_looks_in_the_same_graph_it_writes():
    """The `WHERE NOT EXISTS` guard is what makes CREATE idempotent. Unqualified,
    it reports "already there" for the *other* graph's label row and the insert
    is skipped, so the label never appears in the graph the caller named."""
    (sql,) = _insert_into(f"USE GRAPH '{GRAPH}' CREATE (n:Patient {{node_id: 'x'}})", "rdf_labels")

    guard = sql[sql.index("NOT EXISTS") :]
    assert "graph_id" in guard, f"the idempotency guard is namespace-wide: {guard}"


def test_an_unscoped_create_writes_the_default_graph_and_guards_on_it():
    """No `USE GRAPH` means the default graph, and `graph_id` is
    `NOT NULL DEFAULT ''` after T016 — so leaving the column unnamed *is* the
    default graph, and naming it would change nothing but the shape of every
    existing assertion.

    The guard is the half that has to change. Unqualified it finds a named
    graph's row for the same node ID, reports "already there", and the default
    graph never gets its label. COALESCE both spellings, per ADR-0003 and
    test_translator_default_graph_spelling.py.
    """
    for table in ("rdf_labels", "rdf_props"):
        inserts = _insert_into("CREATE (n:Patient {node_id: 'x', side: 'a'})", table)
        assert inserts, f"no {table} insert was emitted, so this test proves nothing"
        for sql in inserts:
            columns = sql[: sql.index("SELECT")]
            assert "graph_id" not in columns, (
                f"an unscoped create names graph_id in the column list: {sql}"
            )
            guard = sql[sql.index("NOT EXISTS") :]
            assert "COALESCE(graph_id, '') = ''" in guard, (
                f"the default-graph guard is namespace-wide, so a named graph's row "
                f"suppresses the default graph's insert: {guard}"
            )


# --- reads ------------------------------------------------------------------


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:Patient) RETURN n",
        "MATCH (n:Patient) RETURN labels(n)",
        "MATCH (n {side: 'a'}) RETURN n.side",
        "MATCH (a:Patient)-[:seen_by]->(b) RETURN b",
    ],
    ids=["node", "labels", "property", "edge"],
)
def test_every_table_a_scoped_read_touches_carries_the_graph(cypher):
    """One assertion, applied to every table reference in the statement.

    Naming the tables individually would let a new join slip in unscoped, which
    is exactly how this gap got here: `rdf_labels` grew a `graph_id` column in
    T016 and no join learned about it.
    """
    sql = _sql(f"USE GRAPH '{GRAPH}' {cypher}")

    # Each table reference must be followed, somewhere in the statement, by a
    # predicate naming the graph. The cheap proxy for "somewhere" is the count:
    # one graph predicate per table reference, at minimum.
    references = sum(sql.count(t) for t in ("nodes", "rdf_labels", "rdf_props", "rdf_edges"))
    predicates = sql.count(GRAPH)

    assert predicates >= references, (
        f"{references} table references but only {predicates} graph predicates, so at "
        f"least one read is namespace-wide:\n{sql}"
    )


def test_an_unscoped_read_is_left_alone():
    """Without `USE GRAPH` the statement keeps 3.2.0 behaviour. A graph
    predicate added here would silently narrow every existing query to the
    default graph — a much louder break than the one being fixed."""
    sql = _sql("MATCH (n:Patient) RETURN n")

    assert "graph_id" not in sql, f"an unscoped read grew a graph predicate: {sql}"


def test_an_optional_match_keeps_its_null_rows():
    """The graph predicate for a LEFT JOINed table belongs in the `ON` clause.

    In `WHERE`, it is false for the null row the LEFT JOIN exists to produce, so
    the join silently becomes an inner one — `OPTIONAL MATCH` stops being
    optional, and the failure looks like missing data rather than a wrong
    predicate.
    """
    sql = _sql(f"USE GRAPH '{GRAPH}' MATCH (n:Patient) RETURN n.side")

    for line in sql.split("\n"):
        if line.strip().startswith("LEFT JOIN"):
            alias = line.split()[3]
            assert f"{alias}.graph_id" in line, (
                f"the LEFT JOIN of {alias} carries no graph predicate in its ON clause, "
                f"so either it is unscoped or the predicate landed in WHERE: {line}"
            )
            assert f"{alias}.graph_id" not in sql.split("WHERE", 1)[-1], (
                f"{alias} is LEFT JOINed but its graph predicate is in WHERE, which "
                f"discards the null row: {sql}"
            )
