"""Live: IRIS must prepare the thresholded cosine searches and honour the threshold.

Before the fix both statements ended `ORDER BY score DESC HAVING score >= x`, and
IRIS refused them at Prepare with SQLCODE -25. See
`tests/unit/test_vector_score_threshold.py`.
"""

import json
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema


def _unit(dim, hot):
    v = [0.0] * dim
    v[hot % dim] = 1.0
    return v


@pytest.fixture
def thr_graph(iris_connection):
    cur = iris_connection.cursor()
    dim = GraphSchema.get_embedding_dimension(cur) or 4
    eng = IRISGraphEngine(iris_connection, embedding_dimension=dim)
    run = uuid.uuid4().hex[:8]
    graph = f"thr_{run}"
    ids = [f"thr_{run}_{i}" for i in range(4)]
    for i, nid in enumerate(ids):
        eng.create_node(nid, labels=["Thr"], graph=graph)
        eng.store_embedding(nid, _unit(dim, i), graph=graph)
    # Edge rows go in the default graph's route, the one `edge_vector_search` reads
    # with no graph and no model; a named graph with no edge route answers nothing.
    for i in range(3):
        cur.execute(
            "INSERT INTO Graph_KG.kg_EdgeEmbeddings (graph_id, s, p, o_id, emb) "
            "VALUES ('', ?, 'THR', ?, TO_VECTOR(?, DOUBLE, ?))",
            [ids[i], ids[i + 1], json.dumps(_unit(dim, i)), dim],
        )
    iris_connection.commit()
    yield eng, graph, ids, dim
    cur.execute(
        "DELETE FROM Graph_KG.kg_EdgeEmbeddings WHERE graph_id = '' AND s LIKE ?",
        [f"thr_{run}_%"],
    )
    iris_connection.commit()
    eng.erase_graph(graph)


def test_vector_search_threshold_filters(thr_graph):
    eng, graph, ids, dim = thr_graph
    table, _ = eng._route_for_read(graph, None)
    kw = dict(top_k=10, id_col="node_id", graph=graph)
    rows = eng.vector_search(
        f"Graph_KG.{table}", "emb", _unit(dim, 0), score_threshold=0.5, **kw
    )
    assert [r["id"] for r in rows] == [ids[0]]
    assert rows[0]["score"] == pytest.approx(1.0)
    everything = eng.vector_search(f"Graph_KG.{table}", "emb", _unit(dim, 0), **kw)
    assert len(everything) == 4


def test_edge_vector_search_threshold_filters(thr_graph):
    eng, graph, ids, dim = thr_graph
    mine = set(ids)

    def search(**kw):
        found = eng.edge_vector_search(_unit(dim, 1), top_k=1000, **kw)
        return [r for r in found if r["s"] in mine]

    rows = search(score_threshold=0.5)
    assert [(r["s"], r["o_id"]) for r in rows] == [(ids[1], ids[2])]
    assert rows[0]["score"] == pytest.approx(1.0)
    assert len(search()) == 3
