"""4.6.0 R190: `toString()` of a literal float answers openCypher's text.

`RETURN toString(0.95), toString(1.0)` answered `['.95', '1']`: a literal was CAST to VARCHAR, which spells a number
as IRIS does. openCypher spells a float as Java's Double.toString does, the rule R182's CY_FLOAT_TEXT follows for a
stored number: at least one digit after the point, and an exponent below 10^-3 or from 10^7 up (`1.0E10`, `1.0E-4`).
The TCK pins `toString(2.3)` = `'2.3'` and `toString(42)` = `'42'` (TypeConversion4 [1], [5]); the exponent form is
Java's, which the TCK does not exercise.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

CASES = [
    ("0.95", "0.95"),
    ("1.0", "1.0"),
    ("2.3", "2.3"),
    ("-0.5", "-0.5"),
    ("123.456", "123.456"),
    ("1.0e10", "1.0E10"),
    ("12345678.9", "1.23456789E7"),
    ("1e-4", "1.0E-4"),
    ("0.001", "0.001"),
    ("0.0", "0.0"),
    ("1.5e300", "1.5E300"),
    ("42", "42"),
    ("-7", "-7"),
]


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


@pytest.mark.parametrize("literal, want", CASES, ids=[c[0] for c in CASES])
def test_literal(engine, literal, want):
    r = engine.execute_cypher(f"RETURN toString({literal}) AS s")
    assert r.error is None, r.error
    assert r.rows == [[want]]
