"""Spec 162 — Centrality unit tests.

Test-first (Constitution Principle III): these tests are written BEFORE the
implementation in tasks T024–T028 (Degree), T036–T046 (Betweenness), T055–T063
(Closeness), T071–T079 (Eigenvector). They MUST FAIL until those tasks land.

Once the engine wrappers + IRISGraphStore methods are implemented, these tests
verify routing through the GraphStore Protocol — they do NOT exercise live IRIS.
The corresponding e2e tests live in tests/e2e/test_centrality_e2e.py.
"""

import os

import pytest

from iris_vector_graph.result import IVGResult
from iris_vector_graph.store_protocol import GraphStore
from iris_vector_graph._validate import (
    DegreeCentralityInput, BetweennessInput, ClosenessInput, EigenvectorInput,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


CENTRALITY_CAPS = {
    "native_sql": False,
    "bfs": True, "shortest_path": True, "weighted_shortest_path": True,
    "ppr": True, "pagerank": True, "wcc": True, "cdlp": True,
    "subgraph": True, "knn_vec": True,
    "temporal_edges": True, "temporal_window_query": True,
    "temporal_cypher": True, "temporal_aggregate": True,
    "degree_centrality": True, "betweenness": True,
    "closeness": True, "eigenvector": True,
}


class CentralityMockStore:
    """Minimal store that records calls to the 4 centrality methods."""

    def __init__(self, caps_override=None):
        self.called_methods = []
        self.last_call = {}
        self._caps = caps_override or CENTRALITY_CAPS

    def _record(self, method, **kwargs):
        self.called_methods.append(method)
        self.last_call = kwargs
        return IVGResult(columns=["id", "score"], rows=[["n0", 1.0]])

    def execute_degree_centrality(self, direction, predicate, top_k):
        self.called_methods.append("execute_degree_centrality")
        self.last_call = dict(direction=direction, predicate=predicate, top_k=top_k)
        return IVGResult(columns=["id", "score", "degree"], rows=[["n0", 0.5, 3]])

    def execute_betweenness(self, sample_size, direction, max_hops, top_k,
                             mem_budget_mb, progress_callback=None):
        return self._record("execute_betweenness", sample_size=sample_size,
                            direction=direction, max_hops=max_hops, top_k=top_k,
                            mem_budget_mb=mem_budget_mb, has_callback=progress_callback is not None)

    def execute_closeness(self, formula, direction, max_hops, top_k, progress_callback=None):
        return self._record("execute_closeness", formula=formula, direction=direction,
                            max_hops=max_hops, top_k=top_k,
                            has_callback=progress_callback is not None)

    def execute_eigenvector(self, max_iter, tol, top_k, progress_callback=None):
        return self._record("execute_eigenvector", max_iter=max_iter, tol=tol,
                            top_k=top_k, has_callback=progress_callback is not None)

    def capabilities(self):
        return dict(self._caps)


class TestPydanticInputValidation:
    def test_degree_default_direction_is_out(self):
        m = DegreeCentralityInput()
        assert m.direction == "out"
        assert m.top_k == 10000

    def test_degree_rejects_invalid_direction(self):
        with pytest.raises(Exception):
            DegreeCentralityInput(direction="sideways")

    def test_betweenness_rejects_negative_sample_size(self):
        with pytest.raises(Exception):
            BetweennessInput(sample_size=-1)

    def test_betweenness_mem_budget_minimum_is_16(self):
        with pytest.raises(Exception):
            BetweennessInput(mem_budget_mb=8)

    def test_closeness_default_formula_is_harmonic(self):
        m = ClosenessInput()
        assert m.formula == "harmonic"

    def test_closeness_rejects_unknown_formula(self):
        with pytest.raises(Exception):
            ClosenessInput(formula="weird")

    def test_eigenvector_tol_must_be_in_range(self):
        with pytest.raises(Exception):
            EigenvectorInput(tol=1.5)
        with pytest.raises(Exception):
            EigenvectorInput(tol=0.0)

    def test_eigenvector_max_iter_capped_at_1000(self):
        with pytest.raises(Exception):
            EigenvectorInput(max_iter=2000)


class TestStoreProtocolHasCentralityMethods:
    def test_protocol_includes_4_centrality_methods(self):
        for method in ("execute_degree_centrality", "execute_betweenness",
                       "execute_closeness", "execute_eigenvector"):
            assert hasattr(GraphStore, method), f"GraphStore missing {method}"

    def test_mock_store_has_centrality_methods(self):
        store = CentralityMockStore()
        for method in ("execute_degree_centrality", "execute_betweenness",
                       "execute_closeness", "execute_eigenvector"):
            assert hasattr(store, method), f"CentralityMockStore missing {method}"


class TestCapabilitiesIncludeCentralityKeys:
    def test_iris_store_capabilities_has_4_centrality_keys(self):
        from iris_vector_graph.stores.iris_sql_store import _FULL_CAPABILITIES
        for key in ("degree_centrality", "betweenness", "closeness", "eigenvector"):
            assert _FULL_CAPABILITIES.get(key) is True, \
                f"_FULL_CAPABILITIES missing {key}=True"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestEngineRoutingThroughStore:
    """Engine wrappers must route to store.execute_* — not implement inline."""

    def _make_engine_with_mock(self, mock_store):
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine
        conn = MagicMock()
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = conn
        eng._store = mock_store
        return eng

    def test_degree_routes_to_store(self):
        store = CentralityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.degree_centrality(direction="in", predicate="CITES", top_k=20)
        assert "execute_degree_centrality" in store.called_methods
        assert store.last_call["direction"] == "in"
        assert store.last_call["predicate"] == "CITES"
        assert store.last_call["top_k"] == 20

    def test_betweenness_routes_to_store(self):
        store = CentralityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.betweenness_centrality(sample_size=100, mem_budget_mb=128)
        assert "execute_betweenness" in store.called_methods
        assert store.last_call["sample_size"] == 100
        assert store.last_call["mem_budget_mb"] == 128

    def test_closeness_routes_to_store(self):
        store = CentralityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.closeness_centrality(formula="classical", max_hops=5)
        assert "execute_closeness" in store.called_methods
        assert store.last_call["formula"] == "classical"
        assert store.last_call["max_hops"] == 5

    def test_eigenvector_routes_to_store(self):
        store = CentralityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.eigenvector_centrality(max_iter=50, tol=1e-7)
        assert "execute_eigenvector" in store.called_methods
        assert store.last_call["max_iter"] == 50
        assert store.last_call["tol"] == 1e-7

    def test_engine_raises_not_implemented_when_store_unsupported(self):
        caps = dict(CENTRALITY_CAPS)
        caps["betweenness"] = False
        store = CentralityMockStore(caps_override=caps)
        eng = self._make_engine_with_mock(store)
        with pytest.raises(NotImplementedError, match="betweenness"):
            eng.betweenness_centrality()


class TestProgressCallback:
    def test_progress_callback_passed_through_to_store(self):
        store = CentralityMockStore()
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine
        eng = IRISGraphEngine.__new__(IRISGraphEngine)
        eng.conn = MagicMock()
        eng._store = store

        calls = []
        eng.betweenness_centrality(progress_callback=lambda c, t: calls.append((c, t)))
        assert store.last_call["has_callback"] is True
