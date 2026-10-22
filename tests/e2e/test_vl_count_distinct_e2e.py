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


@pytest.fixture(scope="module")
def looped(iris_connection):
    """a with three neighbours and a self-loop: a-a-b, a-a-c, a-a-d are its only two-edge trails (a relationship once
    per path), so `*2..2` ends at b, c and d and never back at a."""
    e = IRISGraphEngine(iris_connection, native_cypher=False)
    for k in "abcd":
        e.create_node(n(f"l{k}"), labels=[L])
    for o, p in (("b", "VC_R"), ("c", "VC_R"), ("d", "VC_S"), ("a", "VC_S")):
        e.create_edge(n("la"), p, n(f"l{o}"))
    yield e
    for k in "abcd":
        e.delete_node(n(f"l{k}"))


@pytest.mark.parametrize("arrow", ["-", "->"], ids=["undirected", "outgoing"])
@pytest.mark.parametrize("end", ["(m)", "(m:{L})"], ids=["any end", "labelled end"])
def test_a_minimum_of_two_hops_is_counted_as_listed(looped, arrow, end):
    # BFSFastCountDistinct counted every node within two hops, the start too by its self-loop (4); the ends at exactly
    # two hops are the three DISTINCT lists
    q = f"MATCH (s:{L} {{id: $x}})-[*2..2]{arrow}{end.format(L=L)}"
    count = looped.execute_cypher(q + " RETURN count(DISTINCT m) AS cnt", {"x": n("la")})
    listed = looped.execute_cypher(q + " RETURN DISTINCT m.id AS m", {"x": n("la")})
    assert getattr(count, "error", None) is None and getattr(listed, "error", None) is None
    assert sorted(r[0] for r in listed.rows) == [n("lb"), n("lc"), n("ld")]
    assert count.rows == [[3]]


@pytest.fixture(scope="module")
def gated(iris_connection):
    """a -R{w:1}-> b (also :B), a -R{w:2}-> c, c -R{w:1}-> d; and in graph G2 only, d -R-> a."""
    e = IRISGraphEngine(iris_connection, native_cypher=False)
    for k in "abcd":
        e.create_node(n(f"g{k}"), labels=[L] + ([OTHER] if k == "b" else []))
    for s, o, w in (("a", "b", 1), ("a", "c", 2), ("c", "d", 1)):
        e.create_edge(n(f"g{s}"), "VC_R", n(f"g{o}"), qualifiers={"w": w})
    for k in "ad":
        e.create_node(n(f"g{k}"), labels=[L], graph=G2)
    e.create_edge(n("gd"), "VC_R", n("ga"), graph=G2)
    yield e
    for k in "abcd":
        e.delete_node(n(f"g{k}"))
    e.erase_graph(G2)


G2 = f"vc{T}_g2"


@pytest.mark.parametrize(
    "cypher,params,want",
    [
        # the start of an incoming hop, named like a parameter: m is the far end and must carry B (a does not)
        ("MATCH (x)<-[*1..1]-(m:{OTHER}) RETURN count(DISTINCT m) AS cnt", {"x": "gc"}, 0),
        # a relationship property on the hop
        ("MATCH (s {{id: $x}})-[*1..1 {{w: 1}}]->(m) RETURN count(DISTINCT m) AS cnt", {"x": "ga"}, 1),
        # a label on a start pinned by node_id, which a does not carry
        ("MATCH (s:{OTHER} {{node_id: $x}})-[*1..1]->(m) RETURN count(DISTINCT m) AS cnt", {"x": "ga"}, 0),
        # a fixed hop after the far end: only c goes on
        ("MATCH (s {{id: $x}})-[*1..1]->(m)-[:VC_R]->(k) RETURN count(DISTINCT m) AS cnt", {"x": "ga"}, 1),
        # a second pattern labelling the far end
        ("MATCH (s {{id: $x}})-[*1..1]->(m), (m:{OTHER}) RETURN count(DISTINCT m) AS cnt", {"x": "ga"}, 1),
        # one graph's edges only: in G2, d -> a is the whole graph
        ("USE GRAPH '{G2}' MATCH (s {{id: $x}})-[*2..2]->(m) RETURN count(DISTINCT m) AS cnt", {"x": "gd"}, 0),
        ("MATCH (s {{id: $x}})-[*2..2]->(m) RETURN count(DISTINCT m) AS cnt", {"x": "ga"}, 1),
    ],
    ids=["incoming start", "relationship property", "start label", "hop after", "second pattern", "named graph",
         "default graph"],
)
def test_the_count_routes_keep_every_check(gated, cypher, params, want):
    r = gated.execute_cypher(cypher.format(L=L, OTHER=OTHER, G2=G2), {k: n(v) for k, v in params.items()})
    assert getattr(r, "error", None) is None, r.error
    assert r.rows == [[want]]
