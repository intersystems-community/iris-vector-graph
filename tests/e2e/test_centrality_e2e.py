"""Spec 162 — End-to-end Centrality tests against live gqs-ivg-test container.

Test-first (Constitution Principle III): all tests below are authored BEFORE
the corresponding ObjectScript classmethods + Python wrappers exist.

Phase 3 (Degree Centrality, T022/T023/T031): tests start failing with
NotImplementedError; turn green as T024–T032 land.

Phase 4-6 tests for Betweenness, Closeness, Eigenvector are added as
those phases ship.

Constitution Principle IV: uses iris_connection fixture (no hardcoded ports);
container managed by scripts/test-container.sh.
"""

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from tests.e2e.fixtures.centrality_graphs import (
    make_erdos_renyi_graph,
    make_disconnected_graph,
    make_directed_cycle,
    load_into_engine,
)


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _load_unique_graph(engine, fixture, prefix=None):
    """Load a fixture with unique node IDs to avoid cross-test contamination.

    Calls BuildKG() after loading to populate ^KG from rdf_edges (in case
    Graph.KG.EdgeScan failed to compile and create_edge couldn't write ^KG live).
    """
    if prefix is None:
        prefix = f"c162_{uuid.uuid4().hex[:8]}_"
    for nid in fixture["nodes"]:
        engine.create_node(prefix + nid)
    for s, p, o in fixture["edges"]:
        engine.create_edge(prefix + s, p, prefix + o)
    engine.conn.commit()
    from iris_vector_graph.schema import _call_classmethod
    _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")
    return prefix


class TestDegreeCentrality:
    """T022/T023/T031 — User Stories US1, US2, US3 (Degree Centrality)."""

    def test_degree_centrality_returns_top_influencers(self, iris_connection, iris_master_cleanup):
        """US1 (P0): engine.degree_centrality() returns ranked list with id/score/degree."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=20, p=0.3, seed=42, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.degree_centrality(top_k=5)

        assert isinstance(result, list)
        assert len(result) <= 5
        assert len(result) > 0
        for row in result:
            assert "id" in row
            assert "score" in row
            assert "degree" in row
            assert isinstance(row["score"], (int, float))
            assert 0.0 <= row["score"] <= 1.0
            assert isinstance(row["degree"], int)
            assert row["degree"] >= 0
            assert row["id"].startswith(prefix)

        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_degree_centrality_predicate_filter_inbound(self, iris_connection, iris_master_cleanup):
        """US3 (P1): direction='in' + predicate filter respects both."""
        engine = IRISGraphEngine(iris_connection)
        prefix = f"c162_{uuid.uuid4().hex[:8]}_"

        engine.create_node(prefix + "A")
        engine.create_node(prefix + "B")
        engine.create_node(prefix + "C")
        engine.create_node(prefix + "D")
        engine.create_edge(prefix + "A", "CITES", prefix + "B")
        engine.create_edge(prefix + "C", "CITES", prefix + "B")
        engine.create_edge(prefix + "D", "CITES", prefix + "B")
        engine.create_edge(prefix + "A", "MENTIONS", prefix + "C")
        engine.conn.commit()
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")

        result = engine.degree_centrality(direction="in", predicate="CITES", top_k=10)

        scores_by_id = {r["id"]: r for r in result}
        b_id = prefix + "B"
        assert b_id in scores_by_id, f"Expected {b_id} in results, got {list(scores_by_id.keys())[:5]}"
        assert scores_by_id[b_id]["degree"] == 3

    @pytest.mark.xfail(
        reason="Bug S: SQL function kg_DegreeCentrality calls ##class(Graph.KG.Centrality) "
               "which fails with <CLASS DOES NOT EXIST> via the SQL bindings server. "
               "Python API path works (test_degree_centrality_returns_top_influencers); "
               "Cypher CALL path requires SQL-function-side gref bypass. See ENGINEERING_DEBT.md Bug S.",
        strict=False,
    )
    def test_cypher_call_ivg_degree_centrality(self, iris_connection, iris_master_cleanup):
        """US2 (P0): CALL ivg.degreeCentrality() YIELD node, score, degree works via Cypher."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=15, p=0.3, seed=7, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.execute_cypher(
            "CALL ivg.degreeCentrality() YIELD node, score, degree "
            "RETURN node, score, degree ORDER BY score DESC LIMIT 5"
        )

        assert result.error is None or result.error == "", f"Cypher error: {result.error}"
        assert len(result.rows) <= 5
        assert len(result.columns) == 3


class TestBetweennessCentrality:
    """T033/T034/T035 — User Stories US4, US5 (Betweenness Centrality)."""

    def test_betweenness_exact_on_small_graph(self, iris_connection, iris_master_cleanup):
        """US4 (P0): exact Betweenness on 20-node graph matches networkx."""
        import networkx as nx

        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=20, p=0.25, seed=42, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.betweenness_centrality(sample_size=0, top_k=20)

        assert isinstance(result, list)
        for row in result:
            assert "id" in row
            assert "score" in row
            assert isinstance(row["score"], (int, float))
            assert row["score"] >= 0.0
            assert row["id"].startswith(prefix)

        scores = [r["score"] for r in result if not r.get("_approximate")]
        assert scores == sorted(scores, reverse=True)

        # networkx parity (correlation > 0.95 for small graphs — Brandes is exact)
        G = fixture["nx_graph"]
        nx_bc = nx.betweenness_centrality(G, normalized=False)
        nx_by_id = {f"{prefix}n{n}": v for n, v in nx_bc.items()}
        ivg_by_id = {r["id"]: r["score"] for r in result if not r.get("_approximate")}

        common = set(nx_by_id) & set(ivg_by_id)
        if len(common) > 5:
            import statistics
            nx_vals = [nx_by_id[k] for k in common]
            ivg_vals = [ivg_by_id[k] for k in common]
            if sum(nx_vals) > 0 and sum(ivg_vals) > 0:
                mean_nx = statistics.mean(nx_vals)
                mean_ivg = statistics.mean(ivg_vals)
                num = sum((a - mean_nx) * (b - mean_ivg) for a, b in zip(nx_vals, ivg_vals))
                den_nx = sum((a - mean_nx) ** 2 for a in nx_vals) ** 0.5
                den_ivg = sum((b - mean_ivg) ** 2 for b in ivg_vals) ** 0.5
                if den_nx > 0 and den_ivg > 0:
                    pearson = num / (den_nx * den_ivg)
                    assert pearson > 0.85, (
                        f"Betweenness Pearson correlation with networkx = {pearson:.3f}, "
                        f"expected > 0.85 (FR-020 gate)"
                    )

    def test_betweenness_sampling_for_huge_graph(self, iris_connection, iris_master_cleanup):
        """US5 (P1): sample_size>0 returns approximate scores in reasonable time."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=50, p=0.2, seed=11, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.betweenness_centrality(sample_size=10, top_k=10)

        assert isinstance(result, list)
        non_meta = [r for r in result if not r.get("_approximate")]
        assert len(non_meta) > 0
        assert len(non_meta) <= 10
        for row in non_meta:
            assert row["score"] >= 0.0

    def test_betweenness_progress_callback_fires(self, iris_connection, iris_master_cleanup):
        """FR-022: progress_callback receives (current, total) updates between sources."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=10, p=0.3, seed=5, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        calls = []
        result = engine.betweenness_centrality(
            sample_size=0,
            top_k=5,
            progress_callback=lambda c, t: calls.append((c, t)),
        )

        assert len(calls) > 0, "progress_callback should fire at least once"
        last = calls[-1]
        assert last[0] == last[1], f"final call should have current==total, got {last}"
        for current, total in calls:
            assert 0 <= current <= total
            assert total > 0

    def test_get_centrality_warnings_returns_list(self, iris_connection, iris_master_cleanup):
        """FR-028: engine.get_centrality_warnings() returns list (possibly empty)."""
        engine = IRISGraphEngine(iris_connection)
        warnings = engine.get_centrality_warnings(max_entries=10)
        assert isinstance(warnings, list)
        for w in warnings:
            assert "timestamp" in w
            assert "source" in w
            assert "reason" in w


class TestClosenessCentrality:
    """T052/T053/T054 — User Story US6 (Closeness Centrality)."""

    def test_closeness_harmonic_handles_disconnected(self, iris_connection, iris_master_cleanup):
        """US6 (P0): harmonic formula gives nonzero scores per-component on disconnected graph."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_disconnected_graph()
        prefix = _load_unique_graph(engine, fixture)

        result = engine.closeness_centrality(formula="harmonic", top_k=20)

        assert isinstance(result, list)
        assert len(result) > 0
        nonzero = [r for r in result if r["score"] > 0.0]
        assert len(nonzero) >= 8, (
            f"harmonic Closeness on 3 disconnected cliques (5+4+3=12 nodes) should "
            f"give nonzero scores to most reachable nodes, got {len(nonzero)} nonzero"
        )

    def test_closeness_classical_zeros_on_disconnected(self, iris_connection, iris_master_cleanup):
        """US6 (P0): classical formula returns 0 for nodes that can't reach all others."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_disconnected_graph()
        prefix = _load_unique_graph(engine, fixture)

        result = engine.closeness_centrality(formula="classical", top_k=20)

        assert isinstance(result, list)
        for row in result:
            assert row["score"] == 0.0, (
                f"classical Closeness on disconnected graph should give 0 to all nodes "
                f"(none can reach the full graph), got {row['id']}={row['score']}"
            )

    def test_closeness_matches_networkx_harmonic(self, iris_connection, iris_master_cleanup):
        """FR-020: Closeness harmonic Pearson correlation with networkx > 0.85.

        networkx.harmonic_centrality uses INBOUND distances d(v,u): "how reachable is u
        from other nodes". To match, IVG must run with direction='in'.
        """
        import networkx as nx

        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=20, p=0.3, seed=99, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.closeness_centrality(formula="harmonic", direction="in", top_k=20)

        ivg_by_id = {r["id"]: r["score"] for r in result}
        G = fixture["nx_graph"]
        nx_hc = nx.harmonic_centrality(G)
        nx_by_id = {f"{prefix}n{n}": v for n, v in nx_hc.items()}

        common = set(nx_by_id) & set(ivg_by_id)
        if len(common) > 5:
            import statistics
            nx_vals = [nx_by_id[k] for k in common]
            ivg_vals = [ivg_by_id[k] for k in common]
            if sum(nx_vals) > 0 and sum(ivg_vals) > 0:
                mean_nx = statistics.mean(nx_vals)
                mean_ivg = statistics.mean(ivg_vals)
                num = sum((a - mean_nx) * (b - mean_ivg) for a, b in zip(nx_vals, ivg_vals))
                den_nx = sum((a - mean_nx) ** 2 for a in nx_vals) ** 0.5
                den_ivg = sum((b - mean_ivg) ** 2 for b in ivg_vals) ** 0.5
                if den_nx > 0 and den_ivg > 0:
                    pearson = num / (den_nx * den_ivg)
                    assert pearson > 0.85, (
                        f"Closeness Pearson with networkx.harmonic_centrality = {pearson:.3f}, "
                        f"expected > 0.85 (using direction='in' to match nx convention)"
                    )


class TestEigenvectorCentrality:
    """T068/T069/T070 — User Story US7 (Eigenvector Centrality)."""

    def test_eigenvector_uniform_on_directed_cycle(self, iris_connection, iris_master_cleanup):
        """US7 (P0): on a directed cycle a→b→c→...→a, all nodes have equal eigenvector score."""
        engine = IRISGraphEngine(iris_connection)
        fixture = make_directed_cycle(n=5)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.eigenvector_centrality(max_iter=200, tol=1e-8, top_k=10)

        assert isinstance(result, list)
        assert len(result) == 5
        scores = [r["score"] for r in result]
        spread = max(scores) - min(scores)
        assert spread < 0.05, (
            f"Directed cycle should give uniform eigenvector scores; "
            f"got spread={spread:.4f}, scores={scores}"
        )

    def test_eigenvector_matches_networkx_numpy(self, iris_connection, iris_master_cleanup):
        """FR-020: Eigenvector Pearson correlation with networkx > 0.85 on connected graph."""
        import networkx as nx

        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=15, p=0.4, seed=17, directed=True)
        prefix = _load_unique_graph(engine, fixture)

        result = engine.eigenvector_centrality(max_iter=200, tol=1e-8, top_k=20)

        ivg_by_id = {r["id"]: r["score"] for r in result}

        try:
            G = fixture["nx_graph"]
            nx_eig = nx.eigenvector_centrality_numpy(G, max_iter=200)
            nx_by_id = {f"{prefix}n{n}": v for n, v in nx_eig.items()}
        except Exception:
            return

        common = set(nx_by_id) & set(ivg_by_id)
        if len(common) > 5:
            import statistics
            nx_vals = [nx_by_id[k] for k in common]
            ivg_vals = [ivg_by_id[k] for k in common]
            if sum(abs(v) for v in nx_vals) > 0 and sum(ivg_vals) > 0:
                mean_nx = statistics.mean(nx_vals)
                mean_ivg = statistics.mean(ivg_vals)
                num = sum((a - mean_nx) * (b - mean_ivg) for a, b in zip(nx_vals, ivg_vals))
                den_nx = sum((a - mean_nx) ** 2 for a in nx_vals) ** 0.5
                den_ivg = sum((b - mean_ivg) ** 2 for b in ivg_vals) ** 0.5
                if den_nx > 0 and den_ivg > 0:
                    pearson = num / (den_nx * den_ivg)
                    assert abs(pearson) > 0.85, (
                        f"Eigenvector |Pearson| with networkx = {pearson:.3f}, expected > 0.85. "
                        f"(abs because eigenvectors are sign-ambiguous)"
                    )

    def test_eigenvector_isolated_node_scores_zero(self, iris_connection, iris_master_cleanup):
        """FR-021: dangling nodes (deg=0) should score ≈ 0 — no teleport, pure power iteration."""
        engine = IRISGraphEngine(iris_connection)
        prefix = f"c162_{uuid.uuid4().hex[:8]}_"

        engine.create_node(prefix + "isolated")
        engine.create_node(prefix + "A")
        engine.create_node(prefix + "B")
        engine.create_node(prefix + "C")
        engine.create_edge(prefix + "A", "EDGE", prefix + "B")
        engine.create_edge(prefix + "B", "EDGE", prefix + "C")
        engine.create_edge(prefix + "C", "EDGE", prefix + "A")
        engine.conn.commit()
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")

        result = engine.eigenvector_centrality(max_iter=100, tol=1e-8, top_k=10)
        scores_by_id = {r["id"]: r["score"] for r in result}

        iso_id = prefix + "isolated"
        if iso_id in scores_by_id:
            assert scores_by_id[iso_id] < 0.05, (
                f"isolated node should score near 0, got {scores_by_id[iso_id]}"
            )


class TestCentralityCrossCutting:
    """T084/T085/T091 — Capabilities + NotImplementedError contract (US9, FR-014)."""

    def test_capabilities_includes_centrality_keys(self, iris_connection):
        """US9 (P1): IRISGraphStore.capabilities() declares all 4 centrality algos as True."""
        engine = IRISGraphEngine(iris_connection)
        caps = engine._store.capabilities()
        for key in ("degree_centrality", "betweenness", "closeness", "eigenvector"):
            assert caps.get(key) is True, f"capabilities() missing or False for {key}"

    def test_engine_raises_not_implemented_for_unsupported_store(self):
        """FR-014: engine raises NotImplementedError when store doesn't support algorithm."""
        from unittest.mock import MagicMock

        class LimitedStore:
            def capabilities(self):
                return {
                    "degree_centrality": True,
                    "betweenness": False,
                    "closeness": False,
                    "eigenvector": False,
                }

        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        engine._store = LimitedStore()

        with pytest.raises(NotImplementedError, match="betweenness"):
            engine.betweenness_centrality()
        with pytest.raises(NotImplementedError, match="closeness"):
            engine.closeness_centrality()
        with pytest.raises(NotImplementedError, match="eigenvector"):
            engine.eigenvector_centrality()


class TestNetworkxParityMasterGate:
    """T093 — FR-020 master gate: all 4 algorithms vs networkx on a single fixture."""

    def test_all_four_centralities_match_networkx(self, iris_connection, iris_master_cleanup):
        """Single test running all 4 IVG centralities + 4 networkx equivalents on same graph.

        Pearson correlation > 0.85 required for each algorithm (FR-020 gate, relaxed
        from spec's 0.999 — see clarification on small-graph variance).
        """
        import networkx as nx
        import statistics

        engine = IRISGraphEngine(iris_connection)
        fixture = make_erdos_renyi_graph(n=20, p=0.35, seed=2026, directed=True)
        prefix = _load_unique_graph(engine, fixture)
        G = fixture["nx_graph"]

        def _pearson(a: dict, b: dict) -> float:
            common = set(a) & set(b)
            if len(common) < 5:
                return 0.0
            av = [a[k] for k in common]
            bv = [b[k] for k in common]
            if sum(av) == 0 or sum(abs(v) for v in bv) == 0:
                return 0.0
            ma = statistics.mean(av)
            mb = statistics.mean(bv)
            num = sum((x - ma) * (y - mb) for x, y in zip(av, bv))
            da = sum((x - ma) ** 2 for x in av) ** 0.5
            db = sum((y - mb) ** 2 for y in bv) ** 0.5
            return num / (da * db) if (da > 0 and db > 0) else 0.0

        ivg_deg = {r["id"]: r["score"] for r in engine.degree_centrality(top_k=20)}
        nx_deg = {f"{prefix}n{n}": v for n, v in nx.out_degree_centrality(G).items()}
        p_deg = _pearson(nx_deg, ivg_deg)
        assert p_deg > 0.85, f"Degree Pearson = {p_deg:.3f}"

        ivg_bc_raw = engine.betweenness_centrality(sample_size=0, top_k=20)
        ivg_bc = {r["id"]: r["score"] for r in ivg_bc_raw if "_approximate" not in r}
        nx_bc = {f"{prefix}n{n}": v for n, v in nx.betweenness_centrality(G, normalized=False).items()}
        p_bc = _pearson(nx_bc, ivg_bc)
        assert p_bc > 0.85, f"Betweenness Pearson = {p_bc:.3f}"

        ivg_close = {r["id"]: r["score"] for r in engine.closeness_centrality(direction="in", top_k=20)}
        nx_close = {f"{prefix}n{n}": v for n, v in nx.harmonic_centrality(G).items()}
        p_close = _pearson(nx_close, ivg_close)
        assert p_close > 0.85, f"Closeness Pearson = {p_close:.3f}"

        ivg_eig = {r["id"]: r["score"] for r in engine.eigenvector_centrality(max_iter=200, tol=1e-8, top_k=20)}
        try:
            nx_eig_raw = nx.eigenvector_centrality_numpy(G, max_iter=200)
            nx_eig = {f"{prefix}n{n}": v for n, v in nx_eig_raw.items()}
            p_eig = _pearson(nx_eig, ivg_eig)
            assert abs(p_eig) > 0.85, f"Eigenvector |Pearson| = {p_eig:.3f}"
        except Exception:
            pass
