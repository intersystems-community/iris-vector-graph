"""An undirected hop under USE GRAPH reads its graph from the hop's CTE.

The undirected hop joins a UNION ALL CTE (`_u<alias>`) of the edges in both directions. Under `USE GRAPH` the WHERE
pins `<alias>.graph_id`, but the CTE did not project `graph_id`, so IRIS refused the statement (SQLCODE -29, "Field
'E3.GRAPH_ID' not found") for every scoped query with an undirected hop that the uniqueness rewrite routes through the
CTE: two hops of one type, an unordered LIMIT, a cross filter. Found by spec 257's three-way tests.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q):
    r = translate_to_sql(parse_query(q))
    return r.sql if isinstance(r.sql, str) else r.sql[0]


@pytest.mark.parametrize(
    "query",
    [
        "USE GRAPH 'g' MATCH (p:P)-[:K]-(x)-[:K]-(y) RETURN count(*) AS n",
        "USE GRAPH 'g' MATCH (p:P)-[:K]-(x:P)-[:K]-(y:P) RETURN y.sid LIMIT 3",
        "USE GRAPH 'g' MATCH (p:P)-[:K]-(x:P)-[:K]-(y:P) WHERE p.age < y.age RETURN p.sid, y.sid",
    ],
)
def test_every_graph_id_the_where_reads_is_projected_by_its_cte(query):
    sql = _sql(query)
    ctes = dict(re.findall(r"(_u\w+) AS \(\s*(.*?)\n\)", sql, re.S))
    assert ctes, sql
    for alias, body in ctes.items():
        edge = alias[2:]
        if re.search(rf"\b{edge}\.graph_id\b", sql):
            for select in body.split("UNION ALL"):
                assert re.search(r"\bgraph_id\b", select.split("FROM")[0]), (alias, select)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (p:P)-[:K]-(x)-[:K]-(y) RETURN count(*) AS n",
        "MATCH (p:P)-[:K]->(x)<-[:K]-(y) RETURN count(*) AS n",
        "MATCH (p:P)-[:K]->(x)-[:K]-(y) RETURN count(*) AS n",
    ],
)
def test_two_hops_tell_parallel_relationships_apart_by_edge_id(query):
    """In a multigraph two relationships may share (s, p, o_id): the hop-to-hop exclusion compares edge_id as well,
    or a path over two parallel relationships is dropped as if it used one twice (SQL 4 against native 8, spec 257)."""
    sql = _sql(query)
    excl = re.findall(r"NOT \(([^()]*(?:\([^()]*\)[^()]*)*)\)", sql)
    excl = [x for x in excl if "_os" in x or ".s =" in x]
    assert excl, sql
    for x in excl:
        assert "edge_id" in x, x
