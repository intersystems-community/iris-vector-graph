"""4.6.0 R140: `vector_distance()` and `vector_similarity()` score a named graph's node with the graph's own vector.

They read `kg_NodeEmbeddings`, where a named graph's vectors are not (they live in its routed `kg_emb_*` table since
spec 227), so under `USE GRAPH` every distance was NULL.

The graphs, worked by hand. G1 holds p = [1,0,0] and q = [1,1,0]; G2 holds a node with the same id p, = [0,1,0].
Against the query [1,0,0]: in G1, p has cosine 1 (distance 0) and q has cosine 1/sqrt(2) (distance 1 - 0.70711 =
0.29289); in G2, p has cosine 0 (distance 1).
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg460Vd"


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g1, g2 = f"ivg460vd1_{run}", f"ivg460vd2_{run}"
    p, q = f"ivg460vd_{run}_p", f"ivg460vd_{run}_q"
    for graph, nid, vec in ((g1, p, [1.0, 0.0, 0.0]), (g1, q, [1.0, 1.0, 0.0]), (g2, p, [0.0, 1.0, 0.0])):
        assert engine.create_node(nid, labels=[LABEL], graph=graph)
        assert engine.store_embedding(nid, vec, graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, g1, g2, p, q
    for graph in (g1, g2):
        with contextlib.suppress(Exception):
            engine.erase_graph(graph)


def _scores(engine, graph, fn, params=None):
    r = engine.execute_cypher(
        f"USE GRAPH '{graph}' MATCH (n:{LABEL}) RETURN n.node_id AS id, {fn} AS s ORDER BY id",
        params or {},
    )
    assert r.error is None, r.error
    return [(row[0], None if row[1] is None else round(float(row[1]), 5)) for row in r.rows]


def test_each_graph_scores_its_own_vector(g):
    engine, g1, g2, p, q = g
    d = round(1 - 1 / math.sqrt(2), 5)
    assert _scores(engine, g1, "vector_distance(n, [1.0, 0.0, 0.0])") == [(p, 0.0), (q, d)]
    assert _scores(engine, g2, "vector_distance(n, [1.0, 0.0, 0.0])") == [(p, 1.0)]


def test_a_parameter_vector_and_similarity(g):
    engine, g1, g2, p, q = g
    s = round(1 / math.sqrt(2), 5)
    assert _scores(engine, g1, "vector_similarity(n, $v)", {"v": [1.0, 0.0, 0.0]}) == [(p, 1.0), (q, s)]
    assert _scores(engine, g2, "vector_similarity(n, $v)", {"v": [1.0, 0.0, 0.0]}) == [(p, 0.0)]


def test_a_distance_filter_reads_the_route(g):
    engine, g1, _, p, _ = g
    r = engine.execute_cypher(
        f"USE GRAPH '{g1}' MATCH (n:{LABEL}) WHERE vector_distance(n, $v) < 0.1 RETURN n.node_id",
        {"v": [1.0, 0.0, 0.0]},
    )
    assert r.error is None, r.error
    assert [row[0] for row in r.rows] == [p]
