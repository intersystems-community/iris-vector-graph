"""Spec 253: an ordered native read with LIMIT k keeps only the rows that can still be written.

IC2, IC7 and IC8 (`ORDER BY m.creationDate DESC, m.sid LIMIT 20`) filed every match into the sort tree and wrote the
first 20. Without DISTINCT a row past the k-th in sort order can never be written, so the tree is cut back to k rows
whenever it holds 2k + 64 (`^||IVG.CypherTrimAt` overrides that in a process), and once cut, a match whose first key
sorts after the k-th row's is not filed at all. Every query runs in shadow mode: the answer, ties included, must be
the SQL path's, and `^||IVG.CypherTrims` must show the tree was cut.
"""

from __future__ import annotations

import os
import uuid

import iris
import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

T = uuid.uuid4().hex[:6]


def n(x):
    return f"ot{T}_{x}"


def _ppg(conn, code):
    iris.createIRIS(conn).classMethodVoid("User.Exec", "Run", code)


def _trims(conn):
    # the Native API cannot name a process-private global: copy the count, made in this connection's process
    key = f"IVGTrims{T}"
    _ppg(conn, f"Set ^{key}=+$Get(^||IVG.CypherTrims)")
    native = iris.createIRIS(conn)
    count = int(native.get(key) or 0)
    native.kill(key)
    return count


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    people = ["a", "b", "c", "d", "e"]
    for k in people:
        e.create_node(n(k), labels=["OtPerson"], properties={"sid": n(k), "firstName": k.upper()})
    for x, y in (("a", "b"), ("a", "c"), ("a", "d"), ("b", "e"), ("c", "e")):
        e.create_edge(n(x), "OT_KNOWS", n(y))
    # 40 posts, two per date (ties on the first key); one post has no date, and where it sorts is the SQL path's
    for i in range(40):
        by = people[1 + i % 4]
        props = {"sid": n(f"m{i:02d}"), "title": f"t{i % 7}"}
        if i != 13:
            props["creationDate"] = f"2012-{1 + (i // 2) % 12:02d}-{1 + i // 24:02d}T10:00:00.000+00:00"
        e.create_node(n(f"m{i:02d}"), labels=["OtPost"], properties=props)
        e.create_edge(n(f"m{i:02d}"), "OT_BY", n(by))
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for k in people:
        e.delete_node(n(k))
    for i in range(40):
        e.delete_node(n(f"m{i:02d}"))


@pytest.fixture
def trim_often(engine):
    _ppg(engine.conn, "Set ^||IVG.CypherTrimAt=3 Kill ^||IVG.CypherTrims")
    yield engine
    _ppg(engine.conn, "Kill ^||IVG.CypherTrimAt, ^||IVG.CypherTrims")


IC2 = (
    "MATCH (p:OtPerson)-[:OT_KNOWS]-(f:OtPerson)<-[:OT_BY]-(m:OtPost) WHERE p.sid = $id "
    "RETURN f.sid, m.sid, m.creationDate ORDER BY m.creationDate {dir}, m.sid LIMIT {k}"
)

CASES = [
    ("IC2 shape, newest first", IC2.format(dir="DESC", k=5), {"id": "a"}),
    ("oldest first", IC2.format(dir="ASC", k=5), {"id": "a"}),
    ("a LIMIT that ends inside a tie", IC2.format(dir="DESC", k=3), {"id": "a"}),
    ("LIMIT 1", IC2.format(dir="DESC", k=1), {"id": "a"}),
    ("a LIMIT larger than the matches", IC2.format(dir="DESC", k=100), {"id": "a"}),
    ("LIMIT 0", IC2.format(dir="DESC", k=0), {"id": "a"}),
    (
        "a single ORDER BY key, many ties",
        "MATCH (p:OtPerson)-[:OT_KNOWS]-(f:OtPerson)<-[:OT_BY]-(m:OtPost) WHERE p.sid = $id "
        "RETURN m.title ORDER BY m.title LIMIT 4",
        {"id": "a"},
    ),
    (
        "a label scan, ordered",
        "MATCH (m:OtPost)-[:OT_BY]->(f:OtPerson) RETURN m.sid, f.firstName ORDER BY f.firstName DESC, m.sid LIMIT 6",
        {},
    ),
]


@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_trimmed_answers_equal_sql(trim_often, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = trim_often.__dict__.get("_native_answers", 0)
    r = trim_often.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert trim_often.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"


def test_the_tree_is_cut(trim_often):
    trim_often.execute_cypher(IC2.format(dir="DESC", k=5), {"id": n("a")})
    assert _trims(trim_often.conn) > 0


def test_distinct_is_never_cut(trim_often):
    """A DISTINCT row past the k-th may be needed once duplicates ahead of it are dropped."""
    r = trim_often.execute_cypher(
        "MATCH (p:OtPerson)-[:OT_KNOWS]-(f:OtPerson)<-[:OT_BY]-(m:OtPost) WHERE p.sid = $id "
        "RETURN DISTINCT m.title ORDER BY m.title LIMIT 3",
        {"id": n("a")},
    )
    assert getattr(r, "error", None) is None, r.error
    assert _trims(trim_often.conn) == 0


def test_without_the_override_a_small_read_is_not_cut(engine):
    _ppg(engine.conn, "Kill ^||IVG.CypherTrimAt, ^||IVG.CypherTrims")
    engine.execute_cypher(IC2.format(dir="DESC", k=5), {"id": n("a")})
    assert _trims(engine.conn) == 0
