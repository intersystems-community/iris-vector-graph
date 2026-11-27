"""`kg_RRF_FUSE` names the index each leg reads, and a leg that fails is raised, not dropped.

Before 4.7.2 the method took no index name. Its text leg read the first BM25 index the registry
listed for the graph, so with two BM25 indexes over one graph a fusion meant for `docs` fused
the other one and nothing said so. And one `try` wrapped both legs: a failed text search was
logged and the fusion went on with the vector leg alone, which is a different ranking that looks
like an answer.

Pure Python: the engine is a stand-in answering from tables written out here. The expected
ranking is worked out by hand in `test_the_named_index_decides_the_ranking`, not read from the
code under test. A live check is in tests/integration/test_rrf_fuse_index_names.py.
"""

from __future__ import annotations

import logging

import pytest

from iris_vector_graph._engine.vector import VectorMixin
from iris_vector_graph.errors import IndexNotFoundError
from iris_vector_graph.sdk import IVGError

C = 60

#: What each BM25 index answers for the query "zebra". `a_other` sorts before `docs` in the registry.
BM25 = {
    "docs": [("B", 3.0)],
    "a_other": [("C", 2.0)],
}
#: Vector leg, best first: A closest, then B, then C.
VECTOR = [("A", 0.99), ("B", 0.90), ("C", 0.10)]


class _Engine:
    """Just what `kg_RRF_FUSE` calls on its engine, over fixed answers."""

    def __init__(self, registry=None, bm25=None, vector=None):
        self._index_registry = registry if registry is not None else {"a_other": "bm25", "docs": "bm25", "hnsw": "hnsw"}
        self._bm25 = BM25 if bm25 is None else bm25
        self._vector = VECTOR if vector is None else vector
        self.searched = []

    def _index_graph(self, name, index_type):
        return ""  # every index here was built for the default graph

    def _fusion_leg_index(self, kinds, graph_id):
        return VectorMixin._fusion_leg_index(self, kinds, graph_id)

    def _named_fusion_index(self, param, name, kinds, graph_id):
        return VectorMixin._named_fusion_index(self, param, name, kinds, graph_id)

    def _warn_if_text_index_ambiguous(self, chosen, graph_id):
        return VectorMixin._warn_if_text_index_ambiguous(self, chosen, graph_id)

    def bm25_search(self, name, query, k=10, **_):
        self.searched.append(("bm25", name))
        answer = self._bm25[name]
        if isinstance(answer, Exception):
            raise answer
        return answer[:k]

    def kg_KNN_VEC(self, query_vector, k=50, label_filter=None, dtype=None, *, graph=None, model_key=None):
        self.searched.append(("vec", "hnsw"))
        if isinstance(self._vector, Exception):
            raise self._vector
        return self._vector[:k]

    def ivf_search(self, name, vec, k=10):
        self.searched.append(("ivf", name))
        return self._vector[:k]


def _fuse(engine, **kw):
    return VectorMixin.kg_RRF_FUSE(engine, 5, 10, 10, C, "[0.1, 0.2]", kw.pop("text", "zebra"), **kw)


def test_the_named_index_decides_the_ranking():
    """`docs` finds B (vector rank 2, text rank 1); `a_other` would find C. The fused order differs.

    By hand, with c = 60 and a leg that did not find a node ranking len(leg) + c:
      B = 1/(60+2) + 1/(60+1) = 0.032522
      A = 1/(60+1) + 1/(60+1+60) = 0.016393 + 0.008264 = 0.024657
      C = 1/(60+3) + 1/(60+1+60) = 0.015873 + 0.008264 = 0.024137
    """
    fused = _fuse(_Engine(), text_index="docs")

    assert [row[0] for row in fused] == ["B", "A", "C"]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0][3] == 3.0  # the docs index's own score for B
    assert fused[1][1] == pytest.approx(1 / 61 + 1 / 121)
    assert fused[2][1] == pytest.approx(1 / 63 + 1 / 121)


def test_the_other_index_gives_the_other_ranking():
    """The same call naming the other index: C (vector rank 3, text rank 1) takes the top."""
    fused = _fuse(_Engine(), text_index="a_other")

    assert [row[0] for row in fused] == ["C", "A", "B"]
    assert fused[0][1] == pytest.approx(1 / 63 + 1 / 61)


def test_the_named_index_is_the_one_searched():
    engine = _Engine()

    _fuse(engine, text_index="docs")

    assert ("bm25", "docs") in engine.searched
    assert ("bm25", "a_other") not in engine.searched


def test_without_a_name_the_first_listed_index_is_still_read_and_the_choice_is_logged(caplog):
    """The default is unchanged (callers with one index lose nothing), but a choice among several is said aloud."""
    engine = _Engine()

    with caplog.at_level(logging.WARNING, logger="iris_vector_graph._engine.vector"):
        fused = _fuse(engine)

    assert ("bm25", "a_other") in engine.searched
    assert [row[0] for row in fused] == ["C", "A", "B"]
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "a_other" in said and "docs" in said and "text_index" in said, said


def test_a_name_that_is_not_an_index_raises_and_lists_the_known_ones():
    with pytest.raises(IndexNotFoundError) as caught:
        _fuse(_Engine(), text_index="nope")

    assert "nope" in str(caught.value) and "docs" in str(caught.value)


def test_an_index_built_for_another_graph_is_refused():
    engine = _Engine()
    engine._index_graph = lambda name, index_type: "elsewhere"

    with pytest.raises(ValueError, match="elsewhere"):
        _fuse(engine, text_index="docs", graph="here")


def test_a_name_of_the_wrong_kind_raises():
    with pytest.raises(ValueError, match="text_index.*bm25"):
        _fuse(_Engine(), text_index="hnsw")


def test_an_empty_text_query_skips_the_leg_and_needs_no_index():
    engine = _Engine(registry={"hnsw": "hnsw"})

    fused = _fuse(engine, text="")

    assert [row[0] for row in fused] == ["A", "B", "C"]
    assert not [s for s in engine.searched if s[0] == "bm25"]


def test_a_named_vector_index_is_read():
    engine = _Engine(registry={"ivf1": "ivf", "docs": "bm25", "hnsw": "hnsw"})

    _fuse(engine, vector_index="ivf1", text_index="docs")

    assert ("ivf", "ivf1") in engine.searched
    assert ("vec", "hnsw") not in engine.searched


def test_a_vector_index_name_that_is_a_bm25_index_raises():
    with pytest.raises(ValueError, match="vector_index"):
        _fuse(_Engine(), vector_index="docs", text_index="docs")


# --- a failed leg is not a missing leg ------------------------------------------------------------


def test_a_failed_text_leg_raises_and_names_the_leg_and_the_index():
    from iris_vector_graph.errors import FusionLegError

    engine = _Engine(bm25={"docs": RuntimeError("index is locked"), "a_other": []})

    with pytest.raises(FusionLegError) as caught:
        _fuse(engine, text_index="docs")

    assert isinstance(caught.value, IVGError)
    assert caught.value.legs == {"text": "docs"}
    assert "text" in str(caught.value) and "docs" in str(caught.value) and "index is locked" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_failed_vector_leg_raises_and_does_not_hide_behind_the_text_leg():
    from iris_vector_graph.errors import FusionLegError

    engine = _Engine(vector=RuntimeError("route refused"))

    with pytest.raises(FusionLegError) as caught:
        _fuse(engine, text_index="docs")

    assert caught.value.legs == {"vector": "hnsw"}
    assert "route refused" in str(caught.value)
    # the text leg still ran: one leg's failure does not stop the other from being tried and reported
    assert ("bm25", "docs") in engine.searched


def test_both_legs_failing_are_both_reported():
    from iris_vector_graph.errors import FusionLegError

    engine = _Engine(vector=RuntimeError("route refused"), bm25={"docs": RuntimeError("locked"), "a_other": []})

    with pytest.raises(FusionLegError) as caught:
        _fuse(engine, text_index="docs")

    assert caught.value.legs == {"vector": "hnsw", "text": "docs"}
    assert "route refused" in str(caught.value) and "locked" in str(caught.value)
