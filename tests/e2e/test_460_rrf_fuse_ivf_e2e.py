"""4.6.0 R086: `kg_RRF_FUSE` with an IVF index fuses the IVF leg's hits.

`ivf_search` answers `(node_id, score)` tuples; `kg_RRF_FUSE` read them as dicts (`r["id"]`), raised TypeError inside
its try, logged it, and fused nothing: with an IVF index the answer was `[]`, even with a BM25 leg (the BM25 search came
after the IVF one in the same try).

The graph, worked by hand. Graph G holds a, b, c with the engine's own model, 2 wide: a = [1,0], b = [0.8,0.6],
c = [0,1], and a `name` property each: a "apple", b "banana", c "cherry". The IVF index has one cell (nlist = 1), so its
search is exact: against q = [1,0] the cosines are a 1, b 0.8, c 0, ranks a 1, b 2, c 3. With c = 60:
- no text: each node's missing text rank is len(text hits) + c = 60, so rrf = 1/(60 + r) + 1/(60 + 60):
  a 1/61 + 1/120, b 1/62 + 1/120, c 1/63 + 1/120; vector scores 1, 0.8, 0; text scores 0.
- text "cherry" (a BM25 index over `name`): the text leg holds c alone, rank 1, so a missing text rank is 1 + 60:
  c 1/63 + 1/61, a 1/61 + 1/121, b 1/62 + 1/121; c first, then a, then b.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

VECTORS = {"a": [1.0, 0.0], "b": [0.8, 0.6], "c": [0.0, 1.0]}
NAMES = {"a": "apple", "b": "banana", "c": "cherry"}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    graph = f"ivg460rrf_{run}"
    ids = {k: f"ivg460rrf_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        engine.create_node(nid, labels=["Ivg460Rrf"], properties={"name": NAMES[k]}, graph=graph)
        assert engine.store_embedding(nid, VECTORS[k], graph=graph)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    ivf, bm25 = f"ivg460rrf_ivf_{run}", f"ivg460rrf_bm25_{run}"
    engine.ivf_build(ivf, nlist=1, graph=graph)
    # The IVF leg is the one under test. An engine whose native vector probe answers registers an `hnsw` entry, and
    # the fusion takes the first eligible vector entry in registry order, `hnsw` whatever the graph; on this container
    # it is registered at construction, before the index built above, so the IVF leg would never be asked. Dropped
    # from this engine so the leg the defect is in is the leg that runs.
    engine._index_registry.pop("hnsw", None)
    yield engine, graph, ids, ivf, bm25
    with contextlib.suppress(Exception):
        engine.bm25_drop(bm25)
    engine.ivf_drop(ivf)
    engine.erase_graph(graph)


def _named(rows, ids):
    inverse = {v: k for k, v in ids.items()}
    return [(inverse.get(r[0], r[0]), round(r[1], 9), round(r[2], 5), round(r[3], 5)) for r in rows]


def test_the_ivf_leg_alone_is_fused(g):
    engine, graph, ids, ivf, _ = g
    assert [i for i in engine._index_registry if engine._index_registry[i] in ("ivf", "hnsw", "bm25")
            and engine._index_graph(i, engine._index_registry[i]) == graph] == [ivf]
    rows = engine.kg_RRF_FUSE(k=3, k1=3, k2=3, c=60, query_vector=json.dumps([1.0, 0.0]), query_text="", graph=graph)
    assert _named(rows, ids) == [
        ("a", round(1 / 61 + 1 / 120, 9), 1.0, 0.0),
        ("b", round(1 / 62 + 1 / 120, 9), 0.8, 0.0),
        ("c", round(1 / 63 + 1 / 120, 9), 0.0, 0.0),
    ]


def test_the_ivf_and_bm25_legs_are_fused(g):
    engine, graph, ids, _, bm25 = g
    engine.bm25_build(bm25, ["name"], graph=graph)
    rows = engine.kg_RRF_FUSE(k=3, k1=3, k2=3, c=60, query_vector=json.dumps([1.0, 0.0]), query_text="cherry",
                              graph=graph)
    assert [(n, r) for n, r, _, _ in _named(rows, ids)] == [
        ("c", round(1 / 63 + 1 / 61, 9)), ("a", round(1 / 61 + 1 / 121, 9)), ("b", round(1 / 62 + 1 / 121, 9))]
    vector_scores = {n: v for n, _, v, _ in _named(rows, ids)}
    assert vector_scores == {"a": 1.0, "b": 0.8, "c": 0.0}
    text = {n: t for n, _, _, t in _named(rows, ids)}
    assert text["a"] == text["b"] == 0.0 and text["c"] > 0
