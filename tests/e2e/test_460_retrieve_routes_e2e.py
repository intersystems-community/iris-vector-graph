"""4.6.0 R117: `CALL ivg.retrieve` reads a named graph's vectors from the graph's route.

The vector arm read `kg_NodeEmbeddings` whatever the graph (the R082 defect, on another procedure); a named graph's
vectors live in a routed `kg_emb_*` table (spec 227), so under `USE GRAPH` the arm found nothing and the fusion was
the BM25 arm alone.

The graph, worked by hand. Graph G holds a, b, c labelled Ivg460Rt, 3 wide: a = [1,0,0], b = [1,1,0], c = [0,1,0].
The engine embeds any query text as [1,0,0] (a stub embedder). There is no BM25 index named `nosuch`, so the BM25 arm
is empty and the fusion is the vector arm's ranks: a (cosine 1) rank 1, b (0.70711) rank 2, c (0) rank 3, each scored
1 / (60 + rank). With limit 2: a 1/61, b 1/62.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg460Rt"
VECTORS = {"a": [1.0, 0.0, 0.0], "b": [1.0, 1.0, 0.0], "c": [0.0, 1.0, 0.0]}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    engine.embedder = lambda _text: [1.0, 0.0, 0.0]
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460rt_{run}"
    ids = {k: f"ivg460rt_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        engine.create_node(nid, labels=[LABEL], graph=graph)
        assert engine.store_embedding(nid, VECTORS[k], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    route = engine.resolve_route(graph=graph, model_key=None)
    assert route is not None and route.table_name.startswith("kg_emb_"), route
    yield engine, graph, ids
    engine.erase_graph(graph)


def _ranked(result, ids):
    """(node, rrf_score) by short name. `node` is answered as a node (`node_id`, `node_labels`, `node_props`)."""
    inverse = {v: k for k, v in ids.items()}
    node = result.columns.index("node_id" if "node_id" in result.columns else "node")
    score = result.columns.index("rrf_score")
    return [(inverse.get(r[node], r[node]), round(float(r[score]), 6)) for r in result.rows]


@pytest.mark.parametrize("label", ["*", LABEL])
def test_retrieve_reads_the_graphs_route(g, label):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.retrieve('anything', 2, 'nosuch', '{label}') "
        "YIELD node, rrf_score RETURN node, rrf_score"
    )
    assert result.error is None, result.error
    assert _ranked(result, ids) == [("a", round(1 / 61, 6)), ("b", round(1 / 62, 6))]


def test_the_sql_names_the_route(g):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.retrieve('anything', 2, 'nosuch') YIELD node, rrf_score RETURN node, rrf_score"
    )
    route = engine.resolve_route(graph=graph, model_key=None)
    assert route.table_name in (result.sql or "") and "kg_NodeEmbeddings" not in (result.sql or "")


def test_a_graph_with_no_route_answers_no_vector_rows(g, iris_connection):
    engine, _graph, _ids = g
    empty = f"ivg460rt_none_{uuid.uuid4().hex[:8]}"
    result = engine.execute_cypher(
        f"USE GRAPH '{empty}' CALL ivg.retrieve('anything', 2, 'nosuch') YIELD node, rrf_score RETURN node, rrf_score"
    )
    assert result.error is None, result.error
    assert result.rows == []
