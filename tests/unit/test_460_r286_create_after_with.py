"""4.6.0 R286 (the CREATE item), pinned against the plan and the translation (no container).

The answers are proven live in tests/e2e/test_460_r286_create_after_with_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.merge_rows import plan_row_create
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _plan(q: str):
    return plan_row_create(parse_query(q), {})


def test_a_with_carrying_a_node_read_through_its_properties_runs_per_row():
    plan = _plan("MATCH (n:K) WITH n CREATE (:H {v: n.k, w: n.k * 2})")
    assert plan is not None
    assert plan.prop_refs == {"__rc0": ("n", "k")}
    assert plan.typed_cols == frozenset({"__rc0"})
    items = plan.prefix.return_clause.items
    assert [(i.alias, i.expression) for i in items] == [("__rc0", ast.PropertyReference(variable="n", property_name="k"))]


def test_a_row_binds_the_property_as_read():
    plan = _plan("MATCH (n:K) WITH n CREATE (:H {v: n.k, w: n.k * 2})")
    node = plan.bind({"__rc0": 7}).query_parts[0].clauses[0].patterns[0].nodes[0]
    assert node.properties == {"v": ast.Literal(value=7), "w": ast.Literal(value=14)}
    # a row whose arithmetic cannot be folded ('7' * 2) is not bound: the plan is not run (the static statements are)
    assert plan.bind({"__rc0": "7"}) is None
    plan = _plan("MATCH (n:K) WITH n CREATE (:H {v: n.k})")
    node = plan.bind({"__rc0": "7"}).query_parts[0].clauses[0].patterns[0].nodes[0]
    assert node.properties["v"] == ast.Literal(value="7")
    node = plan.bind({"__rc0": ["a", "b"]}).query_parts[0].clauses[0].patterns[0].nodes[0]
    assert node.properties["v"] == ast.Literal(value=["a", "b"])
    assert plan.bind({"__rc0": [["a"]]}) is None


def test_values_and_properties_together():
    plan = _plan("MATCH (n:K) WITH n, n.x AS x CREATE (:H {v: n.k, x: x})")
    assert plan.row_vars == ["x", "__rc0"]
    # x is a node property's column, bound as read too (4.6.0 R292)
    assert plan.typed_cols == frozenset({"__rc0", "x"})


@pytest.mark.parametrize("q", [
    # a node that is a pattern node, or that the RETURN reads, is carried by its id since 4.6.0 R292
    # (tests/unit/test_460_r292_create_rows.py)
    "MATCH (n:K) WITH n CREATE (:H {v: n})",  # the node itself as a value
])
def test_a_node_used_otherwise_keeps_the_static_statements(q):
    assert _plan(q) is None


def test_a_copied_property_without_a_with_is_written_as_stored():
    t = translate_to_sql(parse_query("MATCH (n:K) CREATE (:H {v: n.k, w: n.k * 2})"), {})
    copy, arith = [s for s in t.sql if " AS _v" in s]
    assert ", ((SELECT val FROM Graph_KG.rdf_props WHERE s = n0.node_id AND \"key\" = 'k')) AS _v" in copy
    # arithmetic is CY_ARITH's number, the row's type (4.6.0 R292; it was a CAST to VARCHAR, stored as text)
    assert "Graph_KG.CY_ARITH((SELECT val" in arith and "VARCHAR(4000)) AS _v" not in arith
