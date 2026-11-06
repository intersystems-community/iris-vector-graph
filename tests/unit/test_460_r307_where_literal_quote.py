"""4.6.0 R307: a string literal with an apostrophe in a WHERE comparison is one SQL literal.

`_inline_literal` pasted a string literal between quotes as it was, so `WHERE n.p = 'it\\'s'` sent `= 'it's'`, which
IRIS refused ("unmatched quote"), and a literal holding `' OR '1'='1` became SQL: the filter matched every row. Found
by tests/e2e/test_460_r307_value_matrix_e2e.py (reader eq_lit, value "it's").
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("op", ["=", "<>", "<", ">="])
def test_an_apostrophe_is_doubled(op):
    sql = translate_to_sql(parse_query(f"MATCH (n:L) WHERE n.p {op} 'it\\'s' RETURN n.id"), {}).sql
    assert "'it''s'" in sql and "'it's'" not in sql, sql


def test_a_literal_cannot_close_its_quote():
    q = "MATCH (n:L) WHERE n.p = 'x\\' OR \\'1\\'=\\'1' RETURN n.id"
    sql = translate_to_sql(parse_query(q), {}).sql
    assert "'x'' OR ''1''=''1'" in sql, sql
    assert " OR '1'='1'" not in sql, sql
