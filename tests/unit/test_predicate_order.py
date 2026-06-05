"""T192-05: Predicate reordering — EXISTS before range in generated SQL."""
import pytest
from iris_vector_graph.cypher.translator import TranslationContext


def test_predicate_cost_ordering():
    ctx = TranslationContext()
    ctx.where_conditions = [
        "n0.age > 30",
        "EXISTS (SELECT 1 FROM Graph_KG.nodes ep WHERE ep.node_id = n0.node_id)",
        "n0.name = 'Alice'",
        "LOWER(n0.bio) LIKE '%engineer%'",
    ]
    ordered = sorted(ctx.where_conditions, key=ctx._predicate_cost)
    assert ordered[0].startswith("EXISTS"), "EXISTS guard must be first"
    exists_idx = next(i for i, c in enumerate(ordered) if "EXISTS" in c)
    range_idx = next(i for i, c in enumerate(ordered) if ">" in c)
    like_idx = next(i for i, c in enumerate(ordered) if "LIKE" in c)
    assert exists_idx < range_idx, "EXISTS must precede range predicate"
    assert range_idx < like_idx, "range must precede LIKE"


def test_build_stage_sql_where_order():
    """build_stage_sql emits WHERE conditions in cost order."""
    ctx = TranslationContext()
    ctx.select_items = ["n0.node_id"]
    ctx.from_clauses = ["Graph_KG.nodes n0"]
    ctx.where_conditions = [
        "n0.score > 0.5",
        "EXISTS (SELECT 1 FROM Graph_KG.nodes ep WHERE ep.node_id = n0.node_id)",
        "n0.label = 'Person'",
    ]
    sql, _ = ctx.build_stage_sql()
    where_part = sql[sql.index("WHERE"):]
    exists_pos = where_part.index("EXISTS")
    range_pos = where_part.index("> 0.5")
    eq_pos = where_part.index("= 'Person'")
    assert exists_pos < eq_pos < range_pos, (
        f"Expected EXISTS < equality < range, got positions {exists_pos} {eq_pos} {range_pos}"
    )


def test_single_condition_unchanged():
    ctx = TranslationContext()
    ctx.select_items = ["n0.node_id"]
    ctx.from_clauses = ["Graph_KG.nodes n0"]
    ctx.where_conditions = ["n0.age > 10"]
    sql, _ = ctx.build_stage_sql()
    assert "n0.age > 10" in sql
