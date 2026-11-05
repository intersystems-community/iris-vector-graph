"""Division by zero: an integer raises ArithmeticError, a float is IEEE (4.6.0 R297).

Until 4.6.0 every `x / 0` and `x % 0` answered NaN, a documented deviation from openCypher. The translator guards every
`/` and `%` whose divisor is not a provably non-zero literal, emitting

    CASE WHEN <rhs> = 0 AND <lhs> IS NOT NULL THEN <zero branch> ELSE ... END

so the division never reaches IRIS: an unguarded `1/0` inside a predicate takes IRIS down the `SQLCODE -400
<MAXNUMBER>` (or `<DIVIDE>`) path, a fatal error at Query Open that names no Cypher error. The zero branch was
`CAST('NaN' AS DOUBLE)`. It now follows openCypher's error kinds (the TCK's README: ArithmeticError, "such as dividing
by zero"; no TCK scenario divides an integer by zero): two integers raise ArithmeticError, a float operand gives
Infinity, -Infinity or NaN, and where only the row knows the operands' types CY_ARITH decides. The TCK's own float
case stays: `0.0 / 0.0` is NaN (Comparison1 [8]). The live answers are in
tests/e2e/test_460_r297_error_semantics_e2e.py.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

NAN = "CAST('NaN' AS DOUBLE)"
GUARD = "= 0 AND "


def _sql(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params or {}).sql


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) WHERE 1/0 = 1 RETURN n.node_id",
        "MATCH (n) RETURN 1/0",
        "MATCH (n) RETURN 1 % 0",
        "MATCH (n) RETURN 1.5/0",
    ],
)
def test_a_zero_divisor_is_guarded_not_sent_to_iris(cypher):
    sql = _sql(cypher)
    assert GUARD in sql, f"an unguarded zero divisor reaches IRIS and fails the whole statement: {sql}"


@pytest.mark.parametrize("cypher", ["MATCH (n) RETURN 1/0", "MATCH (n) RETURN 1 % 0", "MATCH (n) WHERE 1/0 = 1 RETURN n"])
def test_two_integers_over_zero_raise_arithmetic_error_not_nan(cypher):
    sql = _sql(cypher)
    assert "ArithmeticError: integer" in sql and NAN not in sql, sql


def test_a_float_over_zero_is_ieee():
    sql = _sql("MATCH (n) RETURN 1.5/0")
    assert "CAST('INF' AS DOUBLE)" in sql and "CAST('-INF' AS DOUBLE)" in sql, sql
    assert "ArithmeticError" not in sql, sql


def test_a_nonzero_literal_divisor_needs_no_guard():
    # The guard costs a CASE on every row, so it is only emitted where it can fire.
    sql = _sql("MATCH (n) RETURN 7/2")
    assert GUARD not in sql, sql
    assert "TRUNCATE(" in sql, "integer/integer truncates in Cypher: 7/2 = 3, -7/2 = -3 (4.6.0 R259)"


def test_a_divisor_that_is_not_a_literal_is_guarded():
    # `n.weight` can be zero at runtime, and the translator cannot know, so it guards. A RETURN of it reads
    # CY_ARITH since 4.6.0 R240, which decides a zero divisor itself; a WHERE keeps the CASE, whose zero branch is
    # CY_ARITH's too: only the row knows whether n.weight is an integer.
    sql = _sql("MATCH (n) RETURN 1/n.weight")
    assert GUARD in sql or "CY_ARITH(1, '/', " in sql, sql
    where = _sql("MATCH (n) WHERE 1/n.weight > 0 RETURN n.node_id")
    assert GUARD in where and "CY_ARITH(" in where and NAN not in where, where
