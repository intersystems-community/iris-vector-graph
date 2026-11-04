"""4.6.0 R193: head() and last() of a constant list keep the element's type, as a subscript does.

`[1, 2, 3][0]` folds to the literal 1 at translation; `head([1, 2, 3])` and `last([1, 2, 3])` were translated to
`JSON_ARRAYGET`, which answers the element's text, so the driver handed back '1' and '3'. They fold the same way
now, and a boolean element is read back as a boolean (`bool_expr_columns`). Live: tests/e2e/test_460_r064_list_head_last_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(query):
    return translate_to_sql(parse_query(query), {})


@pytest.mark.parametrize(
    "expr,sql",
    [
        ("head([1, 2, 3])", "1 AS v"),
        ("last([1, 2, 3])", "3 AS v"),
        ("head([1.5, 2])", "CAST('1.5' AS DOUBLE) AS v"),
        ("last(['a', 'b'])", "CAST('b' AS VARCHAR(256)) AS v"),
        ("head([])", "NULL AS v"),
        ("last([])", "NULL AS v"),
        ("head(null)", "NULL AS v"),
    ],
)
def test_head_and_last_of_a_constant_list_fold_to_the_element(expr, sql):
    out = _q(f"RETURN {expr} AS v").sql
    assert sql in out and "JSON_ARRAYGET" not in out, out


@pytest.mark.parametrize("expr", ["head([true, 1])", "last([0, false])", "[true][0]"])
def test_a_boolean_element_is_read_back_as_a_boolean(expr):
    assert _q(f"RETURN {expr} AS v").bool_expr_columns == ["v"]


def test_a_list_from_a_row_is_still_read_at_run_time():
    out = _q("UNWIND [[1, 2]] AS x RETURN head(x) AS v").sql
    assert "JSON_ARRAYGET" in out, out
