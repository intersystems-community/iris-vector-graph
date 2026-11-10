"""4.7.0 row 35b: a SET value the translator cannot translate raises; it was stored as null.

`_translate_expr_for_update` translated any value it had no rule for through `translate_expression`, and caught every
exception but SyntaxError there, answering the SQL `NULL`: the SET then removed or nulled the property and answered
success. Measured on f12bee92: no unit, E2E or TCK (set, merge, create, remove, delete: 280 scenarios) case reached
that except, so no answer depends on it; the case here makes the translation fail on purpose and asserts the error
comes out.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import ast, translator
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def test_a_set_value_that_fails_to_translate_raises(monkeypatch):
    real = translator.translate_expression

    def failing(expr, context, segment="select"):
        if isinstance(expr, ast.FunctionCall) and expr.function_name.lower() == "tolower":
            raise RuntimeError("cannot translate this value")
        return real(expr, context, segment)

    monkeypatch.setattr(translator, "translate_expression", failing)
    with pytest.raises(RuntimeError, match="cannot translate this value"):
        translate_to_sql(parse_query("MATCH (n:P) SET n.p = toLower(n.q)"), {})
