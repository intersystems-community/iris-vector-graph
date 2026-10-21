"""USE GRAPH over a node crossed in (`MATCH (a), (b)`), with the same ids held by another graph: one row, one edge."""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G1, G2 = f"cj{T}_1", f"cj{T}_2"


def n(k):
    return f"cj{T}_{k}"


@pytest.fixture
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for g in (G1, G2):
        for k in "ab":
            e.create_node(n(k), labels=[f"Cj{T}"], graph=g)
    yield e
    for g in (G1, G2):
        e.erase_graph(g)


def test_the_pair_is_read_in_the_graph_named(engine):
    q = f"USE GRAPH '{G1}' MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) RETURN count(*) AS c"
    assert engine.execute_cypher(q, {"a": n("a"), "b": n("b")}).rows == [[1]]


def test_a_create_over_the_pair_makes_one_edge(engine, iris_connection):
    q = f"USE GRAPH '{G1}' MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) CREATE (a)-[:CJ_R]->(b)"
    r = engine.execute_cypher(q, {"a": n("a"), "b": n("b")})
    assert getattr(r, "error", None) is None, r.error
    cur = iris_connection.cursor()
    cur.execute("SELECT graph_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'CJ_R'", [n("a")])
    assert [row[0] for row in cur.fetchall()] == [G1]
