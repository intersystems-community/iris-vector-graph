"""4.6.0 R256: comprehensions whose projection is not a property read, translated (fixes29). The answers are pinned
live, on 2026.1 and 2026.3 with the native planner on and off, in
tests/e2e/test_460_r256_comprehension_projections_e2e.py.

* ``[(a)-->(c) | c {.name}]`` joined c's property row outside the comprehension (SQLCODE -23): it reads it in place.
* ``[x IN ns | id(x)]`` over a collected node list read ``lcN.node_id`` (-29): it reads the element's ``$._id``.
* Two comprehensions over one column in a SELECT failed on 2026.3 (<SUBSCRIPT>) whatever their projection; R228
  wrapped property projections only. The second and later one over the same column now has a no-op REPLACE around
  it; a lone one stays bare (around a variable projection deep in WITH stages a wrapper broke 2026.1, Quantifier10).
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

NS = "MATCH (n:L) WITH collect(n) AS ns "


def _select(cypher: str) -> str:
    sql = translate_to_sql(parse_query(cypher)).sql
    return sql.rsplit("\nSELECT ", 1)[-1].split("\nFROM ", 1)[0]


def test_a_pattern_comprehension_map_projection_reads_in_place():
    sql = translate_to_sql(parse_query("MATCH (a:L) RETURN [(a)-->(c) | c {.name}] AS v")).sql
    assert "LEFT JOIN" not in sql, sql
    assert "WHERE s = pct" in sql and "LIST(" in sql, sql


def test_id_of_a_collected_node_reads_its_json_id():
    sql = _select(NS + "RETURN [x IN ns | id(x)] AS v")
    assert ".node_id" not in sql and "'$._id'" in sql, sql


def test_the_second_comprehension_over_one_column_is_wrapped():
    sql = _select(NS + "RETURN [x IN ns | id(x)] AS a, [x IN ns | x {.name}] AS b")
    first, second = sql.split(" AS a, ", 1)
    assert not first.startswith("REPLACE(") and second.startswith("REPLACE("), sql


def test_a_lone_comprehension_is_not_wrapped():
    assert not _select(NS + "RETURN [x IN ns | id(x)] AS a").startswith("REPLACE(")
