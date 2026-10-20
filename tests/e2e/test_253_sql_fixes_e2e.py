"""Spec 253 Phase 0 (FR-012): SQL-path defects fixed before the native path is built to match them.

(T003, `min`/`max` typing, was dropped: they return the stored text, as `RETURN n.x` does; IVG's values are untyped.)
T005: the k-hop fast path (`MATCH (a {node_id: $s})-[:R]->(b) RETURN count(b) AS c`) read only the default graph's
adjacency while the same query on the SQL path reads every graph.

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
G, G1, G2 = f"sf{T}_g", f"sf{T}_g1", f"sf{T}_g2"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    for i, (x, y) in enumerate(((2.7, 3), (1.2, 5))):
        e.create_node(f"sf{T}_v{i}", labels=["MM"], properties={"x": x, "y": y}, graph=G)
    s = f"sf{T}_s"
    for g, targets in ((G1, ("t1", "t2")), (G2, ("t3",))):
        e.create_node(s, graph=g)
        for t in targets:
            e.create_node(f"sf{T}_{t}", graph=g)
            e.create_edge(s, "R", f"sf{T}_{t}", graph=g)
    yield e
    for g in (G, G1, G2):
        e.erase_graph(g)


def _one(engine, q, params=None):
    r = engine.execute_cypher(q, params or {})
    assert getattr(r, "error", None) is None, r.error
    return r.rows


def test_the_khop_fast_path_counts_every_graph(engine):
    q = "MATCH (a {node_id: $s})-[:R]->(b) RETURN count(b) AS c"
    general = "MATCH (a {node_id: $s})-[:R]->(b) RETURN count(*) AS c"
    want = _one(engine, general, {"s": f"sf{T}_s"})
    assert want == [[3]]
    assert [list(r) for r in _one(engine, q, {"s": f"sf{T}_s"})] == want


def test_the_khop_fast_path_lists_every_graph_s_targets(engine):
    rows = _one(engine, "MATCH (a {node_id: $s})-[:R]->(b) RETURN b.node_id", {"s": f"sf{T}_s"})
    assert sorted(r[0] for r in rows) == [f"sf{T}_t1", f"sf{T}_t2", f"sf{T}_t3"]
