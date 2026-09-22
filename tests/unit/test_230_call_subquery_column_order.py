"""`RETURN` column order is not preserved across a `CALL { ... }` subquery.

Pins a known deviation rather than a guarantee (see `docs/KNOWN_ISSUES.md`, "Cypher
expression semantics"). A correlated `CALL { ... }` whose body returns one aggregate is
translated into a scalar subquery appended to `context.select_items` at
`translator.py:6050` — which happens while the `CALL` clause is being translated, before
the outer `RETURN` clause is looked at. The subquery's column therefore always comes
first, whatever position it holds in the `RETURN` list.

Values and column names are right; only the order differs, so a caller that maps
`columns` to `rows` (what `execute_cypher` returns) reads the correct answer. A caller
that indexes rows positionally against the `RETURN` text does not.

If the projection is ever reordered to follow `RETURN`, this file should fail — that is
the point. Update it deliberately and drop the KNOWN_ISSUES entry with it.
"""

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

CORRELATED_DEGREE = (
    "MATCH (p:Protein) "
    "CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(r) RETURN count(r) AS deg } "
    "RETURN p.node_id, deg"
)


def _select_list(sql):
    """The SELECT list of the outermost statement, as one string.

    The scalar subquery contains its own `FROM`, so the split is on the *last*
    top-level `FROM`, found by tracking parenthesis depth.
    """
    depth = 0
    for i, ch in enumerate(sql):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and sql[i : i + 6].upper() == "\nFROM ":
            return sql[len("SELECT ") : i]
    raise AssertionError(f"no top-level FROM in:\n{sql}")


def test_the_subquery_column_is_projected_before_the_outer_expression():
    result = translate_to_sql(parse_query(CORRELATED_DEGREE), {})
    select_list = _select_list(result.sql)

    deg_at = select_list.index(" AS deg")
    node_at = select_list.index(" AS p_node_id")
    assert deg_at < node_at, (
        "RETURN names p.node_id first; the CALL aggregate is still projected first "
        f"(documented deviation):\n{select_list}"
    )


def test_both_columns_are_present_and_named_from_the_return_clause():
    """Order aside, nothing is dropped or renamed — that is why this is a deviation."""
    result = translate_to_sql(parse_query(CORRELATED_DEGREE), {})
    assert " AS deg" in result.sql
    assert " AS p_node_id" in result.sql
    assert result.column_name_map.get("p_node_id") == "p.node_id", result.column_name_map


def test_the_scalar_subquery_carries_its_own_predicate_parameter():
    """The relationship type is bound, not inlined — it is the first parameter because
    the subquery it belongs to is the first thing in the SELECT list."""
    result = translate_to_sql(parse_query(CORRELATED_DEGREE), {})
    assert result.parameters[0][:2] == ["INTERACTS_WITH", "Protein"], result.parameters
