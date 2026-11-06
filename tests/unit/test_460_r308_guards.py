"""4.6.0 R308, offline: the structural guard (`EXISTS (SELECT 1 FROM rdf_props _sgN ...)`, OPT-3) goes only where a
missing property drops the row anyway: a comparison that is the WHERE itself or one of its AND-ed conditions.

On the release head (7a796f85) every property read in a boolean context got one in the statement's WHERE, so
`RETURN a.name, CASE WHEN a.flag = true THEN a.name END` answered only the nodes with a flag: a missing property
dropped the row instead of reading null. Measured by tests/e2e/test_460_r308_cypher_differential_e2e.py (seeds 1-3:
29+15 queries); the hand cases case_missing_prop, with_case_missing_prop, where_case_missing_prop,
order_case_missing_prop and comp_missing_prop answer by hand.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q):
    s = translate_to_sql(parse_query(q), {}).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("q", [
    "MATCH (a:L) RETURN a.name, CASE WHEN a.flag = true THEN a.name END AS c",
    "MATCH (a:L) RETURN a.name, CASE WHEN a.age > 2 THEN 'hi' ELSE 'lo' END AS c",
    "MATCH (a:L) RETURN a.name, a.flag = true AS c",
    "MATCH (a:L) WITH a, a.x = 1 AS c RETURN a.name, c",
    "MATCH (a:L) RETURN a.name ORDER BY CASE WHEN a.x = 1 THEN 1 ELSE 2 END",
    "MATCH (a:L) RETURN a.name, [y IN [1] WHERE a.x = y] AS c",
    "MATCH (a:L) WHERE (CASE WHEN a.x = 1 THEN 1 ELSE 2 END) = 2 RETURN a.name",
    "MATCH (a:L) WHERE NOT (a.x = 1 OR a.y = 2) RETURN a.name",
])
def test_a_property_read_outside_a_where_condition_gets_no_guard(q):
    assert "_sg" not in _sql(q), _sql(q)


@pytest.mark.parametrize("q", [
    "MATCH (a:L) WHERE a.x = 1 RETURN a.name",
    "MATCH (a:L) WHERE a.x = 1 AND a.y > 2 RETURN a.name",
])
def test_a_where_condition_keeps_its_guard(q):
    assert "_sg" in _sql(q), _sql(q)


@pytest.mark.parametrize("q", [
    "MATCH (a:L)<-[:S]-(b) RETURN a.name, sum(b.score) AS v",
    "MATCH (b:L) RETURN b.kind, sum(b.age) AS v",
    "MATCH (b:L) WITH b.kind AS k, sum(b.age) AS v RETURN k, v",
])
def test_a_sum_of_only_nulls_is_zero(q):
    """openCypher's sum over no values is 0; SQL's SUM of a group whose every value is null is NULL (4.6.0 R308,
    seed 4 #293: `RETURN a.name, sum(b.score)` answered null for a node whose neighbours have no score)."""
    sql = _sql(q)
    # a CASE, not COALESCE: COALESCE(<varchar sum>, 0) answered the text '0'
    assert re.search(r"CASE WHEN SUM\(.+\) IS NULL THEN CAST\(0 AS INTEGER\) ELSE SUM\(", sql), sql


def test_a_long_sum_is_not_repeated():
    """TCK Return6 [16]: the sum's expression is 55,000 characters; repeated in a CASE the statement failed -202."""
    sql = _sql("MATCH (me)-[r1:ATE]->()<-[r2:ATE]-(you) WHERE me.name = 'Michael' "
               "WITH me, count(DISTINCT r1) AS H1, count(DISTINCT r2) AS H2, you "
               "MATCH (me)-[r1:ATE]->()<-[r2:ATE]-(you) "
               "RETURN me, you, sum((1 - abs(r1.times / H1 - r2.times / H2)) * (r1.times + r2.times) / (H1 + H2)) AS sum")
    assert sql.count("SUM(") == 1, len(sql)


@pytest.mark.parametrize("q", [
    "MATCH (a:L) RETURN a.name, CASE a.kind WHEN 'x' THEN 1 ELSE 0 END AS c",
    "MATCH (a:L) WITH a, CASE WHEN a.flag = true THEN 1 ELSE 0 END AS c RETURN a.name, c",
])
def test_a_case_s_number_is_cast(q):
    """IRIS's cached-query literal replacement reads a bare `THEN 1 ELSE 0` as text in some plans: the hand cases
    case_simple and with_case_missing_prop answered '1' / '0' after a redeploy (4.6.0 R308, both drivers)."""
    sql = _sql(q)
    assert "THEN CAST(1 AS INTEGER)" in sql and "ELSE CAST(0 AS INTEGER)" in sql, sql
