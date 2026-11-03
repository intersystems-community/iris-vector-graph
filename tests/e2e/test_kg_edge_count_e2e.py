"""4.6.0 (NR7): `status().adjacency.kg_edge_count` counts edges, as ADMIN_GUIDE §7 says, not source nodes.

14 edges from 13 sources: node 0 has two edges out, a parallel pair to node 1 under two predicates; nodes 1..12 have
one each. Before 4.6.0 `Graph.KG.TraversalBuild.KGEdgeCount` added one per source in ^KG("out"), so the 14 edges
counted 13. A multigraph's parallel edges under one predicate count once each.

The namespace may hold other rows, so the counts are read before and after.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]


def _counts(engine, conn):
    adjacency = engine.status().adjacency
    assert not adjacency.kg_edge_count_capped, "namespace too big for an exact count"
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
        sql_edges = int(cur.fetchone()[0])
    finally:
        cur.close()
    return adjacency.kg_edge_count, sql_edges


def test_fourteen_edges_from_thirteen_sources_count_fourteen(iris_connection):
    e = IRISGraphEngine(iris_connection)
    ids = [f"ec{T}:{i}" for i in range(14)]
    before_kg, before_sql = _counts(e, iris_connection)
    try:
        e.bulk_create_nodes([{"id": n, "labels": [f"Ec{T}"]} for n in ids])
        edges = [(ids[0], "A", ids[1]), (ids[0], "B", ids[1])]  # the parallel pair out of node 0
        edges += [(ids[i], "A", ids[i + 1]) for i in range(1, 13)]
        e.bulk_create_edges([{"source_id": s, "predicate": p, "target_id": o} for s, p, o in edges])
        after_kg, after_sql = _counts(e, iris_connection)
        assert after_sql - before_sql == 14
        assert after_kg - before_kg == 14
        assert after_kg == after_sql
    finally:
        for n in ids:
            e.delete_node(n)


def test_a_multigraph_counts_each_parallel_edge(iris_connection):
    e = IRISGraphEngine(iris_connection)
    g = f"ec{T}multi"
    e.set_multigraph(g, True)
    try:
        before_kg, before_sql = _counts(e, iris_connection)
        e.create_node(f"ec{T}:m0", graph=g)
        e.create_node(f"ec{T}:m1", graph=g)
        e.execute_cypher(
            f"USE GRAPH {g} MATCH (a {{node_id: 'ec{T}:m0'}}), (b {{node_id: 'ec{T}:m1'}}) "
            "CREATE (a)-[:R]->(b), (a)-[:R]->(b), (a)-[:R]->(b)"
        )
        after_kg, after_sql = _counts(e, iris_connection)
        assert after_sql - before_sql == 3
        assert after_kg - before_kg == 3
    finally:
        e.erase_graph(g)
        e.set_multigraph(g, False)
