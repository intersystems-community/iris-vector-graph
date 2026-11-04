"""4.6.0 R158: without `USE GRAPH`, `vector_distance()` and `vector_similarity()` score each row with its own graph's
vector.

R140 read the `USE GRAPH` graph's route. With no `USE GRAPH` they read the default pair's table (`kg_NodeEmbeddings`)
for every row, where a named graph's vectors are not: in an all-graphs read a named graph's node scored NULL, or the
default graph's vector of that id.

The graphs, worked by hand. G1 holds p = [1,0,0] and q = [1,1,0]; G2 holds a node with the same id p, = [0.6,0.8,0].
Each node's `name` says which it is. Against the query [1,0,0]:
- G1 p: cosine 1, distance 0
- G1 q: cosine 1/sqrt(2) = 0.70711, distance 1 - 0.70711 = 0.29289
- G2 p: cosine 0.6 / (|[0.6,0.8,0]| = 1) = 0.6, distance 0.4
The default graph's route is wider (the namespace's 768), and none of these nodes is in it: a row is scored only from
its own graph's route, so the width of another graph's route does not matter.
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "Ivg158Vd"
S = 1 / math.sqrt(2)


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    g1, g2 = f"ivg158a_{run}", f"ivg158b_{run}"
    p, q = f"ivg158_{run}_p", f"ivg158_{run}_q"
    for graph, nid, name, vec in (
        (g1, p, "g1p", [1.0, 0.0, 0.0]),
        (g1, q, "g1q", [1.0, 1.0, 0.0]),
        (g2, p, "g2p", [0.6, 0.8, 0.0]),
    ):
        assert engine.create_node(nid, labels=[LABEL], properties={"name": name}, graph=graph)
        assert engine.store_embedding(nid, vec, graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine
    for graph in (g1, g2):
        engine.erase_graph(graph)


def _scores(engine, fn, params=None):
    r = engine.execute_cypher(f"MATCH (n:{LABEL}) RETURN n.name AS name, {fn} AS s", params or {})
    assert r.error is None, r.error
    return sorted((row[0], None if row[1] is None else round(float(row[1]), 5)) for row in r.rows)


def test_an_all_graphs_distance_reads_each_rows_route(g):
    assert _scores(g, "vector_distance(n, [1.0, 0.0, 0.0])") == [
        ("g1p", 0.0),
        ("g1q", round(1 - S, 5)),
        ("g2p", 0.4),
    ]


def test_an_all_graphs_similarity_with_a_parameter_vector(g):
    assert _scores(g, "vector_similarity(n, $v)", {"v": [1.0, 0.0, 0.0]}) == [
        ("g1p", 1.0),
        ("g1q", round(S, 5)),
        ("g2p", 0.6),
    ]


def test_an_all_graphs_distance_filter(g):
    r = g.execute_cypher(
        f"MATCH (n:{LABEL}) WHERE vector_distance(n, $v) < 0.5 AND vector_distance(n, $v) > 0.1 RETURN n.name",
        {"v": [1.0, 0.0, 0.0]},
    )
    assert r.error is None, r.error
    assert sorted(row[0] for row in r.rows) == ["g1q", "g2p"]
