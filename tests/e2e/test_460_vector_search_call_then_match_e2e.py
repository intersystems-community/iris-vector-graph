"""4.6.0 R118: `CALL ivg.vector.search(<label>, <prop>, <vector>, k) YIELD node, score MATCH ...` keeps the MATCH.

A vector argument is answered by the engine's handler (`_proc_ivg_vector_search`, the store's `execute_knn_vec`); the
question was whether a query that goes on after YIELD was answered by the handler alone, its later clauses dropped.

The graph, worked by hand. Graph G holds a, b, c labelled Ivg460Vm, 3 wide: a = [1,0,0], b = [1,1,0], c = [0,1,0];
a -[:VM_R]-> x, b -[:VM_R]-> y, c -[:VM_R]-> z. The query vector [1,0,0] with k = 2 finds a (cosine 1) and b
(1/sqrt(2)); their VM_R neighbours are x and y.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg460Vm"
VECTORS = {"a": [1.0, 0.0, 0.0], "b": [1.0, 1.0, 0.0], "c": [0.0, 1.0, 0.0]}
NEIGHBOUR = {"a": "x", "b": "y", "c": "z"}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460vm_{run}"
    ids = {k: f"ivg460vm_{run}_{k}" for k in "abcxyz"}
    for k in "abc":
        engine.create_node(ids[k], labels=[LABEL], graph=graph)
        assert engine.store_embedding(ids[k], VECTORS[k], graph=graph)
    for k in "xyz":
        engine.create_node(ids[k], labels=["Ivg460VmEnd"], graph=graph)
    for k, n in NEIGHBOUR.items():
        engine.create_edge(ids[k], "VM_R", ids[n], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, graph, ids
    engine.erase_graph(graph)


def _short(ids, value):
    return {v: k for k, v in ids.items()}.get(value, value)


@pytest.mark.parametrize("vector_arg", ["[1.0, 0.0, 0.0]", "$v"])
def test_the_match_after_yield_runs(g, vector_arg):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', {vector_arg}, 2) "
        "YIELD node, score MATCH (node)-[:VM_R]->(m) RETURN m.node_id AS m",
        {"v": [1.0, 0.0, 0.0]},
    )
    assert result.error is None, result.error
    assert result.columns == ["m"], result.columns
    assert sorted(_short(ids, r[0]) for r in result.rows) == ["x", "y"]


def test_a_where_after_yield_filters(g):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', $v, 3) "
        "YIELD node, score WITH node, score WHERE score > 0.9 RETURN node.node_id AS n",
        {"v": [1.0, 0.0, 0.0]},
    )
    assert result.error is None, result.error
    assert [_short(ids, r[0]) for r in result.rows] == ["a"]


def test_a_with_after_a_seed_node_search(g):
    # seeded by a (left out): b 1/sqrt(2), c 0
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', $seed, 2) "
        "YIELD node, score WITH node, score WHERE score > 0.5 RETURN node.node_id AS n",
        {"seed": ids["a"]},
    )
    assert result.error is None, result.error
    assert [_short(ids, r[0]) for r in result.rows] == ["b"]
