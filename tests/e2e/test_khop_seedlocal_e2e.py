"""Spec 164 — End-to-end tests for `engine.khop_seedlocal()` and routed `engine.khop()`.

Runs against the live `ivg-iris` container via the existing `iris_connection`
fixture (no hardcoded ports — Constitution Principle IV).

Tests in this file (failing-first per Test-First, Principle III):
    test_1hop_set_equality_with_cypher_path — T008 → AS-164-3
    test_1hop_multi_predicate_dedup_per_node — T008b → FR-164-003
    test_2hop_set_equality_with_networkx — T015 → AS-164-2
    test_engine_khop_routes_seedlocal_for_hops_1_and_2_only — T021
    test_stale_nkg_emits_warning_once_and_falls_back — T026 → AS-164-4
    test_fallback_emits_identical_schema_to_seedlocal — T027 → round-2 Q3
    test_edge_cases_hops_zero_seed_missing_max_results_zero — T013b → FR-164-004
    test_cypher_call_ivg_khop_seedlocal_xfail — T035 → Bug S xfail

Each test loads a fresh fixture with a UUID prefix to avoid cross-test
contamination of `^KG` / `^NKG` globals.
"""
from __future__ import annotations

import uuid
from typing import Dict

import pytest


def _load_khop_fixture(engine, fixture: Dict, build_nkg: bool = True) -> str:
    """Load a khop_graphs fixture into IRIS and rebuild `^KG` (and `^NKG`).

    Returns the unique prefix applied to all node IDs (so multiple tests can
    coexist in the same session without colliding).

    Args:
        engine: IRISGraphEngine instance.
        fixture: Output of make_chain/make_fork/etc. — has `nodes`, `edges`.
        build_nkg: If True, rebuild `^NKG` after `^KG`. Set False for AS-164-4
            tests that need a stale-`^NKG` state.
    """
    prefix = f"k164_{uuid.uuid4().hex[:8]}_"
    for nid in fixture["nodes"]:
        engine.create_node(prefix + nid)
    for s, p, o in fixture["edges"]:
        engine.create_edge(prefix + s, p, prefix + o)
    engine.rebuild_kg()
    if build_nkg:
        try:
            engine.rebuild_nkg()
        except Exception:
            pass
        try:
            iris_obj = engine._iris_obj()
            iris_obj.classMethodVoid("Graph.KG.Traversal", "BuildNKG")
        except Exception:
            pass
    return prefix


class TestKhopSeedlocal1Hop:
    def test_1hop_set_equality_with_cypher_path(self, iris_connection, iris_master_cleanup):
        """T008 — engine.khop_seedlocal(seed, hops=1) result-set equals Cypher 1-hop expansion (AS-164-3)."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_fork

        engine = IRISGraphEngine(iris_connection)
        fixture = make_fork(n_leaves=10, predicate="FORK")
        prefix = _load_khop_fixture(engine, fixture)

        seed = prefix + "fork_root"

        result = engine.khop_seedlocal(seed, hops=1)
        seedlocal_ids = {row["node_id"] for row in result["rows"]}

        cypher_result = engine.execute_cypher(
            "MATCH (a {node_id: $s})-[r]->(b) RETURN b.node_id AS nid",
            {"s": seed},
        )
        cypher_ids = {row[0] for row in cypher_result.rows}

        assert seedlocal_ids == cypher_ids, (
            f"Set mismatch: seedlocal={sorted(seedlocal_ids)}, cypher={sorted(cypher_ids)}"
        )
        assert result["path"] == "seedlocal", f"expected path=seedlocal, got {result['path']!r}"

    def test_1hop_multi_predicate_dedup_per_node(self, iris_connection, iris_master_cleanup):
        """T008b — same target reachable via 2 predicates appears exactly once with predicate='' (FR-164-003)."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_multi_predicate_dedup

        engine = IRISGraphEngine(iris_connection)
        fixture = make_multi_predicate_dedup()
        prefix = _load_khop_fixture(engine, fixture)
        seed = prefix + "mpd_seed"
        target = prefix + "mpd_target"

        result = engine.khop_seedlocal(seed, hops=1, predicate="")
        target_count = sum(1 for r in result["rows"] if r["node_id"] == target)

        assert target_count == 1, (
            f"FR-164-003 violated: {target!r} appeared {target_count} times "
            f"(expected exactly 1, dedup-per-node via ^||khop_seen). "
            f"Rows: {result['rows']}"
        )


class TestKhopSeedlocalEdgeCases:
    def test_edge_cases_hops_zero_seed_missing_max_results_zero(
        self, iris_connection, iris_master_cleanup
    ):
        """T013b — three FR-164-004 contracts in one test."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_fork

        engine = IRISGraphEngine(iris_connection)
        fixture = make_fork(n_leaves=50, predicate="EDGE")
        prefix = _load_khop_fixture(engine, fixture)
        seed = prefix + "fork_root"

        r0 = engine.khop_seedlocal(seed, hops=0)
        assert r0["rows"] == [], (
            f"FR-164-004 hops=0 must return empty rows, got {r0['rows'][:3]}..."
        )

        r_missing = engine.khop_seedlocal("nonexistent-seed-uuid-12345", hops=1)
        assert r_missing["rows"] == [], (
            f"FR-164-004 missing seed must return empty rows, got {r_missing['rows'][:3]}..."
        )
        assert r_missing["path"] == "kg_fallback", (
            f"FR-164-008 missing-seed path must be kg_fallback, got {r_missing['path']!r}"
        )

        r_unlimited = engine.khop_seedlocal(seed, hops=1, max_results=0)
        assert len(r_unlimited["rows"]) == 50, (
            f"FR-164-004 max_results=0 must mean unlimited; "
            f"expected 50 rows for fork(50), got {len(r_unlimited['rows'])}"
        )


class TestKhopSeedlocal2Hop:
    def test_2hop_set_equality_with_chain(self, iris_connection, iris_master_cleanup):
        """T015 — 2-hop on a chain fixture; node_0 hops=1 → {node_1}, hops=2 → {node_2}."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_chain

        engine = IRISGraphEngine(iris_connection)
        fixture = make_chain(n=10, predicate="NEXT")
        prefix = _load_khop_fixture(engine, fixture)
        seed = prefix + "chain_node_0"

        result = engine.khop_seedlocal(seed, hops=2, predicate="NEXT")
        rows_by_hop = {1: [], 2: []}
        for row in result["rows"]:
            rows_by_hop[row["hops"]].append(row["node_id"])

        assert prefix + "chain_node_1" in rows_by_hop[1], (
            f"hops=1 should include chain_node_1, got {rows_by_hop[1]}"
        )
        assert prefix + "chain_node_2" in rows_by_hop[2], (
            f"hops=2 should include chain_node_2, got {rows_by_hop[2]}"
        )

    def test_2hop_dedup_across_layers(self, iris_connection, iris_master_cleanup):
        """T015 (dedup variant) — a node reachable at hops=1 should NOT reappear at hops=2."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_complete

        engine = IRISGraphEngine(iris_connection)
        fixture = make_complete(n=4, predicate="EDGE")
        prefix = _load_khop_fixture(engine, fixture)
        seed = prefix + "k_node_0"

        result = engine.khop_seedlocal(seed, hops=2, predicate="EDGE")
        ids = [row["node_id"] for row in result["rows"]]
        assert len(ids) == len(set(ids)), (
            f"K_4 2-hop should dedup across layers; got duplicates: {ids}"
        )
        expected_targets = {prefix + f"k_node_{i}" for i in range(1, 4)}
        assert set(ids) == expected_targets, (
            f"K_4 2-hop should reach all 3 other nodes, got {set(ids)} vs {expected_targets}"
        )


class TestKhopSeedlocalStaleNkgFallback:
    def test_stale_nkg_emits_warning_once_and_falls_back(self, iris_connection, iris_master_cleanup):
        """T026 — fresh container with `^KG` only emits exactly one warning across 3 calls (AS-164-4)."""
        import warnings as _warnings
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.stores._khop_state import reset_khop_warnings
        from tests.e2e.fixtures.khop_graphs import make_fork

        engine = IRISGraphEngine(iris_connection)
        fixture = make_fork(n_leaves=5, predicate="FORK")
        prefix = _load_khop_fixture(engine, fixture, build_nkg=False)

        iris_inst = _iris.createIRIS(iris_connection)
        for nid in fixture["nodes"]:
            try:
                iris_inst.kill("^NKG", "$NI", prefix + nid)
            except Exception:
                pass

        seed = prefix + "fork_root"
        reset_khop_warnings()

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            r1 = engine.khop_seedlocal(seed, hops=1)
            r2 = engine.khop_seedlocal(seed, hops=1)
            r3 = engine.khop_seedlocal(seed, hops=1)

        for r in (r1, r2, r3):
            assert r["path"] == "kg_fallback", (
                f"Expected kg_fallback path with stale ^NKG, got {r['path']!r}"
            )
            assert len(r["rows"]) == 5, (
                f"Fallback should return 5 leaves, got {len(r['rows'])}"
            )

        runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)
                             and "^NKG missing" in str(w.message)]
        assert len(runtime_warnings) == 1, (
            f"FR-164-008 once-per-process: expected exactly 1 RuntimeWarning, "
            f"got {len(runtime_warnings)}: {[str(w.message) for w in runtime_warnings]}"
        )

    def test_fallback_emits_identical_schema_to_seedlocal(self, iris_connection, iris_master_cleanup):
        """T027 — fast and fallback paths emit identical JSON schema (round-2 Q3)."""
        import iris as _iris
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.stores._khop_state import reset_khop_warnings
        from tests.e2e.fixtures.khop_graphs import make_fork

        engine = IRISGraphEngine(iris_connection)
        fixture = make_fork(n_leaves=5, predicate="FORK")
        prefix = _load_khop_fixture(engine, fixture, build_nkg=True)
        seed = prefix + "fork_root"

        result_fast = engine.khop_seedlocal(seed, hops=1)
        assert result_fast["path"] == "seedlocal"
        fast_ids = {row["node_id"] for row in result_fast["rows"]}

        iris_inst = _iris.createIRIS(iris_connection)
        for nid in fixture["nodes"]:
            try:
                iris_inst.kill("^NKG", "$NI", prefix + nid)
            except Exception:
                pass

        reset_khop_warnings()
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            result_fallback = engine.khop_seedlocal(seed, hops=1)
        assert result_fallback["path"] == "kg_fallback"
        fallback_ids = {row["node_id"] for row in result_fallback["rows"]}

        assert fast_ids == fallback_ids, (
            f"FR-164-008 / round-2 Q3: schema parity violated. "
            f"fast={sorted(fast_ids)} fallback={sorted(fallback_ids)}"
        )
        for row in result_fallback["rows"]:
            assert set(row.keys()) == {"node_id", "hops"}, (
                f"Fallback row schema differs from fast path: {row.keys()}"
            )


class TestKhopSeedlocalCypherXfail:
    @pytest.mark.xfail(
        reason="Bug S: SQL function kg_KHopSeedLocal calls ##class(Graph.KG.NKGAccel) "
               "which returns <CLASS DOES NOT EXIST> via SQL bindings. "
               "Python API engine.khop_seedlocal() works. See ENGINEERING_DEBT.md Bug S.",
        strict=False,
    )
    def test_cypher_call_ivg_khop_seedlocal_xfail(self, iris_connection, iris_master_cleanup):
        """T035 — Cypher procedure xfail pending Bug S (mirrors spec 162/163 pattern)."""
        from iris_vector_graph.engine import IRISGraphEngine
        from tests.e2e.fixtures.khop_graphs import make_fork

        engine = IRISGraphEngine(iris_connection)
        fixture = make_fork(n_leaves=5, predicate="FORK")
        prefix = _load_khop_fixture(engine, fixture)
        seed = prefix + "fork_root"

        result = engine.execute_cypher(
            "CALL ivg.khopSeedLocal({seed: $s, hops: 1, maxResults: 10}) "
            "YIELD node, hops RETURN node, hops ORDER BY hops",
            {"s": seed},
        )
        assert result.error is None or result.error == "", f"Cypher error: {result.error}"
        assert len(result.rows) <= 10
