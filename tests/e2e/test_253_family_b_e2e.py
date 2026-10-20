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
    for k, first in people.items():
        props = {"sid": n(k), "firstName": first}
        if k != "f":
            props["lastName"] = f"L{k}"
        e.create_node(n(k), labels=["FbPerson"], properties=props)
    # a-b-c triangle, c-d-e chain, e-e self-loop, a-f
    for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "e"), ("a", "f")):
        e.create_edge(n(x), "FB_KNOWS", n(y))
    e._native_privileges_cached = True
    e.cypher_shadow = True
    yield e
    e.cypher_shadow = False
    for k in people:
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
