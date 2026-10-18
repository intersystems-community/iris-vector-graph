"""DijkstraJson walks only the relationship types it is given.

A -KNOWS(5)- B, and a cheaper detour A -LIKES- X -LIKES- B (unweighted edges cost 1). Without
types the cheapest path is the detour (2); restricted to KNOWS it is the direct edge (5).
Written straight into ^KG("out"/"in", 0, ...) under unique node ids, which is what DijkstraJson
reads, and removed afterwards.

Run against a scratch namespace, e.g. `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def detour_graph(iris_connection):
    import iris

    native = iris.createIRIS(iris_connection)
    tag = uuid.uuid4().hex[:8]
    a, b, x = f"dj_{tag}_a", f"dj_{tag}_b", f"dj_{tag}_x"
    edges = [(a, "KNOWS", b, 5), (a, "LIKES", x, ""), (b, "LIKES", x, "")]
    for s, p, o, w in edges:
        native.set(w, "KG", "out", 0, s, p, o)
        native.set(w, "KG", "in", 0, o, p, s)
    try:
        yield a, b
    finally:
        for n in (a, b, x):
            native.kill("KG", "out", 0, n)
            native.kill("KG", "in", 0, n)


def _cost(engine, a, b, types_arg=""):
    q = f"CALL ivg.shortestPath.weighted($a, $b, 'weight', 9999, 10, 'both'{types_arg}) YIELD totalCost RETURN totalCost"
    res = engine.execute_cypher(q, {"a": a, "b": b, "types": ["KNOWS"]})
    assert getattr(res, "error", None) is None
    return res.rows[0][-1] if res.rows else None


def test_without_types_the_detour_wins(iris_connection, detour_graph):
    a, b = detour_graph
    assert _cost(IRISGraphEngine(iris_connection), a, b) == 2


def test_restricted_to_knows_the_direct_edge_wins(iris_connection, detour_graph):
    a, b = detour_graph
    engine = IRISGraphEngine(iris_connection)
    assert _cost(engine, a, b, ", ['KNOWS']") == 5
    assert _cost(engine, a, b, ", $types") == 5
