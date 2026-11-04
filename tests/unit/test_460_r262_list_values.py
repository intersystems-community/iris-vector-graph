"""4.6.0 R262, R267 (the list cases), offline: the translations. The live answers, under both driver versions and
on both paths, are in tests/e2e/test_460_r262_list_values_e2e.py.

- `[x IN L | x]` (and `[x IN L]`) is L: through JSON_TABLE each element came back as text, which JSON_ARRAYAGG quoted;
- a parameter holding one list is iterated as the literal it is (R255's rows), not by JSON_TABLE, which flattens it;
- a node property in a list is its JSON number (CY_JSON_NUM: 5.0, not 5) or its stored boolean, a relationship
  property is read by CY_REL_PROP (JSON true, not 1), a float column is CY_JSON_NUM of its double (1.0);
- `x[-1]` of a row list counts from the end with JSON_ARRAYLENGTH: JSON_ARRAYGET of -1 fails with <RANGE>.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(query: str, params=None):
    return translate_to_sql(parse_query(query), dict(params or {}))


def _final(r):
    return r.sql.rsplit("\n)\n", 1)[-1]


def test_an_identity_comprehension_is_its_source():
    r = _q("WITH [{a: 1}, {a: 2}] AS ms RETURN [x IN ms | x] AS l")
    assert "JSON_TABLE" not in _final(r) and "Stage1.ms AS l" in _final(r), r.sql
    assert "JSON_TABLE" not in _q("RETURN [x IN [true] | x] AS l").sql


def test_a_filtering_comprehension_still_iterates():
    assert "JSON_TABLE" in _q("WITH [1, 2] AS l RETURN [x IN l WHERE x > 1 | x] AS m").sql


def test_unwind_of_a_parameter_holding_one_list_is_one_row():
    r = _q("UNWIND $p AS x RETURN x", {"p": [[1, 2]]})
    assert "JSON_TABLE" not in r.sql and r.parameters == [["[1,2]"]], (r.sql, r.parameters)


def test_a_comprehension_over_a_parameter_holding_one_list_is_one_element():
    r = _q("RETURN [y IN $p | size(y)] AS s", {"p": [[1, 2]]})
    assert "JSON_TABLE" not in r.sql and "'[1,2]'" in r.sql, r.sql
    assert sum(len(p) for p in r.parameters) == r.sql.count("?"), (r.sql, r.parameters)


def test_a_parameter_holding_two_lists_iterates_as_before():
    assert "JSON_TABLE" in _q("UNWIND $q AS x RETURN x", {"q": [[1, 2], [3]]}).sql


def test_a_node_property_in_a_list_is_its_json_value():
    r = _q("MATCH (n:L) RETURN [n.g] AS l, collect(n.g) AS c")
    assert r.sql.count("CY_JSON_NUM(") >= 2, r.sql
    assert "LIST(" in r.sql, r.sql


def test_a_relationship_property_in_a_list_reads_through_cy_rel_prop():
    r = _q("MATCH ()-[r:R]->() RETURN [r.flag] AS l")
    head = r.sql.split("\nFROM ")[0]
    assert "CY_REL_PROP(" in head.split(" AS l")[0] and "JSON_VALUE(" not in head, head


def test_a_float_column_in_a_list_is_spelled_as_a_float():
    r = _q("WITH 1.0 AS f RETURN [f] AS l")
    assert "CY_JSON_NUM(CAST(Stage1.f AS DOUBLE))" in r.sql, r.sql


def test_a_negative_index_counts_from_the_end():
    r = _q("UNWIND [1, 2, 3] AS v WITH collect(v) AS x WITH x[-1] AS y RETURN y")
    # a WITH column of the element reads CY_LIST_VAL, which counts a negative index from the end (4.6.0 R273)
    assert "CY_LIST_VAL(Stage1.x, -1) AS y" in r.sql, r.sql
    assert "JSON_ARRAYGET(Stage1.x, -1)" not in r.sql
    r = _q("UNWIND [1, 2, 3] AS v WITH collect(v) AS x WITH x[-1] = 3 AS y RETURN y")
    assert "JSON_ARRAYLENGTH(Stage1.x) >= 1 THEN" in r.sql and "JSON_ARRAYLENGTH(Stage1.x) - 1" in r.sql, r.sql
