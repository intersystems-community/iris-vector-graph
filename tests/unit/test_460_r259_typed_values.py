"""4.6.0 R259 (the cases R248 left), offline: the translations. The live answers, under both driver versions and on
both paths, are in tests/e2e/test_460_r259_typed_values_e2e.py.

- A RETURN of coalesce() over a stored value reads the winner through CY_TYPED, from a CASE (COALESCE CAST every
  argument to VARCHAR: the stored 1.25 came back '1.25'), a number literal in double parentheses (IRIS made a bare
  one a VARCHAR parameter behind an ORDER BY), a boolean literal as its stored spelling. A WHERE keeps the CASTs.
- A CREATE writes a WITH column holding a node property as it is stored (the CAST to VARCHAR stored '5').
- Integer division of statically integer operands truncates toward zero (TRUNCATE(.., 0)); it was FLOOR.
"""

from __future__ import annotations

import re

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(query: str, params=None):
    return translate_to_sql(parse_query(query), dict(params or {}))


def test_a_returned_coalesce_of_a_stored_value_reads_its_type():
    r = _q("MATCH (n:L) RETURN coalesce(n.f, $k) AS c, coalesce(n.g, 2) AS d, coalesce(n.t, false) AS e", {"k": 2})
    head = r.sql.split("\nFROM ")[0]
    assert "CY_TYPED(CASE WHEN " in head and "COALESCE(" not in head.split("AS c")[0], head
    assert "ELSE ((2)) END" in head, head
    assert "ELSE 'false' END" in head, head
    assert {"c", "d", "e"} <= set(r.typed_value_columns)
    assert "e" in r.prop_text_expr_columns  # a stored 'true' reads as the boolean


def test_a_coalesce_in_a_where_keeps_its_casts():
    r = _q("MATCH (n:L) WHERE coalesce(n.f, $k) > $lo RETURN n.name", {"k": 2, "lo": 1.5})
    assert "COALESCE(CAST(" in r.sql, r.sql


def test_a_coalesce_of_no_stored_value_is_left_as_it_was():
    r = _q("WITH 1 AS x RETURN coalesce(x, 2) AS c")
    assert "CY_TYPED(CASE" not in r.sql, r.sql


def test_a_create_writes_a_with_column_of_a_stored_value_uncast():
    r = _q("MATCH (n:QG {k: 1}) SET n.k = 5 WITH n, n.k AS k CREATE (:QH {id: 'h1', v: k})")
    writes = [s for s in r.sql if re.search(r'rdf_props \(s, "key", val\) SELECT \?, \?, ', s)]
    assert any("SELECT ?, ?, Stage1.k" in s for s in writes), writes
    assert not any("CAST(Stage1.k" in s for s in writes), writes


def test_a_create_of_a_computed_with_column_keeps_its_cast():
    r = _q("MATCH (n:QG) WITH n, n.k * 2 AS k CREATE (:QH {id: 'h1', v: k})")
    writes = [s for s in r.sql if re.search(r"INSERT INTO (\w+\.)?rdf_props", s) and "Stage1.k" in s]
    assert writes and all("CAST(Stage1.k" in s for s in writes), writes


def test_static_integer_division_truncates():
    r = _q("RETURN -7 / 2 AS a, 7 / 2 AS b")
    assert "TRUNCATE(-7 / 2, 0) AS a" in r.sql and "TRUNCATE(7 / 2, 0) AS b" in r.sql, r.sql
    assert "FLOOR(" not in r.sql


def test_a_zero_guarded_integer_division_truncates():
    # count() is an integer and the divisor no literal: the NaN guard's division truncates too
    r = _q("MATCH (n) RETURN count(n) / count(n) AS q")
    assert "TRUNCATE(COUNT(" in r.sql and "FLOOR(" not in r.sql, r.sql
