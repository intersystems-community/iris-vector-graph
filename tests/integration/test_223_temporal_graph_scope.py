"""E2E integration tests for spec-223 — Graph-Scoped Temporal Storage.

These tests MUST run against a live IRIS container. The isolation guarantee is
in the global subscript structure (^KG("tout", graphId, ts, ...)), which cannot
be verified by mocking.

Decisive fixture: two graphs with identical source/predicate/target/timestamp
coordinates. Each graph must see only its own data.
"""
import os
import time
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
_PREFIX = f"t223_{uuid.uuid4().hex[:8]}"
_GRAPH_A = f"{_PREFIX}_acme"
_GRAPH_B = f"{_PREFIX}_metro"
_SRC = f"{_PREFIX}_svc_auth"
_PRED = "CALLS"
_TGT = f"{_PREFIX}_svc_db"
_TS = 1_750_000_000  # fixed historical timestamp


@pytest.fixture(scope="module")
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection, embedding_dimension=768)
    e.initialize_schema()
    yield e
    # Purge test data
    try:
        e._store._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
    except Exception:
        pass


# ── helpers ──────────────────────────────────────────────────────────────────

def _insert(eng, graph, ts=_TS, weight=1.0):
    """Insert via InsertEdge with explicit graphId — Phase 1 uses ObjectScript directly.
    Signature: InsertEdge(graphId, source, predicate, target, timestamp, weight, ...)
    """
    store = eng._store
    store._call_classmethod(
        "Graph.KG.TemporalIndex", "InsertEdge",
        graph, _SRC, _PRED, _TGT, str(ts), str(weight),
    )


def _query(eng, graph, ts_start=0, ts_end=9_999_999_999):
    """Query via QueryWindow with explicit graphId.
    Signature: QueryWindow(graphId, source, predicate, tsStart, tsEnd)
    """
    import json
    store = eng._store
    raw = str(store._call_classmethod(
        "Graph.KG.TemporalIndex", "QueryWindow",
        graph, _SRC, _PRED, str(ts_start), str(ts_end),
    ))
    return json.loads(raw)


# ── Phase 1: ObjectScript isolation tests ─────────────────────────────────────

@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestQueryWindowIsolation:
    """T001 — decisive isolation fixture."""

    def test_each_graph_sees_only_its_own_edge(self, eng):
        """Two graphs, identical coordinates — each QueryWindow returns only its own."""
        _insert(eng, _GRAPH_A, ts=_TS, weight=0.5)
        _insert(eng, _GRAPH_B, ts=_TS, weight=0.9)

        rows_a = _query(eng, _GRAPH_A)
        rows_b = _query(eng, _GRAPH_B)
        rows_default = _query(eng, "")

        assert len(rows_a) == 1, f"Graph A expected 1 edge, got {len(rows_a)}"
        assert len(rows_b) == 1, f"Graph B expected 1 edge, got {len(rows_b)}"
        assert abs(rows_a[0]["w"] - 0.5) < 1e-6, "Graph A weight wrong"
        assert abs(rows_b[0]["w"] - 0.9) < 1e-6, "Graph B weight wrong"
        # Default graph (key 0) has no data from A or B
        assert len(rows_default) == 0, f"Default graph should be empty, got {rows_default}"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPurgeScopedToGraph:
    """T002 — purge one graph, leave other intact."""

    def test_purge_graph_a_leaves_graph_b_intact(self, eng):
        src = f"{_PREFIX}_purge_s"; tgt = f"{_PREFIX}_purge_t"
        ts = _TS + 1000
        store = eng._store
        store._call_classmethod("Graph.KG.TemporalIndex","InsertEdge", _GRAPH_A, src, "PURGE_TEST", tgt, str(ts), "1.0")
        store._call_classmethod("Graph.KG.TemporalIndex","InsertEdge", _GRAPH_B, src, "PURGE_TEST", tgt, str(ts), "1.0")

        import json
        raw = str(store._call_classmethod("Graph.KG.TemporalIndex","PurgeRawBefore", _GRAPH_A, str(ts + 1)))
        deleted = int(str(raw).split(":")[0])
        assert deleted >= 1, f"Expected at least 1 deleted, got {deleted}"

        rows_b = json.loads(str(store._call_classmethod(
            "Graph.KG.TemporalIndex","QueryWindow", _GRAPH_B, src, "PURGE_TEST", "0", "9999999999"
        )))
        assert len(rows_b) == 1, f"Graph B should still have its edge, got {rows_b}"

        rows_a = json.loads(str(store._call_classmethod(
            "Graph.KG.TemporalIndex","QueryWindow", _GRAPH_A, src, "PURGE_TEST", "0", "9999999999"
        )))
        assert len(rows_a) == 0, f"Graph A should be empty after purge, got {rows_a}"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestVelocityScoped:
    """T003 — velocity detection scoped to graph."""

    def test_velocity_returns_only_graph_a_count(self, eng):
        src = f"{_PREFIX}_vel_s"; tgt = f"{_PREFIX}_vel_t"
        store = eng._store
        now = int(time.time())
        for i in range(5):
            store._call_classmethod(
                "Graph.KG.TemporalIndex","InsertEdge",
                _GRAPH_A, src, "VEL_TEST", tgt, str(now - 60 + i), "1.0",
            )
        vel_a = int(str(store._call_classmethod(
            "Graph.KG.TemporalIndex","GetVelocity", _GRAPH_A, src, "300", str(now + 1)
        )))
        vel_b = int(str(store._call_classmethod(
            "Graph.KG.TemporalIndex","GetVelocity", _GRAPH_B, src, "300", str(now + 1)
        )))
        assert vel_a >= 5, f"Graph A velocity should be >=5, got {vel_a}"
        assert vel_b == 0, f"Graph B velocity should be 0, got {vel_b}"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestAggregateScoped:
    """T004 — aggregate counts scoped to graph."""

    def test_aggregate_count_reflects_only_own_graph(self, eng):
        import json
        src = f"{_PREFIX}_agg_s"; tgt = f"{_PREFIX}_agg_t"
        store = eng._store
        ts_base = _TS + 2000
        for i in range(3):
            store._call_classmethod(
                "Graph.KG.TemporalIndex","InsertEdge",
                _GRAPH_A, src, "AGG_TEST", tgt, str(ts_base + i), str(float(i + 1)),
            )
        store._call_classmethod(
            "Graph.KG.TemporalIndex","InsertEdge",
            _GRAPH_B, src, "AGG_TEST", tgt, str(ts_base), "99.0",
        )
        raw_a = str(store._call_classmethod(
            "Graph.KG.TemporalIndex","GetAggregate",
            _GRAPH_A, src, "AGG_TEST", "count", str(ts_base - 1), str(ts_base + 10)
        ))
        raw_b = str(store._call_classmethod(
            "Graph.KG.TemporalIndex","GetAggregate",
            _GRAPH_B, src, "AGG_TEST", "count", str(ts_base - 1), str(ts_base + 10)
        ))
        count_a = int(raw_a) if raw_a.isdigit() else 0
        count_b = int(raw_b) if raw_b.isdigit() else 0
        assert count_a == 3, f"Graph A count should be 3, got {count_a}"
        assert count_b == 1, f"Graph B count should be 1, got {count_b}"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestDefaultGraphBackwardCompat:
    """T005 — single-tenant callers (no graph arg) must work unchanged."""

    def test_default_graph_roundtrip(self, eng):
        """Phase 2 gate: Python create_edge_temporal → get_edges_in_window."""
        src = f"{_PREFIX}_compat_s"; tgt = f"{_PREFIX}_compat_t"
        ts = _TS + 3000
        eng.create_edge_temporal(src, "COMPAT", tgt, timestamp=ts, weight=0.7)
        edges = eng.get_edges_in_window(src, "COMPAT", 0, 9_999_999_999)
        matching = [e for e in edges if e.get("ts") == ts]
        assert len(matching) >= 1, f"Default graph roundtrip failed: {edges}"
        assert abs(matching[0]["w"] - 0.7) < 1e-6


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPythonGraphScoping:
    """T033 — Python create_edge_temporal(graph=) reaches scoped global (Phase 2)."""

    def test_python_scoped_write_and_read(self, eng):
        src = f"{_PREFIX}_py_s"; tgt = f"{_PREFIX}_py_t"
        ts = _TS + 4000
        eng.create_edge_temporal(src, "PY_SCOPE", tgt, timestamp=ts, weight=0.3,
                                  graph=_GRAPH_A)
        edges_a = eng.get_edges_in_window(src, "PY_SCOPE", 0, 9_999_999_999,
                                           graph=_GRAPH_A)
        edges_b = eng.get_edges_in_window(src, "PY_SCOPE", 0, 9_999_999_999,
                                           graph=_GRAPH_B)
        assert len(edges_a) == 1, f"Graph A should have edge, got {edges_a}"
        assert len(edges_b) == 0, f"Graph B should be empty, got {edges_b}"


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestMigrationUtility:
    """T006/T007 — MigrateToGraphScoped."""

    def test_migration_on_empty_returns_zero(self, eng):
        """MigrateToGraphScoped on a fresh store returns 0 (no flat entries to migrate)."""
        result = eng._store._call_classmethod("Graph.KG.TemporalIndex","MigrateToGraphScoped")
        count = int(str(result)) if str(result).isdigit() else -1
        assert count == 0, f"Migration on empty/scoped store should return 0, got {count}"

    def test_migration_idempotent_on_scoped_data(self, eng):
        """T007 — running MigrateToGraphScoped twice on already-scoped data returns 0 both times."""
        r1 = int(str(eng._store._call_classmethod("Graph.KG.TemporalIndex","MigrateToGraphScoped")))
        r2 = int(str(eng._store._call_classmethod("Graph.KG.TemporalIndex","MigrateToGraphScoped")))
        assert r1 == 0, f"First run on scoped data should return 0, got {r1}"
        assert r2 == 0, f"Second run should also return 0, got {r2}"
