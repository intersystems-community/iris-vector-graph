"""4.6.0 R210: `SET r.p = r.q`, `SET r.p = n.x` and `SET n.p = r.w` write what they read.

Refused with SyntaxError since R187 (SQLCODE -23 before). Each case builds, with a Cypher CREATE and labels unique to
the run, `(a {id: 'a', x: 5, s: '007'})-[:T {q: 7, w: 0.5, str: '007', b: true}]->(b {id: 'b'})` and
`(c {id: 'c', x: 9})-[:T {q: 8}]->(b)`. Worked by hand, per edge (from a, from c):

- `SET r.p = r.q`: p 7 and 8 (numbers); `SET r.p2 = r.str`: '007' (a string, not 7), absent on c's edge;
  `SET r.pb = r.b`: true; `SET r.z = r.missing` after `SET r.z = 1`: z gone
- `SET r += {p: r.q, k: 1}`: p 7 and 8, k 1
- `MATCH (n)-[r:T]->() SET r.nx = n.x`: 5 and 9 (each edge its own source); `SET r.ns = n.s`: '007', absent on c's
- `MATCH (n)-[r:T]->() SET n.rw = r.w`: a 0.5, c none (its edge has no w); `SET n.rq = r.q`: a 7, c 8;
  `SET n.rs = r.str`: a '007'; `SET n += {rq2: r.q}`: a 7, c 8
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    label, t = f"L{uuid.uuid4().hex[:8]}", f"T{uuid.uuid4().hex[:8]}"

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    with contextlib.suppress(Exception):
        iris_connection.commit()
    run_q(
        f"CREATE (a:{label} {{id: 'a', x: 5, s: '007'}})-[:{t} {{q: 7, w: 0.5, str: '007', b: true}}]->"
        f"(b:{label} {{id: 'b'}}), (c:{label} {{id: 'c', x: 9}})-[:{t} {{q: 8}}]->(b)"
    )
    yield run_q, label, t
    run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _edges(run_q, label, t, *keys):
    ret = ", ".join(f"r.{k}" for k in keys)
    return run_q(f"MATCH (n:{label})-[r:{t}]->() RETURN n.id, {ret} ORDER BY n.id")


def test_a_relationship_copies_its_own_property(g):
    run_q, label, t = g
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r.p = r.q")
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r.p2 = r.str")
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r.pb = r.b")
    assert _edges(run_q, label, t, "p", "p2", "pb") == [["a", 7, "007", True], ["c", 8, None, None]]


def test_a_missing_property_removes_the_key(g):
    run_q, label, t = g
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r.z = 1")
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r.z = r.missing")
    rows = run_q(f"MATCH (n:{label})-[r:{t}]->() RETURN n.id, keys(r) ORDER BY n.id")
    assert all("z" not in k for _, k in rows), rows


def test_a_relationship_map_copies_its_own_property(g):
    run_q, label, t = g
    run_q(f"MATCH (:{label})-[r:{t}]->() SET r += {{p: r.q, k: 1}}")
    assert _edges(run_q, label, t, "p", "k", "q") == [["a", 7, 1, 7], ["c", 8, 1, 8]]


def test_a_relationship_reads_its_rows_node(g):
    run_q, label, t = g
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET r.nx = n.x")
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET r.ns = n.s")
    assert _edges(run_q, label, t, "nx", "ns") == [["a", 5, "007"], ["c", 9, None]]


def test_a_node_reads_its_rows_relationship(g):
    run_q, label, t = g
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET n.rw = r.w")
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET n.rq = r.q")
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET n.rs = r.str")
    run_q(f"MATCH (n:{label})-[r:{t}]->() SET n += {{rq2: r.q}}")
    rows = run_q(f"MATCH (n:{label}) RETURN n.id, n.rw, n.rq, n.rs, n.rq2 ORDER BY n.id")
    assert rows == [["a", 0.5, 7, "007", 7], ["b", None, None, None, None], ["c", None, 8, None, 8]]
