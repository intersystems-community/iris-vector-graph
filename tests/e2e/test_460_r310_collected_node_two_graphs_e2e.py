"""4.6.0 R310 follow-up, live (fixes66): a property of a collected node is read in the node's own graph.

`collect(n)` holds each node as {"_id", "_labels", "_props"} JSON, and `x.k` of such a node read the rdf_props row of
its `_id` in any graph: with one node id in two graphs, a read over every graph answered one graph's value for both.
By hand: node `g310:x` in graph g310a with k 'A' and in graph g310b with k 'B'.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

GRAPHS = ("g310a", "g310b")
ORDERED = "MATCH (n:G310) WITH n ORDER BY n.k WITH collect(n) AS ns "


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)

    def clean():
        for g in GRAPHS:
            e.execute_cypher(f"USE GRAPH '{g}' MATCH (n:G310) DETACH DELETE n")

    clean()
    assert e.create_node("g310:x", labels=["G310"], properties={"k": "A"}, graph="g310a")
    assert e.create_node("g310:x", labels=["G310"], properties={"k": "B"}, graph="g310b")
    try:
        yield e
    finally:
        clean()


def _col(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    assert not r.error, r.error
    return [row[0] for row in r.rows]


@pytest.mark.parametrize("tail", [
    "UNWIND ns AS x RETURN x.k AS k",
    "UNWIND ns AS x WITH x RETURN x.k AS k",
    "UNWIND range(0, size(ns) - 1) AS i RETURN ns[i].k AS k",
    "UNWIND range(0, size(ns) - 1) AS i WITH ns[i] AS m RETURN m.k AS k",
])
def test_every_graph(eng, tail):
    assert _col(eng, ORDERED + tail) == ["A", "B"]


@pytest.mark.parametrize("g, want", [("g310a", "A"), ("g310b", "B")])
def test_one_graph(eng, g, want):
    assert _col(eng, f"USE GRAPH '{g}' MATCH (n:G310) WITH collect(n) AS ns UNWIND ns AS x RETURN x.k AS k") == [want]
