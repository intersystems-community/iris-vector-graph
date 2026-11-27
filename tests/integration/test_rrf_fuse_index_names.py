"""`kg_RRF_FUSE` over a named BM25 index, against a live container.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_rrf_fuse_index_names.py

Two BM25 indexes cover the default graph. `docs` finds the word "zebra" in node B; `a_zebra_other`
(registered first, so it is the one an unnamed call reads) finds it in node C.
The vector leg ranks A, B, C. The fused order for each index is worked out here by hand (see
tests/unit/test_rrf_fuse_index_names.py for the arithmetic), not read from the engine.
"""

from __future__ import annotations

import math
import os

import pytest

from tests.conftest import SESSION_EMBEDDING_DIM

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

DOCS = "docs"
OTHER = "a_zebra_other"
A, B, C = "rrf_idx_A", "rrf_idx_B", "rrf_idx_C"
CLS = "Graph.KG.BM25Index"


def _unit(*weights):
    vec = [0.0] * SESSION_EMBEDDING_DIM
    for i, w in enumerate(weights):
        vec[i] = w
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


@pytest.fixture()
def fusion(iris_connection, iris_master_cleanup):
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM)
    for node, name, alt in (
        (A, "lion pride", "gazelle"),
        (B, "zebra herd", "gazelle"),
        (C, "okapi forest", "zebra"),
    ):
        eng.create_node(node, properties={"name": name, "alt": alt})
    # vector rank for the query e0: A (1.0), B (0.894), C (0.0)
    eng.store_embedding(A, _unit(1.0))
    eng.store_embedding(B, _unit(1.0, 0.5))
    eng.store_embedding(C, _unit(0.0, 0.0, 1.0))
    for name in (DOCS, OTHER):
        eng._iris_obj().classMethodValue(CLS, "Drop", name)
        eng._index_registry.pop(name, None)
    # registered first, so it is the index an unnamed call reads
    eng.bm25_build(OTHER, ["alt"])
    eng.bm25_build(DOCS, ["name"])
    assert "hnsw" in eng._index_registry, "the vector leg needs the server-side route registered"
    assert [h[0] for h in eng.bm25_search(DOCS, "zebra", k=5)] == [B]
    assert [h[0] for h in eng.bm25_search(OTHER, "zebra", k=5)] == [C]
    yield eng
    for name in (DOCS, OTHER):
        eng._iris_obj().classMethodValue(CLS, "Drop", name)


def _mine(rows):
    return [r for r in rows if r[0] in (A, B, C)]


def _query():
    import json

    return json.dumps(_unit(1.0))


def test_fusing_over_docs_answers_b_first(fusion):
    """B is second by vector and first by text over `docs`: 1/62 + 1/61. A, found only by vector, follows."""
    fused = _mine(fusion.kg_RRF_FUSE(10, 10, 10, 60, _query(), "zebra", text_index=DOCS))

    assert [r[0] for r in fused] == [B, A, C]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61, rel=1e-6)
    assert fused[0][3] > 0  # B's text score is the one `docs` gave it
    assert fused[1][3] == 0  # A is not in the text leg


def test_fusing_over_the_other_index_answers_c_first(fusion):
    fused = _mine(fusion.kg_RRF_FUSE(10, 10, 10, 60, _query(), "zebra", text_index=OTHER))

    assert [r[0] for r in fused] == [C, A, B]
    assert fused[0][1] == pytest.approx(1 / 63 + 1 / 61, rel=1e-6)


def test_without_a_name_the_first_listed_index_is_read(fusion, caplog):
    """The default is unchanged: 'a_zebra_other' was registered before 'docs'. It is now said at WARNING."""
    import logging

    with caplog.at_level(logging.WARNING, logger="iris_vector_graph._engine.vector"):
        fused = _mine(fusion.kg_RRF_FUSE(10, 10, 10, 60, _query(), "zebra"))

    assert [r[0] for r in fused] == [C, A, B]
    assert any("text_index" in r.getMessage() and DOCS in r.getMessage() for r in caplog.records)


def test_a_text_leg_that_fails_raises(fusion):
    """Not a vector-only fusion: the text leg's failure reaches the caller."""
    from iris_vector_graph.errors import FusionLegError

    fusion._index_registry["dropped_idx"] = "bm25"
    fusion._index_graph_scopes["dropped_idx"] = ""  # registered for the default graph, but nothing is in ^BM25Idx

    original = fusion.bm25_search

    def failing(name, query, k=10, **kw):
        if name == "dropped_idx":
            raise RuntimeError("index is locked by another writer")
        return original(name, query, k=k, **kw)

    fusion.bm25_search = failing
    with pytest.raises(FusionLegError) as caught:
        fusion.kg_RRF_FUSE(10, 10, 10, 60, _query(), "zebra", text_index="dropped_idx")

    assert caught.value.legs == {"text": "dropped_idx"}
    assert "locked by another writer" in str(caught.value)
