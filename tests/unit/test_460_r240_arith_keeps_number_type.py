"""4.6.0 R240: arithmetic over a stored number keeps the number's int/float type.

`RETURN n.g * 2` was cast to DOUBLE and decoded as an INTEGER (a property in arithmetic defaulted to 'int'), so
`n.f * 3` with f = 1.5 answered 4 and `toString(n.g * 2)` with g = 2.0 answered '4'. A RETURN's arithmetic over a
stored value (and a WITH's, where its column is already text) is CY_ARITH(a, op, b) now, which answers a $DOUBLE when
an operand is a float and an integer otherwise; its column is not decoded as an integer, and toString() spells it with
CY_TOSTRING. Where the arithmetic is compared, summed or passed to another function, it is translated as before.

tests/e2e/test_460_r240_arith_keeps_number_type_e2e.py checks the answers and their Python types.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _tr(q, params=None):
    return translate_to_sql(parse_query(q), dict(params or {"k": 2}))


def _final(q, params=None):
    r = _tr(q, params)
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return r, sql[-1], r.parameters[-1]


@pytest.mark.parametrize(
    "expr, op",
    [("n.g * 2", "*"), ("n.i + 1", "+"), ("n.g - 1", "-"), ("n.i / 2", "/"), ("n.g * $k", "*"), ("n.g * n.i", "*")],
)
def test_the_return_reads_cy_arith_and_is_not_decoded_as_an_integer(expr, op):
    r, sql, params = _final(f"MATCH (n:X) RETURN {expr} AS v")
    assert "CY_ARITH(" in sql and f"'{op}'" in sql, sql
    assert "v" not in [c.lower() for c in r.int_expr_columns], r.int_expr_columns
    assert "v" in [c.lower() for c in r.mixed_expr_columns], r.mixed_expr_columns
    assert sql.count("?") == len(params), (sql, params)


def test_tostring_spells_the_typed_value_once():
    _, sql, params = _final("MATCH (n:X) RETURN toString(n.g * $k) AS s")
    assert "CY_TOSTRING(COALESCE(Graph_KG.CY_ARITH(" in sql, sql  # COALESCE: CY_ARITH's message survives (R297)
    assert sql.count("?") == len(params) and params[0] == 2, (sql, params)


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X) WHERE n.g * 2 > 3 RETURN n.g",
        "MATCH (n:X) RETURN sum(n.g * 2) AS s",
        "MATCH (n:X) RETURN n.g * 2 > 3 AS b",
        "MATCH (n:X) RETURN abs(n.g * 2) AS a",
        "MATCH (n:X) RETURN 2 * 3 AS c",
        "MATCH (n:X) RETURN n.g * 2.0 AS f",
        "MATCH (n:X) RETURN n.s + 'x' AS t",
        "MATCH (n:X) RETURN n.a + n.b AS t",
    ],
)
def test_elsewhere_the_arithmetic_is_as_before(q):
    _, sql, _ = _final(q)
    # 4.6.0 R300: the one CY_ARITH a + may hold is the branch for a row's string (ISNUMERIC 0), which concatenates
    assert sql.count("CY_ARITH(") == sql.count("COALESCE(Graph_KG.CY_ARITH("), sql
    assert "CY_ARITH" not in sql or ("ISNUMERIC(" in sql and "'+'" in sql), sql


def test_a_with_types_the_column_it_already_spells_as_text():
    r = _tr("MATCH (n:X) WITH n.g * 2 AS d RETURN d, toString(d) AS s")
    sql = r.sql if isinstance(r.sql, str) else r.sql[-1]
    assert "ELSE Graph_KG.CY_ARITH(" in sql or "ELSE CY_ARITH(" in sql, sql
    assert "CY_TOSTRING(Stage1.d)" in sql, sql


def test_cy_arith_is_a_setup_routine():
    statements = [s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG") if "CY_ARITH(" in s]
    assert len(statements) == 1 and "LANGUAGE OBJECTSCRIPT" in statements[0]
    # the DBAPI DDL tokenizer breaks on these inside an ObjectScript body (memory: iris_ddl_objectscript_apostrophe)
    body = statements[0].split("{", 1)[1]
    assert "'" not in body and "?" not in body and ":1" not in body


def test_a_set_of_the_arithmetic_stores_the_typed_value():
    r = _tr("MATCH (n:X) SET n.r = n.g * 2")
    writes = [s for s in r.sql if "INTO" in s or s.startswith("UPDATE")]
    assert writes and all("CY_ARITH((SELECT _upd.val FROM" in s and "NUMERIC" not in s for s in writes), writes
