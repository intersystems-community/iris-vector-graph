"""Spec 253, T001 (FR-009): an all-graphs read keeps each node in its own graph.

With no `USE GRAPH` a Cypher read sees every graph, and its answer is the union of each graph's own answer
(CONTEXT.md, All-graphs read): the same node id in two graphs is two nodes, each with its own labels and
properties, and an edge meets only nodes of its own graph. The SQL path joined every alias on node_id alone, so
a node id in two graphs returned each graph's properties for each graph's row, labels unioned, and an edge of g1
reached g2's node of the same id.

Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid
from collections import Counter

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
G1, G2 = f"ag{T}_g1", f"ag{T}_g2"
N, M, K = f"ag{T}_n", f"ag{T}_m", f"ag{T}_k"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.create_node(N, labels=["A"], properties={"name": "a1"}, graph=G1)
    e.create_node(N, labels=["B"], properties={"name": "a2"}, graph=G2)
    e.create_node(M, labels=["M"], properties={"name": "m_g1"}, graph=G1)
    e.create_node(M, labels=["M"], properties={"name": "m_g2"}, graph=G2)
    e.create_node(K, labels=["K"], properties={"name": "k_g2"}, graph=G2)
    e.create_edge(N, "R", M, graph=G1)
    e.create_edge(N, "R", K, graph=G2)
    yield e
    for g in (G1, G2):
        e.erase_graph(g)


def _rows(engine, q, **params):
    r = engine.execute_cypher(q, params or {"x": N})
    assert getattr(r, "error", None) is None, r.error
    return r.rows


def _bag(rows):
    return Counter(tuple(str(v) for v in row) for row in rows)


def test_a_node_id_in_two_graphs_is_two_nodes_with_their_own_properties(engine):
    rows = _rows(engine, "MATCH (n {id: $x}) RETURN n.name")
    assert _bag(rows) == Counter({("a1",): 1, ("a2",): 1})


def test_each_node_has_its_own_graph_s_labels(engine):
    rows = _rows(engine, "MATCH (n {id: $x}) RETURN labels(n) AS l")
    assert sorted(str(r[0]) for r in rows) == sorted(['["A"]', '["B"]'])


def test_an_edge_meets_only_nodes_of_its_own_graph(engine):
    rows = _rows(engine, "MATCH (n {id: $x})-[:R]->(m) RETURN m.name")
    assert _bag(rows) == Counter({("m_g1",): 1, ("k_g2",): 1})


def test_a_count_over_an_all_graphs_hop(engine):
    assert _rows(engine, "MATCH (n {id: $x})-[:R]->(m) RETURN count(*) AS c") == [[2]]


def test_a_label_filter_reads_the_node_s_own_graph(engine):
    rows = _rows(engine, "MATCH (n:A {id: $x}) RETURN n.name")
    assert _bag(rows) == Counter({("a1",): 1})


def test_var_length_from_a_node_in_two_graphs(engine):
    rows = _rows(engine, "MATCH (n)-[:R*1..2]->(m) WHERE n.id = $x RETURN DISTINCT m.name")
    assert _bag(rows) == Counter({("m_g1",): 1, ("k_g2",): 1})


def test_use_graph_is_unchanged(engine):
    rows = engine.execute_cypher(f"USE GRAPH '{G1}' MATCH (n {{id: $x}})-[:R]->(m) RETURN n.name, m.name", {"x": N}).rows
    assert _bag(rows) == Counter({("a1", "m_g1"): 1})


def test_var_length_from_an_inline_id_projects_the_return(engine):
    # T008: `(n {id: $x})-[:R*1..2]->(m) RETURN m.name` answered raw BFS rows (id, hops, pred) instead of m.name
    r = engine.execute_cypher("MATCH (n {id: $x})-[:R*1..2]->(m) RETURN DISTINCT m.name", {"x": N})
    assert r.columns == ["m.name"], r.columns
    assert _bag(r.rows) == Counter({("m_g1",): 1, ("k_g2",): 1})
