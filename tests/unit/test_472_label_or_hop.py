"""4.7.2: an OR of labels on a hop's far end.

An outside run measured `MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test` at 2.0 s against 0.027 s without the label
test. The OR of one `EXISTS` per label planned as a scan of the label table per row; one `EXISTS ... label IN (...)`
plans as the test without the OR does (live, tests/e2e/test_472_label_or_hop_e2e.py). And the pattern form
`(a)-[:R]->(b:Req|Test)` ANDed its labels on a far end an earlier hop had registered, so it answered no rows where
a node holds one of them.

These tests read the SQL the translator gives; the answers are checked live.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def sql_params(q, params=None):
    t = translate_to_sql(parse_query(q), params or {})
    sql = t.sql if isinstance(t.sql, str) else "\n".join(t.sql)
    return sql, list(t.parameters[0])


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id",
        "MATCH (a:Doc)-[:REFERS]->(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id",
        "MATCH (a:Doc)<-[:REFERS]-(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id",
    ],
)
def test_where_or_of_labels_is_one_exists_with_in(q):
    sql, params = sql_params(q)
    assert "label IN (?, ?)" in sql, sql
    assert sql.count("_lp.label") == 1, sql  # not one EXISTS per label
    assert params[-2:] == ["Req", "Test"], params


def test_where_or_of_three_labels_nested_or_is_flattened():
    sql, params = sql_params("MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test OR b:Spec RETURN b.node_id")
    assert "label IN (?, ?, ?)" in sql, sql
    assert params[-3:] == ["Req", "Test", "Spec"], params


def test_or_that_is_not_only_labels_keeps_its_branches():
    sql, _ = sql_params("MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b.name = 'x' RETURN b.node_id")
    assert "IN (?" not in sql.split("WHERE", 1)[1], sql


def test_or_of_labels_on_two_variables_keeps_its_branches():
    sql, _ = sql_params("MATCH (a:Doc)-[:REFERS]-(b) WHERE a:Spec OR b:Test RETURN b.node_id")
    assert sql.count("_lp.label = ?") == 2, sql


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:Doc)-[:REFERS]->(b:Req|Test) RETURN a.node_id, b.node_id",
        "MATCH (a:Doc)<-[:REFERS]-(b:Req|Test) RETURN a.node_id, b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b:Req|Test) RETURN a.node_id, b.node_id",
    ],
)
def test_pattern_label_or_on_a_far_end_is_one_test_for_any(q):
    sql, params = sql_params(q)
    # the labels are not each a join of their own (that is `Req AND Test`)
    far_label_joins = re.findall(r"JOIN \S*rdf_labels l\d+ ON l\d+\.s = n2\.node_id[^\n]*label = \?", sql)
    assert far_label_joins == [], sql
    assert re.search(r"label IN \(\?, \?\)", sql), sql
    assert params[-2:] == ["Req", "Test"], params


def test_pattern_label_and_still_joins_each_label():
    sql, params = sql_params("MATCH (a:Doc)-[:REFERS]->(b:Req:Test) RETURN b.node_id")
    assert "IN (?" not in sql.split("WHERE", 1)[-1], sql
    assert params[-2:] == ["Req", "Test"], params


def test_optional_far_end_label_or_goes_into_the_edge_join():
    sql, params = sql_params(
        "MATCH (a:Doc) OPTIONAL MATCH (a)-[:REFERS]->(b:Req|Test) RETURN a.node_id, b.node_id"
    )
    assert re.search(r"label IN \(\?, \?\)", sql), sql
    assert params[-2:] == ["Req", "Test"], params
    # inside the ON of the edge join, so a Doc with no such neighbour still gives its row of nulls
    edge_join = next(line for line in sql.splitlines() if "rdf_edges" in line and "JOIN" in line)
    assert "label IN" in edge_join, sql


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:Doc)-[:REFERS]-(b) WHERE b:Req OR b:Test RETURN a.node_id, b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b) WHERE NOT (b:Req OR b:Test) RETURN b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b) WHERE (b:Req OR b:Test) AND a.name = 'x' RETURN b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b) WHERE a.name = 'x' AND (b:Req OR b:Test) RETURN b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b) RETURN b:Req OR b:Test AS f, b.node_id",
        "MATCH (a:Doc)-[:REFERS]-(b) WITH a, b WHERE b:Req OR b:Test RETURN count(*)",
        "MATCH (a:Doc) OPTIONAL MATCH (a)-[:REFERS]->(b:Req|Test) WHERE b.name <> 'x' RETURN a.node_id, b.node_id",
    ],
)
def test_every_marker_has_its_parameter_in_order(q):
    """The label values are bound where they are written: one `?` for each parameter, in the SQL's order."""
    sql, params = sql_params(q)
    assert sql.count("?") == len(params), (sql, params)
    labels = [p for p in params if p in ("Req", "Test")]
    # each place the test is written binds Req then Test (a hop split in two branches, or a SELECT and a WHERE, write it twice)
    assert labels and labels == ["Req", "Test"] * (len(labels) // 2), params


def test_standalone_label_or_keeps_a_node_holding_two_labels_once():
    sql, _ = sql_params("MATCH (b:Req|Test) RETURN b.node_id")
    assert "label IN ('Req', 'Test')" in sql, sql
    assert "NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels _ld" in sql, sql
