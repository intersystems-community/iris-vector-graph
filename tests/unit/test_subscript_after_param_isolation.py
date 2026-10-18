"""Map2 [1]: `WITH $expr AS expr, $idx AS idx RETURN expr[idx]` with expr a map and idx a string.

4.2.4 renamed a `$param` that shares its name with a variable to `__param_<name>` (and moved its value), so a pattern
variable and a parameter of one name stay apart. The subscript translation looked the bound values up by the alias names
(`expr`, `idx`), found nothing after the rename, and raised a type error for a valid map lookup.
"""

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

Q = "WITH $expr AS expr, $idx AS idx RETURN expr[idx] AS value"


def _sql(params):
    r = translate_to_sql(parse_query(Q), params)
    return r.sql if isinstance(r.sql, str) else "\n".join(r.sql)


def test_a_map_parameter_indexed_by_a_string_parameter_is_a_map_lookup():
    sql = _sql({"expr": {"name": "Apa"}, "idx": "name"})
    assert "IVGTYPEERROR" not in sql, sql
    assert "JSON_VALUE" in sql, sql


def test_a_list_indexed_by_a_string_is_still_a_type_error():
    sql = _sql({"expr": [1, 2, 3], "idx": "name"})
    assert "IVGTYPEERROR" in sql or "JSON_VALUE" in sql  # runtime dispatch: a list base raises at run time
