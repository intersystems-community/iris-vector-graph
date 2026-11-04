"""4.6.0 R253: a value keeps its type inside a list, translated (fixes29). The answers are pinned live, on 2026.1 and
2026.3 with the native planner on and off, in tests/e2e/test_460_r253_list_value_types_e2e.py; this checks the SQL.

* A relationship property is read out of the qualifiers JSON as text (IVG's JSON_VALUE), and a list built with
  JSON_ARRAY / JSON_ARRAYAGG quoted it: ``[r.km]`` answered ``["1"]`` where ``r.km`` answers 1. Inside a list it is
  now JSON text typed the way the scalar read types it (a number's canonical spelling is the number).
* JSON_ARRAYAGG quotes a JSON object it is given, so ``collect(n {.name})`` listed strings. A list holding map
  projections is concatenated from JSON texts (LIST for an aggregate), so each element is the map.
* A map projection's value was put between quotes unescaped, so a name holding a quote broke the JSON, and every value
  was a string. It is JSON_ARRAY's text of the value: escaped, and typed by what the row holds.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _select(cypher: str) -> str:
    return translate_to_sql(parse_query(cypher)).sql.split("\nFROM ", 1)[0]


def test_a_relationship_property_in_a_list_literal_is_typed_json():
    sql = _select("MATCH (a)-[r:ROAD]->(b) RETURN [r.km] AS v")
    assert sql.startswith("SELECT ('[' || CASE WHEN "), sql
    assert "REGEX_MATCH(" in sql, sql


def test_a_comprehension_over_a_relationship_property_list_reads_typed_elements():
    sql = _select("MATCH (a)-[r:ROAD]->(b) RETURN [x IN [r.km] | x] AS v")
    assert "REGEX_MATCH(" in sql, sql


def test_collected_relationship_properties_are_typed_json():
    sql = _select("MATCH (a)-[r:ROAD]->(b) RETURN collect(r.km) AS v")
    assert "LIST(" in sql and "JSON_ARRAYAGG(" not in sql, sql


def test_collected_map_projections_are_concatenated_not_quoted():
    sql = _select("MATCH (n:L) RETURN collect(n {.name}) AS v")
    assert "LIST(" in sql and "JSON_ARRAYAGG(" not in sql, sql


def test_collect_distinct_of_map_projections_stays_distinct():
    sql = _select("MATCH (n:L) RETURN collect(DISTINCT n {.name}) AS v")
    assert "LIST(DISTINCT " in sql, sql


def test_a_map_projection_value_is_escaped_json():
    sql = _select("MATCH (n:L) RETURN n {.name} AS m")
    assert "JSON_ARRAY(" in sql, sql
    assert "'\"'||p" not in sql.replace(" ", ""), sql


def test_a_node_property_list_is_unchanged():
    # a node property holds its stored type in the row, which JSON_ARRAYAGG keeps
    sql = _select("MATCH (n:L) RETURN collect(n.k) AS v")
    assert "JSON_ARRAYAGG(" in sql and "LIST(" not in sql, sql
