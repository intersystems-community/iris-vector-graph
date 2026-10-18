"""An undirected hop in a RETURN statement becomes one UNION ALL branch per direction.

IRIS cannot push a join predicate into the UNION ALL CTE an undirected hop reads: it
materializes every edge of the type and drives the join from it, so a lookup from a
bound person walks the whole KNOWS graph (SNB BI18 SF0.1: 7.5 s; the directed branches
7 ms). A join distributes over UNION ALL, so the branches return the CTE's rows; the
outer statement projects, groups and orders them through `_r` columns.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(text, params=None):
    r = translate_to_sql(parse_query(text), params or {})
    sql = r.sql if isinstance(r.sql, str) else r.sql[0]
    flat = r.parameters[0] if r.parameters and isinstance(r.parameters[0], list) else r.parameters
    flat = list(flat or [])
    assert sql.count("?") == len(flat)
    return sql, flat


def _branches(sql):
    return len(re.findall(r"\bUNION ALL\b", sql)) + 1


BI18 = (
    "MATCH (p:Person)-[:KNOWS]-(m:Person)-[:KNOWS]-(f:Person) "
    "WHERE p.sid = $pid AND f.sid <> $pid "
    "RETURN f.sid AS person, count(DISTINCT m) AS mutual ORDER BY mutual DESC, person"
)


def test_two_undirected_hops_are_four_directed_branches():
    sql, params = _sql(BI18, {"pid": "7"})
    assert "_ue" not in sql, sql
    assert _branches(sql) == 4, sql
    assert ") _r" in sql
    # the edge joins are the table itself, typed, and the reverse branch drops self-loops
    assert re.search(r"JOIN (?:\w+\.)?rdf_edges e3 ON e3\.s = n0\.node_id AND e3\.p = 'KNOWS'\n", sql), sql
    assert re.search(
        r"JOIN (?:\w+\.)?rdf_edges e3 ON e3\.o_id = n0\.node_id AND e3\.p = 'KNOWS' AND e3\.s <> e3\.o_id\n", sql
    ), sql


def test_every_placeholder_has_a_parameter():
    sql, params = _sql(BI18, {"pid": "7"})
    assert sql.count("?") == len(params)


def test_the_outer_statement_reads_only_the_union():
    sql, _ = _sql(BI18, {"pid": "7"})
    outer_head = sql[: sql.index("FROM (")]
    tail = sql[sql.rindex(") _r") :]
    for part in (outer_head, tail):
        # node, edge and label aliases live in the branches; a hoisted property join reads _r
        assert not re.search(r"(?<![\w.])(?:n|e|l)\d+\.", part), part
    assert "COUNT(DISTINCT _r.n2__node_id)" in sql
    assert re.search(r"LEFT JOIN (?:\w+\.)?rdf_props p10 ON p10\.s = _r\.n5__node_id", tail), tail


def test_no_cte_column_survives_in_a_branch():
    sql, _ = _sql(BI18, {"pid": "7"})
    assert not re.search(r"\be\d+\._(?:src|dst|os|oo|p)\b", sql), sql


def test_relationship_uniqueness_compares_physical_columns():
    sql, _ = _sql(BI18, {"pid": "7"})
    assert "NOT (e6.s = e3.s AND e6.p = e3.p AND e6.o_id = e3.o_id)" in sql


def test_one_hop_with_limit_and_property_order():
    sql, params = _sql(
        "MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $pid "
        "RETURN f.sid AS sid ORDER BY f.lastName LIMIT 5",
        {"pid": "7"},
    )
    assert _branches(sql) == 2, sql
    assert sql.count("?") == len(params)
    assert "_ue" not in sql


def test_untyped_undirected_hop_has_no_type_filter():
    sql, _ = _sql("MATCH (a)--(b) WHERE a.id = 'x' RETURN b.id AS id")
    assert _branches(sql) == 2, sql
    assert ".p = " not in sql.split("FROM (", 1)[1].split(") _r")[0].replace("p8.", "")


def test_multi_type_undirected_hop_filters_with_in():
    sql, _ = _sql("MATCH (a)-[:R|S]-(b) WHERE a.id = 'x' RETURN b.id AS id")
    assert re.search(r"e\d+\.p IN \('R', 'S'\)", sql), sql


def test_a_returned_relationship_comes_through_the_union():
    sql, params = _sql("MATCH (a)-[r:KNOWS]-(b) WHERE a.id = 'x' RETURN r, b.id AS id")
    assert _branches(sql) == 2, sql
    assert sql.count("?") == len(params)
    assert not re.search(r"\be\d+\._(?:src|dst|os|oo|p)\b", sql), sql


@pytest.mark.parametrize(
    "text",
    [
        # OPTIONAL: a LEFT JOIN does not distribute over UNION ALL (the null row would repeat)
        "MATCH (a:A) OPTIONAL MATCH (a)-[:R]-(b) RETURN a.id AS a, b.id AS b",
        # four hops: sixteen branches cost more to compile than the CTE costs to run
        "MATCH (a)-[:R]-(b)-[:R]-(c)-[:R]-(d)-[:R]-(e) RETURN count(*) AS n",
        # a variable-length hop runs through its own executor path
        "MATCH (a)-[:R*1..2]-(b) WHERE a.id = 'x' RETURN b.id AS id",
    ],
)
def test_shapes_that_keep_the_cte(text):
    sql, params = _sql(text)
    assert ") _r" not in sql, sql
    assert sql.count("?") == len(params)


def test_three_anchored_hops_are_eight_branches():
    sql, params = _sql(
        "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a) "
        "WHERE a.sid = $x AND b.sid < c.sid RETURN count(*)",
        {"x": "7"},
    )
    assert _branches(sql) == 8, sql
    assert sql.count("?") == len(params)
    assert "_ue" not in sql


def test_an_unanchored_scan_keeps_the_cte():
    # BI11: no node is bound, so every branch is a full scan of its own (8 x 13 s against the CTE's 17 s)
    sql, _ = _sql(
        "MATCH (a:Person)-[:KNOWS]-(b:Person)-[:KNOWS]-(c:Person)-[:KNOWS]-(a) "
        "WHERE a.sid < b.sid AND b.sid < c.sid RETURN count(*)"
    )
    assert ") _r" not in sql and "_ue" in sql, sql


def test_a_branch_never_projects_only_a_constant():
    # IRIS fails a UNION ALL of constant-only branches at fetch with <WIDE CHAR>
    sql, _ = _sql("MATCH (a)-[:KNOWS]-(b) WHERE a.id = $x RETURN count(*) AS n", {"x": "7"})
    assert _branches(sql) == 2, sql
    for b in sql.split("FROM (\n", 1)[1].split("\nUNION ALL\n"):
        assert not b.startswith("SELECT 1 AS"), b


def test_projection_only_props_join_the_outer_statement():
    sql, params = _sql(
        "MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $pid RETURN f.firstName AS name, f.lastName AS last",
        {"pid": "7"},
    )
    assert sql.count("?") == len(params)
    branches = sql.split("FROM (\n", 1)[1].rsplit("\n) _r", 1)[0]
    outer = sql.rsplit("\n) _r", 1)[1]
    assert "'firstName'" not in branches and "'lastName'" not in branches
    assert re.search(r"LEFT JOIN (?:\w+\.)?rdf_props (p\d+) ON \1\.s = _r\.n2__node_id", outer), outer
    # the anchor's property stays in the branches: the WHERE needs it there
    assert "p.sid" not in sql and re.search(r"p\d+\.val = \?", branches)


def test_a_property_the_where_reads_stays_in_the_branches():
    sql, params = _sql(
        "MATCH (p:Person)-[:KNOWS]-(f:Person) WHERE p.sid = $pid AND f.age > 3 RETURN f.age AS age",
        {"pid": "7"},
    )
    assert sql.count("?") == len(params)
    branches = sql.split("FROM (\n", 1)[1].rsplit("\n) _r", 1)[0]
    assert branches.count("rdf_props") >= 4  # anchor + age, in both branches


def test_directed_query_is_untouched():
    sql, _ = _sql("MATCH (a)-[:R]->(b) WHERE a.id = 'x' RETURN b.id AS id")
    assert ") _r" not in sql and "UNION ALL" not in sql


def test_branches_skip_runtime_plan_choice():
    # RTPC re-costs every branch at compile (BI14: 23 s -> 11 s without it); the anchor is an equality either way
    sql, _ = _sql(BI18, {"pid": "7"})
    body = sql.split("FROM (\n", 1)[1].rsplit("\n) _r", 1)[0]
    branches = body.split("\nUNION ALL\n")
    assert all(b.startswith("SELECT %NORUNTIME ") for b in branches), branches[0][:80]
    assert not sql.startswith("SELECT %NORUNTIME")
