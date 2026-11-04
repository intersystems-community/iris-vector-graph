"""4.6.0 R127: degree, betweenness, closeness and eigenvector centrality and personalized PageRank raise when the path
that ran fails, as the community methods do since R105.

Each answered ``[]`` (PPR: ``{}``) when the store's path reported an error, so a failure read as a graph with no
nodes. Each test forces the failure in the step the store's method calls, on the engine's real store, and pins every
other branch: the degree path's own fallback triggers only on "CLASS DOES NOT EXIST" / "DBSRV", so the error here
names neither; PPR's Native Reader is made to answer nothing and its result cache is off, so the Owner's
``Graph.KG.PageRank.RunJson`` is the step that fails.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine

FAILURE = "<UNDEFINED> Graph.KG.Centrality forced failure"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return IRISGraphEngine(conn, embedding_dimension=4)


def test_degree_centrality_raises_the_classmethod_failure():
    eng = _engine()
    with patch.object(eng._store, "_call_classmethod", side_effect=RuntimeError(FAILURE)) as call:
        with pytest.raises(RuntimeError, match="degree_centrality failed: .*forced failure"):
            eng.degree_centrality(direction="out")
    assert call.call_args.args[:2] == ("Graph.KG.Centrality", "DegreeCentralityJson")


@pytest.mark.parametrize("method,step,kwargs", [
    ("betweenness_centrality", "_betweenness_gref", {}),
    ("closeness_centrality", "_closeness_gref", {}),
    ("eigenvector_centrality", "_eigenvector_gref", {}),
])
def test_centrality_raises_the_failure_of_the_path_that_ran(method, step, kwargs):
    eng = _engine()
    with patch.object(eng._store, step, side_effect=RuntimeError(FAILURE)) as ran:
        with pytest.raises(RuntimeError, match=f"{method} failed: .*forced failure"):
            getattr(eng, method)(**kwargs)
    assert ran.called


def _ppr_owner_fails(eng):
    eng._store._algo_cache_off = True
    return (
        patch.object(eng._store, "_native_answer", return_value=None),
        patch.object(eng._store, "_call_classmethod", side_effect=RuntimeError(FAILURE)),
    )


@pytest.mark.parametrize("call", [
    lambda e: e.kg_PERSONALIZED_PAGERANK(["n1"]),
    lambda e: e.kg_PPR(["n1"]),
    lambda e: e.ppr("n1"),
], ids=["kg_PERSONALIZED_PAGERANK", "kg_PPR", "ppr"])
def test_personalized_pagerank_raises_the_owner_failure(call):
    eng = _engine()
    native, owner = _ppr_owner_fails(eng)
    with native, owner as run_json, patch.object(eng, "_detect_arno", return_value=False):
        with pytest.raises(RuntimeError, match="kg_PERSONALIZED_PAGERANK failed: .*forced failure"):
            call(eng)
    assert run_json.call_args.args[:2] == ("Graph.KG.PageRank", "RunJson")


def test_personalized_pagerank_without_a_store_path_still_uses_the_fallback():
    """A store that does not offer PPR is not a failure: the SQL fallback answers, as before."""
    eng = _engine()
    eng._store_capabilities = {**eng._store_capabilities, "ppr": False}
    eng.capabilities = IRISCapabilities()  # nothing deployed: no RunJson fast path either
    with patch.object(eng, "_kg_PERSONALIZED_PAGERANK_python_fallback", return_value={"n1": 1.0}) as fallback:
        assert eng.kg_PERSONALIZED_PAGERANK(["n1"]) == {"n1": 1.0}
    assert fallback.called
