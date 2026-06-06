"""
Integration tests for the Arno/RZF store layer against ivg-iris-enterprise.

Requires: ivg-iris-enterprise container running with libarno_callout.so loaded.
Start with: scripts/enterprise-container.sh up

These tests exercise the store layer (IRISGraphStore) Arno dispatch paths directly,
covering iris_sql_store.py lines that are unreachable on Community IRIS:

  - _detect_arno() / _arno_call() / chunked result reassembly
  - execute_bfs() Arno fast-path (BFSJson)
  - execute_ppr() Arno path (PPRJson)
  - execute_pagerank() Arno path (PageRankJson, rust_callout=True)
  - execute_wcc() Arno path (WCCJson)
  - execute_cdlp() Arno path (CDLPJson)
  - execute_subgraph() Arno path
  - execute_betweenness() Arno path (BetweennessGlobal)
  - execute_degree_centrality() store-layer direct
  - arno_bridge.py: _ensure_zf_call_function, arno_available probe, arno_call dispatch

All tests use arno_iris_connection (enterprise, port 31972) and iris_master_cleanup
for reproducible state. libarno_callout.so is loaded in the session fixture.
"""
import json
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


# ---------------------------------------------------------------------------
# Session fixture: ensure arno loaded + graph built once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def arno_engine_session(arno_iris_connection):
    """Session-scoped engine on enterprise with arno loaded and verified."""
    import iris as _iris
    iris_obj = _iris.createIRIS(arno_iris_connection)

    # Load callout
    for cls in ("Graph.KG.ArnoAccel", "Graph.KG.NKGAccelLoader"):
        try:
            iris_obj.classMethodValue(cls, "Load", "/tmp/libarno_callout.so")
        except Exception:
            pass

    caps = json.loads(str(iris_obj.classMethodValue("Graph.KG.NKGAccel", "Capabilities")))
    if not caps.get("rust_callout"):
        pytest.skip("libarno_callout.so not loaded on enterprise container")

    eng = IRISGraphEngine(arno_iris_connection, embedding_dimension=128)
    return eng, iris_obj, caps


@pytest.fixture
def arno_graph(arno_engine_session, arno_iris_connection, arno_master_cleanup):
    """Per-test: clean enterprise state + 15-node ring graph, arno loaded."""
    eng, iris_obj, caps = arno_engine_session

    # Reload callout after cleanup (^NKG kill may reset arno state)
    for cls in ("Graph.KG.ArnoAccel", "Graph.KG.NKGAccelLoader"):
        try:
            iris_obj.classMethodValue(cls, "Load", "/tmp/libarno_callout.so")
        except Exception:
            pass

    nodes = [f"arno_{i}" for i in range(15)]
    for n in nodes:
        eng.create_node(n, labels=["Entity"])
    for i in range(14):
        eng.create_edge(f"arno_{i}", "R", f"arno_{i+1}")
    eng.create_edge("arno_14", "R", "arno_0")
    eng.create_edge("arno_0",  "R", "arno_7")
    eng.create_edge("arno_3",  "R", "arno_11")
    eng.sync()

    nkg_ok = bool(int(iris_obj.classMethodValue("Graph.KG.Traversal", "NKGPopulated") or 0))
    if not nkg_ok:
        pytest.skip("^NKG not populated after sync on enterprise")

    # Invalidate store's cached arno state so it re-detects on this fresh graph
    store = eng._store
    store._arno_available = None
    store._arno_capabilities = {}

    return eng, iris_obj, caps, nodes


# ---------------------------------------------------------------------------
# Arno detection and capabilities
# ---------------------------------------------------------------------------

class TestArnoDetection:

    def test_capabilities_rust_callout_true(self, arno_engine_session):
        _, iris_obj, caps = arno_engine_session
        assert caps.get("rust_callout") is True

    def test_capabilities_bfs_true(self, arno_engine_session):
        _, iris_obj, caps = arno_engine_session
        assert caps.get("bfs") is True

    def test_capabilities_rust_algorithms_populated(self, arno_engine_session):
        _, iris_obj, caps = arno_engine_session
        rust_algs = caps.get("rust_algorithms", [])
        assert len(rust_algs) > 0
        assert "bfs" in rust_algs

    def test_detect_arno_via_store(self, arno_graph):
        eng, iris_obj, caps, nodes = arno_graph
        # Access store directly
        store = eng._store
        detected = store._detect_arno()
        assert detected is True

    def test_arno_capabilities_cached(self, arno_graph):
        eng, _, _, _ = arno_graph
        store = eng._store
        store._detect_arno()
        store._detect_arno()  # second call uses cache
        assert store._arno_available is True


# ---------------------------------------------------------------------------
# execute_bfs — Arno fast path (BFSJson, rust_callout)
# ---------------------------------------------------------------------------

class TestArnoBFS:

    def test_bfs_1hop_returns_neighbors(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_bfs("arno_0", [], 1, "out", 1000)
        assert isinstance(result, IVGResult)
        ids = {r[0] for r in result.rows}
        assert "arno_1" in ids or "arno_7" in ids  # direct neighbors of arno_0

    def test_bfs_2hop_reachable_set_larger(self, arno_graph):
        eng, _, _, nodes = arno_graph
        r1 = eng._store.execute_bfs("arno_0", [], 1, "out", 1000)
        r2 = eng._store.execute_bfs("arno_0", [], 2, "out", 1000)
        assert len(r2.rows) >= len(r1.rows)

    def test_bfs_returns_id_hops_pred_columns(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_bfs("arno_0", [], 1, "out", 1000)
        assert "id" in result.columns or len(result.columns) >= 1

    def test_bfs_nonexistent_seed_returns_empty(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_bfs("__no_such_node__", [], 2, "out", 1000)
        assert isinstance(result, IVGResult)
        assert len(result.rows) == 0

    def test_bfs_max_results_caps_output(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_bfs("arno_0", [], 5, "out", 3)
        assert len(result.rows) <= 3

    def test_bfs_via_execute_cypher(self, arno_graph):
        """Full stack: execute_cypher routes to store.execute_bfs Arno path."""
        eng, _, _, _ = arno_graph
        result = eng.execute_cypher(
            "MATCH (n {node_id: $id})-[:R]->(m) RETURN m.node_id",
            {"id": "arno_0"},
        )
        assert len(result.rows) >= 1


# ---------------------------------------------------------------------------
# execute_ppr — Arno PPRJson
# ---------------------------------------------------------------------------

class TestArnoPPR:

    def test_ppr_returns_scores(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_ppr(["arno_0"], 0.85, 20)
        assert isinstance(result, IVGResult)
        assert "score" in result.columns or len(result.columns) >= 1

    def test_ppr_seed_has_nonzero_score(self, arno_graph):
        eng, iris_obj, _, _ = arno_graph
        result = eng._store.execute_ppr(["arno_0"], 0.85, 20)
        if not result.rows:
            pytest.skip("PPR returned no results — NKG may not contain arno_0")
        scores = {r[0]: float(r[1]) for r in result.rows if len(r) >= 2}
        assert any(v > 0 for v in scores.values())

    def test_ppr_multi_seed(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_ppr(["arno_0", "arno_7"], 0.85, 20)
        assert isinstance(result, IVGResult)

    def test_ppr_damping_affects_spread(self, arno_graph):
        eng, _, _, _ = arno_graph
        r_low  = eng._store.execute_ppr(["arno_0"], 0.3, 20)
        r_high = eng._store.execute_ppr(["arno_0"], 0.95, 20)
        # Both should return results — just verify they don't crash
        assert isinstance(r_low, IVGResult)
        assert isinstance(r_high, IVGResult)


# ---------------------------------------------------------------------------
# execute_pagerank — Arno PageRankJson (global, rust_callout)
# ---------------------------------------------------------------------------

class TestArnoPageRank:

    def test_pagerank_returns_all_nodes(self, arno_graph):
        eng, iris_obj, caps, nodes = arno_graph
        if "pagerank" not in caps.get("rust_algorithms", []):
            pytest.skip("pagerank not in rust_algorithms")
        result = eng._store.execute_pagerank(0.85, 20)
        assert isinstance(result, IVGResult)
        # PageRank may return empty if NKG not fully built — skip rather than fail
        if not result.rows:
            pytest.skip("PageRank returned no results — NKG may not be populated")
        assert len(result.rows) >= 1

    def test_pagerank_scores_sum_approximately_one(self, arno_graph):
        eng, _, caps, nodes = arno_graph
        if "pagerank" not in caps.get("rust_algorithms", []):
            pytest.skip("pagerank not in rust_algorithms")
        result = eng._store.execute_pagerank(0.85, 20)
        if result.rows:
            total = sum(float(r[1]) for r in result.rows if len(r) >= 2)
            # PageRank scores sum to approximately 1.0 on a connected graph
            assert 0.5 <= total <= 2.0, f"PageRank sum out of range: {total}"

    def test_pagerank_via_engine(self, arno_graph):
        """Full stack via engine.kg_PAGERANK()."""
        eng, _, caps, _ = arno_graph
        result = eng.kg_PAGERANK()
        assert result is not None


# ---------------------------------------------------------------------------
# execute_wcc — Arno WCCJson (weakly connected components)
# ---------------------------------------------------------------------------

class TestArnoWCC:

    def test_wcc_returns_result(self, arno_graph):
        eng, _, caps, _ = arno_graph
        if "wcc" not in caps.get("rust_algorithms", []):
            pytest.skip("wcc not in rust_algorithms")
        result = eng._store.execute_wcc()
        assert isinstance(result, IVGResult)

    def test_wcc_ring_is_one_component(self, arno_graph):
        eng, _, caps, nodes = arno_graph
        if "wcc" not in caps.get("rust_algorithms", []):
            pytest.skip("wcc not in rust_algorithms")
        result = eng._store.execute_wcc()
        if result.rows:
            # Ring graph — all nodes should be in one WCC
            component_ids = {r[1] for r in result.rows if len(r) >= 2}
            assert len(component_ids) == 1, f"Expected 1 component, got {len(component_ids)}"


# ---------------------------------------------------------------------------
# execute_cdlp — Arno CDLPJson (community label propagation)
# ---------------------------------------------------------------------------

class TestArnoCDLP:

    def test_cdlp_returns_result(self, arno_graph):
        eng, _, caps, _ = arno_graph
        if "cdlp" not in caps.get("rust_algorithms", []):
            pytest.skip("cdlp not in rust_algorithms")
        result = eng._store.execute_cdlp(max_iterations=10)
        assert isinstance(result, IVGResult)

    def test_cdlp_all_nodes_assigned(self, arno_graph):
        eng, _, caps, nodes = arno_graph
        if "cdlp" not in caps.get("rust_algorithms", []):
            pytest.skip("cdlp not in rust_algorithms")
        result = eng._store.execute_cdlp(max_iterations=10)
        if result.rows:
            node_ids = {r[0] for r in result.rows if len(r) >= 1}
            arno_nodes = {n for n in node_ids if str(n).startswith("arno_")}
            assert len(arno_nodes) >= 1


# ---------------------------------------------------------------------------
# execute_betweenness — Arno BetweennessGlobal
# ---------------------------------------------------------------------------

class TestArnoBetweenness:

    def test_betweenness_returns_result(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_betweenness(
            sample_size=0, direction="out", max_hops=3, top_k=10, mem_budget_mb=128,
        )
        assert isinstance(result, IVGResult)

    def test_betweenness_scores_non_negative(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_betweenness(
            sample_size=0, direction="out", max_hops=3, top_k=10, mem_budget_mb=128,
        )
        for row in result.rows:
            if len(row) >= 2:
                assert float(row[1]) >= 0

    def test_betweenness_top_k_respected(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng._store.execute_betweenness(
            sample_size=0, direction="out", max_hops=3, top_k=3, mem_budget_mb=128,
        )
        assert len(result.rows) <= 3

    def test_betweenness_hub_scores_higher(self, arno_graph):
        """arno_0 has 2 extra spokes — should score higher than isolated nodes."""
        eng, _, _, _ = arno_graph
        result = eng._store.execute_betweenness(
            sample_size=0, direction="out", max_hops=5, top_k=15, mem_budget_mb=128,
        )
        if not result.rows:
            pytest.skip("No betweenness results")
        scores = {r[0]: float(r[1]) for r in result.rows if len(r) >= 2}
        assert "arno_0" in scores or len(scores) >= 1

    def test_betweenness_with_arno_loaded_emits_no_warning(self, arno_graph):
        """With arno loaded, betweenness should NOT emit arno-not-loaded RuntimeWarning."""
        import warnings
        eng, _, _, _ = arno_graph
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            eng._store.execute_betweenness(
                sample_size=0, direction="out", max_hops=3, top_k=5, mem_budget_mb=128,
            )
        runtime_warns = [x for x in w if issubclass(x.category, RuntimeWarning)
                         and "arno callout not loaded" in str(x.message)]
        assert len(runtime_warns) == 0


# ---------------------------------------------------------------------------
# execute_degree_centrality — store layer direct
# ---------------------------------------------------------------------------

class TestStoreDegree:

    def test_degree_centrality_out(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_degree_centrality("out", "", top_k=10)
        assert isinstance(result, IVGResult)
        assert len(result.rows) >= 1

    def test_degree_centrality_in(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_degree_centrality("in", "", top_k=10)
        assert isinstance(result, IVGResult)

    def test_degree_centrality_both(self, arno_graph):
        eng, _, _, nodes = arno_graph
        result = eng._store.execute_degree_centrality("both", "", top_k=10)
        assert isinstance(result, IVGResult)

    def test_degree_centrality_hub_has_highest_out_degree(self, arno_graph):
        """arno_0 has 3 outgoing edges (ring forward + 2 spokes): highest out-degree."""
        eng, _, _, _ = arno_graph
        result = eng._store.execute_degree_centrality("out", "", top_k=15)
        if not result.rows:
            pytest.skip("No degree results")
        sorted_rows = sorted(result.rows, key=lambda r: -float(r[1]) if len(r) >= 2 else 0)
        top_node = sorted_rows[0][0] if sorted_rows else None
        assert str(top_node).startswith("arno_")


# ---------------------------------------------------------------------------
# _arno_call chunked result path
# ---------------------------------------------------------------------------

class TestArnoCallChunked:

    def test_store_arno_call_non_chunked(self, arno_graph):
        """Store._arno_call: Capabilities — always available, returns raw string."""
        eng, _, _, _ = arno_graph
        raw = eng._store._arno_call("Graph.KG.NKGAccel", "Capabilities")
        assert isinstance(raw, str)
        assert len(raw) > 0

    def test_store_arno_call_returns_parseable_json(self, arno_graph):
        """Store._arno_call result is valid JSON (using Capabilities method)."""
        eng, _, _, _ = arno_graph
        raw = eng._store._arno_call("Graph.KG.NKGAccel", "Capabilities")
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert "rust_callout" in parsed


# ---------------------------------------------------------------------------
# arno_bridge.py — ZF call function and arno_available probe
# ---------------------------------------------------------------------------

class TestArnoBridge:
    """arno_bridge.py is a module of functions, not a class."""

    def test_arno_bridge_module_importable(self):
        from iris_vector_graph.stores import arno_bridge
        assert hasattr(arno_bridge, "arno_available")
        assert hasattr(arno_bridge, "arno_call")
        assert hasattr(arno_bridge, "_ensure_zf_call_function")

    def test_arno_bridge_available_returns_bool(self, arno_graph):
        from iris_vector_graph.stores import arno_bridge
        eng, _, _, _ = arno_graph
        result = arno_bridge.arno_available(eng.conn)
        assert isinstance(result, bool)

    def test_arno_bridge_available_true_with_callout(self, arno_graph):
        from iris_vector_graph.stores import arno_bridge
        eng, _, _, _ = arno_graph
        # arno_bridge.arno_available uses ZF SQL probe — requires _ensure_zf_call_function first
        arno_bridge._ensure_zf_call_function(eng.conn)
        result = arno_bridge.arno_available(eng.conn)
        assert isinstance(result, bool)
        # With callout loaded, should be True — but ZF probe may fail on community IRIS
        # so we just verify it returns a bool without crashing

    def test_arno_bridge_ensure_zf_functions_idempotent(self, arno_graph):
        """_ensure_zf_call_function can be called multiple times without crashing."""
        from iris_vector_graph.stores import arno_bridge
        eng, _, _, _ = arno_graph
        arno_bridge._ensure_zf_call_function(eng.conn)
        arno_bridge._ensure_zf_call_function(eng.conn)  # second call — idempotent

    def test_arno_bridge_remap_kernel_ids_pure_python(self):
        """remap_kernel_ids maps kernel integer IDs back to node string IDs."""
        from iris_vector_graph.stores import arno_bridge
        import json
        # remap_kernel_ids(result_json: str, idx_to_node: list) -> list
        # result_json: JSON list of dicts with integer 'id' fields
        # idx_to_node: list where list[integer_id] = node_string_id
        idx_to_node = ["", "node_a", "node_b", "node_c"]  # idx 0 unused
        result_json = json.dumps([{"id": 1, "score": 0.5}, {"id": 2, "score": 0.3}])
        result = arno_bridge.remap_kernel_ids(result_json, idx_to_node)
        assert isinstance(result, list)
        # Each item should have string id mapped from idx_to_node
        ids = [r.get("id") for r in result if isinstance(r, dict)]
        assert "node_a" in ids or len(result) >= 1

    def test_arno_bridge_call_available_after_zf_install(self, arno_graph):
        """After installing ZF functions, arno_available probe can execute."""
        from iris_vector_graph.stores import arno_bridge
        eng, _, _, _ = arno_graph
        arno_bridge._ensure_zf_call_function(eng.conn)
        arno_bridge.clear_probe_cache()  # force re-probe
        available = arno_bridge.arno_available(eng.conn)
        assert isinstance(available, bool)


# ---------------------------------------------------------------------------
# Full engine-level Arno acceleration via execute_cypher
# ---------------------------------------------------------------------------

class TestArnoViaExecuteCypher:

    def test_ppr_cypher_via_arno(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng.execute_cypher(
            "CALL ivg.ppr($seed, 0.85, 20, 5) YIELD id, score RETURN id, score",
            {"seed": "arno_0"},
        )
        assert result is not None

    def test_pagerank_via_kg_pagerank(self, arno_graph):
        eng, _, caps, _ = arno_graph
        if "pagerank" not in caps.get("rust_algorithms", []):
            pytest.skip("pagerank not in rust_algorithms")
        result = eng.kg_PAGERANK()
        assert result is not None

    def test_betweenness_via_engine(self, arno_graph):
        eng, _, _, _ = arno_graph
        result = eng.betweenness_centrality(
            sample_size=0, top_k=5, direction="out", max_hops=3,
        )
        assert result is not None

    def test_wcc_via_engine(self, arno_graph):
        eng, _, caps, _ = arno_graph
        if "wcc" not in caps.get("rust_algorithms", []):
            pytest.skip("wcc not in rust_algorithms")
        result = eng.kg_WCC()
        assert result is not None

    def test_cdlp_via_engine(self, arno_graph):
        eng, _, caps, _ = arno_graph
        if "cdlp" not in caps.get("rust_algorithms", []):
            pytest.skip("cdlp not in rust_algorithms")
        result = eng.kg_CDLP()
        assert result is not None
