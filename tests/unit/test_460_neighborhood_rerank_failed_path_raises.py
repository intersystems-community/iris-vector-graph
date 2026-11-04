"""4.6.0 R145: `betweenness_centrality_neighborhood` and `bfs_vector_rerank` raise when the store reports an error,
as the centrality methods do since R127.

Both answered ``[]`` on a store error, so a failure read as a seed with no neighbourhood. The neighbourhood tests run
on the engine's real store with the step it calls patched (`_iris_obj`, whose `classMethodValue` the store calls for
`Graph.KG.NKGAccel.IsLoaded` and then `BetweennessNeighborhood`): once raising, once answering the `ERROR:` text the
ObjectScript method returns for a seed `^NKG` does not hold. No store IVG ships implements
`execute_bfs_vector_rerank`, so its store-error case runs on a store class that defines it, and the engine's real store
gets `NotImplementedError` naming the missing method instead of an `AttributeError`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

FAILURE = "<UNDEFINED> Graph.KG.NKGAccelCentrality forced failure"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return IRISGraphEngine(conn, embedding_dimension=4)


def _native(neighborhood):
    """A native connection: Arno loaded (so no fallback warning), and `neighborhood` for BetweennessNeighborhood."""
    native = MagicMock()

    def class_method_value(cls, method, *args):
        if (cls, method) == ("Graph.KG.NKGAccel", "IsLoaded"):
            return 1
        assert (cls, method) == ("Graph.KG.NKGAccel", "BetweennessNeighborhood"), (cls, method)
        if isinstance(neighborhood, Exception):
            raise neighborhood
        return neighborhood

    native.classMethodValue.side_effect = class_method_value
    return native


def test_neighborhood_betweenness_raises_the_classmethod_failure():
    eng = _engine()
    native = _native(RuntimeError(FAILURE))
    with patch.object(eng._store, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match="betweenness_centrality_neighborhood failed: .*forced failure"):
            eng.betweenness_centrality_neighborhood("seed", hops=2)
    assert native.classMethodValue.call_args.args[:2] == ("Graph.KG.NKGAccel", "BetweennessNeighborhood")


def test_neighborhood_betweenness_raises_the_error_the_objectscript_method_answers():
    eng = _engine()
    with patch.object(eng._store, "_iris_obj", return_value=_native("ERROR:seed node not found: seed")):
        with pytest.raises(RuntimeError, match="betweenness_centrality_neighborhood failed: ERROR:seed node not found"):
            eng.betweenness_centrality_neighborhood("seed")


def test_neighborhood_betweenness_still_answers_its_scores():
    eng = _engine()
    answer = 'OK:[{"id":"b","score":2.0},{"id":"a","score":0.5}]'
    with patch.object(eng._store, "_iris_obj", return_value=_native(answer)):
        assert eng.betweenness_centrality_neighborhood("a", top_k=5) == [
            {"id": "b", "score": 2.0}, {"id": "a", "score": 0.5}]


class _RerankStore:
    """A store that offers the rerank, as a third-party store may: IVG's own does not."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_bfs_vector_rerank(self, seed, query_vec, hops, top_k, max_buckets):
        self.calls.append((seed, list(query_vec), hops, top_k, max_buckets))
        return self.result


def test_bfs_vector_rerank_raises_the_store_error():
    eng = _engine()
    eng._store = _RerankStore(IVGResult(columns=["id", "score", "hops"], rows=[], error="NICHE index not built"))
    with pytest.raises(RuntimeError, match="bfs_vector_rerank failed: NICHE index not built"):
        eng.bfs_vector_rerank("seed", [1.0, 0.0, 0.0, 0.0], hops=1, top_k=5)
    assert eng._store.calls == [("seed", [1.0, 0.0, 0.0, 0.0], 1, 5, 32)]


def test_bfs_vector_rerank_still_answers_the_store_rows():
    eng = _engine()
    eng._store = _RerankStore(IVGResult(columns=["id", "score", "hops"], rows=[["n1", 0.95, 1]]))
    assert eng.bfs_vector_rerank("seed", [1.0, 0.0, 0.0, 0.0]) == [{"id": "n1", "score": 0.95, "hops": 1}]


def test_bfs_vector_rerank_on_a_store_without_it_says_so():
    eng = _engine()
    assert not hasattr(eng._store, "execute_bfs_vector_rerank")
    with pytest.raises(NotImplementedError, match="execute_bfs_vector_rerank"):
        eng.bfs_vector_rerank("seed", [1.0, 0.0, 0.0, 0.0])
