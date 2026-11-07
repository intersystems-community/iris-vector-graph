"""4.6.0 R315: `x IN <list>` where the list is a stored property, a WITH alias or another expression is a membership
test, and a WITH alias is in scope anywhere in that WITH's WHERE.

Round-3 walkthrough (WRONG item, on d004e79e): `MATCH (s:Server) WHERE 'web' IN s.tags RETURN s.name` answered no
rows with result.error SQLCODE -1 (`< ( expected, IDENTIFIER (p2) found ... AND :%qpar(6) IN p2>`): the translator wrote
SQL's `x IN p2.val`, which needs a parenthesised list. `RETURN 'web' IN s.tags` did the same, and
`WITH s, s.tags AS t WHERE 'web' IN t` raised `Undefined variable: t`: the WHERE's alias expansion replaced only an
alias that was a whole operand, not one inside a function call, an IN or a list predicate. The live answers are in
tests/e2e/test_460_r315_in_over_expressions_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

# SQL `x IN <column>`: IN followed by anything that is not a parenthesised list or subquery
BARE_IN = re.compile(r"\bIN\s+(?!\()[A-Za-z_%][\w.%]*")


def sql_of(query, params=None):
    result = translate_to_sql(parse_query(query), dict(params or {}))
    return result.sql if isinstance(result.sql, str) else "\n;\n".join(result.sql)


QUERIES = [
    "MATCH (s:Server) WHERE 'web' IN s.tags RETURN s.name ORDER BY s.name",
    "MATCH (s) WHERE 'web' IN s.tags RETURN s.name",
    "MATCH (s:Server) RETURN s.name, 'web' IN s.tags AS b",
    "MATCH (s:Server) WHERE $x IN s.tags RETURN s.name",
    "MATCH (s:Server) WHERE s.kind IN s.tags RETURN s.name",
    "MATCH (s:Server) WHERE NOT 'web' IN s.tags RETURN s.name",
    "MATCH (s:Server) WHERE 'web' IN s.tags AND s.name STARTS WITH 'w' RETURN s.name",
    "MATCH ()-[r:R]->() WHERE 'hot' IN r.tags RETURN r.name",
    "MATCH ()-[r:R]->() RETURN 'hot' IN r.tags AS b",
    "MATCH (s:Server) WITH s, s.tags AS t WHERE 'web' IN t RETURN s.name",
    "MATCH (s:Server) WITH s.tags AS t WHERE 'web' IN t RETURN t",
    "MATCH (s:Server) WITH s, s.tags AS t RETURN 'web' IN t AS b",
    "MATCH (s:Server) WITH s, s.tags AS t WHERE size(t) > 1 RETURN s.name",
    "MATCH (s:Server) WITH s, s.tags AS t WHERE any(x IN t WHERE x = 'web') RETURN s.name",
    "MATCH (s:Server) WITH collect(s.name) AS names RETURN 'web1' IN names AS b",
    "USE GRAPH 'g' MATCH (s:Server) WHERE 'web' IN s.tags RETURN s.name",
]


@pytest.mark.parametrize("query", QUERIES)
def test_in_over_an_expression_translates_to_a_membership_test(query):
    sql = sql_of(query, {"x": "web"})
    assert not BARE_IN.search(sql), BARE_IN.search(sql).group(0) + "\n" + sql
    if " IN " in query.split("RETURN")[0] + query.split("RETURN")[-1] and "IN [" not in query and "size(" not in query:
        # a membership over the list's elements: `x IN (JSON_VALUE(..))` compared x with the whole list's text
        assert "JSON_TABLE" in sql or "CY_LIST_HAS(" in sql, sql


def test_a_with_alias_inside_its_own_where_reads_the_aliased_expression():
    sql = sql_of("MATCH (s:Server) WITH s, s.tags AS t WHERE size(t) > 1 RETURN s.name")
    assert "tags" in sql


def test_a_list_predicate_variable_named_like_an_alias_is_not_replaced():
    """`WITH s.tags AS t WHERE any(t IN ['a'] WHERE t = 'a')`: the inner t is the predicate's own, not the alias."""
    sql = sql_of("MATCH (s:Server) WITH s, s.tags AS t WHERE any(t IN ['a'] WHERE t = 'a') RETURN s.name")
    assert sql


@pytest.mark.parametrize("where", ["result", "result AND lhs IS NOT NULL", "NOT result"])
def test_a_whole_operand_alias_is_left_to_the_where_expansion(where):
    """`WITH lhs, rhs, lhs < rhs AS result WHERE result` compares by Cypher's rules (CY_CMP), as before R315: expanded
    into `lhs < rhs` first, it compared SQL values and kept '' < 1 (the R308 differential's cross-type case)."""
    sql = sql_of("MATCH p = (n:Z)-[r]->() WITH [n, r, p, '', 1, 3.14] AS types UNWIND range(0, 5) AS i "
                 "UNWIND range(0, 5) AS j WITH types[i] AS lhs, types[j] AS rhs WHERE i <> j "
                 f"WITH lhs, rhs, lhs < rhs AS result WHERE {where} RETURN lhs, rhs")
    where_sql = sql.rsplit("WHERE", 1)[1]
    assert "CY_CMP(" in where_sql and not re.search(r"\blhs < \w", where_sql), where_sql


def test_in_over_a_list_literal_or_a_list_parameter_keeps_its_sql_in_list():
    assert re.search(r"\bIN \(", sql_of("MATCH (s:Server) WHERE s.name IN ['web1', 'web2'] RETURN s.name"))
    assert re.search(r"\bIN \(", sql_of("MATCH (s:Server) WHERE s.name IN $l RETURN s.name", {"l": ["web1"]}))


@pytest.mark.parametrize("query, params", [
    ("MATCH (s:Server) WHERE 'web' IN s.tags RETURN s.name", {}),
    ("MATCH (s:Server) WHERE $x IN s.tags RETURN s.name", {"x": "web"}),
    ("MATCH (b:Box) RETURN '2' IN b.nums AS b", {}),
    ("MATCH (s:Server) WHERE s.kind IN s.tags RETURN s.name", {}),
    ("MATCH ()-[r:R]->() WHERE 'hot' IN r.tags RETURN r.name", {}),
    ("MATCH (s:Server) RETURN null IN s.tags AS b", {}),
    ("MATCH (s:Server) RETURN $x IN s.tags AS b", {"x": None}),
])
def test_a_scalar_in_a_stored_list_is_tested_by_type(query, params):
    """CY_LIST_HAS compares each element by its JSON type: '2' IN [1, 2, 3] is false, 2 IN [1, 2.0] true, and a null
    element or a null x with no match gives null (an empty list: false). A parameter's value goes in as a literal."""
    sql = sql_of(query, params)
    assert "CY_LIST_HAS(" in sql, sql
    if params.get("x"):
        assert params["x"] not in str(translate_to_sql(parse_query(query), dict(params)).parameters)


def test_cy_list_has_is_created_and_holds_no_character_the_ddl_tokenizer_breaks_on():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph/schema.py").read_text()
    line = next(x for x in src.splitlines() if "FUNCTION Graph_KG.CY_LIST_HAS(" in x)
    body = line[line.index("{"):]
    assert "'" not in body and "?" not in body and ":1" not in body


@pytest.mark.parametrize("query", [
    "MATCH (a:A) RETURN a.name, $x IN a.tags AS b",
    "MATCH (a:A) WHERE $x IN a.tags RETURN a.name",
    "MATCH (a:A) WITH collect(a.name) AS names RETURN $x IN names AS b",
    "MATCH (a:A)-[r]->(b) RETURN b.name, $x IN r.l AS b2",
])
def test_every_bind_marker_has_its_parameter(query):
    """A parameter x translated and then unused left one more parameter than marker: <ARGUMENT ERROR> Incorrect number
    of parameters (found by the R308 differential's new IN family)."""
    from iris_vector_graph.cypher.translator import translate_to_sql as tts

    result = tts(parse_query(query), {"x": "q"})
    statements = [result.sql] if isinstance(result.sql, str) else list(result.sql)
    params = result.parameters if result.parameters and isinstance(result.parameters[0], list) else [result.parameters]
    for sql, ps in zip(statements, params):
        assert sql.count("?") == len(ps), (sql, ps)
