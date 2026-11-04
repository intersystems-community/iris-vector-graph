"""4.6.0 R082: `CALL ivg.vector.search` with a seed node (or text) follows embedding routes on the translator route.

A vector query is answered by the store's `execute_knn_vec`, which reads the pair's route since R073. A seed-node id or
a text query goes through the translator's `VecSearch` CTE, which read `kg_NodeEmbeddings` whatever the graph: a named
graph's vectors live in a routed `kg_emb_*` table (spec 227), so the seed's vector subquery found nothing and every score
was NULL.

The graph here, worked by hand. Graph G holds a, b, c labelled Ivg460Vs, with the engine's own model, 3 wide:
a = [1,0,0], b = [1,1,0], c = [0,1,0]. Seeded by a (which the search leaves out), the cosines are b 1/sqrt(2) = 0.70711
and c 0; seeded by c, b 0.70711 then a 0.
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg460Vs"
VECTORS = {"a": [1.0, 0.0, 0.0], "b": [1.0, 1.0, 0.0], "c": [0.0, 1.0, 0.0]}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460vs_{run}"
    ids = {k: f"ivg460vs_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        engine.create_node(nid, labels=[LABEL], graph=graph)
        assert engine.store_embedding(nid, VECTORS[k], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    route = engine.resolve_route(graph=graph, model_key=None)
    assert route is not None and route.table_name.startswith("kg_emb_"), route
    yield engine, graph, ids
    engine.erase_graph(graph)


def _named(result, ids):
    """(node, score) pairs, nodes by their short name. The translator route answers `node` as a node (`node_id`,
    `node_labels`, `node_props`), so the columns are found by name."""
    inverse = {v: k for k, v in ids.items()}
    node = result.columns.index("node_id" if "node_id" in result.columns else "node")
    score = result.columns.index("score")
    return [(inverse.get(r[node], r[node]), None if r[score] is None else round(float(r[score]), 5))
            for r in result.rows]


@pytest.mark.parametrize(
    "seed, expected",
    [("a", [("b", round(1 / math.sqrt(2), 5)), ("c", 0.0)]), ("c", [("b", round(1 / math.sqrt(2), 5)), ("a", 0.0)])],
)
def test_a_seed_node_search_reads_the_graphs_route(g, seed, expected):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', '{ids[seed]}', 2) "
        "YIELD node, score RETURN node, score"
    )
    assert result.error is None, result.error
    assert _named(result, ids) == expected


def test_a_seed_node_search_with_a_parameter_reads_the_graphs_route(g):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', $seed, 1) "
        "YIELD node, score RETURN node, score",
        {"seed": ids["a"]},
    )
    assert result.error is None, result.error
    assert _named(result, ids) == [("b", round(1 / math.sqrt(2), 5))]


def test_the_translated_sql_names_the_route_not_the_legacy_table(g):
    engine, graph, ids = g
    result = engine.execute_cypher(
        f"USE GRAPH '{graph}' CALL ivg.vector.search('{LABEL}', 'embedding', '{ids['a']}', 2) "
        "YIELD node, score RETURN node, score"
    )
    route = engine.resolve_route(graph=graph, model_key=None)
    assert route.table_name in (result.sql or "") and "kg_NodeEmbeddings" not in (result.sql or "")
