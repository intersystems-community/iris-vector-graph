"""`n.prop >= $x` and `<=` repeated the operand text (the NaN decomposition) but bound its parameter once: the statement had more
placeholders than parameters, failed at execution ('Incorrect number of parameters') and the engine returned no rows."""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(query, params):
    r = translate_to_sql(parse_query(query), params)
    text = r.sql if isinstance(r.sql, str) else r.sql[0]
    bound = r.parameters[0] if r.parameters and isinstance(r.parameters[0], (list, tuple)) else (r.parameters or [])
    return text, list(bound)


@pytest.mark.parametrize("op", ["<", "<=", ">", ">=", "=", "<>"])
@pytest.mark.parametrize("prop", ["creationDate", "length"])
def test_property_compared_with_a_parameter_binds_every_placeholder(op, prop):
    text, bound = _sql(f"MATCH (m:Post) WHERE m.{prop} {op} $a RETURN count(m)", {"a": "2011"})
    assert text.count("?") == len(bound)


def test_two_sided_window_binds_every_placeholder():
    text, bound = _sql("MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) WHERE m.creationDate >= $a AND m.creationDate < $b RETURN t.name AS tag, count(m) AS n", {"a": "2011", "b": "2012"})
    assert text.count("?") == len(bound)
    assert bound.count("2011") >= 1 and bound.count("2012") >= 1


def test_literal_comparisons_keep_the_nan_safe_form():
    text, _ = _sql("MATCH (m:Post) WHERE m.length >= 5 RETURN count(m)", {})
    assert " OR " in text
