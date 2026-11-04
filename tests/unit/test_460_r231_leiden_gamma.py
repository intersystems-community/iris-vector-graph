"""4.6.0 R231: the Python-side Leiden routes that cannot compute the Constant Potts Model refuse `gamma != 1.0`.

COMMUNITIES defines gamma: 1.0 is modularity, any other value the Constant Potts Model (leidenalg's
CPMVertexPartition). The networkx fallback (no leidenalg) passed gamma to `louvain_communities(resolution=...)`, which
scales modularity's expected edges, another objective, and answered it as Leiden. Arno's `kg_leiden_run` was asked
with any gamma, unchecked. Now the fallback returns an error naming the model and what it needs, and Arno is asked at
1.0 only, so `execute_leiden` goes on to leidenalg. The live proof per route, ObjectScript included, is
tests/e2e/test_460_r231_leiden_gamma_e2e.py.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from tests.unit.test_460_r216_betweenness_undirected import _FakeLazyKG

# CI installs [dev,full], which has both. Imported here, before any patch.dict(sys.modules): one that removed igraph
# on exit would leave it unimportable ("already initialized in a different Python interpreter").
import igraph  # noqa: E402,F401
import leidenalg  # noqa: E402,F401

EDGES = [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")]


def _store():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = mock.Mock()
    store._iris_obj = lambda: mock.Mock()
    return store


def _lazy(store, gamma, hide_leidenalg):
    patches = {"leidenalg": None} if hide_leidenalg else {}
    with mock.patch.dict(sys.modules, patches), \
            mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=_FakeLazyKG(EDGES)):
        return store._leiden_lazykg(10, gamma, 1e-4, 0, 256, 0, None)


@pytest.mark.parametrize("gamma", [0.5, 2.0])
def test_the_networkx_fallback_refuses_cpm(gamma):
    result = _lazy(_store(), gamma, hide_leidenalg=True)
    assert result.rows == []
    assert "Constant Potts" in result.error and f"gamma={gamma}" in result.error and "leidenalg" in result.error


def test_the_networkx_fallback_answers_modularity():
    result = _lazy(_store(), 1.0, hide_leidenalg=True)
    assert result.error is None
    groups = {}
    for node, community, _size in result.rows:
        groups.setdefault(community, set()).add(node)
    assert sorted(sorted(g) for g in groups.values()) == [["a", "b", "c"], ["x", "y", "z"]]


@pytest.mark.parametrize("gamma", [0.5, 2.0])
def test_arno_is_not_asked_for_cpm(gamma):
    from iris_vector_graph.stores.arno_bridge import ArnoError

    with mock.patch("iris_vector_graph.stores.arno_bridge.build_kg_adjacency_chunked") as upload, \
            mock.patch("iris_vector_graph.stores.arno_bridge.arno_call") as call:
        with pytest.raises(ArnoError, match="Constant Potts"):
            _store()._leiden_arno(10, gamma, 1e-4, 0, 256, 0)
    upload.assert_not_called()
    call.assert_not_called()


def test_execute_leiden_goes_past_arno_to_leidenalg_for_cpm(monkeypatch):
    monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    store = _store()
    store._named_graph_with_edges = lambda: "g2"  # past ^NKG, to Arno and then this process
    with mock.patch("iris_vector_graph.stores.arno_bridge.arno_call") as call, \
            mock.patch("iris_vector_graph.stores.lazy_kg.LazyKG", return_value=_FakeLazyKG(EDGES)):
        result = store.execute_leiden(10, 2.0, 1e-4, 0, 256, 0, None)
    call.assert_not_called()
    assert result.error is None
    assert sorted(size for _n, _c, size in result.rows) == [1] * 6  # CPM at 2.0: singletons
