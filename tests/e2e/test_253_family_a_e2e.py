"""Spec 253, T020 (family A, first slice): anchored fixed-hop patterns answered natively, held equal to SQL.

Each query runs in shadow mode (FR-006): the engine answers natively and also on the SQL path, and raises on any
difference. Each case also asserts the native path answered (the planner did not decline), so a planner that quietly
declined everything would fail here rather than pass by agreeing with itself.

The fixture is a small SNB-shaped graph in the default graph; the multigraph case uses a named graph.
Run against `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]
GM = f"fa{T}_gm"


def n(x):
    return f"fa{T}_{x}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    people = {"p1": ("Ann", "Lee"), "p2": ("Bo", "Lee"), "p3": ("Cy", "Moe"), "p4": ("Di", None)}
    for k, (first, last) in people.items():
        props = {"sid": n(k), "firstName": first}
        if last:
            props["lastName"] = last
        e.create_node(n(k), labels=["FaPerson"], properties=props)
    for k, date in (("m1", "2020-01-02"), ("m2", "2021-05-06"), ("m3", "2019-12-31")):
        e.create_node(n(k), labels=["FaPost"], properties={"sid": n(k), "creationDate": date})
    e.create_node(n("f1"), labels=["FaForum"], properties={"sid": n("f1"), "title": "Forum one"})
    for a, b in (("p1", "p2"), ("p3", "p1"), ("p1", "p4")):
        e.create_edge(n(a), "FA_KNOWS", n(b))
    for m in ("m1", "m2", "m3"):
        e.create_edge(n(m), "FA_HAS_CREATOR", n("p1"))
        e.create_edge(n("f1"), "FA_CONTAINER_OF", n(m))
    e.create_edge(n("f1"), "FA_HAS_MODERATOR", n("p3"))
    e.set_multigraph(GM, True)
    for k in ("x", "y"):
        e.create_node(n(k), labels=["FaM"], properties={"name": k}, graph=GM)
    cur = iris_connection.cursor()
    for ek in (0, 1):
        cur.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, ekey) VALUES (?, 'FA_R', ?, ?, ?)", [n("x"), n("y"), GM, ek]
        )
    iris_connection.commit()
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for k in list(people) + ["m1", "m2", "m3", "f1"]:
        e.delete_node(n(k))
    e.erase_graph(GM)
    e.set_multigraph(GM, False)


CASES = [
    ("profile", "MATCH (p:FaPerson) WHERE p.sid = $id RETURN p.firstName, p.lastName", {"id": "p1"}),
    ("inline map", "MATCH (p:FaPerson {sid: $id}) RETURN p.firstName", {"id": "p2"}),
    ("missing property is null", "MATCH (p:FaPerson) WHERE p.sid = $id RETURN p.lastName", {"id": "p4"}),
    ("no match", "MATCH (p:FaPerson) WHERE p.sid = $id RETURN p.firstName", {"id": "nobody"}),
    (
        "incoming hop, order, limit",
        "MATCH (p:FaPerson)<-[:FA_HAS_CREATOR]-(m:FaPost) WHERE p.sid = $id "
        "RETURN m.sid, m.creationDate ORDER BY m.creationDate DESC, m.sid LIMIT 2",
        {"id": "p1"},
    ),
    (
        "undirected hop ordered",
        "MATCH (p:FaPerson)-[:FA_KNOWS]-(f:FaPerson) WHERE p.sid = $id RETURN f.sid, f.firstName ORDER BY f.sid",
        {"id": "p1"},
    ),
    (
        "two-hop chain",
        "MATCH (m:FaPost)<-[:FA_CONTAINER_OF]-(f:FaForum)-[:FA_HAS_MODERATOR]->(mod:FaPerson) WHERE m.sid = $id "
        "RETURN f.sid, f.title, mod.sid, mod.firstName",
        {"id": "m2"},
    ),
    ("distinct", "MATCH (p:FaPerson)-[:FA_KNOWS]-(f) WHERE p.sid = $id RETURN DISTINCT f.lastName", {"id": "p1"}),
    ("outgoing hop", "MATCH (p:FaPerson)-[:FA_KNOWS]->(f) WHERE p.sid = $id RETURN f.sid", {"id": "p1"}),
]


@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_family_a_native_equals_sql(engine, name, cypher, params):
    params = {k: (n(v) if v != "nobody" else v) for k, v in params.items()}
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(cypher, params)  # shadow mode raises NativeMismatch on any difference
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"


def test_parallel_edges_of_a_multigraph_are_separate_rows(engine):
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(f"USE GRAPH '{GM}' MATCH (a:FaM)-[:FA_R]->(b) WHERE a.name = 'x' RETURN b.name")
    assert sorted(x[0] for x in r.rows) == ["y", "y"]
    assert engine.__dict__.get("_native_answers", 0) == before + 1
