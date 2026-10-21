"""A variable-length read from a node named by its id: each reached node counts once, and the start's label holds.

DRKG's D_Q2 (`MATCH (n:Entity {id: $x})-[*1..2]->(m) RETURN count(DISTINCT m)`) answered 61,331 where the graph has
45,993: the BFS route counted its rows, one per node per depth it is reached at, so a node one hop out and two hops
out counted twice. And `:Entity`, a label no DRKG node carries in IVG, did not narrow it: the route took the id and
never read the label. Here: a node reached at depths 1 and 2 and by two paths at depth 2, counted once; a start
without the label, or with one label of two, matching nothing.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
L, OTHER = f"VcA{T}", f"VcB{T}"


def n(k):
    return f"vc{T}_{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=False)
    for k in "abcde":
        e.create_node(n(k), labels=[L], properties={"name": k})
    # a -> b, a -> c, b -> c, c -> d, b -> d: c is one hop and two hops out, d two hops by two paths
    for s, o in (("a", "b"), ("a", "c"), ("b", "c"), ("c", "d"), ("b", "d"), ("d", "e")):
        e.create_edge(n(s), "VC_R", n(o))
    yield e
    for k in "abcde":
        e.delete_node(n(k))


@pytest.mark.parametrize(
    "cypher,want",
    [
        ("MATCH (s:{L} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", 3),
        ("MATCH (s {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", 3),
        ("MATCH (s:{L} {{id: $x}})-[:VC_R*1..3]->(m) RETURN count(DISTINCT m) AS cnt", 4),
        ("MATCH (s:{L} {{id: $x}})-[*1..1]->(m) RETURN count(DISTINCT m) AS cnt", 2),
        # a label the start does not carry, alone or beside its own
        ("MATCH (s:{OTHER} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", 0),
        ("MATCH (s:{L}:{OTHER} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS cnt", 0),
    ],
)
def test_a_variable_length_count_counts_each_node_once(engine, cypher, want):
    r = engine.execute_cypher(cypher.format(L=L, OTHER=OTHER), {"x": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert r.rows == [[want]]


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (s:{OTHER} {{id: $x}})-[*1..2]->(m) RETURN DISTINCT m.id AS id",
        "MATCH (s:{OTHER} {{id: $x}})-[*1..2]->(m) RETURN DISTINCT m.name AS name",
        "MATCH (s:{OTHER})-[*1..2]->(m) WHERE s.id = $x RETURN count(DISTINCT m) AS cnt",
    ],
)
def test_a_start_without_the_label_matches_nothing(engine, cypher):
    r = engine.execute_cypher(cypher.format(L=L, OTHER=OTHER), {"x": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert r.rows in ([], [[0]])


def test_the_same_reads_with_the_label_find_the_nodes(engine):
    # the label check narrows only where the label is missing: the start's own label finds b, c and d
    for cypher in (
        "MATCH (s:{L} {{id: $x}})-[*1..2]->(m) RETURN DISTINCT m.id AS id",
        "MATCH (s:{L})-[*1..2]->(m) WHERE s.id = $x RETURN DISTINCT m.id AS id",
    ):
        r = engine.execute_cypher(cypher.format(L=L, OTHER=OTHER), {"x": n("a")})
        assert sorted(row[0] for row in r.rows) == [n("b"), n("c"), n("d")], cypher
