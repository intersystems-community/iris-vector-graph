"""4.7.0: SET of an UNWIND variable over a parameter list, offline (the answers are live in
tests/e2e/test_470_unwind_set_e2e.py).

On 4.6.5 (f1a442e7) and release-4.7.0 before this, `UNWIND $p AS e MATCH (n {id: 'x'}) SET n.f = e` raised
"UndefinedVariable: Variable `e` not defined" and `UNWIND $rows AS r MATCH (n {id: r.id}) SET n += r` "SET n +=
takes a map literal or a map parameter", for every list. The write runs row by row from the list (`_run_unwind_dml_rows`):
each row's SET values now read the row's value. A boolean carried through a WITH to a SET was written as '1'.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q, params):
    s = translate_to_sql(parse_query(q), dict(params))
    return ("\n".join(s.sql) if not isinstance(s.sql, str) else s.sql), s.parameters


@pytest.mark.parametrize("q, params", [
    ("UNWIND $p AS e MATCH (n {id: 'x'}) SET n.f = e", {"p": [1, 2]}),
    ("UNWIND $p AS e CREATE (n:X) SET n.f = e", {"p": [1, 2]}),
    ("UNWIND $p AS e MATCH (n {id: 'x'}) SET n.f = e * 2", {"p": [3]}),
    ("UNWIND $rows AS r MATCH (n {id: r.id}) SET n += r", {"rows": [{"id": "a", "v": 1}]}),
    ("UNWIND $rows AS r MATCH (n {id: r.id}) SET n = r", {"rows": [{"id": "a", "v": 1}]}),
])
def test_it_translates(q, params):
    _sql(q, params)


def test_each_rows_value_is_bound():
    sql, params = _sql("UNWIND $p AS e MATCH (n {id: 'x'}) SET n.f = e", {"p": [7, "s"]})
    flat = [v for p in params for v in (p if isinstance(p, list) else [p])]
    assert 7 in flat and "s" in flat


def test_a_merge_on_create_set_reads_the_row():
    _sql("UNWIND $p AS e MERGE (n:X {id: e}) ON CREATE SET n.f = e", {"p": ["a"]})


def test_a_comprehension_variable_of_the_same_name_is_its_own():
    """`[e IN [10, 20] | e]` inside the value binds its own e: the row's value is not put in for it."""
    from iris_vector_graph.cypher import ast
    from iris_vector_graph.cypher.translator import _bind_variable

    tree = parse_query("RETURN [e IN [10, 20] | e] AS l, e AS x")
    items = tree.return_clause.items
    bound = _bind_variable(items, "e", ast.Literal(7))
    assert bound[0].expression is items[0].expression
    assert bound[1].expression == ast.Literal(7)


@pytest.mark.parametrize("value, sql", [
    (True, "'true'"),
    (False, "'false'"),
    ([1, "a"], "'[1, \"a\"]'"),
    ({"k": 1}, "'{\"k\": 1}'"),
    ("s", "'s'"),
    (3, "CAST(3 AS BIGINT)"),
])
def test_a_pair_value_is_spelled_as_a_set_stores_it(value, sql):
    from iris_vector_graph.stores.iris_sql_store import _pair_value_sql

    assert _pair_value_sql(value) == sql
