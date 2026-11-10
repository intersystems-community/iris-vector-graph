"""4.7.0 row 35b: the SQL toBoolean reads a boolean and the strings 'true' and 'false' only, naming its argument once.

The answers are in tests/e2e/test_470_to_boolean_e2e.py.
"""

from __future__ import annotations

from iris_vector_graph.cypher import translator


def test_to_boolean_reads_true_and_false_only():
    sql = translator._scalar_type_conversion("toboolean", ["x"], None)
    assert "'yes'" not in sql and "'1'" not in sql and "'y'" not in sql
    assert "WHEN 'true' THEN 1 WHEN 'false' THEN 0" in sql and sql.count("(x)") == 1  # the argument named once
    store_sql = translator._SET_CONVERSIONS["toboolean"][0]("x")
    assert "'yes'" not in store_sql and "'1'" not in store_sql
