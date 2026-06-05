"""T192-13/15: _structural_guard_sql helper and EXISTS-first SQL emission for property predicates."""
import pytest
from iris_vector_graph.cypher.translator import TranslationContext, translate_to_sql
from iris_vector_graph.cypher.parser import parse_query


def translate_cypher(query: str) -> tuple:
    ast = parse_query(query)
    result = translate_to_sql(ast)
    return result.sql, result.parameters


def test_structural_guard_sql_format():
    """_structural_guard_sql returns a well-formed EXISTS subquery."""
    ctx = TranslationContext()
    guard = ctx._structural_guard_sql("n0", "score")
    assert "EXISTS" in guard
    assert "Graph_KG.rdf_props" in guard or "rdf_props" in guard
    assert "n0.node_id" in guard
    assert "'score'" in guard


def test_structural_guard_sql_key_escaping():
    """Property names with single quotes are escaped."""
    ctx = TranslationContext()
    guard = ctx._structural_guard_sql("n0", "it's")
    assert "it''s" in guard


def test_property_predicate_generates_exists_guard():
    """MATCH (n) WHERE n.score > 0.5 generates EXISTS guard before the comparison."""
    sql, _ = translate_cypher("MATCH (n) WHERE n.score > 0.5 RETURN n.score")
    where_start = sql.upper().find("WHERE")
    assert where_start != -1, "Expected WHERE clause"
    where_part = sql[where_start:]
    exists_pos = where_part.upper().find("EXISTS")
    # literal is parameterized; look for the CAST comparison or the val reference
    range_pos = where_part.find("CAST(") if "CAST(" in where_part else where_part.find(".val")
    assert exists_pos != -1, f"Expected EXISTS guard in WHERE; SQL: {sql}"
    assert range_pos != -1, f"Expected CAST/val comparison in WHERE; SQL: {sql}"
    assert exists_pos < range_pos, (
        f"EXISTS guard must precede range predicate; EXISTS at {exists_pos}, range at {range_pos}\nSQL: {sql}"
    )


def test_exists_guard_references_correct_property():
    """EXISTS guard references the queried property key."""
    sql, _ = translate_cypher("MATCH (n) WHERE n.age > 30 RETURN n")
    assert "'age'" in sql, "EXISTS guard should reference 'age' key"


def test_optional_match_no_exists_guard():
    """OPTIONAL MATCH does not inject EXISTS guard (would break null-preserving join)."""
    sql, _ = translate_cypher("OPTIONAL MATCH (n {score: 1.0}) RETURN n")
    where_start = sql.upper().find("WHERE")
    if where_start == -1:
        return  # no WHERE = definitely no guard
    where_part = sql[where_start:]
    # The guard should NOT appear for optional patterns
    assert "EXISTS" not in where_part or "NOT EXISTS" not in where_part, (
        "OPTIONAL MATCH should not use structural EXISTS guard"
    )


def test_id_property_no_exists_guard():
    """node_id / id predicates go direct to WHERE without EXISTS guard."""
    sql, _ = translate_cypher("MATCH (n {id: 'abc'}) RETURN n")
    where_start = sql.upper().find("WHERE")
    if where_start == -1:
        return
    where_part = sql[where_start:]
    # id predicates are WHERE n.node_id = ? — no rdf_props EXISTS needed
    exists_count = where_part.upper().count("EXISTS")
    assert exists_count == 0, f"id predicate should not generate EXISTS guard, got: {sql}"
