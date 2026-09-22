"""Spec 230 US1 (FR-001) — `USE GRAPH ''` is a graph, not the absence of one.

ADR-0003 spells the default graph `''`. The translator tested `context.graph_context`
for *truth*, so the one graph whose name is falsy took the same path as a query that
named no graph at all: no predicate on any table, and `USE GRAPH '' MATCH (n:Thing)`
answered with every graph's `Thing`. The clause a caller writes to narrow the query
was the one value that widened it.

The distinction the fix has to keep is `''` against `None`:

* `''` is a graph the caller named. Every graph-owned table the statement touches
  gets a predicate, exactly as a named graph does.
* `None` is no `USE GRAPH` clause, and that still means the whole namespace. It is
  the pre-214 default every existing caller relies on, and narrowing it belongs to
  a deprecation, not to a correctness fix.

`COALESCE(graph_id, '')` on both sides throughout, because a row written before the
column acquired its default spells "no graph" as NULL and a bare `= ''` sees only
half of the default graph (`tests/unit/test_graph_id_tightening.py`).

Asserted on the generated SQL: the defect is the predicate's absence, and a live
query cannot tell "no predicate" from "one graph holds everything". The behavioural
half is `tests/e2e/test_230_scope_isolation.py`.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

NAMED = "ivg230:default:named"

#: What a predicate on `alias` looks like once the graph is the default one.
def _pred(alias: str, graph: str = "") -> str:
    return f"COALESCE({alias}graph_id, '') = COALESCE('{graph}', '')"


def _translate(cypher: str):
    return translate_to_sql(parse_query(cypher), {})


def _sql(cypher: str) -> str:
    return _translate(cypher).sql


def _dml(cypher: str) -> list[str]:
    """A write's statements. `SQLQuery.sql` carries the list itself for DML."""
    sql = _translate(cypher).sql
    return list(sql) if isinstance(sql, list) else [sql]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_the_default_graph_scopes_the_anchor_table():
    sql = _sql("USE GRAPH '' MATCH (n:Thing) RETURN n.id AS id")

    assert _pred("n0.") in sql, sql


def test_the_default_graph_scopes_the_label_and_property_joins():
    """In the `ON` clause, not in `WHERE`.

    A property join is a LEFT JOIN so an absent property returns null rather than
    dropping the row; a predicate on it in `WHERE` is false for exactly that null
    row and silently turns the join inner.
    """
    sql = _sql("USE GRAPH '' MATCH (n:Thing) RETURN n.id AS id")

    label_join = next(line for line in sql.splitlines() if "rdf_labels" in line)
    prop_join = next(line for line in sql.splitlines() if "rdf_props" in line)
    assert _pred("l1.") in label_join, sql
    assert _pred("p2.") in prop_join, sql


def test_the_default_graph_scopes_an_edge_traversal():
    sql = _sql("USE GRAPH '' MATCH (a:Thing)-[r:R]->(b:Thing) RETURN a.id AS x")

    assert _pred("e3.") in sql, sql
    assert _pred("n0.") in sql, sql
    assert _pred("n2.") in sql, sql


def test_the_default_graph_scopes_a_vector_subquery():
    """Otherwise `USE GRAPH ''` scores a node against another graph's vector for it.

    Spec 227 re-keyed the embedding tables to `(graph_id, node_id)`, so the scalar
    subquery reading `WHERE node_id = ...` alone can now match a row belonging to a
    different graph — and `VECTOR_COSINE` of the wrong vector is a number, not an
    error.
    """
    sql = _sql("USE GRAPH '' MATCH (n:Thing) RETURN ivg.vector_similarity(n, [1.0,2.0]) AS sc")

    # The predicate has to be inside the scalar subquery, not merely somewhere in the
    # statement — the anchor table carries one too, and a test that only looked for
    # the text would pass with the vector read left unscoped.
    subquery = sql[sql.index("SELECT emb") : sql.index("TO_VECTOR")]
    assert _pred("") in subquery, sql


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def test_the_default_graph_scopes_a_set():
    """`SET` is an UPDATE plus an insert-if-absent, and both halves need the graph.

    Unscoped, the UPDATE writes another graph's property row and the `NOT EXISTS`
    guard finds it, reports the property already present, and the default graph
    never gets one.
    """
    statements = _dml("USE GRAPH '' MATCH (n:Thing) SET n.name = 'x'")

    update = next(s for s in statements if s.startswith("UPDATE"))
    insert = next(s for s in statements if s.startswith("INSERT"))
    assert "graph_id" in update, update
    assert "graph_id" in insert.split("NOT EXISTS")[0], insert
    assert "graph_id" in insert.split("NOT EXISTS")[1], insert


def test_the_default_graph_scopes_a_delete():
    """Every table the delete reaches, including the connected-edge constraint check."""
    statements = _dml("USE GRAPH '' MATCH (n:Thing) DELETE n")

    for statement in statements:
        assert "graph_id" in statement, statement


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


def test_a_named_graph_still_scopes_everything_it_did():
    sql = _sql(f"USE GRAPH '{NAMED}' MATCH (a:Thing)-[r:R]->(b:Thing) RETURN a.id AS x")

    for alias in ("n0.", "n2.", "e3."):
        assert _pred(alias, NAMED) in sql, sql


def test_no_use_graph_clause_still_spans_the_namespace():
    """The boundary of the fix, asserted so it cannot drift by accident.

    No clause is `graph_context is None`, and that is the pre-214 behaviour every
    existing caller compiled against. Making it mean the default graph is a
    breaking change to every query that never knew about graphs, and it belongs to
    a deprecation with its own release note — not to this one.
    """
    sql = _sql("MATCH (n:Thing) RETURN n.id AS id")

    assert "graph_id" not in sql, sql
