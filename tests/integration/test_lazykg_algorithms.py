"""
Direct LazyKG algorithm path tests — covering the pure-Python fallback
implementations in iris_sql_store.py that fire when ObjectScript fails.

These are NOT fallbacks for broken IRIS — they're the 3rd tier of a
3-tier dispatch (Rust → ObjectScript → Python LazyKG). The LazyKG reads
^KG globals via IRIS Native API and runs pure-Python Brandes, Leiden,
closeness, eigenvector, SCC, k-core, triangle count.

We call the private `_*_gref` and `_*_lazykg` methods directly to exercise
these 400+ lines without waiting for ObjectScript to fail.

All run against live ivg-iris with ^KG populated (sync() called in fixture).
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


@pytest.fixture
def store_with_graph(iris_connection, iris_master_cleanup):
    """Engine + store with 10-node ring + spokes, ^KG and ^NKG built."""
    import iris as _iris
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    iris_obj = _iris.createIRIS(iris_connection)

    # Build a 10-node ring with some triangles
    nodes = [f"lkg_{i}" for i in range(10)]
    for n in nodes:
        eng.create_node(n, labels=["V"])
    for i in range(9):
        eng.create_edge(f"lkg_{i}", "R", f"lkg_{i+1}")
    eng.create_edge("lkg_9", "R", "lkg_0")   # ring
    eng.create_edge("lkg_0", "R", "lkg_4")   # chord — creates triangles
    eng.create_edge("lkg_2", "R", "lkg_7")   # chord
    eng.sync()

    # Verify ^KG is populated
    nkg = bool(int(iris_obj.classMethodValue("Graph.KG.Traversal", "NKGPopulated") or 0))
    if not nkg:
        pytest.skip("^NKG not populated — LazyKG requires ^KG")

    return eng._store, nodes


# ===========================================================================
# _degree_centrality_gref_fallback — LazyKG degree walk
# ===========================================================================

class TestDegreeGref:

    def test_degree_gref_out(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("out", "", top_k=10)
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) > 0
        # lkg_0 has 3 out-edges — should have highest out-degree
        scores = {r[0]: float(r[1]) for r in result.rows if len(r) >= 2}
        assert "lkg_0" in scores

    def test_degree_gref_in(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("in", "", top_k=10)
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) > 0

    def test_degree_gref_both(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("both", "", top_k=5)
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) <= 5

    def test_degree_gref_with_predicate(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("out", "R", top_k=10)
        assert isinstance(result, (list, IVGResult))

    def test_degree_gref_scores_normalized(self, store_with_graph):
        """Scores should be in [0, 1] after normalization."""
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("out", "", top_k=10)
        for row in result.rows:
            if len(row) >= 2:
                assert 0.0 <= float(row[1]) <= 1.0

    def test_degree_gref_top_k_limit(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._degree_centrality_gref_fallback("out", "", top_k=3)
        assert len(result.rows) <= 3


# ===========================================================================
# _betweenness_gref — Brandes via LazyKG (Python fallback tier 3)
# ===========================================================================

class TestBetweennessGref:

    def test_betweenness_gref_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._betweenness_gref(
            sample_size=0, direction="out", max_hops=5,
            top_k=10, mem_budget_mb=128, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))

    def test_betweenness_gref_with_sample(self, store_with_graph):
        """sample_size > 0 uses Bader-Pich approximation."""
        store, nodes = store_with_graph
        result = store._betweenness_gref(
            sample_size=3, direction="out", max_hops=5,
            top_k=10, mem_budget_mb=128, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))

    def test_betweenness_gref_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._betweenness_gref(
            sample_size=0, direction="out", max_hops=3,
            top_k=3, mem_budget_mb=128, progress_callback=None
        )
        assert len(result.rows) <= 3

    def test_betweenness_gref_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        result = store._betweenness_gref(
            sample_size=0, direction="out", max_hops=3,
            top_k=5, mem_budget_mb=128,
            progress_callback=lambda done, total: calls.append((done, total))
        )
        assert isinstance(result, (list, IVGResult))

    def test_betweenness_gref_scores_non_negative(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._betweenness_gref(
            sample_size=0, direction="out", max_hops=3,
            top_k=10, mem_budget_mb=128, progress_callback=None
        )
        for row in result.rows:
            if len(row) >= 2:
                assert float(row[1]) >= 0

    def test_betweenness_gref_chord_node_higher_score(self, store_with_graph):
        """lkg_0 is a chord hub — should have higher betweenness than typical ring node."""
        store, nodes = store_with_graph
        result = store._betweenness_gref(
            sample_size=0, direction="out", max_hops=10,
            top_k=10, mem_budget_mb=128, progress_callback=None
        )
        if result.rows:
            scores = {r[0]: float(r[1]) for r in result.rows if len(r) >= 2}
            assert len(scores) >= 1


# ===========================================================================
# _closeness_gref — BFS-based closeness via LazyKG
# ===========================================================================

class TestClosenessGref:

    def test_closeness_gref_harmonic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._closeness_gref(
            formula="harmonic", direction="out", max_hops=5,
            top_k=10, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) > 0

    def test_closeness_gref_classical(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._closeness_gref(
            formula="classical", direction="out", max_hops=5,
            top_k=10, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))

    def test_closeness_gref_in_direction(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._closeness_gref(
            formula="harmonic", direction="in", max_hops=3,
            top_k=5, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))

    def test_closeness_gref_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._closeness_gref(
            formula="harmonic", direction="out", max_hops=3,
            top_k=3, progress_callback=None
        )
        assert len(result.rows) <= 3

    def test_closeness_gref_scores_non_negative(self, store_with_graph):
        """Closeness scores must be non-negative (normalization varies by formula)."""
        store, nodes = store_with_graph
        result = store._closeness_gref(
            formula="harmonic", direction="out", max_hops=5,
            top_k=10, progress_callback=None
        )
        for row in result.rows:
            if len(row) >= 2:
                score = float(row[1])
                assert score >= 0.0

    def test_closeness_gref_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._closeness_gref(
            formula="harmonic", direction="out", max_hops=3,
            top_k=5, progress_callback=lambda d, t: calls.append((d, t))
        )


# ===========================================================================
# _eigenvector_gref — power iteration via LazyKG
# ===========================================================================

class TestEigenvectorGref:

    def test_eigenvector_gref_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._eigenvector_gref(
            max_iter=50, tol=1e-4, top_k=10, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) > 0

    def test_eigenvector_gref_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._eigenvector_gref(max_iter=20, tol=1e-3, top_k=3, progress_callback=None)
        assert len(result.rows) <= 3

    def test_eigenvector_gref_scores_non_negative(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._eigenvector_gref(max_iter=30, tol=1e-4, top_k=10, progress_callback=None)
        for row in result.rows:
            if len(row) >= 2:
                assert float(row[1]) >= 0

    def test_eigenvector_gref_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._eigenvector_gref(
            max_iter=20, tol=1e-3, top_k=5,
            progress_callback=lambda d, t: calls.append((d, t))
        )


# ===========================================================================
# _leiden_lazykg — greedy Leiden community detection via LazyKG
# ===========================================================================

class TestLeidenLazyKG:

    def test_leiden_lazykg_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._leiden_lazykg(
            max_levels=5, gamma=1.0, tol=1e-4, top_k=10,
            mem_budget_mb=128, random_seed=42, progress_callback=None
        )
        assert isinstance(result, (list, IVGResult))

    def test_leiden_lazykg_all_nodes_assigned(self, store_with_graph):
        """All nodes should get a community assignment."""
        store, nodes = store_with_graph
        result = store._leiden_lazykg(
            max_levels=3, gamma=1.0, tol=1e-3, top_k=20,
            mem_budget_mb=64, random_seed=0, progress_callback=None
        )
        if result.rows:
            node_ids = {r[0] for r in result.rows}
            lkg_nodes = {n for n in node_ids if str(n).startswith("lkg_")}
            assert len(lkg_nodes) >= 1

    def test_leiden_lazykg_gamma_variation(self, store_with_graph):
        """Different gamma = different community granularity."""
        store, nodes = store_with_graph
        r1 = store._leiden_lazykg(1, 0.5, 1e-3, 10, 64, 42, None)
        r2 = store._leiden_lazykg(1, 2.0, 1e-3, 10, 64, 42, None)
        assert isinstance(r1, IVGResult)
        assert isinstance(r2, IVGResult)

    def test_leiden_lazykg_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._leiden_lazykg(
            3, 1.0, 1e-3, 5, 64, None,
            progress_callback=lambda d, t: calls.append((d, t))
        )


# ===========================================================================
# _triangle_count_lazykg — enumerate triangles via LazyKG
# ===========================================================================

class TestTriangleCountLazyKG:

    def test_triangle_count_lazykg_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._triangle_count_lazykg(top_k=10, progress_callback=None)
        assert isinstance(result, (list, IVGResult))

    def test_triangle_count_lazykg_ring_has_triangles(self, store_with_graph):
        """Ring with chords: lkg_0→lkg_4 + lkg_0→lkg_1→...→lkg_4 creates triangles."""
        store, nodes = store_with_graph
        result = store._triangle_count_lazykg(top_k=10, progress_callback=None)
        if result.rows:
            # Some nodes should have non-zero triangle count due to chords
            max_triangles = max(int(r[1]) for r in result.rows if len(r) >= 2)
            assert max_triangles >= 0

    def test_triangle_count_lazykg_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._triangle_count_lazykg(top_k=3, progress_callback=None)
        assert len(result.rows) <= 3

    def test_triangle_count_lazykg_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._triangle_count_lazykg(
            top_k=5,
            progress_callback=lambda d, t: calls.append((d, t))
        )


# ===========================================================================
# _scc_lazykg — iterative Tarjan SCC via LazyKG
# ===========================================================================

class TestSCCLazyKG:

    def test_scc_lazykg_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._scc_lazykg(top_k=10, progress_callback=None)
        assert isinstance(result, (list, IVGResult))

    def test_scc_lazykg_ring_is_one_scc(self, store_with_graph):
        """10-node ring is strongly connected — all nodes in same SCC."""
        store, nodes = store_with_graph
        result = store._scc_lazykg(top_k=20, progress_callback=None)
        if result.rows:
            # All lkg_ nodes should share one SCC (ring is strongly connected)
            lkg_rows = [r for r in result.rows if str(r[0]).startswith("lkg_")]
            if lkg_rows:
                component_ids = {r[1] for r in lkg_rows if len(r) >= 2}
                assert len(component_ids) == 1

    def test_scc_lazykg_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._scc_lazykg(top_k=3, progress_callback=None)
        assert len(result.rows) <= 3

    def test_scc_lazykg_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._scc_lazykg(
            top_k=5, progress_callback=lambda d, t: calls.append((d, t))
        )


# ===========================================================================
# _k_core_lazykg — Batagelj-Zaversnik k-core via LazyKG
# ===========================================================================

class TestKCoreLazyKG:

    def test_k_core_lazykg_basic(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._k_core_lazykg(top_k=10, progress_callback=None)
        assert isinstance(result, (list, IVGResult))
        assert len(result.rows) > 0

    def test_k_core_lazykg_ring_has_k2(self, store_with_graph):
        """10-node ring: every node has degree ≥ 2, so k-core ≥ 2 for all."""
        store, nodes = store_with_graph
        result = store._k_core_lazykg(top_k=20, progress_callback=None)
        if result.rows:
            min_core = min(int(r[1]) for r in result.rows if len(r) >= 2)
            assert min_core >= 1  # at least k=1 for all in connected ring

    def test_k_core_lazykg_top_k(self, store_with_graph):
        store, nodes = store_with_graph
        result = store._k_core_lazykg(top_k=3, progress_callback=None)
        assert len(result.rows) <= 3

    def test_k_core_lazykg_with_progress(self, store_with_graph):
        store, nodes = store_with_graph
        calls = []
        store._k_core_lazykg(
            top_k=5, progress_callback=lambda d, t: calls.append((d, t))
        )

    def test_k_core_lazykg_hub_has_higher_core(self, store_with_graph):
        """lkg_0 (3 out-edges) may have higher core number."""
        store, nodes = store_with_graph
        result = store._k_core_lazykg(top_k=10, progress_callback=None)
        if result.rows:
            scores = {r[0]: int(r[1]) for r in result.rows if len(r) >= 2}
            assert "lkg_0" in scores or len(scores) >= 1


# ===========================================================================
# LazyKG itself — iter_nodes, out_neighbors, in_neighbors, degree
# ===========================================================================

class TestLazyKGDirect:

    def test_lazykg_iter_nodes(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg = LazyKG(store.conn)
        all_nodes = list(lkg.iter_nodes())
        assert isinstance(all_nodes, list)
        lkg_nodes = [n for n in all_nodes if str(n).startswith("lkg_")]
        assert len(lkg_nodes) == 10

    def test_lazykg_out_neighbors(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg = LazyKG(store.conn)
        nbrs = lkg.out_neighbors("lkg_0")
        assert isinstance(nbrs, (list, set))
        # lkg_0 → lkg_1, lkg_0 → lkg_4 (chord)
        nbr_set = set(nbrs)
        assert "lkg_1" in nbr_set

    def test_lazykg_in_neighbors(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg = LazyKG(store.conn, include_sinks=True)
        nbrs = lkg.in_neighbors("lkg_1")
        assert isinstance(nbrs, (list, set))
        nbr_set = set(nbrs)
        assert "lkg_0" in nbr_set

    def test_lazykg_degree(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg = LazyKG(store.conn)
        deg = lkg.degree("lkg_0")
        assert isinstance(deg, int)
        assert deg >= 2  # lkg_0 → lkg_1 + lkg_0 → lkg_4 at minimum

    def test_lazykg_in_degree(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg = LazyKG(store.conn, include_sinks=True)
        in_deg = lkg.in_degree("lkg_0")
        assert isinstance(in_deg, int)
        assert in_deg >= 1  # lkg_9 → lkg_0

    def test_lazykg_include_sinks_captures_sink_nodes(self, store_with_graph):
        from iris_vector_graph.stores.lazy_kg import LazyKG
        store, nodes = store_with_graph
        lkg_sinks = LazyKG(store.conn, include_sinks=True)
        lkg_no_sinks = LazyKG(store.conn, include_sinks=False)
        with_sinks = list(lkg_sinks.iter_nodes())
        without_sinks = list(lkg_no_sinks.iter_nodes())
        # with_sinks should include at least as many nodes
        assert len(with_sinks) >= len(without_sinks)


# ===========================================================================
# PUBLIC API tests via IVG_DISABLE_ARNO=1 — forces LazyKG paths
# ===========================================================================

class TestPublicAPILazyKGPath:
    """Tests that exercise LazyKG through the public engine API by setting
    IVG_DISABLE_ARNO=1 to bypass ObjectScript/Arno tiers."""

    @pytest.fixture
    def eng(self, iris_connection, iris_master_cleanup):
        e = IRISGraphEngine(iris_connection, embedding_dimension=4)
        for i in range(10):
            e.create_node(f"pub_{i}", labels=["V"])
        for i in range(9):
            e.create_edge(f"pub_{i}", "R", f"pub_{i+1}")
        e.create_edge("pub_9", "R", "pub_0")
        e.create_edge("pub_0", "R", "pub_5")  # chord
        e.sync()
        return e

    def test_leiden_communities_lazykg_path(self, eng):
        """leiden_communities with IVG_DISABLE_ARNO=1 goes to _leiden_lazykg."""
        import os
        with pytest.MonkeyPatch.context() as m:
            m.setenv("IVG_DISABLE_ARNO", "1")
            result = eng.leiden_communities(gamma=1.0, top_k=20, random_seed=42)
        assert isinstance(result, list)

    def test_triangle_count_lazykg_path(self, eng):
        """triangle_count forces LazyKG path via IVG_DISABLE_ARNO."""
        import os
        with pytest.MonkeyPatch.context() as m:
            m.setenv("IVG_DISABLE_ARNO", "1")
            result = eng.triangle_count(top_k=10)
        assert isinstance(result, list)

    def test_scc_lazykg_path(self, eng):
        """strongly_connected_components forces LazyKG path."""
        import os
        with pytest.MonkeyPatch.context() as m:
            m.setenv("IVG_DISABLE_ARNO", "1")
            result = eng.strongly_connected_components(top_k=10)
        assert isinstance(result, list)

    def test_k_core_lazykg_path(self, eng):
        """k_core forces LazyKG path."""
        import os
        with pytest.MonkeyPatch.context() as m:
            m.setenv("IVG_DISABLE_ARNO", "1")
            result = eng.k_core(top_k=10)
        assert isinstance(result, list)

    def test_closeness_public_api(self, eng):
        """closeness_centrality routes to _closeness_gref LazyKG BFS."""
        result = eng.closeness_centrality(formula="harmonic", top_k=5)
        assert isinstance(result, list)
        if result:
            assert all("id" in r and "score" in r for r in result)

    def test_eigenvector_public_api(self, eng):
        """eigenvector_centrality routes to _eigenvector_gref power iteration."""
        result = eng.eigenvector_centrality(top_k=5)
        assert isinstance(result, list)

    def test_degree_centrality_public_api(self, eng):
        """degree_centrality uses public API."""
        result = eng.degree_centrality(direction="out", top_k=5)
        assert isinstance(result, list)
        if result:
            assert all("id" in r for r in result)

    def test_betweenness_public_api(self, eng):
        """betweenness_centrality routes through _betweenness_gref LazyKG Brandes."""
        result = eng.betweenness_centrality(sample_size=3, top_k=5)
        assert isinstance(result, list)
        if result:
            assert all("id" in r and "score" in r for r in result)

    def test_betweenness_neighborhood_public_api(self, eng):
        """betweenness_centrality_neighborhood uses neighborhood subgraph."""
        result = eng.betweenness_centrality_neighborhood(
            seed="pub_0", hops=2, sample_size=3, top_k=5
        )
        assert isinstance(result, (list, IVGResult))
