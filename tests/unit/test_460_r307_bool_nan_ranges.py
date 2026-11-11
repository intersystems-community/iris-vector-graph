"""4.6.0 R307: a range comparison of a property with a boolean orders booleans; with NaN it is false.

`WHERE n.p >= true` cast the stored 'true' to DOUBLE (0) and the parameter to 1, so a stored true was not >= true;
openCypher orders false < true. `WHERE n.p >= $v` with v NaN held for a stored NaN (IRIS orders NaN); a comparison
with NaN is false in openCypher. Found by tests/e2e/test_460_r307_value_matrix_e2e.py (ge_param, gt_param).
"""

from __future__ import annotations

import math

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q,params", [
    ("MATCH (n:L) WHERE n.p >= $v RETURN n.id", {"v": True}),
    ("MATCH (n:L) WHERE n.p < false RETURN n.id", {}),
])
def test_a_boolean_bound_compares_by_cy_cmp(q, params):
    sq = translate_to_sql(parse_query(q), params)
    assert "CY_CMP(" in sq.sql and "AS DOUBLE)" not in sq.sql, sq.sql
    assert "'true')" in sq.sql or "'false')" in sq.sql, sq.sql


@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_nan_orders_nothing(op):
    sq = translate_to_sql(parse_query(f"MATCH (n:L) WHERE n.p {op} $v RETURN n.id"), {"v": math.nan})
    assert "(1=0)" in sq.sql, sq.sql


def test_a_number_bound_still_casts():
    sq = translate_to_sql(parse_query("MATCH (n:L) WHERE n.p >= $v RETURN n.id"), {"v": 0.5})
    # the bound value meets the row's number (row 24's typed CASE, `... AS DOUBLE) END) >= ?`); its plain copy for
    # the plan inlines the number, as it may not repeat the marker (4.7.0 gate: "Incorrect number of parameters")
    assert "AS DOUBLE) END) >= ?" in sq.sql, sq.sql
    assert sq.sql.count("?") == len(sq.parameters[0]), sq.sql


@pytest.mark.parametrize("v,texts,double", [
    (math.inf, ("'Infinity'", "'inf'"), "CAST('Infinity' AS DOUBLE)"),
    (-math.inf, ("'-Infinity'", "'-inf'"), "CAST('-Infinity' AS DOUBLE)"),
])
def test_an_infinite_number_filter_matches_its_text(v, texts, double):
    """4.6.0 R307: `n.p = $v` with v infinite missed the API's stored text 'inf' (str(inf)); it is compared with every
    spelling, as a finite number is (R168), and with the double."""
    sql = translate_to_sql(parse_query("MATCH (n:L) WHERE n.p = $v RETURN n.id"), {"v": v}).sql
    assert all(t in sql for t in texts) and double in sql, sql
