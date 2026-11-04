"""4.6.0 R226 and R260: a WITH stage keeps each row in its own graph (offline: the SQL that does it).

CONTEXT.md, All-graphs read: without USE GRAPH the answer is the union of each graph's own answer, and the same node
id in two graphs is two nodes. A WITH stage projected node ids alone and its own reads were never correlated, so a
read after the stage met every graph's rows of an id, and an aggregate counted two graphs' nodes of one id as one.
These pin the translation, with a stand-in engine reporting a namespace of several graphs; the answers worked out by
hand on a live namespace are in tests/e2e/test_460_r226_with_stage_graph_e2e.py.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


class _Engine:
    _schema_prefix = "Graph_KG"

    def __init__(self, several=True):
        self.several = several
        self.probes = 0

    def _more_than_one_graph(self):
        self.probes += 1
        return self.several

    def _graphs_with_edges(self):
        return ["", "g1"] if self.several else [""]

    def _named_graph_with_edges(self):
        return "g1" if self.several else None

    def get_table_mapping(self, label):
        return None


def _sql(query: str, engine=None) -> str:
    return translate_to_sql(parse_query(query), {"a": "x"}, engine=engine or _Engine()).sql


def _stage(sql: str, n: int = 1) -> str:
    """The body of Stage<n>, as a CTE or inlined as a derived table."""
    m = re.search(rf"Stage{n} AS \(\n(.*?)\n\)", sql, re.S) or re.search(rf"\((SELECT .*?)\) Stage{n}\b", sql, re.S)
    assert m, sql
    return m.group(1)


def test_r260_a_stage_s_own_reads_join_in_the_row_s_graph():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH y.name AS name, count(*) AS c RETURN name, c")
    body = _stage(sql)
    assert "e2.graph_id = n0.graph_id" in body, sql
    assert "n1.graph_id = e2.graph_id" in body, sql
    assert "p3.graph_id = n1.graph_id" in body, sql


def test_a_stage_carries_each_node_s_graph():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH x, y RETURN y.name")
    body = _stage(sql)
    assert "n0.graph_id AS __g_x" in body and "n1.graph_id AS __g_y" in body, sql


def test_a_property_read_after_the_stage_is_in_the_row_s_graph():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH y RETURN y.name")
    assert re.search(r"ON p\d+\.s = y AND p\d+\.graph_id = Stage1\.__g_y\b", sql), sql


def test_a_hop_from_a_carried_node_is_in_the_row_s_graph():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH y MATCH (y)-[:K]->(z) RETURN z.name")
    assert re.search(r"ON e\d+\.s = Stage1\.y AND e\d+\.graph_id = Stage1\.__g_y\b", sql), sql


def test_labels_of_a_carried_node_are_its_graph_s():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH y RETURN labels(y)")
    assert re.search(r"WHERE s = (?:Stage1\.)?y AND graph_id = Stage1\.__g_y\b", sql), sql


def test_an_aggregate_stage_groups_per_graph_and_node():
    sql = _sql("MATCH (x:P)-[:K]->(y) WITH y, count(*) AS c RETURN y.name, c")
    assert re.search(r"GROUP BY n2\.node_id, n2\.graph_id\b", _stage(sql)), sql


def test_a_second_stage_forwards_the_graph():
    sql = _sql("MATCH (x {node_id: $a})-[:K]->(y) WITH y WITH y AS z RETURN z.name")
    assert "Stage1.__g_y AS __g_z" in _stage(sql, 2), sql
    assert re.search(r"p\d+\.graph_id = Stage2\.__g_z\b", sql), sql


def test_a_procedure_row_s_graph_goes_through_the_stage():
    sql = _sql(
        "CALL ivg.degreeCentrality({}) YIELD node, degree WITH node, degree WHERE degree > 0 "
        "RETURN node.name, degree"
    )
    assert "COALESCE(DegCent.graph_id, '') AS __g_node" in _stage(sql), sql
    assert re.search(r"ON p\d+\.s = node AND p\d+\.graph_id = Stage1\.__g_node\b", sql), sql


@pytest.mark.parametrize("query", [
    "MATCH (x:P)-[:K]->(y) RETURN count(DISTINCT y)",
    "MATCH (x:P)-[:K]->(y) WITH count(DISTINCT y) AS c RETURN c",
])
def test_count_distinct_of_a_node_counts_graph_and_id(query):
    sql = _sql(query)
    assert "COUNT(DISTINCT %EXACT(n2.graph_id || CHAR(1) || n2.node_id))" in sql, sql


def test_return_node_and_an_aggregate_group_per_graph_and_node():
    sql = _sql("MATCH (x:P)-[:K]->(y) RETURN y, count(*) AS c")
    assert re.search(r"GROUP BY n2\.node_id, n2\.graph_id\b", sql), sql


def test_under_use_graph_nothing_is_carried():
    sql = _sql("USE GRAPH 'g1' MATCH (x:P)-[:K]->(y) WITH y, count(DISTINCT x) AS c RETURN y.name, c")
    assert "__g_" not in sql and "CHAR(1)" not in sql, sql


def test_in_a_namespace_of_one_graph_nothing_is_carried_and_the_probe_runs_once():
    engine = _Engine(several=False)
    sql = _sql("MATCH (x:P)-[:K]->(y) WITH y, count(DISTINCT x) AS c WITH y, c RETURN y.name, c", engine)
    assert "__g_" not in sql and "CHAR(1)" not in sql, sql
    assert "graph_id" not in _stage(sql), sql
    several = _Engine()
    _sql("MATCH (x:P)-[:K]->(y) WITH y, count(DISTINCT x) AS c WITH y, c RETURN y.name, count(DISTINCT y)", several)
    # the stage passes and the aggregates ask the namespace once per translation, not once each
    assert several.probes <= 2, several.probes
