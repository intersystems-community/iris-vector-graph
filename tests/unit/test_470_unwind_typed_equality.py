"""4.7.0: an UNWIND element of a parameter list compared with a stored property compares as the same literal would,
offline (live: tests/e2e/test_470_unwind_wrong_node_e2e.py).

`UNWIND $p AS e [WITH e] MATCH (n) WHERE n.id = e DETACH DELETE n` with `$p = ['x', [1], true]`: through a WITH, true
was compared as its JSON_TABLE text 1 (and a stored boolean true was missed); row by row, true was inlined as 1 and
`[1]` as raw SQL text (`p.val = [1]`, SQLCODE -12). Both now read a boolean by its stored spellings, as `n.id = true`
does (`_stored_bool_in`), and a list by its JSON.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import rewrite_ambiguous_param_unwind, translate_to_sql

MIXED = {"p": ["x", [1], True]}


def _sql(q):
    s = translate_to_sql(rewrite_ambiguous_param_unwind(parse_query(q), MIXED), dict(MIXED)).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("q", [
    "UNWIND $p AS e WITH e MATCH (n:X) WHERE n.id = e DETACH DELETE n",
    "UNWIND $p AS e WITH e MATCH (n:X {id: e}) DETACH DELETE n",
    "UNWIND $p AS e MATCH (n:X) WHERE n.id = e RETURN n.id",
])
def test_a_row_with_kinds_compares_by_them(q):
    sql = _sql(q)
    assert "IN ('true', '1', 'True')" in sql and "SUBSTRING(" in sql and "__j" in sql, sql


@pytest.mark.parametrize("q", [
    "UNWIND $p AS e MATCH (n:X) WHERE n.id = e DETACH DELETE n",
    "UNWIND $p AS e MATCH (n:X {id: e}) DETACH DELETE n",
])
def test_row_by_row_each_element_is_a_literal(q):
    sql = _sql(q)
    assert "= [1]" not in sql, "the list was inlined as raw SQL"
    assert "IN ('true', '1', 'True')" in sql, "true was inlined as 1"


def test_an_id_through_a_with_names_the_node_only_for_a_string():
    sql = _sql("UNWIND $p AS e WITH e MATCH (n:X {id: e}) DETACH DELETE n")
    assert ".node_id = Stage2.e" in sql and "IN ('true', '1', 'True')" in sql and "LEFT JOIN" in sql, sql


def test_a_string_element_compares_as_before():
    sql = _sql("UNWIND $p AS e MATCH (n:X) WHERE n.id = e DETACH DELETE n")
    assert "p2.val = 'x'" in sql or "= 'x'" in sql
