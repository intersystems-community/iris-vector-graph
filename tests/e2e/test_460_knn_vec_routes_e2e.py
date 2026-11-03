"""4.6.0 R073: the store's `execute_knn_vec` (and so `CALL ivg.vector.search` with a vector) follows embedding routes.

Since spec 227 a named graph's vectors, and a declared model's, live in a routed table (`kg_emb_*`) that
`Graph_KG.embedding_registry` names; `kg_KNN_VEC` reads that table. The store's `execute_knn_vec` read
`Graph_KG.kg_NodeEmbeddings` whatever the graph or model, so a routed graph answered no neighbours at all.

The graph here, worked by hand. Graph G holds a, b, c, labelled Ivg460Knn, each with two vectors:
- the engine's own (undeclared) model, 3 wide: a = [1,0,0], b = [1,1,0], c = [0,1,0]. Against q = [1,0,0] the cosines
  are a 1, b 1/sqrt(2) = 0.70711, c 0; the two nearest are a then b.
- model ivg460-m2, 4 wide: a = [1,0,0,0], b = [0,1,0,0], c = [0,0,0,1]. Against q = [0,0,0,1] the nearest is c (1),
  the others 0.
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

MODEL_2 = "ivg460-m2"
UNDECLARED = {"a": [1.0, 0.0, 0.0], "b": [1.0, 1.0, 0.0], "c": [0.0, 1.0, 0.0]}
SECOND = {"a": [1.0, 0.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0, 0.0], "c": [0.0, 0.0, 0.0, 1.0]}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460knn_{run}"
    ids = {k: f"ivg460knn_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        engine.create_node(nid, labels=["Ivg460Knn"], graph=graph)
        assert engine.store_embedding(nid, UNDECLARED[k], graph=graph)
        assert engine.store_embedding(nid, SECOND[k], graph=graph, model_key=MODEL_2)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    # the routes exist, and neither is the legacy table: the vectors are where kg_KNN_VEC reads them
    for model in (None, MODEL_2):
        route = engine.resolve_route(graph=graph, model_key=model)
        assert route is not None and route.table_name.startswith("kg_emb_"), route
    yield engine, graph, ids
    engine.erase_graph(graph)


def _named(rows, ids):
    inverse = {v: k for k, v in ids.items()}
    return [(inverse.get(r[0], r[0]), round(float(r[1]), 5)) for r in rows]


def test_the_store_reads_the_graphs_route(g):
    engine, graph, ids = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 2, None, graph=graph)
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("a", 1.0), ("b", round(1 / math.sqrt(2), 5))]


def test_the_store_reads_the_declared_models_route(g):
    engine, graph, ids = g
    result = engine.cypher.execute_knn_vec([0.0, 0.0, 0.0, 1.0], 1, None, graph=graph, model_key=MODEL_2)
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("c", 1.0)]


def test_the_store_with_a_label_filter_reads_the_route(g):
    engine, graph, ids = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 3, "Ivg460Knn", graph=graph)
    assert result.error is None, result.error
    assert _named(result.rows, ids) == [("a", 1.0), ("b", round(1 / math.sqrt(2), 5)), ("c", 0.0)]


def test_call_ivg_vector_search_with_a_vector_reads_the_route(g):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('Ivg460Knn', 'embedding', [1.0, 0.0, 0.0], 2) "
        "YIELD node, score RETURN node, score"
    )
    assert result.error is None, result.error
    assert result.columns == ["node", "score"]
    assert _named(result.rows, ids) == [("a", 1.0), ("b", round(1 / math.sqrt(2), 5))]


def test_a_pair_with_no_route_answers_no_neighbours(g):
    # FR-013: no route means nowhere to read, not the default graph's or another model's vectors
    engine, graph, _ = g
    result = engine.cypher.execute_knn_vec([1.0, 0.0, 0.0], 2, None, graph=graph, model_key="ivg460-no-such-model")
    assert (result.error, result.rows) == (None, [])
