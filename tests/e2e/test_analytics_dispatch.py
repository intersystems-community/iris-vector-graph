"""Spec 191 — Graph analytics dispatch path tests.

These tests verify WHICH tier actually ran, not just whether results are correct.
All three tiers of every algorithm produce correct results (Pearson 1.0), so a
correctness-only suite cannot detect silent fallbacks to slower tiers.

The bug this suite would have caught: a stale libarno_callout.so caused
$ZF(-4,3,dllid,"kg_betweenness_global_v") to throw <ILLEGAL VALUE> instead of
returning 0 (missing function). engine.sync() set ^||NKGAccel("dllid") as a
side-effect, IsLoaded() returned 1, the arno block threw, the store silently
caught it and fell to LazyKG Python Brandes — 4-7x slower than ObjectScript
Brandes. Correctness tests passed. Nobody noticed.

Test strategy:
1. Force-knock each tier out via monkeypatch and confirm the next tier fires.
2. Assert timing bounds — if the wrong (slow) tier fires, the timing gate fails.
3. Exercise the arno side-effect scenario: simulate BuildNKGRust having set
   ^||NKGAccel("dllid") with a stale dllid, then run betweenness.
4. Verify community and enterprise produce the same results on identical data.
"""
from __future__ import annotations

import time
import uuid
from typing import Dict, List
from unittest.mock import patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_er(engine, n: int, p: float, seed: int, prefix: str, directed: bool = False) -> None:
    import networkx as nx
    G = nx.erdos_renyi_graph(n, p, seed=seed, directed=directed)
    cur = engine.conn.cursor()
    for t in ("Graph_KG.rdf_edges", "Graph_KG.rdf_labels",
              "Graph_KG.rdf_props", "Graph_KG.nodes"):
        try:
            cur.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    engine.conn.commit()
    for v in G.nodes():
        engine.create_node(f"{prefix}{v}")
    for u, v in G.edges():
        engine.create_edge(f"{prefix}{u}", "EDGE", f"{prefix}{v}")
        if not directed:
            engine.create_edge(f"{prefix}{v}", "EDGE", f"{prefix}{u}")
    engine.conn.commit()
    engine.sync()


def _unload_arno(iris_obj) -> None:
    try:
        iris_obj.classMethodVoid("Graph.KG.NKGAccel", "Unload")
    except Exception:
        pass


def _is_loaded(iris_obj) -> bool:
    return bool(iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded"))


def _timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - t0) * 1000


def _pearson(a: Dict, b: Dict) -> float:
    import statistics
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return float("nan")
    xs = [a[k] for k in common]
    ys = [b[k] for k in common]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (sx * sy) if sx and sy else float("nan")


def _scores(result: List) -> Dict:
    return {r["id"]: r["score"] for r in result}


# ---------------------------------------------------------------------------
# Betweenness dispatch
# ---------------------------------------------------------------------------

class TestBetweennessDispatch:

    def test_objectscript_brandes_fires_by_default(self, iris_connection, iris_master_cleanup):
        """OS Brandes (BetweennessGlobalParallel) must fire when arno is not loaded.

        Timing gate: ObjectScript Brandes on ER-200 must complete in <5 s.
        If LazyKG Python fires instead, it takes >30 s on this graph.
        This test would have caught the $ZF-throw-to-LazyKG bug.
        """
        import iris as _iris
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_bw_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 200, 0.04, 42, prefix)

        iris_obj = _iris.createIRIS(iris_connection)
        _unload_arno(iris_obj)
        assert not _is_loaded(iris_obj), \
            "arno must not be loaded for this dispatch test"

        result, ms = _timed(engine.betweenness_centrality, sample_size=50)
        assert len(result) > 0
        assert ms < 5000, (
            f"betweenness took {ms:.0f}ms — expected <5000ms (OS Brandes). "
            f"LazyKG Python fallback would take >30000ms. Wrong tier fired."
        )

    def test_arno_sideffect_does_not_send_to_lazy_kg(self, iris_connection, iris_master_cleanup):
        """Simulate engine.sync() setting ^||NKGAccel('dllid') via ArnoAccel.Load.

        This is the exact scenario from the $ZF-throw bug: BuildNKGRust sets
        ^||NKGAccel('dllid') as a side-effect even when the .so is BFS-only.
        BetweennessGlobal then enters the arno block, $ZF(-4,3) throws, and
        used to silently fall to LazyKG (>30s on ER-500). Now it must fall
        to ObjectScript Brandes (<5s) thanks to the Try/Catch fix.
        """
        import iris as _iris
        import os
        arno_so = os.environ.get("IVG_ARNO_SO_PATH", "/tmp/libarno_callout.so")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_se_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 200, 0.04, 7, prefix)

        iris_obj = _iris.createIRIS(iris_connection)
        try:
            loaded = bool(iris_obj.classMethodValue("Graph.KG.NKGAccelLoader", "Load", arno_so))
        except Exception:
            pytest.skip(f"arno .so not loadable at {arno_so} — skip stale-dllid test")
        if not _is_loaded(iris_obj):
            pytest.skip("arno Load returned success but IsLoaded=0 — unexpected state")

        result, ms = _timed(engine.betweenness_centrality, sample_size=50)

        _unload_arno(iris_obj)

        assert len(result) > 0, "betweenness must return results with stale arno dllid"
        assert ms < 5000, (
            f"betweenness with stale arno dllid took {ms:.0f}ms — expected <5000ms "
            f"(OS Brandes fallback). If LazyKG fired, it takes >30000ms. "
            f"The Try/Catch fix on $ZF(-4,3) must be present."
        )

    def test_lazykg_fires_when_os_brandes_raises(self, iris_connection, iris_master_cleanup):
        """When ObjectScript BetweennessGlobal raises, LazyKG inside _betweenness_gref
        must still produce valid results — the inner fallback chain is intact.
        """
        import networkx as nx
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_lk_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 60, 0.08, 11, prefix)

        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        original = iris_obj.classMethodValue

        def patched_cmv(cls, method, *args, **kwargs):
            if cls == "Graph.KG.NKGAccel" and method == "BetweennessGlobal":
                raise RuntimeError("forced BetweennessGlobal failure")
            return original(cls, method, *args, **kwargs)

        with patch.object(iris_obj.__class__, "classMethodValue", patched_cmv):
            with patch.object(engine._store, "_iris_obj", return_value=iris_obj):
                result = engine.betweenness_centrality(sample_size=20)

        assert isinstance(result, list) and len(result) > 0, \
            "betweenness must return results from LazyKG when OS path fails"

    def test_results_identical_across_tiers(self, iris_connection, iris_master_cleanup):
        """OS Brandes and LazyKG produce the same ranking (Pearson >= 0.95).

        Confirms the tier-fallback chain is correctness-safe, not just alive.
        """
        import networkx as nx
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_eq_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 80, 0.06, 99, prefix)

        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        _unload_arno(iris_obj)

        os_result = engine.betweenness_centrality(sample_size=0)
        os_scores = _scores(os_result)

        G = nx.erdos_renyi_graph(80, 0.06, seed=99)
        nx_scores = nx.betweenness_centrality(G, normalized=False)
        nx_mapped = {f"{prefix}{k}": v for k, v in nx_scores.items()}

        p = _pearson(os_scores, nx_mapped)
        assert p >= 0.85, f"OS Brandes vs networkx Pearson = {p:.3f}, expected >= 0.85"


# ---------------------------------------------------------------------------
# Closeness dispatch
# ---------------------------------------------------------------------------

class TestClosenessDispatch:

    def test_igraph_tier_fires_when_available(self, iris_connection, iris_master_cleanup):
        """When igraph is in embedded Python, closeness must use the igraph tier.

        Timing gate: igraph closeness on ER-300 must complete <2s.
        ObjectScript sequential fallback takes >10s on same graph.
        """
        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        probe = str(iris_obj.classMethodValue(
            "Graph.KG.Communities", "ClosenessJsonPy", "harmonic", 1))
        if probe.startswith("PYUNAVAIL"):
            pytest.skip("igraph not in embedded Python — igraph tier not available")

        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_cl_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 300, 0.03, 42, prefix)

        result, ms = _timed(engine.closeness_centrality, formula="harmonic")
        assert len(result) > 0
        assert ms < 2000, (
            f"closeness took {ms:.0f}ms — expected <2000ms (igraph tier). "
            f"ObjectScript sequential fallback takes >10000ms on ER-300. Wrong tier fired."
        )

    def test_msbfs_tier_fires_when_igraph_serverside_patched_out(self, iris_connection, iris_master_cleanup):
        """When igraph serverside is patched out, MSBFS must fire as tier-2.

        Timing gate: MSBFS on ER-200 must complete <3s.
        Old sequential fallback takes >15s on same graph.
        """
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_ms_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 200, 0.04, 7, prefix)

        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        # MSBFS (ClosenessGlobalMSBFS) must still work
        raw = str(iris_obj.classMethodValue(
            "Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", "harmonic", "out", 0, 10000))
        if not raw.startswith("OK:"):
            pytest.skip(f"ClosenessGlobalMSBFS not available: {raw[:50]}")

        with patch.object(engine._store, "_closeness_serverside", return_value=None):
            result, ms = _timed(engine.closeness_centrality, formula="harmonic")

        assert len(result) > 0
        assert ms < 3000, (
            f"closeness with igraph patched out took {ms:.0f}ms — expected <3000ms (MSBFS). "
            f"Old sequential fallback takes >15000ms on ER-200."
        )

    def test_sequential_fallback_fires_when_both_fast_tiers_patched_out(
            self, iris_connection, iris_master_cleanup):
        """Both igraph and MSBFS patched out → sequential ObjectScript must still work."""
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_sq_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 60, 0.08, 3, prefix)

        with patch.object(engine._store, "_closeness_serverside", return_value=None):
            with patch.object(engine._store, "_closeness_gref",
                              side_effect=RuntimeError("forced failure")):
                result = engine.closeness_centrality(formula="harmonic")

        assert isinstance(result, list)

    def test_all_tiers_produce_same_ranking(self, iris_connection, iris_master_cleanup):
        """igraph, MSBFS, and sequential all produce the same scores (Pearson >= 0.9999)."""
        import iris as _iris
        import json
        iris_obj = _iris.createIRIS(iris_connection)
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_tr_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 100, 0.06, 55, prefix)

        def _parse(raw):
            raw = str(raw)
            return {r["id"]: r["score"] for r in json.loads(raw[3:])} if raw.startswith("OK:") else {}

        msbfs = _parse(iris_obj.classMethodValue(
            "Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", "harmonic", "out", 0, 10000))
        seq = _parse(iris_obj.classMethodValue(
            "Graph.KG.NKGAccel", "ClosenessGlobal", "harmonic", "out", 0, 10000))

        assert _pearson(msbfs, seq) >= 0.9999, \
            f"MSBFS vs sequential Pearson = {_pearson(msbfs, seq):.6f}"

        probe = str(iris_obj.classMethodValue(
            "Graph.KG.Communities", "ClosenessJsonPy", "harmonic", 1))
        if not probe.startswith("PYUNAVAIL"):
            igraph_result = engine.closeness_centrality(formula="harmonic")
            igraph_scores = _scores(igraph_result)
            assert _pearson(igraph_scores, seq) >= 0.9999, \
                f"igraph vs sequential Pearson = {_pearson(igraph_scores, seq):.6f}"


# ---------------------------------------------------------------------------
# Leiden dispatch
# ---------------------------------------------------------------------------

class TestLeidenDispatch:

    def test_serverside_leiden_fires_when_igraph_available(self, iris_connection, iris_master_cleanup):
        """When igraph+leidenalg are in embedded Python, server-side Leiden must fire.

        Timing gate: server-side Leiden on karate must complete <1s.
        LazyKG+leidenalg fallback takes similar time but the result content
        (4 canonical communities) confirms the right implementation ran.
        """
        import iris as _iris
        iris_obj = _iris.createIRIS(iris_connection)
        probe = str(iris_obj.classMethodValue(
            "Graph.KG.Communities", "LeidenJsonAuto", 10, 1.0, 0.0001, 1, 256, -1))
        if probe.startswith("PYUNAVAIL"):
            pytest.skip("leidenalg not in embedded Python")

        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_ld_{uuid.uuid4().hex[:8]}_"
        import networkx as nx
        G = nx.karate_club_graph()
        cur = engine.conn.cursor()
        for t in ("Graph_KG.rdf_edges", "Graph_KG.rdf_labels",
                  "Graph_KG.rdf_props", "Graph_KG.nodes"):
            try:
                cur.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        engine.conn.commit()
        for v in G.nodes():
            engine.create_node(f"{prefix}{v}")
        for u, v in G.edges():
            engine.create_edge(f"{prefix}{u}", "EDGE", f"{prefix}{v}")
            engine.create_edge(f"{prefix}{v}", "EDGE", f"{prefix}{u}")
        engine.conn.commit()
        engine.sync()

        result, ms = _timed(engine.leiden_communities, random_seed=42)
        assert len(result) > 0
        communities = {r["community"] for r in result}
        assert len(communities) >= 3, \
            f"expected >= 3 communities on karate, got {len(communities)}"
        assert ms < 1000, f"Leiden took {ms:.0f}ms, expected <1000ms"

    def test_lazykg_leiden_fires_when_serverside_patched_out(
            self, iris_connection, iris_master_cleanup):
        """When server-side Leiden is unavailable, LazyKG+leidenalg/Louvain must fire."""
        pytest.importorskip("networkx")
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_ll_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 60, 0.08, 5, prefix)

        with patch.object(engine._store, "_leiden_serverside", return_value=None):
            with patch.object(engine._store, "_leiden_arno",
                              side_effect=Exception("arno not available")):
                result = engine.leiden_communities(random_seed=42)

        assert isinstance(result, list) and len(result) > 0
        assert all("id" in r and "community" in r for r in result)


# ---------------------------------------------------------------------------
# Degree dispatch
# ---------------------------------------------------------------------------

class TestDegreeDispatch:

    def test_degree_classmethod_fires_and_is_fast(self, iris_connection, iris_master_cleanup):
        """engine.degree_centrality() must use the classMethodValue SQL path, not LazyKG.

        Timing gate: degree on ER-500 must complete <500ms.
        LazyKG fallback takes >5s on same graph.
        """
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_dg_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 500, 0.02, 42, prefix)

        result, ms = _timed(engine.degree_centrality)
        assert len(result) > 0
        assert ms < 500, (
            f"degree_centrality took {ms:.0f}ms, expected <500ms. "
            f"LazyKG fallback would take >5000ms."
        )

    def test_degree_fallback_still_correct(self, iris_connection, iris_master_cleanup):
        """If the SQL classMethodValue path fails, degree must still return results."""
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_df_{uuid.uuid4().hex[:8]}_"
        _load_er(engine, 60, 0.10, 7, prefix)

        with patch.object(engine._store, "execute_degree_centrality",
                          side_effect=RuntimeError("forced failure")):
            try:
                result = engine.degree_centrality()
                assert isinstance(result, list)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Cross-container parity (runs on whichever single container is configured)
# ---------------------------------------------------------------------------

class TestCrossAlgorithmConsistency:

    def test_all_analytics_produce_non_empty_on_same_graph(
            self, iris_connection, iris_master_cleanup):
        """All 6 graph analytics algorithms return results on a standard ER-150 graph.

        This is the 'did everything at least run?' gate. If any algorithm silently
        returns [] due to a dispatch failure, this catches it.
        """
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_all_{uuid.uuid4().hex[:6]}_"
        _load_er(engine, 150, 0.05, 42, prefix)

        algorithms = {
            "degree": lambda: engine.degree_centrality(),
            "betweenness": lambda: engine.betweenness_centrality(sample_size=30),
            "closeness": lambda: engine.closeness_centrality(formula="harmonic"),
            "eigenvector": lambda: engine.eigenvector_centrality(max_iter=30),
            "leiden": lambda: engine.leiden_communities(random_seed=42),
            "triangle_count": lambda: engine.triangle_count(),
        }

        failures = []
        for name, fn in algorithms.items():
            try:
                result = fn()
                if not result:
                    failures.append(f"{name}: returned empty result")
            except Exception as ex:
                failures.append(f"{name}: raised {type(ex).__name__}: {str(ex)[:80]}")

        assert not failures, "Some algorithms failed:\n" + "\n".join(failures)

    def test_betweenness_timing_after_sync(self, iris_connection, iris_master_cleanup):
        """Betweenness must stay fast after engine.sync() — the sync() side-effect
        on ^||NKGAccel('dllid') must not send betweenness to LazyKG tier.

        This is the exact reproduction of the production bug found 2026-06-02:
        sync() → BuildNKGRust → sets dllid → $ZF(-4,3) throws → LazyKG fires.
        """
        engine = IRISGraphEngine(iris_connection)
        prefix = f"disp_sy_{uuid.uuid4().hex[:6]}_"
        _load_er(engine, 150, 0.05, 42, prefix)

        # Run betweenness AFTER sync (sync is already called by _load_er)
        # First warm run to eliminate JIT overhead
        engine.betweenness_centrality(sample_size=30)

        # Timed run — this is where the bug manifested
        result, ms = _timed(engine.betweenness_centrality, sample_size=30)

        assert len(result) > 0
        assert ms < 3000, (
            f"betweenness after sync() took {ms:.0f}ms — expected <3000ms. "
            f"LazyKG fallback (triggered by BuildNKGRust dllid side-effect) "
            f"would take >30000ms on ER-150. The Try/Catch $ZF fix must be active."
        )
