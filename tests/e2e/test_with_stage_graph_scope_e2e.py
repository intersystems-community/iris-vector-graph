"""`USE GRAPH` scopes the reads inside a WITH stage (found 2026-10-24 while fixing multigraph relationship equality).

The graph-scope pass (`translator._apply_graph_scope_to_reads`) ran on the query's last part only, so the MATCH before a
WITH read every graph: with one node id in two graphs, `USE GRAPH 'g1' MATCH (a:L) WITH a RETURN count(*)` answered 4
(two graphs' node rows by two graphs' label rows) where the answer is 1. The pass now runs on each stage before it is
built. Both paths are held to the same answers.

Run: IVG_NAMESPACE=IVGFIX IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972.
"""

from __future__ import annotations

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture(scope="module")
def graphs(iris_connection):
    """The same ids in two graphs: x, y with x -K-> y; graph 2 also holds z with x -K-> z and a prop that differs."""
    e = IRISGraphEngine(iris_connection, native_cypher=True)
    e._native_privileges_cached = True
    t = uuid.uuid4().hex[:5]
    g1, g2, label = f"ws{t}_1", f"ws{t}_2", f"Ws{t}"
    for g, name in ((g1, "one"), (g2, "two")):
        e.create_node(f"{t}x", labels=[label], properties={"name": name}, graph=g)
        e.create_node(f"{t}y", labels=[label], properties={"name": name}, graph=g)
        e.create_edge(f"{t}x", "K", f"{t}y", graph=g)
    e.create_node(f"{t}z", labels=[label], properties={"name": "two"}, graph=g2)
    e.create_edge(f"{t}x", "K", f"{t}z", graph=g2)
    yield e, g1, g2, label
    for g in (g1, g2):
        e.erase_graph(g)


CASES = [
    ("a label count through a stage", "MATCH (a:{L}) WITH a RETURN count(*) AS n", [[2]], [[3]]),
    ("a hop through a stage", "MATCH (a:{L})-[:K]->(b) WITH a, b RETURN count(*) AS n", [[1]], [[2]]),
    ("a staged relationship", "MATCH (a:{L})-[r:K]->(b) WITH a, b, r RETURN count(*) AS n", [[1]], [[2]]),
    (
        "a property read after a stage",
        "MATCH (a:{L})-[:K]->(b) WITH b RETURN b.name AS name, count(*) AS n ORDER BY name",
        [["one", 1]],
        [["two", 2]],
    ),
    (
        "a second MATCH after a stage",
        "MATCH (a:{L}) WITH a MATCH (a)-[:K]->(b) RETURN count(*) AS n",
        [[1]],
        [[2]],
    ),
]


@pytest.mark.parametrize("native", [True, False], ids=["native", "sql"])
@pytest.mark.parametrize("name,body,in_g1,in_g2", CASES, ids=[c[0] for c in CASES])
def test_a_with_stage_reads_only_the_named_graph(graphs, native, name, body, in_g1, in_g2):
    e, g1, g2, label = graphs
    e.native_cypher = native
    try:
        for g, expect in ((g1, in_g1), (g2, in_g2)):
            r = e.execute_cypher(f"USE GRAPH '{g}' " + body.format(L=label))
            assert getattr(r, "error", None) is None, r.error
            assert [list(x) for x in r.rows] == expect, (g, r.rows)
    finally:
        e.native_cypher = True
