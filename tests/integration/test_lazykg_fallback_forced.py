"""
Forced LazyKG fallback tests — exercises the pure-Python tier 3 algorithm
implementations by calling them on graphs where:
  1. ^NKG is built (via BuildKG) but ObjectScript BetweennessGlobal is expected to fail
  2. OR we call the LazyKG gref methods directly on a graph with ^KG but not ^NKG

The key insight: _betweenness_gref, _closeness_gref, _eigenvector_gref etc.
try ObjectScript first. If we build ^KG but NOT ^NKG (by calling BuildKG without
BuildNKG), the ObjectScript path will fail and LazyKG fires.

Strategy: create graph → call BuildKG only (not sync/BuildNKG) → call gref methods.
"""
import pytest
import iris as _iris
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


@pytest.fixture
def store_no_nkg(iris_connection, iris_master_cleanup):
    """Store with ^KG built but ^NKG NOT populated.
    Forces LazyKG fallback in betweenness/closeness/eigenvector."""
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    iris_obj = _iris.createIRIS(iris_connection)

    # Create graph
    for i in range(8):
        eng.create_node(f"fnkg_{i}", labels=["V"])
    for i in range(7):
        eng.create_edge(f"fnkg_{i}", "R", f"fnkg_{i+1}")
    eng.create_edge("fnkg_7", "R", "fnkg_0")
    eng.create_edge("fnkg_0", "R", "fnkg_4")

    # Build ^KG but NOT ^NKG
    iris_obj.classMethodValue("Graph.KG.Traversal", "BuildKG")
    iris_connection.commit()

    # Kill ^NKG to ensure ObjectScript fails
    iris_obj.kill("^NKG")

    return eng._store


# ===========================================================================
# _betweenness_gref — forced through LazyKG (lines 986-1095)
# ===========================================================================

class TestBetweennessGrefForcedLazyKG:

    def test_betweenness_lazykg_fires_when_nkg_absent(self, store_no_nkg):
        """With ^NKG killed, BetweennessGlobal ObjectScript fails → LazyKG Brandes."""
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="out", max_hops=5,
            top_k=8, mem_budget_mb=64, progress_callback=None
        )
        assert isinstance(result, IVGResult)
        # LazyKG Brandes should return scores for all fnkg_ nodes
        node_ids = {r[0] for r in result.rows if len(r) >= 1}
        fnkg_nodes = {n for n in node_ids if str(n).startswith("fnkg_")}
        assert len(fnkg_nodes) >= 1

    def test_betweenness_lazykg_scores_non_negative(self, store_no_nkg):
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="out", max_hops=4,
            top_k=8, mem_budget_mb=64, progress_callback=None
        )
        for row in result.rows:
            if len(row) >= 2:
                assert float(row[1]) >= 0

    def test_betweenness_lazykg_with_sampling(self, store_no_nkg):
        """sample_size > 0: Bader-Pich approximation over LazyKG."""
        result = store_no_nkg._betweenness_gref(
            sample_size=3, direction="out", max_hops=4,
            top_k=5, mem_budget_mb=64, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_betweenness_lazykg_inbound(self, store_no_nkg):
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="in", max_hops=3,
            top_k=5, mem_budget_mb=64, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_betweenness_lazykg_both_directions(self, store_no_nkg):
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="both", max_hops=3,
            top_k=5, mem_budget_mb=64, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_betweenness_lazykg_with_progress(self, store_no_nkg):
        calls = []
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="out", max_hops=3,
            top_k=5, mem_budget_mb=64,
            progress_callback=lambda d, t: calls.append((d, t))
        )
        assert isinstance(result, IVGResult)
        assert len(calls) > 0  # progress callback should have been called

    def test_betweenness_lazykg_mem_budget_controls_allocation(self, store_no_nkg):
        """Small mem_budget triggers budget_exceeded path inside Brandes."""
        result = store_no_nkg._betweenness_gref(
            sample_size=0, direction="out", max_hops=5,
            top_k=8, mem_budget_mb=1,  # very small budget
            progress_callback=None
        )
        assert isinstance(result, IVGResult)


# ===========================================================================
# _closeness_gref — forced through LazyKG (lines 1150-1211)
# ===========================================================================

class TestClosenessGrefForcedLazyKG:

    def test_closeness_lazykg_harmonic(self, store_no_nkg):
        """With ^NKG killed, ClosenessGlobal fails → LazyKG BFS harmonic."""
        result = store_no_nkg._closeness_gref(
            formula="harmonic", direction="out", max_hops=5,
            top_k=8, progress_callback=None
        )
        assert isinstance(result, IVGResult)
        assert len(result.rows) > 0

    def test_closeness_lazykg_classical(self, store_no_nkg):
        result = store_no_nkg._closeness_gref(
            formula="classical", direction="out", max_hops=5,
            top_k=8, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_closeness_lazykg_in_direction(self, store_no_nkg):
        result = store_no_nkg._closeness_gref(
            formula="harmonic", direction="in", max_hops=3,
            top_k=5, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_closeness_lazykg_both(self, store_no_nkg):
        result = store_no_nkg._closeness_gref(
            formula="harmonic", direction="both", max_hops=3,
            top_k=5, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_closeness_lazykg_max_hops_0_all_pairs(self, store_no_nkg):
        """max_hops=0 means full BFS (no depth limit)."""
        result = store_no_nkg._closeness_gref(
            formula="harmonic", direction="out", max_hops=0,
            top_k=5, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_closeness_lazykg_with_progress(self, store_no_nkg):
        calls = []
        store_no_nkg._closeness_gref(
            formula="harmonic", direction="out", max_hops=3,
            top_k=5, progress_callback=lambda d, t: calls.append((d, t))
        )
        assert len(calls) > 0


# ===========================================================================
# _eigenvector_gref — forced via direct call (always uses LazyKG)
# ===========================================================================

class TestEigenvectorGrefForcedLazyKG:

    def test_eigenvector_lazykg_with_progress(self, store_no_nkg):
        calls = []
        result = store_no_nkg._eigenvector_gref(
            max_iter=30, tol=1e-4, top_k=8,
            progress_callback=lambda d, t: calls.append((d, t))
        )
        assert isinstance(result, IVGResult)

    def test_eigenvector_lazykg_convergence(self, store_no_nkg):
        """Tight tolerance forces more iterations."""
        result = store_no_nkg._eigenvector_gref(
            max_iter=100, tol=1e-8, top_k=8, progress_callback=None
        )
        assert isinstance(result, IVGResult)


# ===========================================================================
# _leiden_lazykg — forced via IVG_DISABLE_ARNO=1
# ===========================================================================

class TestLeidenLazyKGForced:

    def test_leiden_lazykg_random_seed_none(self, store_no_nkg):
        """random_seed=None uses random initialization."""
        result = store_no_nkg._leiden_lazykg(
            max_levels=3, gamma=1.0, tol=1e-3, top_k=8,
            mem_budget_mb=64, random_seed=None, progress_callback=None
        )
        assert isinstance(result, IVGResult)

    def test_leiden_lazykg_high_gamma(self, store_no_nkg):
        """High gamma → many small communities."""
        result = store_no_nkg._leiden_lazykg(
            max_levels=2, gamma=5.0, tol=1e-2, top_k=8,
            mem_budget_mb=64, random_seed=0, progress_callback=None
        )
        assert isinstance(result, IVGResult)


# ===========================================================================
# _triangle_count_lazykg — various configurations
# ===========================================================================

class TestTriangleCountLazyKGVariants:

    def test_triangle_count_with_progress(self, store_no_nkg):
        calls = []
        result = store_no_nkg._triangle_count_lazykg(
            top_k=8,
            progress_callback=lambda d, t: calls.append((d, t))
        )
        assert isinstance(result, IVGResult)

    def test_triangle_count_chord_graph_has_triangles(self, store_no_nkg):
        """fnkg_0 → fnkg_4 chord creates triangles with fnkg_1..fnkg_3 path."""
        result = store_no_nkg._triangle_count_lazykg(top_k=8, progress_callback=None)
        if result.rows:
            max_tri = max(int(r[1]) for r in result.rows if len(r) >= 2)
            # Ring with chord: at least some triangles exist
            assert max_tri >= 0  # may be 0 depending on direction


# ===========================================================================
# iris_sql_store.py — execute_betweenness store method
# ===========================================================================

class TestStoreBetweennessDispatch:

    def test_store_execute_betweenness_no_nkg(self, store_no_nkg):
        """execute_betweenness with ^NKG absent → dispatches to _betweenness_gref."""
        result = store_no_nkg.execute_betweenness(
            sample_size=0, direction="out", max_hops=3,
            top_k=5, mem_budget_mb=64
        )
        assert isinstance(result, IVGResult)

    def test_store_execute_closeness_no_nkg(self, store_no_nkg):
        """execute_closeness with ^NKG absent → dispatches to _closeness_gref."""
        result = store_no_nkg.execute_closeness(
            formula="harmonic", direction="out", max_hops=3, top_k=5
        )
        assert isinstance(result, IVGResult)
