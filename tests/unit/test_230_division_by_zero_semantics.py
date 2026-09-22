"""Division by zero yields NaN, and that is a deliberate deviation from openCypher.

`tests/e2e/test_stress_api.py::test_sql_execution_error_returns_500` sent
`MATCH (n) WHERE 1/0 = 1 RETURN n.node_id` expecting the endpoint to answer 400 or 500,
and got 200 with zero rows. Nothing was broken: the translator guards every `/` and `%`
whose divisor is not a provably non-zero literal, emitting

    CASE WHEN <rhs> = 0 AND <lhs> IS NOT NULL THEN CAST('NaN' AS DOUBLE) ELSE ... END

so the division never reaches IRIS. openCypher raises an arithmetic error here, which is
why this deserves a test that says so rather than a test that happens to pass: the
divergence is a documented API decision (docs/KNOWN_ISSUES.md), not an accident, and the
day someone makes `1/0` raise, an endpoint test elsewhere must not be the thing that
notices.

The guard exists because an unguarded `1/0` inside a predicate takes IRIS down the
`SQLCODE -400 <MAXNUMBER>` path — a fatal error at Query Open, not a row-level null.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

NAN_GUARD = "CAST('NaN' AS DOUBLE)"


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
    assert NAN_GUARD in sql, (
        f"an unguarded zero divisor reaches IRIS and fails the whole statement: {sql}"
    )


def test_a_nonzero_literal_divisor_needs_no_guard():
    # The guard costs a CASE on every row, so it is only emitted where it can fire.
    sql = _sql("MATCH (n) RETURN 7/2")
    assert NAN_GUARD not in sql, sql
    assert "FLOOR(" in sql, "integer/integer is floor division in Cypher: 7/2 = 3"


def test_a_divisor_that_is_not_a_literal_is_guarded():
    # `n.weight` can be zero at runtime, and the translator cannot know, so it guards.
    sql = _sql("MATCH (n) RETURN 1/n.weight")
    assert NAN_GUARD in sql, sql
