"""4.6.0 R223: a vector query of the wrong width raises ``ValueError`` naming both widths, never ``[]``.

Walkthrough 2 (W14) queried 4-wide vectors with a 3-wide vector and got a log line and ``[]``. The width check
needs a width to compare with; the walkthrough's registry route recorded none, which this file reproduces by
clearing the route's recorded dimension (the state a row adopted without a declared width is in), and restores.

By hand, with the column ``w`` wide (``w`` read from the catalog) and two nodes embedded as the unit vectors
``e0`` and ``e1``: querying ``e0`` answers ``a`` first with cosine 1.0; querying a ``w - 1``-wide vector raises
``ValueError`` naming ``w - 1`` dimensions and ``w``-dimensional vectors, with the route's width recorded and without.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.schema import GraphSchema

    cur = iris_connection.cursor()
    width = GraphSchema.get_embedding_dimension(cur, "Graph_KG.kg_NodeEmbeddings")
    assert width and width >= 2, width
    e = IRISGraphEngine(iris_connection, embedding_dimension=int(width))
    run = uuid.uuid4().hex[:8]
    a, b = f"r223_{run}_a", f"r223_{run}_b"
    for nid in (a, b):
        assert e.create_node(nid, labels=["V223"])
    unit = lambda i: [1.0 if j == i else 0.0 for j in range(width)]  # noqa: E731
    assert e.store_embeddings([{"node_id": a, "embedding": unit(0)}, {"node_id": b, "embedding": unit(1)}])
    yield e, int(width), a, b, iris_connection
    for nid in (a, b):
        with contextlib.suppress(Exception):
            e.delete_node(nid, graph=None)


@contextlib.contextmanager
def _route_without_width(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT dimension FROM Graph_KG.embedding_registry WHERE table_name = 'kg_NodeEmbeddings' "
        "AND COALESCE(graph_id, '') = '' AND kind = 'node'"
    )
    rows = cur.fetchall()
    cur.execute(
        "UPDATE Graph_KG.embedding_registry SET dimension = NULL WHERE table_name = 'kg_NodeEmbeddings' "
        "AND COALESCE(graph_id, '') = '' AND kind = 'node'"
    )
    conn.commit()
    try:
        yield
    finally:
        if rows:
            cur.execute(
                "UPDATE Graph_KG.embedding_registry SET dimension = ? WHERE table_name = 'kg_NodeEmbeddings' "
                "AND COALESCE(graph_id, '') = '' AND kind = 'node'",
                [rows[0][0]],
            )
            conn.commit()


def test_the_right_width_answers(g):
    e, width, a, _, _ = g
    hits = e.kg_KNN_VEC(json.dumps([1.0] + [0.0] * (width - 1)), k=50)
    assert hits[0][0] == a and abs(hits[0][1] - 1.0) < 1e-9


@pytest.mark.parametrize("recorded", [True, False])
def test_a_wrong_width_raises_naming_both(g, recorded):
    from iris_vector_graph import IRISGraphEngine

    e, width, _, _, conn = g
    ctx = contextlib.nullcontext() if recorded else _route_without_width(conn)
    with ctx:
        fresh = IRISGraphEngine(conn, embedding_dimension=width)  # no route cached from before the update
        for search in (
            lambda: fresh.search_nodes_by_vector([1.0] + [0.0] * (width - 2), k=2),
            lambda: fresh.kg_KNN_VEC(json.dumps([1.0] + [0.0] * (width - 2)), k=2),
        ):
            with pytest.raises(ValueError) as exc:
                search()
            assert f"{width - 1} dimensions" in str(exc.value)
            assert f"{width}-dimensional" in str(exc.value)
