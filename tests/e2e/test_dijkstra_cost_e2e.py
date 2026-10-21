"""Graph.KG.TraversalPaths.DijkstraCost: the totalCost DijkstraJson reports, found from both ends.

For pairs of nodes of a few random weighted graphs (weights from 0.25 to 5, some zero or negative, which both read as
1; parallel predicates; self-loops; unreachable pairs), in both directions DijkstraJson takes, typed and untyped, under
cost caps that cut some pairs off, the cost search answers DijkstraJson's totalCost, or "" where it finds no path.
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
    rng = random.Random(11)
    made = []
    for gi, (n, m) in enumerate(((12, 18), (25, 45), (30, 32))):
        nodes = [f"dc{T}g{gi}n{i}" for i in range(n)]
        for x in nodes:
            e.create_node(x, labels=[f"Dc{T}"])
        for _ in range(m):
            a, b = rng.choice(nodes), rng.choice(nodes)
            w = rng.choice((0.25, 0.5, 1, 1.5, 2, 3, 5, 0, -1))
            e.create_edge(a, rng.choice(("DC_A", "DC_B")), b, weight=w)
        e.create_edge(nodes[0], "DC_A", nodes[0], weight=2)
        made.append(nodes)
    yield iris_connection, made
    for nodes in made:
        for x in nodes:
            e.delete_node(x)


def _call(conn, method, *args):
    import iris

    return iris.createIRIS(conn).classMethodValue("Graph.KG.TraversalPaths", method, *args)


@pytest.mark.parametrize("direction", ["both", "out"])
@pytest.mark.parametrize("preds", ["", '["DC_A"]'])
@pytest.mark.parametrize("max_cost", [3, 9999])
def test_the_cost_search_answers_dijkstra_json_s_total_cost(graphs, direction, preds, max_cost):
    conn, made = graphs
    seen = []
    for nodes in made:
        for a in nodes[::3]:
            for b in nodes[1::4]:
                raw = str(_call(conn, "DijkstraJson", a, b, "weight", max_cost, 10, direction, preds) or "{}")
                path = json.loads(raw) if raw else {}
                want = float(path["totalCost"]) if path else None
                got_raw = _call(conn, "DijkstraCost", a, b, max_cost, direction, preds)
                got = None if got_raw is None or str(got_raw) == "" else float(got_raw)  # a cost of 0 is a cost
                assert (got is None) == (want is None) and (got is None or abs(got - want) < 1e-9), (a, b, direction, preds, max_cost, got, want)
                seen.append(want)
    # the graphs gave both outcomes, so the comparison tested something
    assert None in seen and any(w for w in seen if w)
