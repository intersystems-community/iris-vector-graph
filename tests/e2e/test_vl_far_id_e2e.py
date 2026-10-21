"""A variable-length hop whose far node is named by its id, on the SQL path.

The translator handed every pattern with an id on either end to the engine's BFS routes, and those read only the
start: `(s {id: $a})-[:R*1..3]->(t {id: $b}) RETURN count(DISTINCT t)` answered every node within three hops (4 here)
instead of 1, `RETURN count(*)` came back as BFS's raw (id, hops, pred) rows, and `(s:L)-[*]->(t {node_id: $b})` counted
every reachable node as a start. A far node pinned by id now goes to CY_VLP_PATHS, which runs the statement as SQL and
holds t to its id; a start pinned with a free far end keeps the BFS route.

Graph: a -> b -> c, a -> c, c -> d -> e. From a, c is one hop (a -> c) and two (a -> b -> c) away: two paths.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
L = f"Vf{T}"


def n(k):
    return f"vf{T}_{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=False)
    for k in "abcde":
        e.create_node(n(k), labels=[L], properties={"name": k})
    for s, o in (("a", "b"), ("b", "c"), ("a", "c"), ("c", "d"), ("d", "e")):
        e.create_edge(n(s), "VF_R", n(o))
    yield e
    for k in "abcde":
        e.delete_node(n(k))


CASES = [
    ("paths between two ids", "MATCH (s {{id: $a}})-[:VF_R*1..2]->(t {{id: $b}}) RETURN count(*) AS x", [[2]]),
    ("a reachable id", "MATCH (s {{id: $a}})-[:VF_R*1..3]->(t {{id: $b}}) RETURN count(DISTINCT t) AS x", [[1]]),
    ("an unreachable id", "MATCH (s {{id: $b}})-[:VF_R*1..3]->(t {{id: $a}}) RETURN count(DISTINCT t) AS x", [[0]]),
    ("node_id on both ends", "MATCH (s {{node_id: $a}})-[:VF_R*1..2]->(t {{node_id: $b}}) RETURN count(*) AS x", [[2]]),
    ("the starts reaching a node_id", "MATCH (s:{L})-[:VF_R*1..2]->(t {{node_id: $b}}) RETURN count(DISTINCT s) AS x", [[2]]),
    ("the starts reaching an id", "MATCH (s:{L})-[:VF_R*1..2]->(t:{L} {{id: $b}}) RETURN s.name AS x ORDER BY x", [["a"], ["a"], ["b"]]),
    ("a far id pinned in WHERE", "MATCH (s:{L})-[:VF_R*1..2]->(t) WHERE t.id = $b RETURN DISTINCT s.name AS x ORDER BY x", [["a"], ["b"]]),
    ("the far node's property", "MATCH (s {{id: $a}})-[:VF_R*1..3]->(t {{id: $b}}) RETURN DISTINCT t.name AS x", [["c"]]),
]


@pytest.mark.parametrize("name,cypher,want", CASES, ids=[c[0] for c in CASES])
def test_a_far_id_holds_on_the_sql_path(engine, name, cypher, want):
    r = engine.execute_cypher(cypher.format(L=L), {"a": n("a"), "b": n("c")})
    assert getattr(r, "error", None) is None, r.error
    assert [list(row) for row in r.rows] == want


def test_a_start_id_with_a_free_far_end_keeps_its_answer(engine):
    r = engine.execute_cypher("MATCH (s {id: $a})-[:VF_R*1..2]->(t) RETURN count(DISTINCT t) AS x", {"a": n("a")})
    assert r.rows == [[3]]  # b, c, d
