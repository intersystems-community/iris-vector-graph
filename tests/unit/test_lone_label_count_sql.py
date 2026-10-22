"""A whole-label count reads the label index alone (`MATCH (p:L) RETURN count(p)`, BI_count_persons).

The native planner declines the shape (a native scan walks every member), and the SQL path joined `nodes` to
`rdf_labels`: 4.7 ms at SF1 against 0.7 for the label rows alone (Neo4j 0.8). `rdf_labels` is keyed
`(graph_id, s, label)` with a foreign key to `nodes`, so each label row is one node of the label: the join added
nothing. `count(DISTINCT p)` keeps the SQL path's meaning (distinct ids across graphs, KNOWN_ISSUES).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q):
    r = translate_to_sql(parse_query(q))
    return (r.sql if isinstance(r.sql, str) else r.sql[0]), r.parameters, r


@pytest.mark.parametrize(
    "query,select,params",
    [
        ("MATCH (p:Person) RETURN count(p)", "SELECT COUNT(*) AS count_p_", ["Person"]),
        ("MATCH (p:Person) RETURN count(*) AS n", "SELECT COUNT(*) AS n", ["Person"]),
        ("MATCH (p:Person) RETURN count(DISTINCT p) AS n, count(*) AS m", "SELECT COUNT(DISTINCT s) AS n, COUNT(*) AS m",
         ["Person"]),
        ("USE GRAPH 'g' MATCH (p:Person) RETURN count(p) AS n", "SELECT COUNT(*) AS n", ["Person", "g"]),
        ("USE GRAPH '' MATCH (p:Person) RETURN count(p) AS n", "SELECT COUNT(*) AS n", ["Person", ""]),
    ],
)
def test_a_whole_label_count_reads_the_label_rows(query, select, params):
    sql, p, r = _sql(query)
    assert sql.startswith(select), sql
    assert "rdf_labels" in sql and "nodes" not in sql, sql
    assert ("graph_id = ?" in sql) is (len(params) == 2), sql
    assert p == [params]


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (p:Person:Admin) RETURN count(p)",          # two labels
        "MATCH (p:Person {name: 'x'}) RETURN count(p)",    # a property
        "MATCH (p:Person) WHERE p.age > 3 RETURN count(p)",
        "MATCH (p:Person)-[:K]->(q) RETURN count(p)",
        "MATCH (p) RETURN count(p)",                       # no label
        "MATCH (p:Person) RETURN count(p.name)",           # a property's count skips nulls
        "OPTIONAL MATCH (p:Person) RETURN count(p)",
        "MATCH (p:Person) RETURN p.name, count(p)",        # grouped
        "MATCH (p:Person|Admin) RETURN count(p)",
    ],
)
def test_anything_more_keeps_the_join(query):
    sql, _p, _r = _sql(query)
    assert "nodes" in sql, sql
