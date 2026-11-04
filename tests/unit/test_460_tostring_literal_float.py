"""4.6.0 R190: `toString()` of a literal float spells it as openCypher does, through R182's CY_FLOAT_TEXT.

`RETURN toString(0.95), toString(1.0)` answered `['.95', '1']`: the literal was `CAST(x AS VARCHAR)`, IRIS's spelling.
A float literal (and its negation) now goes through `CY_FLOAT_TEXT`, which turns IRIS's canonical decimal into Java's
Double.toString text (`0.95`, `1.0`, `1.0E10`, `1.0E-4`); an integer literal keeps its CAST (`'42'`).
tests/e2e/test_460_tostring_literal_float_e2e.py checks the answers.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _select(q):
    r = translate_to_sql(parse_query(q), {})
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


@pytest.mark.parametrize(
    "literal, canonical",
    [("0.95", ".95"), ("1.0", "1"), ("1.0e10", "10000000000"), ("1e-4", ".0001"), ("-0.5", "-.5"), ("2.3", "2.3"),
     ("12345678.9", "12345678.9"), ("0.0", "0")],
)
def test_a_float_literal_goes_through_cy_float_text(literal, canonical):
    """The literal's shortest decimal, in the canonical form CY_FLOAT_TEXT reads: a bound double CASTs to every digit
    of the binary value ('.94999999999999995559')."""
    sql = _select(f"RETURN toString({literal}) AS s")
    assert f"CY_FLOAT_TEXT('{canonical}')" in sql, sql


@pytest.mark.parametrize("literal", ["42", "-7", "true", "'apa'"])
def test_anything_else_keeps_its_translation(literal):
    sql = _select(f"RETURN toString({literal}) AS s")
    assert "CY_FLOAT_TEXT" not in sql, sql
