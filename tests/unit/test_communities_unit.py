"""Spec 163 — Communities unit tests.

Test-first (Constitution Principle III): tests written BEFORE the implementation
in T026-T050 (per-algorithm Phase 3-6). They MUST FAIL with NotImplementedError
until those tasks land.

Once the engine wrappers + IRISGraphStore methods are implemented, these tests
verify routing through the GraphStore Protocol — they do NOT exercise live IRIS.
The corresponding e2e tests live in tests/e2e/test_communities_e2e.py.
"""

import os
import pytest

from iris_vector_graph.result import IVGResult
from iris_vector_graph._validate import (
    LeidenInput, TriangleCountInput, SCCInput, KCoreInput,
)


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


COMMUNITY_CAPS = {
    "native_sql": False,
    "bfs": True, "shortest_path": True, "weighted_shortest_path": True,
    "ppr": True, "pagerank": True, "wcc": True, "cdlp": True,
    "subgraph": True, "knn_vec": True,
    "temporal_edges": True, "temporal_window_query": True,
    "temporal_cypher": True, "temporal_aggregate": True,
    "degree_centrality": True, "betweenness": True,
    "closeness": True, "eigenvector": True,
    "leiden": True, "triangle_count": True, "scc": True, "k_core": True,
}


class CommunityMockStore:
    """Minimal store recording calls to the 4 community methods."""

    def __init__(self, caps_override=None):
        self.called_methods = []
        self.last_call = {}
        self._caps = caps_override or COMMUNITY_CAPS

    def execute_leiden(self, max_levels, gamma, tol, top_k, mem_budget_mb,
                       random_seed=None, progress_callback=None):
        self.called_methods.append("execute_leiden")
        self.last_call = dict(max_levels=max_levels, gamma=gamma, tol=tol,
                              top_k=top_k, mem_budget_mb=mem_budget_mb,
                              random_seed=random_seed,
                              has_callback=progress_callback is not None)
        return IVGResult(columns=["id", "community", "size"],
                         rows=[["n0", 0, 1]])

    def execute_triangle_count(self, top_k, progress_callback=None):
        self.called_methods.append("execute_triangle_count")
        self.last_call = dict(top_k=top_k, has_callback=progress_callback is not None)
        return IVGResult(columns=["id", "triangles", "lcc"], rows=[["n0", 0, 0.0]])

    def execute_scc(self, top_k, progress_callback=None):
        self.called_methods.append("execute_scc")
        self.last_call = dict(top_k=top_k, has_callback=progress_callback is not None)
        return IVGResult(columns=["id", "component", "size"], rows=[["n0", 0, 1]])

    def execute_k_core(self, top_k, progress_callback=None):
        self.called_methods.append("execute_k_core")
        self.last_call = dict(top_k=top_k, has_callback=progress_callback is not None)
        return IVGResult(columns=["id", "coreness"], rows=[["n0", 0]])

    def get_node_count(self, label=None):
        return IVGResult(columns=["count"], rows=[[1]])

    def capabilities(self):
        return dict(self._caps)


class TestPydanticInputValidation:
    def test_leiden_defaults(self):
        m = LeidenInput()
        assert m.max_levels == 10
        assert m.gamma == 1.0
        assert m.tol == 1e-4
        assert m.random_seed is None

    def test_leiden_rejects_negative_gamma(self):
        with pytest.raises(Exception):
            LeidenInput(gamma=-1.0)

    def test_leiden_rejects_zero_tol(self):
        with pytest.raises(Exception):
            LeidenInput(tol=0.0)

    def test_leiden_rejects_max_levels_zero(self):
        with pytest.raises(Exception):
            LeidenInput(max_levels=0)

    def test_leiden_accepts_random_seed(self):
        m = LeidenInput(random_seed=42)
        assert m.random_seed == 42

    def test_triangle_count_defaults(self):
        m = TriangleCountInput()
        assert m.top_k == 10000

    def test_scc_defaults(self):
        m = SCCInput()
        assert m.top_k == 10000

    def test_k_core_defaults(self):
        m = KCoreInput()
        assert m.top_k == 10000


class TestCapabilitiesIncludeCommunityKeys:
    def test_iris_store_capabilities_has_4_community_keys(self):
        from iris_vector_graph.stores.iris_sql_store import _FULL_CAPABILITIES
        for key in ("leiden", "triangle_count", "scc", "k_core"):
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

    def test_leiden_routes_to_store(self):
        store = CommunityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.leiden_communities(gamma=1.5, random_seed=42, top_k=50)
        assert "execute_leiden" in store.called_methods
        assert store.last_call["gamma"] == 1.5
        assert store.last_call["random_seed"] == 42
        assert store.last_call["top_k"] == 50

    def test_triangle_count_routes_to_store(self):
        store = CommunityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.triangle_count(top_k=20)
        assert "execute_triangle_count" in store.called_methods
        assert store.last_call["top_k"] == 20

    def test_scc_routes_to_store(self):
        store = CommunityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.strongly_connected_components(top_k=100)
        assert "execute_scc" in store.called_methods
        assert store.last_call["top_k"] == 100

    def test_k_core_routes_to_store(self):
        store = CommunityMockStore()
        eng = self._make_engine_with_mock(store)
        eng.k_core(top_k=10)
        assert "execute_k_core" in store.called_methods
        assert store.last_call["top_k"] == 10

    def test_engine_raises_not_implemented_when_store_unsupported(self):
        caps = dict(COMMUNITY_CAPS)
        caps["leiden"] = False
        store = CommunityMockStore(caps_override=caps)
        eng = self._make_engine_with_mock(store)
        with pytest.raises(NotImplementedError, match="leiden"):
            eng.leiden_communities()
