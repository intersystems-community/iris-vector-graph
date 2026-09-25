"""Spec 233 FR-014: the direction rule on Graph.KG.FHIRGraph.ExpandConcepts, live.

A three-node chain c -> b -> a in a scratch graph, called directly (constitution IV).
"""

import json
import uuid

import pytest


@pytest.fixture
def chain(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    tag = uuid.uuid4().hex[:8]
    graph = f"scratch{tag}"
    a, b, c = (f"{n}-{tag}" for n in "abc")
    for n in (a, b, c):
        eng.create_node(n)
    eng.create_edge(c, "sub", b, graph=graph)
    eng.create_edge(b, "sub", a, graph=graph)
    try:
        yield eng, graph, a, b, c
    finally:
        eng.delete_edge(c, "sub", b, graph=graph)
        eng.delete_edge(b, "sub", a, graph=graph)
        for n in (a, b, c):
            eng.delete_node(n)


def _expand(eng, graph, seed, hops, *direction):
    out = eng._iris_obj().classMethodValue(
        "Graph.KG.FHIRGraph", "ExpandConcepts", graph, json.dumps([seed]), "", hops, *direction
    )
    return json.loads(str(out))


def test_in_is_the_default(chain):
    eng, graph, a, b, c = chain
    assert _expand(eng, graph, a, 2)["ids"] == [a, b, c]
    assert _expand(eng, graph, c, 2, "out")["ids"] == [c, b, a]
    assert _expand(eng, graph, a, 2, "out")["ids"] == [a]


def test_in_walks_inbound(chain):
    eng, graph, a, b, c = chain
    assert _expand(eng, graph, a, 2, "in")["ids"] == [a, b, c]
    assert _expand(eng, graph, a, 1, "in")["ids"] == [a, b]
    assert _expand(eng, graph, c, 2, "in")["ids"] == [c]


def test_both_walks_either_way(chain):
    eng, graph, a, b, c = chain
    assert set(_expand(eng, graph, b, 1, "both")["ids"]) == {a, b, c}
    assert _expand(eng, graph, b, 0, "both")["ids"] == [b]


@pytest.mark.parametrize("bad", ["", "up", "OUT"])
def test_bad_direction_is_an_error(chain, bad):
    eng, graph, _, _, c = chain
    assert _expand(eng, graph, c, 1, bad)["status"] == "error"
