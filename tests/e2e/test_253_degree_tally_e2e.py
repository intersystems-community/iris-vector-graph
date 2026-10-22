"""Spec 253: an outgoing tallied hop whose far node is checked for nothing is counted from the degree cache.

DRKG's D_Q1 (`MATCH (s:Entity {id: $x})-[r]->(n) RETURN count(r)`) walked the hub's 23,866 adjacency entries to count
them: 9 ms in the executor, where Memgraph answers 1.1 ms from a stored degree. Graph.KG.Adj.Degree keeps each source's
out-degree in a cache validated by its generation, counting a multigraph's parallel edges, which is what the tally counts.
In shadow mode, against the SQL path: untyped, typed and two-typed hops; a multigraph's parallel edges; and the shapes the
cache cannot answer (a far node with a label or a property, an incoming hop, count(DISTINCT)), which walk as before.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]
G, GM = f"dt{T}_g", f"dt{T}_m"
E = f"DtE{T}"


def n(k):
    return f"dt{T}_{k}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    e.set_multigraph(GM, True)
    for g in (G, GM):
        for k in "abcd":
            e.create_node(n(k), labels=[E], properties={"kind": "x" if k == "b" else "y"}, graph=g)
        for o, p in (("b", "DT_R"), ("c", "DT_R"), ("d", "DT_S"), ("a", "DT_S")):  # a self-loop too
            e.create_edge(n("a"), p, n(o), graph=g)
    # a parallel edge: in multigraph mode a second CREATE of (a, DT_R, b) is a second edge (create_edge keeps one)
    r = e.execute_cypher(f"USE GRAPH '{GM}' MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) CREATE (a)-[:DT_R]->(b)", {"a": n("a"), "b": n("b")})
    assert getattr(r, "error", None) is None, r.error
    # the cache as bulk_load_session leaves it (WarmCache); a read never fills it, so a cold source walks
    import iris

    for g in (G, GM):
        iris.createIRIS(iris_connection).classMethodValue("Graph.KG.Adj", "WarmAll", g)
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for g in (G, GM):
        e.erase_graph(g)
    e.set_multigraph(GM, False)


def _degree_runs(conn) -> int:
    import iris

    native = iris.createIRIS(conn)
    native.classMethodVoid("User.Exec", "Run", "Set ^IVGTestDegreeRuns=+$Get(^||IVG.CypherDegreeTally) Kill ^||IVG.CypherDegreeTally")
    runs = int(native.get("IVGTestDegreeRuns") or 0)
    native.kill("IVGTestDegreeRuns")
    return runs


FROM_DEGREE = [
    ("untyped", "MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS c"),
    ("typed", "MATCH (s:{E} {{id: $x}})-[:DT_R]->(m) RETURN count(*) AS c"),
    ("two types", "MATCH (s:{E} {{id: $x}})-[:DT_R|DT_S]->(m) RETURN count(m) AS c"),
    ("grouped by the anchor", "MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN s.kind AS k, count(r) AS c"),
]
WALKED = [
    ("a far label", "MATCH (s:{E} {{id: $x}})-[r]->(m:{E}) RETURN count(r) AS c"),
    ("a far property", "MATCH (s:{E} {{id: $x}})-[r]->(m {{kind: 'x'}}) RETURN count(r) AS c"),
    ("incoming", "MATCH (s:{E} {{id: $x}})<-[r]-(m) RETURN count(r) AS c"),
    ("distinct", "MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(DISTINCT m) AS c"),
]


@pytest.mark.parametrize("g", [G, GM], ids=["plain", "multigraph"])
@pytest.mark.parametrize("name,cypher", FROM_DEGREE, ids=[c[0] for c in FROM_DEGREE])
def test_a_tally_from_the_degree_answers_as_sql(engine, iris_connection, g, name, cypher):
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(f"USE GRAPH '{g}' " + cypher.format(E=E), {"x": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
    assert _degree_runs(iris_connection) > 0, "the tally walked the adjacency"


@pytest.mark.parametrize("g", [G, GM], ids=["plain", "multigraph"])
@pytest.mark.parametrize("name,cypher", WALKED, ids=[c[0] for c in WALKED])
def test_a_tally_the_degree_cannot_answer_still_answers_as_sql(engine, iris_connection, g, name, cypher):
    _degree_runs(iris_connection)
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(f"USE GRAPH '{g}' " + cypher.format(E=E), {"x": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
    assert _degree_runs(iris_connection) == 0, "the degree answered a tally it cannot"


def test_a_source_whose_cache_is_stale_walks_and_writes_nothing(engine, iris_connection):
    # a new edge moves the source's generation: its cached degree no longer holds, so the tally walks, and the read
    # leaves the cache as it found it (the degree reader would have stored a fresh entry: a write, inside a read)
    import iris

    native = iris.createIRIS(iris_connection)
    e2 = n("e")
    engine.create_node(e2, labels=[E], graph=G)
    engine.create_edge(e2, "DT_R", n("a"), graph=G)
    _degree_runs(iris_connection)
    r = engine.execute_cypher(f"USE GRAPH '{G}' MATCH (s:{E} {{id: $x}})-[r]->(m) RETURN count(r) AS c", {"x": e2})
    assert r.rows == [[1]]
    assert _degree_runs(iris_connection) == 0
    assert not native.isDefined("^KG", "degc", G, e2)


def test_the_multigraph_counts_the_parallel_edge(engine):
    r = engine.execute_cypher(f"USE GRAPH '{GM}' MATCH (s:{E} {{id: $x}})-[:DT_R]->(m) RETURN count(*) AS c", {"x": n("a")})
    assert r.rows == [[3]]


def _ends_runs(conn) -> int:
    import iris

    native = iris.createIRIS(conn)
    native.classMethodVoid("User.Exec", "Run", "Set ^IVGTestEndsRuns=+$Get(^||IVG.CypherEndsCount) Kill ^||IVG.CypherEndsCount")
    runs = int(native.get("IVGTestEndsRuns") or 0)
    native.kill("IVGTestEndsRuns")
    return runs


@pytest.mark.parametrize("g", [G, GM], ids=["plain", "multigraph"])
@pytest.mark.parametrize(
    "cypher,counted",
    [
        ("MATCH (s:{E} {{id: $x}})-[*1..2]->(m) RETURN count(DISTINCT m) AS c", True),
        ("MATCH (s:{E} {{id: $x}})-[:DT_R*1..3]->(m) RETURN count(DISTINCT m) AS c", True),
        ("MATCH (s:{E} {{id: $x}})-[*1..2]->(m:{E}) RETURN count(DISTINCT m) AS c", False),  # a checked end walks
        ("MATCH (s:{E} {{id: $x}})-[*0..2]->(m) RETURN count(DISTINCT m) AS c", True),  # the start is an end
    ],
)
def test_a_lone_variable_length_count_counts_the_ends(engine, iris_connection, g, cypher, counted):
    # DRKG D_Q2: the walk's ends are distinct already, so count(DISTINCT end) of an unchecked end is their number; the
    # executor emitted and grouped each of 45,993 ends
    _ends_runs(iris_connection)
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(f"USE GRAPH '{g}' " + cypher.format(E=E), {"x": n("a")})
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1
    assert (_ends_runs(iris_connection) > 0) is counted
