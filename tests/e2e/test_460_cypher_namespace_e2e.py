"""4.6.0 R067: `engine.cypher.execute_bfs` and `execute_knn_vec` answer instead of raising AttributeError.

Both forwarded to engine methods of their own names, which do not exist (the R065 class of defect). The answers here
are worked by hand.

BFS graph: a -P-> b -P-> c, a -P-> d, b -Q-> e. Out from a over P to 2 hops reaches b and d at 1 hop and c at 2; e is
reached only over Q, so it is not in the answer.

KNN vectors (768 wide, the session's kg_NodeEmbeddings width), all nodes under one label of this run's own: n1 = x,
n2 = x + y, n3 = y, n4 = -x, where x and y are the first two unit axes. Against the query x the cosines are 1, 1/sqrt(2),
0 and -1, so the three nearest are n1, n2, n3 in that order.
"""

from __future__ import annotations

import contextlib
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

DIM = 768


def _axis(*pairs) -> list:
    v = [0.0] * DIM
    for i, x in pairs:
        v[i] = x
    return v


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=DIM)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"ivg460cy_{run}_{k}" for k in ("a", "b", "c", "d", "e", "n1", "n2", "n3", "n4")}
    label = f"Ivg460Cy{run}"
    for nid in ids.values():
        engine.create_node(nid, labels=[label])
    for s, p, o in (("a", "P", "b"), ("b", "P", "c"), ("a", "P", "d"), ("b", "Q", "e")):
        engine.create_edge(ids[s], f"IVG460CY_{p}", ids[o])
    vectors = {"n1": _axis((0, 1.0)), "n2": _axis((0, 1.0), (1, 1.0)), "n3": _axis((1, 1.0)), "n4": _axis((0, -1.0))}
    for k, v in vectors.items():
        engine.store_embedding(ids[k], v)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, label
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


def test_execute_bfs_answers_the_nodes_and_their_hops(g):
    engine, ids, _ = g
    result = engine.cypher.execute_bfs(ids["a"], ["IVG460CY_P"], 2, "out", 100)
    assert result.error is None, result.error
    col = {name: i for i, name in enumerate(result.columns)}
    inverse = {v: k for k, v in ids.items()}
    hops = {inverse.get(r[col["id"]], r[col["id"]]): int(r[col["hops"]]) for r in result.rows}
    assert hops == {"b": 1, "d": 1, "c": 2}, result.rows


def test_execute_knn_vec_answers_the_nearest_three_in_order(g):
    engine, ids, label = g
    result = engine.cypher.execute_knn_vec(_axis((0, 1.0)), 3, label)
    assert result.error is None, result.error
    inverse = {v: k for k, v in ids.items()}
    got = [(inverse.get(r[0], r[0]), float(r[1])) for r in result.rows]
    assert [k for k, _ in got] == ["n1", "n2", "n3"], got
    assert [s for _, s in got] == pytest.approx([1.0, 1 / math.sqrt(2), 0.0], abs=1e-6), got
