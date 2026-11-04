"""4.6.0 R078: `Graph.KG.PyOps.VectorSearch` and `HybridSearch` call the IVG Schema's procedures.

Both ran `CALL kg_KNN_VEC(...)` / `CALL kg_RRF_FUSE(...)` unqualified through `iris.sql.exec`, which resolves an
unqualified procedure in `SQLUser`, where IVG defines none: neither method could answer. They now name `Graph_KG`,
which the deploy rewrites to the namespace's IVG Schema like every other name in the class (spec 262).

The data, worked by hand, in the default graph (whose table `kg_KNN_VEC` reads): a, b, c labelled with this run's
label, a = [1,0,...], b = [1,1,0,...], c = [0,1,0,...] (zeros to the table's width), and one document, c's: "zebra
crossing".
- VectorSearch(q = [1,0,...], k = 2, label): cosines a 1, b 1/sqrt(2) = 0.70711, c 0, so [a 1.0, b 0.70711].
- HybridSearch(q, "zebra", k = 3, c = 60): vector ranks a 1, b 2, c 3; text ranks c 1 (the only document). A row
  missing from a leg scores 1/(60 + 1e9) for it. rrf: c 1/63 + 1/61, a 1/61 + 1/(60 + 1e9), b 1/62 + 1/(60 + 1e9);
  c first, then a, then b. vs is the cosine; bm25 is c's term count, 1, and None for a and b.
"""

from __future__ import annotations

import contextlib
import json
import math
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

VECTORS = {"a": [1.0, 0.0], "b": [1.0, 1.0], "c": [0.0, 1.0]}
MISSING = 1.0 / (60 + 1_000_000_000)


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    width = int(engine._iris_obj().classMethodValue("Graph.KG.PyOps", "getExpectedDimension"))
    run = uuid.uuid4().hex[:8]
    label = f"Ivg460PyOps{run}"
    ids = {k: f"ivg460pyops_{run}_{k}" for k in "abc"}
    cur = iris_connection.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {engine._schema_prefix}.kg_NodeEmbeddings WHERE COALESCE(graph_id, '') = ''")
    if int(cur.fetchone()[0]):
        pytest.fail("the default graph already holds vectors; the hand-worked ranks assume only this file's")
    for k, nid in ids.items():
        engine.create_node(nid, labels=[label])
        assert engine.store_embedding(nid, _pad(VECTORS[k], width))
    cur.execute(f"INSERT INTO {engine._schema_prefix}.docs (graph_id, id, text) VALUES ('', ?, ?)",
                [ids["c"], "zebra crossing"])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, label, ids, width
    cur.execute(f"DELETE FROM {engine._schema_prefix}.docs WHERE id = ?", [ids["c"]])
    for nid in ids.values():
        engine.delete_node(nid, graph=None)
    with contextlib.suppress(Exception):
        iris_connection.commit()


def _pad(vec, width):
    return list(vec) + [0.0] * (width - len(vec))


def _call(engine, width, method, *args):
    native = engine._iris_obj()
    vec = native.classMethodObject("%Library.DynamicArray", "%FromJSON", json.dumps(_pad([1.0], width)))
    out = native.classMethodObject("Graph.KG.PyOps", method, vec, *args)
    return json.loads(out.invoke("%ToJSON"))


def _named(rows, ids):
    inverse = {v: k for k, v in ids.items()}
    return [inverse.get(r["id"], r["id"]) for r in rows]


def test_vector_search_answers_the_labelled_nearest_neighbours(g):
    engine, label, ids, width = g
    rows = _call(engine, width, "VectorSearch", 2, label, "")
    assert [(n, round(r["score"], 5)) for n, r in zip(_named(rows, ids), rows)] == [
        ("a", 1.0), ("b", round(1 / math.sqrt(2), 5))]


def test_hybrid_search_fuses_the_vector_and_text_ranks(g):
    engine, _, ids, width = g
    rows = _call(engine, width, "HybridSearch", "zebra", 3, 60, "")
    assert _named(rows, ids) == ["c", "a", "b"]
    by = {n: r for n, r in zip(_named(rows, ids), rows)}
    expected = {"c": 1 / 63 + 1 / 61, "a": 1 / 61 + MISSING, "b": 1 / 62 + MISSING}
    assert {n: round(r["score"], 9) for n, r in by.items()} == {n: round(v, 9) for n, v in expected.items()}
    assert {n: (round(r["extras"]["vs"], 5), r["extras"]["bm25"]) for n, r in by.items()} == {
        "c": (0.0, 1.0), "a": (1.0, None), "b": (round(1 / math.sqrt(2), 5), None)}
