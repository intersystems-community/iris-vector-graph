"""4.6.0 R292 (the CREATE items), pinned against the plan and the translation (no container).

The answers are proven live in tests/e2e/test_460_r292_create_rows_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.merge_rows import plan_row_create
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _plan(q: str):
    return plan_row_create(parse_query(q), {})


# ── arithmetic in the per-row statements (R189) ──────────────────────────────


@pytest.mark.parametrize("op", ["*", "+", "-", "/"])
def test_arithmetic_over_a_property_is_written_as_the_row_s_number(op):
    t = translate_to_sql(parse_query(f"MATCH (n:K) CREATE (:H {{v: n.k {op} 2}})"), {})
    (stmt,) = [s for s in t.sql if " AS _v" in s]
    assert "Graph_KG.CY_ARITH((SELECT val FROM Graph_KG.rdf_props WHERE s = n0.node_id" in stmt, stmt
    # - * / under COALESCE, so the error CY_ARITH raises keeps its message (R297); + raises none
    assert (f"'{op}', 2)) AS _v" if op == "+" else f"'{op}', 2), NULL)) AS _v") in stmt, stmt
    assert "AS VARCHAR(4000)) AS _v" not in stmt


def test_other_computed_values_keep_the_cast():
    t = translate_to_sql(parse_query("MATCH (n:K) CREATE (:H {v: n.k + 'a', w: n.k % 2})"), {})
    stmts = [s for s in t.sql if " AS _v" in s]
    assert len(stmts) == 2 and all("AS VARCHAR(4000)) AS _v" in s for s in stmts), stmts


# ── a WITH column of a node property (R120) ──────────────────────────────────


@pytest.mark.parametrize("q,col", [
    ("MATCH (n:K) WITH n.k AS k CREATE (:H {v: k})", "k"),
    ("MATCH (n:K) WITH n.k AS k WITH k AS j CREATE (:H {v: j})", "j"),
    ("MATCH (n:K) WITH n.k AS k, n.k * 2 AS d CREATE (:H {v: k, d: d})", "k"),
])
def test_a_property_column_binds_as_the_prefix_read_it(q, col):
    plan = _plan(q)
    assert plan is not None
    assert col in plan.typed_cols


@pytest.mark.parametrize("q,col", [
    ("UNWIND range(1, 3) AS i WITH i CREATE (:H {v: i})", "i"),  # read as text under 5.4.1: re-typed
    ("MATCH (n:K) WITH n.k * 2 AS d CREATE (:H {v: d})", "d"),
    ("MATCH (n:K) WITH n.k AS k WITH k + 1 AS j CREATE (:H {v: j})", "j"),
])
def test_other_columns_are_re_typed(q, col):
    plan = _plan(q)
    assert plan is not None and col in plan.row_vars
    assert col not in plan.typed_cols


# ── a WITH carrying the node a CREATE links from ─────────────────────────────


def test_a_carried_node_is_matched_again_by_its_id_once_per_row():
    plan = _plan("MATCH (n:K) WITH n CREATE (n)-[:R]->(:H {v: n.k})")
    assert plan is not None
    (col,) = plan.node_cols
    assert plan.node_cols[col] == "n"
    assert col in plan.row_vars and col in plan.typed_cols
    items = plan.prefix.return_clause.items
    assert [(i.alias, i.expression) for i in items] == [
        (col, ast.PropertyReference(variable="n", property_name="node_id"))
    ]
    q = plan.bind({col: "id-1"})
    first, create = q.query_parts[0].clauses
    assert isinstance(first, ast.MatchClause) and not first.optional
    node = first.patterns[0].nodes[0]
    assert node.variable == "n" and node.properties == {"node_id": ast.Literal(value="id-1")}
    assert isinstance(create, ast.CreateClause)
    # the CREATE reads the matched node as a MATCH ... CREATE does
    assert create.patterns[0].nodes[1].properties["v"] == ast.PropertyReference(variable="n", property_name="k")


def test_a_null_node_keeps_the_static_statements():
    plan = _plan("MATCH (n:K) WITH n CREATE (n)-[:R]->(:H)")
    (col,) = plan.node_cols
    assert plan.bind({col: None}) is None


def test_values_and_two_nodes():
    plan = _plan("MATCH (a:K), (b:K) WITH a, b, a.k AS k CREATE (a)-[:R {w: k}]->(b) RETURN a.k, b.k")
    assert plan is not None
    assert sorted(plan.node_cols.values()) == ["a", "b"]
    assert "k" in plan.row_vars and "k" in plan.typed_cols
    row = {c: f"id-{v}" for c, v in plan.node_cols.items()}
    row["k"] = 3
    q = plan.bind(row)
    match = q.query_parts[0].clauses[0]
    assert sorted(p.nodes[0].variable for p in match.patterns) == ["a", "b"]
    rel = q.query_parts[0].clauses[1].patterns[0].relationships[0]
    assert rel.properties == {"w": ast.Literal(value=3)}


@pytest.mark.parametrize("q", [
    "MATCH (a)-[r:R]->(b) WITH r, b CREATE (b)-[:S]->(:H {w: type(r)})",  # a relationship the WITH carries
    "MATCH p = (a)-[:R]->() WITH p CREATE (:H {v: length(p)})",  # a path
    "MATCH (n:K) WITH n CREATE (n)-[:R]->(:H) RETURN count(*)",  # an aggregating RETURN
])
def test_other_carried_values_keep_the_static_statements(q):
    assert _plan(q) is None


def test_a_return_of_the_carried_node_is_carried_by_its_id():
    plan = _plan("MATCH (n:K) WITH n CREATE (h:H {v: n.k}) RETURN n, h.v")
    assert plan is not None and list(plan.node_cols.values()) == ["n"]
