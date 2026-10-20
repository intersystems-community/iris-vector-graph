"""Spec 253: an aggregate over an aggregate, natively (`MATCH ... WITH keys, count(..) AS n [WHERE n op number]
RETURN ...`).

The first stage is the chain's grouped aggregate, whose keys may be whole nodes (their identity) as well as node
properties; the plan's "stage2" groups its rows again: a WHERE on its columns against numbers, then the RETURN's
grouping keys, counts, ORDER BY and LIMIT over the first stage's columns.
"""

import pytest

from iris_vector_graph.cypher.native_plan import plan_query
from iris_vector_graph.cypher.parser import parse_query

BI12 = (
    "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n "
    "RETURN n AS postCount, count(p) AS people ORDER BY people DESC, postCount LIMIT 10"
)
BI13 = "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n WHERE n < 3 RETURN count(p)"


def _plan(cypher):
    return plan_query(parse_query(cypher), {})


def test_the_first_stage_groups_by_the_node_itself():
    p = _plan(BI12)
    assert p["agg"]["keys"] == [[0, "__node"]]
    assert p["agg"]["aggs"] == [{"kind": "count"}]
    assert p["agg"]["cols"] == [["k", 0], ["a", 0]]


def test_the_second_stage_groups_the_first_stage_s_rows():
    p = _plan(BI12)
    assert p["stage2"] == {
        "where": [],
        "keys": [1],
        "aggs": [{"kind": "count"}],
        "cols": [["k", 0], ["a", 0]],
        "order": [[1, True], [0, False]],
    }
    assert p["width"] == 2 and p["limit"] == 10 and p["ordered"] is True


def test_a_filter_on_the_first_stage_s_count():
    p = _plan(BI13)
    assert p["stage2"]["where"] == [[1, "<", 3]]
    assert p["stage2"]["keys"] == [] and p["stage2"]["cols"] == [["a", 0]]
    assert p["width"] == 1 and p["ordered"] is False


def test_count_distinct_over_a_first_stage_column():
    p = _plan("MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p.city AS c, count(m) AS n RETURN count(DISTINCT c)")
    assert p["agg"]["keys"] == [[0, "city"]]
    assert p["stage2"]["aggs"] == [{"kind": "count_distinct", "col": 0}]


@pytest.mark.parametrize(
    "cypher",
    [
        # the WITH orders, limits or de-duplicates: not a plain grouping
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n ORDER BY n LIMIT 3 RETURN count(p)",
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH DISTINCT p, count(m) AS n RETURN count(p)",
        # a WITH without an aggregate
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, m RETURN count(p)",
        # a RETURN of a whole node, or of a name the WITH does not carry
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n RETURN p, n",
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n RETURN m.sid, n",
        # a second-stage function other than count, a filter against a string, a filter on a key
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n RETURN sum(n)",
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p, count(m) AS n WHERE n < '3' RETURN count(p)",
        "MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) WITH p.city AS c, count(m) AS n WHERE c < 3 RETURN count(c)",
    ],
)
def test_the_planner_declines_these_pipelines(cypher):
    assert _plan(cypher) is None


def test_a_single_stage_query_has_no_second_stage():
    assert "stage2" not in _plan("MATCH (p:Person)<-[:HAS_CREATOR]-(m:Post) RETURN p.sid, count(m)")
