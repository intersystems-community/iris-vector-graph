"""4.6.3, live: the search indexes and the BFS pager answer ids holding a quote or a backslash exactly.

The sweep of ``iris_src/src/Graph/KG`` for JSON built by concatenation (the R306 fix covered Communities and the
centrality classes; tests/e2e/test_463_json_id_escaping_e2e.py) found more ids written between quotes unescaped:

- ``Graph.KG.BM25Index.Search`` escaped a quote but not a backslash, so ``c\\d`` (``\\d`` is no JSON escape) broke
  ``bm25_search`` and ``CALL ivg.bm25.search``;
- ``Graph.KG.PLAIDSearch.Search`` and ``Graph.KG.VecIndex.Search`` escaped nothing;
- ``Graph.KG.TraversalBFS.ReadBFSPage`` wrote its ``next_o`` cursor (a node id) escaping a quote only.

Each answer is checked against one computed here: BM25 scores against a twin document with a plain id and the same
text (the same score, so the id is all that differs), PLAID's MaxSim and the vector index's cosine with numpy.

Runs in the scratch namespace ``IVG_NAMESPACE`` names (IVG463*, IVG460* or IVG262*, IVG installed); erases its graph
and drops the indexes it builds.

    IVG_NAMESPACE=IVG463B pytest tests/e2e/test_463_search_id_escaping_e2e.py
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith(("IVG463", "IVG460", "IVG262")),
                       reason="IVG_NAMESPACE must name an IVG463*/IVG460*/IVG262* scratch namespace: it erases its graph"),
]

Q, B, QB = 'a"b', "c\\d", 'x"\\y'
LION = 'r"\\s'
IDX = "t463esc"


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(ns262.connect(NS))
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    yield e
    for drop in (lambda: e.vec_drop(IDX), lambda: e.plaid_drop(IDX)):
        drop()
    ns262.native(e.conn).classMethodVoid("Graph.KG.BM25Index", "Drop", IDX)
    ns262.native(e.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    e.conn.close()


def test_bm25_search_answers_the_ids_exactly(engine):
    for v in (Q, B, QB, "plain"):
        engine.create_node(v, properties={"text": "zebra giraffe"})
    engine.create_node("other", properties={"text": "lion"})
    engine.create_node(LION, properties={"text": "lion"})
    assert engine.bm25_build(IDX, ["text"])["indexed"] == 6
    got = dict(engine.bm25_search(IDX, "zebra", k=10))
    assert set(got) == {Q, B, QB, "plain"}
    # the same text scores the same: the id is all that differs between the four
    assert got[Q] == got[B] == got[QB] == got["plain"] > 0
    rows = engine.execute_cypher(
        f"CALL ivg.bm25.search('{IDX}', 'zebra', 10) YIELD node, score RETURN node.node_id, score").rows
    assert {r[0]: r[1] for r in rows} == pytest.approx(got)


def test_bm25_search_report_answers_the_ids_exactly(engine):
    report = engine.bm25_search(IDX, "zebra lion", k=10, max_df_fraction=0.5, report=True)
    # zebra is in 4 of the 6 documents, over the cutoff; lion's two documents score alike
    assert report["skipped"] == ["zebra"]
    got = {r[0]: r[1] for r in report["results"]}
    assert set(got) == {"other", LION} and got["other"] == got[LION] > 0


def test_plaid_search_answers_the_ids_and_maxsim(engine):
    docs = [
        {"id": Q, "tokens": [[1.0, 0.0], [0.0, 1.0]]},
        {"id": B, "tokens": [[1.0, 0.0]]},
        {"id": QB, "tokens": [[0.6, 0.8]]},
    ]
    engine.plaid_build(IDX, docs, n_clusters=2)
    query = [[1.0, 0.0], [0.0, 1.0]]
    want = {d["id"]: float(sum(max(np.dot(q, t) for t in d["tokens"]) for q in query)) for d in docs}
    assert want == {Q: 2.0, B: 1.0, QB: pytest.approx(1.4)}
    got = {d["id"]: d["score"] for d in engine.plaid_search(IDX, query, k=10, nprobe=10)}
    assert got == pytest.approx(want, abs=1e-7)


def test_vec_search_answers_the_ids_and_cosine(engine):
    vecs = {Q: [1.0, 0.0, 0.0], B: [0.6, 0.8, 0.0], QB: [0.0, 0.0, 1.0]}
    engine.vec_create_index(IDX, 3, "cosine")
    for k, v in vecs.items():
        engine.vec_insert(IDX, k, v)
    engine.vec_build(IDX)
    query = [1.0, 0.0, 0.0]
    # the cosine distance, 1 - cosine similarity (its `score` is minus the distance: VecIndex.Search)
    want = {k: 1.0 - float(np.dot(query, v) / (np.linalg.norm(query) * np.linalg.norm(v))) for k, v in vecs.items()}
    got = {d["id"]: d["distance"] for d in engine.vec_search(IDX, query, k=10)}
    assert got == pytest.approx(want, abs=1e-9)


def test_the_bfs_pager_carries_an_escaped_cursor(engine):
    from iris_vector_graph.engine import _bfs_stream_pages

    nat = ns262.native(engine.conn)
    tag = "t463esc"
    items = [(1, Q, "s0", "R"), (1, B, "s0", "R"), (2, QB, B, "R")]
    # ^ArnoKG("bfs_r", tag, step, o) = $ListBuild(s, p, w), what the BFS routes stage for the pager
    for step, o, s, p in items:
        nat.set(_listbuild(nat, s, p, 1), "ArnoKG", "bfs_r", tag, step, o)
    got = list(_bfs_stream_pages(engine.conn, tag, page_size=1))
    assert [(r["step"], r["o"], r["s"]) for r in got] == [(1, Q, "s0"), (1, B, "s0"), (2, QB, B)]


def _listbuild(nat, *items):
    from iris import IRISList

    lb = IRISList()
    for x in items:
        lb.add(x)
    return lb.getBuffer()
