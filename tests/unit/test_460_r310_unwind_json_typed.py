"""4.6.0 R310: an element of a list a row holds, unwound, reads as its JSON type under either driver (fixes64).

`WITH [3, 1, 2] AS l UNWIND l AS i RETURN i` read JSON_TABLE's VARCHAR column: intersystems-irispython 5.4.0 answered
the IRIS numbers it holds (3, and Decimal('1.5') for 1.5), 5.4.1 their text ('3', '1.5'); `i + 0` and a WITH of `i`
the same. Measured by hand: JSON_TABLE hands an ObjectScript function a JSON integer as $LIST type 4/5 and a number
with a fraction or an exponent (1.0, 1.5, 1e20) as type 6, so CY_JSON_TYPED marks the first an integer and the second
a float, as the JSON spelled them; CY_TYPED cannot (a node value's integral decimal reads as an integer, R218). Pinned
live, both drivers, in tests/e2e/test_460_r310_unwind_json_typed_e2e.py. A boolean element is IRIS's 1 / 0 in that
column and is not told from the string '1' here (KNOWN_ISSUES).
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _t(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params)


def test_an_unwound_with_list_element_reads_through_cy_json_typed():
    t = _t("WITH [3, 1, 2] AS l UNWIND l AS i RETURN i")
    assert "Graph_KG.CY_JSON_TYPED(u0.i) AS i" in t.sql, t.sql
    assert "i" in t.typed_value_columns


def test_an_unwound_collected_list_element_reads_through_cy_json_typed():
    t = _t("UNWIND [3, 1, 2] AS v WITH collect(v) AS l UNWIND l AS i RETURN i")
    # 4.7.0 rows 21, 22: the row as its own JSON text (CY_LIST_KINDS' "j"), marked for the engine to read as JSON
    assert "CHAR(1) || 'J' || u1.i__j AS i" in t.sql, t.sql


def test_arithmetic_over_an_unwound_element_reads_through_cy_json_typed():
    t = _t("WITH [3, 1.5] AS l UNWIND l AS x RETURN x + 1 AS y")
    assert "Graph_KG.CY_JSON_TYPED(CASE " in t.sql and "y" in t.typed_value_columns, t.sql


def test_an_unwound_element_carried_by_a_with_reads_through_cy_json_typed():
    t = _t("WITH [3, 1.5] AS l UNWIND l AS x WITH x RETURN x")
    assert "Graph_KG.CY_JSON_TYPED(Stage2.x) AS x" in t.sql, t.sql


def test_a_renamed_with_column_of_an_unwound_element_is_typed_too():
    t = _t("WITH [3, 1.5] AS l UNWIND l AS x WITH x AS y RETURN y")
    assert "Graph_KG.CY_JSON_TYPED(Stage2.y) AS y" in t.sql, t.sql


def test_a_literal_unwind_keeps_its_bound_values():
    t = _t("UNWIND [3, 1, 2] AS i RETURN i")
    assert "CY_JSON_TYPED" not in t.sql, t.sql


def test_a_range_keeps_its_integer_column():
    t = _t("WITH 3 AS n UNWIND range(1, n) AS i RETURN i")
    assert "CY_JSON_TYPED" not in t.sql, t.sql


def test_an_unwound_collected_node_is_read_as_a_node():
    t = _t("MATCH (n:L) WITH collect(n) AS ns UNWIND ns AS x RETURN x.name AS name")
    assert "CY_JSON_TYPED" not in t.sql, t.sql


def test_a_rebound_name_is_not_typed():
    t = _t("WITH [3] AS l UNWIND l AS x WITH 'a' AS x RETURN x")
    assert "CY_JSON_TYPED" not in t.sql, t.sql


def test_a_sort_key_reads_the_value_not_its_mark():
    """ORDER BY of the typed column sorted CY_JSON_TYPED's marked text: 1.5 and 3 before 'a' (TCK ReturnOrderBy1)."""
    sql = _t("WITH [3, 'a', 1.5] AS l UNWIND l AS x RETURN x ORDER BY x").sql
    assert "CY_SORT_KEY(u0.x," in sql and "CY_SORT_KEY(Graph_KG.CY_JSON_TYPED" not in sql, sql


def test_a_mixed_graph_list_keeps_its_own_reading():
    t = _t("MATCH p = (n:N)-[r:REL]->() UNWIND [n, r, p, 1.5, 'text'] AS v RETURN v")
    assert "CY_JSON_TYPED" not in t.sql, t.sql
