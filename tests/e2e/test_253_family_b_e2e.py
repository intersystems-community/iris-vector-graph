"""Spec 253, T030 (family B, first slice): variable-length ends and DISTINCT / count(DISTINCT), natively.

The SNB IC1 and IC3 shapes: a variable-length relationship whose paths nothing downstream can tell apart (RETURN
DISTINCT over the far node's properties, or count(DISTINCT far node)), so the walk yields each end once
(Graph.KG.VarLength). Plus `<>` filters and count(*). Each query runs in shadow mode and must be answered natively.

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


def n(x):
    return f"fb{T}_{x}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    people = {"a": "Ann", "b": "Bo", "c": "Ann", "d": "Di", "e": "Ann", "f": "Fe"}
    # rank: strings a range compares as the SQL path does (canonical numbers before other strings, numerically)
    ranks = {"a": "9", "b": "10", "c": "01", "d": "1.5", "e": "x"}
    for k, first in people.items():
        props = {"sid": n(k), "firstName": first, "joined": f"2011-0{ord(k) - 96}-15"}
        if k in ranks:
            props["rank"] = ranks[k]
        if k != "f":
            props["lastName"] = f"L{k}"
        # FbStar: a second, smaller label (c, and e with its self-loop), so a label scan may start there
        e.create_node(n(k), labels=["FbPerson", "FbStar"] if k in "ce" else ["FbPerson"], properties=props)
    # a-b-c triangle, c-d-e chain, e-e self-loop, a-f
    for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "e"), ("a", "f")):
        e.create_edge(n(x), "FB_KNOWS", n(y))
    # posts, each by one person: two by c, none by a
    posts = {"p1": ("b", "Tx"), "p2": ("c", "Tx"), "p3": ("c", "Ty"), "p4": ("d", "Ty"), "p5": ("f", "Tz")}
    for k, (by, title) in posts.items():
        e.create_node(n(k), labels=["FbPost"], properties={"sid": n(k), "title": title})
        e.create_edge(n(k), "FB_BY", n(by))
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for k in [*people, *posts]:
        e.delete_node(n(k))


IC1 = (
    "MATCH (p:FbPerson)-[:FB_KNOWS*1..3]-(f:FbPerson) WHERE p.sid = $id AND f.firstName = $first AND f.sid <> $id "
    "RETURN DISTINCT f.sid, f.lastName ORDER BY f.sid LIMIT 20"
)
IC3 = "MATCH (p:FbPerson)-[:FB_KNOWS*2..2]-(f:FbPerson) WHERE p.sid = $id AND f.sid <> $id RETURN count(DISTINCT f)"

CASES = [
    ("IC1 shape", IC1, {"id": "a", "first": "Ann"}),
    ("IC1 from the chain end", IC1, {"id": "e", "first": "Ann"}),
    ("IC3 shape", IC3, {"id": "a"}),
    ("IC3 from the self-loop", IC3, {"id": "e"}),
    (
        "directed ends",
        "MATCH (p:FbPerson)-[:FB_KNOWS*1..2]->(f) WHERE p.sid = $id RETURN DISTINCT f.sid ORDER BY f.sid",
        {"id": "c"},
    ),
    (
        "min zero includes the source",
        "MATCH (p:FbPerson)-[:FB_KNOWS*0..1]->(f) WHERE p.sid = $id RETURN DISTINCT f.sid ORDER BY f.sid",
        {"id": "a"},
    ),
    ("count star over a hop", "MATCH (p:FbPerson)-[:FB_KNOWS]-(f) WHERE p.sid = $id RETURN count(*)", {"id": "a"}),
    (
        "grouped count, ordered by the count",
        "MATCH (p:FbPerson)-[:FB_KNOWS]->(f:FbPerson) RETURN p.firstName AS name, count(f) AS n ORDER BY n DESC, name LIMIT 10",
        {},
    ),
    (
        "grouped count with a missing key",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f:FbPerson) RETURN f.lastName AS last, count(p) AS n ORDER BY last",
        {},
    ),
    (
        "grouped count(DISTINCT)",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f:FbPerson) RETURN p.firstName AS name, count(DISTINCT f) AS c ORDER BY c DESC, name",
        {},
    ),
    ("ungrouped count over a label", "MATCH (p:FbPerson) RETURN count(p)", {}),
    (
        "a date window",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f) WHERE p.sid = $id AND f.joined > $lo AND f.joined <= $hi RETURN f.sid ORDER BY f.sid",
        {"id": "a", "lo": "2011-02-01", "hi": "2011-06-15"},
    ),
    (
        "a range on numeric-looking strings",
        "MATCH (p:FbPerson) WHERE p.rank < $r RETURN p.sid ORDER BY p.sid",
        {"r": "10"},
    ),
    (
        "a mirrored range, and >= on a missing property",
        "MATCH (p:FbPerson) WHERE '9' <= p.rank AND p.lastName >= 'Lb' RETURN p.sid ORDER BY p.sid",
        {},
    ),
    (
        "a range feeding a grouped count",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f:FbPerson) WHERE f.joined < $hi RETURN p.firstName AS name, count(f) AS n ORDER BY n DESC, name",
        {"hi": "2011-04-01"},
    ),
    (
        "IC9 shape: variable length, then a hop, DISTINCT",
        "MATCH (p:FbPerson)-[:FB_KNOWS*1..2]-(f:FbPerson)<-[:FB_BY]-(m:FbPost) WHERE p.sid = $id AND f.sid <> $id "
        "RETURN DISTINCT f.sid, m.sid ORDER BY m.sid DESC LIMIT 3",
        {"id": "a"},
    ),
    (
        "IC5 shape: variable length, then a hop, grouped count(DISTINCT)",
        "MATCH (p:FbPerson)-[:FB_KNOWS*1..2]-(f:FbPerson)<-[:FB_BY]-(m:FbPost) WHERE p.sid = $id AND f.sid <> $id "
        "RETURN m.title AS title, count(DISTINCT f) AS c ORDER BY c DESC, title",
        {"id": "a"},
    ),
    (
        "a hop, then variable length from the post's side",
        "MATCH (m:FbPost)-[:FB_BY]->(f:FbPerson)-[:FB_KNOWS*2..2]->(g:FbPerson) WHERE m.title = $t RETURN DISTINCT g.sid ORDER BY g.sid",
        {"t": "Tx"},
    ),
    (
        "a label scan started from the smaller label (posts), not the first (people)",
        "MATCH (p:FbPerson)<-[:FB_BY]-(m:FbPost) RETURN p.sid AS s, count(m) AS n ORDER BY n DESC, s",
        {},
    ),
    (
        "two hops of one type from the smaller label: each relationship once per match",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f)-[:FB_KNOWS]-(s:FbStar) RETURN count(*)",
        {},
    ),
    (
        "the same, grouped and tallied",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f)-[:FB_KNOWS]-(s:FbStar) RETURN s.sid AS s, count(p) AS n ORDER BY s",
        {},
    ),
    (
        "a tallied last hop",
        "MATCH (p:FbPerson)<-[:FB_BY]-(m:FbPost) RETURN p.firstName AS name, count(m) AS n ORDER BY name",
        {},
    ),
    (
        "not-equals on a missing property",
        "MATCH (p:FbPerson)-[:FB_KNOWS]-(f) WHERE p.sid = $id AND f.lastName <> 'Lb' RETURN f.sid ORDER BY f.sid",
        {"id": "a"},
    ),
]


@pytest.mark.parametrize("name,cypher,params", CASES, ids=[c[0] for c in CASES])
def test_family_b_native_equals_sql(engine, name, cypher, params):
    params = {k: (n(v) if k == "id" else v) for k, v in params.items()}
    before = engine.__dict__.get("_native_answers", 0)
    r = engine.execute_cypher(cypher, params)
    assert getattr(r, "error", None) is None, r.error
    assert engine.__dict__.get("_native_answers", 0) == before + 1, "the native path did not answer"
