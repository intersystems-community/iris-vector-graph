"""A `$name` is a parameter even when a pattern variable has the same name.

opsreview (4.0.0 to 4.2.3): ``MATCH (b:Violation) WHERE b.id = $b`` with ``{"b": "nope"}`` returned the node; the
SQL ended ``p2.val = n0.node_id``, i.e. ``$b`` had been read as the variable ``b``. The parser emitted the same
``Variable`` for ``b`` and ``$b``, and the translator treats a name as a parameter only when no variable has it.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def sql_and_params(query, params):
    out = translate_to_sql(parse_query(query), params)
    text = " ".join(out.sql) if isinstance(out.sql, list) else out.sql
    return " ".join(text.split()), out.parameters


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (b:Violation) WHERE b.id = $b RETURN count(b)",
        "MATCH (b:Violation {id: $b}) RETURN count(b)",
    ],
)
def test_a_parameter_with_a_variables_name_is_compared_by_value(query):
    sql, params = sql_and_params(query, {"b": "nope"})

    assert "p2.val = n0.node_id" not in sql
    assert "nope" in repr(params)


def test_a_parameter_with_another_name_is_unchanged():
    sql, params = sql_and_params("MATCH (b:Violation) WHERE b.id = $x RETURN count(b)", {"x": "nope"})

    assert "p2.val = n0.node_id" not in sql
    assert "nope" in repr(params)


def test_the_same_query_parses_to_the_same_tree_every_time():
    """The rename must not mutate a parse result another caller may hold."""
    q = "MATCH (b:Violation) WHERE b.id = $b RETURN count(b)"
    tree = parse_query(q)
    translate_to_sql(tree, {"b": "one"})
    first = translate_to_sql(tree, {"b": "two"})
    assert "two" in repr(first.parameters) and "one" not in repr(first.parameters)


def test_a_variable_and_a_parameter_of_one_name_in_a_merge_pattern():
    """`MATCH (v) MATCH (s {id: $s}) MERGE (v)-[:R]->(s)` filtered nothing (and once failed with -23)."""
    sql, params = sql_and_params(
        "MATCH (v:Violation {id: 'viol:1'}) MATCH (s {id: $s}) MERGE (v)-[:R]->(s) RETURN v.id",
        {"s": "viol:1"},
    )

    assert "p" in sql  # translated
    assert "viol:1" in repr(params)


def test_the_value_is_not_left_under_the_variables_name():
    """The translator reads input_params[<variable>] as a value supplied for that variable's node."""
    from iris_vector_graph.cypher.translator import _isolate_colliding_parameters

    tree = parse_query("MATCH (b:Violation) WHERE b.id = $b RETURN count(b)")
    _, values = _isolate_colliding_parameters(tree, {"b": "nope", "other": 1})

    assert values == {"__param_b": "nope", "other": 1}


def test_the_clash_analysis_is_remembered_on_a_cached_tree_and_not_wrong_for_the_next_call():
    """parse_query is cached, so the same tree is translated again and again: the second call must not re-walk it, and a
    clash found once must still be isolated on every later call."""
    from iris_vector_graph.cypher import translator as tr
    from iris_vector_graph.cypher.parser import parse_query

    tree = parse_query("MATCH (b {id: $b}) RETURN b.id AS x")
    first, values1 = tr._isolate_colliding_parameters(tree, {"b": 7})
    assert tree.param_clash == frozenset({"b"})
    second, values2 = tr._isolate_colliding_parameters(tree, {"b": 8})
    assert values1 == {"__param_b": 7} and values2 == {"__param_b": 8}
    assert first is not tree and second is not tree

    clean = parse_query("MATCH (n {id: $x}) RETURN n.id AS x2")
    same, values = tr._isolate_colliding_parameters(clean, {"x": 1})
    assert clean.param_clash == frozenset()
    assert same is clean and values == {"x": 1}
