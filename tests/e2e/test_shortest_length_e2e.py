"""Graph.KG.TraversalPaths.ShortestLength: the length ShortestPathJson's paths have, found from both ends.

For every pair of nodes of a few random graphs (with self-loops, parallel predicates, unreachable pairs), in both
directions it takes, typed and untyped, under hop limits that cut some pairs off, the distance search answers the
length of the path routine's first path, or -1 where that finds none. Both read the default graph.
"""

from __future__ import annotations

import json
import os
import random
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

T = uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def graphs(iris_connection):
    e = IRISGraphEngine(iris_connection)
    rng = random.Random(7)
    made = []
    for gi, (n, m) in enumerate(((12, 14), (25, 40), (30, 30))):
        nodes = [f"sl{T}g{gi}n{i}" for i in range(n)]
        for x in nodes:
            e.create_node(x, labels=[f"Sl{T}"])
        for _ in range(m):
            a, b = rng.choice(nodes), rng.choice(nodes)
            e.create_edge(a, rng.choice(("SL_A", "SL_B")), b)
        e.create_edge(nodes[0], "SL_A", nodes[0])  # a self-loop
        made.append(nodes)
    yield iris_connection, made
    for nodes in made:
        for x in nodes:
            e.delete_node(x)


def _call(conn, method, *args):
    import iris

    return iris.createIRIS(conn).classMethodValue("Graph.KG.TraversalPaths", method, *args)


@pytest.mark.parametrize("direction", ["both", "out"])
@pytest.mark.parametrize("preds", ["", '["SL_A"]', '["SL_A","SL_B"]'])
@pytest.mark.parametrize("max_hops", [2, 4, 8])
def test_the_distance_search_answers_the_path_routine_s_length(graphs, direction, preds, max_hops):
    conn, made = graphs
    wants = []
    for nodes in made:
        for a in nodes[::3]:
            for b in nodes[1::4]:
                paths = json.loads(str(_call(conn, "ShortestPathJson", a, b, max_hops, preds, direction, 0)) or "[]")
                want = paths[0]["length"] if paths else -1
                got = int(_call(conn, "ShortestLength", a, b, max_hops, preds, direction))
                assert got == want, (a, b, direction, preds, max_hops)
                wants.append(want)
    # the graphs gave both outcomes, so the comparison tested something
    assert -1 in wants and any(w > 0 for w in wants)
